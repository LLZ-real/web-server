"""静态公开资源：/static/<name>，只提供 public/ 下白名单内的 CSS/JS。

放在登录门禁之前，因为登录页也需要样式；用白名单杜绝任意文件读取。

缓存策略：默认 no-cache + must-revalidate，靠 ETag 做 304 协商，
避免改了 app.js 后浏览器仍用旧缓存（曾导致前端调用不存在的接口）。
页面引用时可带 ?v=<内容哈希>，带版本戳的请求允许长缓存。
"""

import hashlib
import os
from urllib.parse import parse_qs, urlparse

PUBLIC_DIR = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public")
)

ALLOWED = {
    "dash.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def _digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return ""


def asset_version(name):
    """给模板用：返回该静态资源的内容哈希，作为 ?v= 版本戳。"""
    path = os.path.join(PUBLIC_DIR, name)
    return _digest(path)


def handle(handler, name):
    ctype = ALLOWED.get(name)
    if not ctype:
        return handler.send_text(404, "Not Found", note="static-missing")
    path = os.path.join(PUBLIC_DIR, name)
    try:
        with open(path, "rb") as f:
            body = f.read()
    except OSError:
        return handler.send_text(404, "Not Found", note="static-missing")

    digest = _digest(path)
    etag = '"%s"' % digest
    versioned = bool(parse_qs(urlparse(handler.path).query).get("v"))

    if handler.headers.get("If-None-Match") == etag:
        handler._send_head_bare(304, {"ETag": etag})
        handler.log_result(304, 0, "static cache-hit")
        return

    cache = "public, max-age=31536000, immutable" if versioned else "no-cache, must-revalidate"
    handler._send_head(
        200, ctype, len(body),
        {"ETag": etag, "Cache-Control": cache},
    )
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(200, len(body), "static %s" % name)
