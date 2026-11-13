"""文件下载：GET /files/<path>，支持 ETag 条件请求与单段 Range。

加密文件需要先 POST /unlock 拿到 token，再用 `?token=` 下载；
密码本身永远不出现在 URL 与日志里。
"""

import os
from urllib.parse import parse_qs, quote, unquote, urlparse

from utils import crypto, mime, pathsafe, session

CHUNK = 64 * 1024

# 活动类型一律 attachment，杜绝同源存储型 XSS（AUDIT §1.5）
ATTACH_EXTS = {
    ".html", ".htm", ".xhtml", ".svg", ".svgz", ".js", ".mjs",
    ".xml", ".xht", ".shtml", ".jsp", ".php", ".phtml", ".hta",
}


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str, size: int):
    """解析 "bytes=0-1023" / "bytes=1024-" / "bytes=-500"，返回 (start, end)。

    size=0 时任何 Range 都不可满足（修复 AUDIT §2.4 的 206 + 负 end）。
    """
    _, _, spec = header.partition("=")
    spec = spec.strip()
    if "," in spec:
        spec = spec.split(",")[0]
    if size <= 0:
        raise RangeNotSatisfiable
    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        n = int(end_s)
        if n <= 0:
            raise RangeNotSatisfiable
        return max(0, size - n), size - 1
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start >= size or end < start:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def _etag_matches(header: str, etag: str) -> bool:
    """RFC 7232 弱比较：支持 `*`、`W/` 前缀与逗号分隔的多个 ETag。"""
    if not header:
        return False
    bare = etag.lstrip("W/").strip('"')
    for item in header.split(","):
        item = item.strip()
        if item == "*":
            return True
        if item.lstrip("W/").strip('"') == bare:
            return True
    return False


def _content_disposition(name: str) -> str:
    return "attachment; filename*=UTF-8''%s" % quote(name, safe="")


def stream_plain(handler, real, start, end):
    remaining = end - start + 1
    with open(real, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(CHUNK, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def stream_encrypted(handler, real, keys, start, end):
    for chunk in crypto.iter_decrypted(real, keys, start, end):
        handler.wfile.write(chunk)


def handle(handler, raw_name):
    name = unquote(raw_name)
    try:
        real = pathsafe.resolve(name)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")
    if not os.path.isfile(real):
        return handler.send_text(404, "Not Found", note="missing")

    stat = os.stat(real)
    encrypted = crypto.is_encrypted(real)
    keys = None

    if encrypted:
        token = (parse_qs(urlparse(handler.path).query).get("token") or [""])[0]
        keys = session.key_for(token, real, handler._auth_token)
        if keys is None:
            return handler.send_text(
                401, "Unauthorized: password required", note="password required",
                no_store=True,
            )
        size = crypto.plain_size(real)
        if size < 0 or size > crypto.file_size(real):
            return handler.send_text(500, "Internal Server Error", note="bad header")
    else:
        size = stat.st_size

    etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)
    no_store = bool(encrypted)
    extra = {"Accept-Ranges": "bytes", "ETag": etag}

    # 活动类型强制下载；文件名用 RFC 5987 编码，避免响应头注入
    ext = os.path.splitext(real)[1].lower()
    if ext in ATTACH_EXTS:
        extra["Content-Disposition"] = _content_disposition(os.path.basename(real))
        extra["X-Content-Type-Options"] = "nosniff"

    # 条件请求：命中则 304（加密文件同样 no-store）
    if _etag_matches(handler.headers.get("If-None-Match", ""), etag):
        handler._send_head_bare(304, {"ETag": etag}, no_store=no_store)
        handler.log_result(304, 0, "cache-hit")
        return

    range_header = handler.headers.get("Range")
    start, end, status = 0, max(size - 1, 0), 200
    if range_header:
        try:
            start, end = parse_range(range_header, size)
        except RangeNotSatisfiable:
            handler._send_head(
                416, "text/plain; charset=utf-8", 0,
                {"Content-Range": "bytes */%d" % size, "ETag": etag},
                no_store=no_store,
            )
            handler.log_result(416, 0, "range-unsatisfiable")
            return
        except ValueError:  # 语法错误按 RFC 7233 忽略 Range
            start, end = 0, max(size - 1, 0)
        else:
            if start == 0 and end == size - 1:
                status = 200  # 请求整个文件时不返回 206
            else:
                status = 206
                extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)

    length = (end - start + 1) if size > 0 else 0
    note = "range=%d-%d" % (start, end) if status == 206 else "full"
    if encrypted:
        note += " encrypted"

    handler._send_head(status, mime.guess(real), length, extra, no_store=no_store)
    if not handler._head_only and length > 0:
        try:
            if encrypted:
                stream_encrypted(handler, real, keys, start, end)
            else:
                stream_plain(handler, real, start, end)
        except crypto.IntegrityError:
            # 响应头已发出，只能断连接；traceback 不回客户端
            handler.close_connection = True
            handler.log_result(status, 0, "integrity-check-failed")
            return
        except (ConnectionResetError, BrokenPipeError):
            handler.close_connection = True
            return
    handler.log_result(status, length, note)
