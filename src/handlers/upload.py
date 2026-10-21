"""文件上传：POST /upload，解析 multipart/form-data 落盘。

已知限制：整个 body 一次性读入内存（见设计文档 3.4.5）。
"""

import json
import os
import uuid

from utils import multipart, pathsafe

MAX_NAME_TRIES = 100


def unique_name(filename: str) -> str:
    """同名冲突时生成 "a (1).txt" 之类的候选名，兜底加 uuid 后缀。"""
    base, ext = os.path.splitext(filename)
    candidate = filename
    for i in range(1, MAX_NAME_TRIES):
        if not os.path.exists(os.path.join(pathsafe.STORAGE, candidate)):
            return candidate
        candidate = "%s (%d)%s" % (base, i, ext)
    return "%s-%s%s" % (base, uuid.uuid4().hex[:8], ext)


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

    body = handler.rfile.read(length)
    parts = multipart.parse(body, boundary)

    saved = []
    for part in parts:
        raw = multipart.filename_of(part["headers"])
        if raw is None:
            continue  # 非文件字段，跳过

        filename = multipart.sanitize(raw)
        if not filename:
            filename = uuid.uuid4().hex

        try:
            candidate = pathsafe.resolve(unique_name(filename))
        except pathsafe.PathEscape:
            return handler.send_text(403, "Forbidden", note="path-escape")

        with open(candidate, "wb") as f:
            f.write(part["body"])
        saved.append(os.path.basename(candidate))

    if not saved:
        return handler.send_text(400, "Bad Request: malformed multipart")

    payload = json.dumps({"ok": True, "filename": saved[0], "files": saved}).encode("utf-8")
    handler._send_head(
        200, "application/json; charset=utf-8", len(payload), {"Connection": "keep-alive"}
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(
        200,
        len(payload),
        "multipart file=%s size=%d" % (saved[0], sum(len(p["body"]) for p in parts)),
    )
