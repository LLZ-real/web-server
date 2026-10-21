"""文件下载：GET /files/<name>，支持 ETag 条件请求与单段 Range。"""

import os
from urllib.parse import unquote

from utils import mime, pathsafe

CHUNK = 64 * 1024


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str, size: int):
    """解析 "bytes=0-1023" / "bytes=1024-" / "bytes=-500"，返回 (start, end)。"""
    _, _, spec = header.partition("=")
    spec = spec.strip()
    if "," in spec:
        spec = spec.split(",")[0]  # 多段 Range 本项目不支持，只处理第一段

    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        # 后缀形式：最后 N 字节
        n = int(end_s)
        if n <= 0:
            raise RangeNotSatisfiable
        return max(0, size - n), size - 1

    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start >= size or end < start:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def stream(handler, real, start, end):
    """流式发送 [start, end]，内存占用恒定为 64KB。"""
    remaining = end - start + 1
    with open(real, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(CHUNK, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
    return end - start + 1


def handle(handler, raw_name):
    name = unquote(raw_name)
    try:
        real = pathsafe.resolve(name)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")

    if not os.path.isfile(real):
        return handler.send_text(404, "Not Found", note="missing")

    stat = os.stat(real)
    size = stat.st_size
    etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)

    # 条件请求：命中则 304，无 body、无 Content-Length
    if handler.headers.get("If-None-Match") == etag:
        handler._send_head_bare(304, {"ETag": etag})
        handler.log_result(304, 0, "cache-hit")
        return

    range_header = handler.headers.get("Range")
    start, end, status = 0, max(size - 1, 0), 200
    extra = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
    }

    if range_header and size > 0:
        try:
            start, end = parse_range(range_header, size)
        except RangeNotSatisfiable:
            handler._send_head(
                416,
                "text/plain; charset=utf-8",
                0,
                {"Content-Range": "bytes */%d" % size, "ETag": etag},
            )
            handler.log_result(416, 0, "range-unsatisfiable")
            return
        except ValueError:  # 语法错误按 RFC 7233 忽略 Range，退化成 200
            start, end = 0, size - 1
        else:
            status = 206
            extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)

    length = (end - start + 1) if size > 0 else 0
    note = "range=%d-%d" % (start, end) if status == 206 else "full"

    handler._send_head(status, mime.guess(real), length, extra)
    if not handler._head_only and length > 0:
        stream(handler, real, start, end)
    handler.log_result(status, length, note)
