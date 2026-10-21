"""文件上传：POST /upload?dir=<子目录>，解析 multipart/form-data 落盘。

表单可带一个普通字段 password：非空时文件加密落盘，下载需要该密码。
已知限制：整个 body 一次性读入内存（见设计文档 3.4.5）。
"""

import json
import os
import uuid
from urllib.parse import parse_qs, urlparse

from utils import crypto, multipart, pathsafe

MAX_NAME_TRIES = 100


def unique_name(real_dir: str, filename: str) -> str:
    """同名冲突时生成 "a (1).txt" 之类的候选名，兜底加 uuid 后缀。"""
    base, ext = os.path.splitext(filename)
    candidate = filename
    for i in range(1, MAX_NAME_TRIES):
        if not os.path.exists(os.path.join(real_dir, candidate)):
            return candidate
        candidate = "%s (%d)%s" % (base, i, ext)
    return "%s-%s%s" % (base, uuid.uuid4().hex[:8], ext)


def extract_password(parts) -> str:
    for part in parts:
        if multipart.filename_of(part["headers"]) is not None:
            continue
        if multipart.field_name(part["headers"]) == "password":
            value = part["body"].decode("utf-8", "replace")
            return value if value.strip() else ""
    return ""


def handle(handler):
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        return handler.send_text(400, "Bad Request: expected multipart")

    boundary = multipart.get_boundary(content_type)
    if not boundary:
        return handler.send_text(400, "Bad Request: missing boundary")

    try:
        length = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        return handler.send_text(400, "Bad Request: missing Content-Length")
    if length <= 0:
        return handler.send_text(400, "Bad Request: empty body")

    query = parse_qs(urlparse(handler.path).query)
    rel_dir = (query.get("dir") or [""])[0]
    try:
        real_dir = pathsafe.resolve(rel_dir)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")
    if not os.path.isdir(real_dir):
        return handler.send_text(404, "Not Found", note="dir-missing")

    body = handler.rfile.read(length)
    parts = multipart.parse(body, boundary)
    password = extract_password(parts)

    saved = []
    total = 0
    for part in parts:
        raw = multipart.filename_of(part["headers"])
        if raw is None:
            continue  # 非文件字段

        data = part["body"]
        total += len(data)
        filename = multipart.sanitize(raw) or uuid.uuid4().hex

        candidate = unique_name(real_dir, filename)
        try:
            target = pathsafe.resolve(
                (rel_dir + "/" if rel_dir else "") + candidate
            )
        except pathsafe.PathEscape:
            return handler.send_text(403, "Forbidden", note="path-escape")

        if password:
            data = crypto.encrypt_bytes(data, password)

        with open(target, "wb") as f:
            f.write(data)
        saved.append(pathsafe.relative(target))

    if not saved:
        return handler.send_text(400, "Bad Request: malformed multipart")

    payload = json.dumps(
        {"ok": True, "filename": saved[0], "files": saved, "encrypted": bool(password)}
    ).encode("utf-8")
    handler._send_head(
        200, "application/json; charset=utf-8", len(payload), {"Connection": "keep-alive"}
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(
        200,
        len(payload),
        "multipart file=%s size=%d%s"
        % (saved[0], total, " encrypted" if password else ""),
    )
