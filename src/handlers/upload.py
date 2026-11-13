"""文件上传：POST /upload?dir=<子目录>，解析 multipart/form-data 落盘。

表单可带一个普通字段 password：非空时文件加密落盘（v2 带完整性校验）。
修复：用 O_EXCL 原子创建消除并发同名覆盖（AUDIT §2.2）。
"""

import json
import os
import uuid
from urllib.parse import parse_qs, urlparse

from utils import crypto, multipart, pathsafe

MAX_NAME_TRIES = 100


def _candidates(filename: str):
    base, ext = os.path.splitext(filename)
    yield filename
    for i in range(1, MAX_NAME_TRIES):
        yield "%s (%d)%s" % (base, i, ext)
    yield "%s-%s%s" % (base, uuid.uuid4().hex[:8], ext)


def atomic_create(real_dir: str, filename: str, data: bytes):
    """用 O_EXCL 原子创建，冲突时换下一个候选名；返回 (绝对路径, 最终文件名)。"""
    last_err = None
    for candidate in _candidates(filename):
        target = os.path.join(real_dir, candidate)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except Exception:
                try:
                    os.remove(target)
                except OSError:
                    pass
                raise
            return target, candidate
        except FileExistsError:
            continue
        except OSError as e:
            last_err = e
            continue
    raise OSError("no free filename: %s" % (last_err or "exhausted"))


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
    length = handler._body_len
    if length <= 0:
        return handler.send_text(400, "Bad Request: empty body")
    if length > 256 * 1024 * 1024:  # 256MB 上限，避免无界内存占用
        return handler.send_text(413, "Payload Too Large")

    query = parse_qs(urlparse(handler.path).query)
    rel_dir = (query.get("dir") or [""])[0]
    try:
        real_dir = pathsafe.resolve(rel_dir)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")
    if not os.path.isdir(real_dir):
        return handler.send_text(404, "Not Found", note="dir-missing")

    body = handler.read_body(length)
    handler._body_done = True
    parts = multipart.parse(body, boundary)
    password = extract_password(parts)

    saved, total = [], 0
    for part in parts:
        raw = multipart.filename_of(part["headers"])
        if raw is None:
            continue
        data = part["body"]
        total += len(data)
        filename = multipart.sanitize(raw) or uuid.uuid4().hex
        if password:
            data = crypto.encrypt_bytes(data, password)
        try:
            target, final_name = atomic_create(real_dir, filename, data)
        except OSError:
            return handler.send_text(500, "Internal Server Error", note="write-failed")
        try:
            # 复核最终路径仍在 storage 内（防御文件名拼接意外）
            checked = pathsafe.resolve(
                (rel_dir + "/" if rel_dir else "") + final_name
            )
        except pathsafe.PathEscape:
            try:
                os.remove(target)
            except OSError:
                pass
            return handler.send_text(403, "Forbidden", note="path-escape")
        if os.path.normcase(checked) != os.path.normcase(target):
            try:
                os.remove(target)
            except OSError:
                pass
            return handler.send_text(403, "Forbidden", note="path-escape")
        saved.append(pathsafe.relative(checked))

    if not saved:
        return handler.send_text(400, "Bad Request: malformed multipart")

    payload = json.dumps({
        "ok": True, "filename": saved[0], "files": saved,
        "encrypted": bool(password), "bytes": total,
    }).encode("utf-8")
    handler._send_head(
        200, "application/json; charset=utf-8", len(payload),
        {"Connection": "keep-alive"},
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(
        200, len(payload),
        "multipart file=%s size=%d%s" % (saved[0], total, " encrypted" if password else ""),
    )
