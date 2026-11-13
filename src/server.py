"""Web 文件服务器入口：进程启动、路由分发、全局异常兜底、日志与实时指标。

零第三方依赖：只用标准库 http.server + socket。
"""

import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import (  # noqa: E402
    dashboard,
    debug,
    delete,
    download,
    listing,
    login,
    mkdir,
    search,
    static,
    unlock,
    upload,
)
from utils import auth, metrics, pathsafe, ratelimit, reqlog  # noqa: E402

HOST = "0.0.0.0"
PORT = 8000

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
STORAGE_DIR = pathsafe.STORAGE

METHODS_NOT_ALLOWED = ("PUT", "PATCH", "OPTIONS", "TRACE", "CONNECT")
ALLOWED_METHODS = "GET, HEAD, POST, DELETE"

# 全局安全响应头：用户上传的内容在同源下默认不应获得执行能力
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
}
# 页面类响应使用较宽的 CSP（需要内联脚本/样式、data: 图标）
PAGE_CSP = SECURITY_HEADERS["Content-Security-Policy"]

# 活动类型强制下载，杜绝同源存储型 XSS（AUDIT §1.5）
_ATTACH_EXTS = {
    ".html", ".htm", ".xhtml", ".svg", ".svgz", ".js", ".mjs",
    ".xml", ".xht", ".shtml", ".jsp", ".php", ".phtml", ".hta",
}


class FileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # 启用 keep-alive
    server_version = "WebFileServer/2.0"
    sys_version = ""  # 不暴露 Python 精确版本

    # ---------- 请求生命周期 ----------

    def _begin(self):
        """公共初始化：解析 query、还原角色、登记待消费的请求体长度。"""
        self._response_started = False
        self._bytes_in = 0
        self._body_len = 0
        self._body_done = False
        try:
            self._body_len = max(0, int(self.headers.get("Content-Length", "0") or "0"))
        except ValueError:
            self._body_len = 0
        self._auth_token = auth.cookie_token(self.headers.get("Cookie"))
        parsed = urlparse(self.path)
        self.query = parsed.query
        self.role = auth.role_for_token(self._auth_token)
        metrics.conn_open()
        return parsed.path

    def _drain_body(self):
        """发响应前把未读的请求体消费掉，避免 keep-alive 下污染下一个请求。

        AUDIT §1.1：本项目大量 400/401/403/404 分支在从未 read body 的情况下
        返回，导致残留字节被当成下一个请求行。这里统一在写响应头前补齐。
        """
        if self._body_done or self._body_len <= 0:
            return
        remaining = self._body_len - self._bytes_in
        if remaining > 0:
            try:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    self._bytes_in += len(chunk)
            except OSError:
                self.close_connection = True
        self._body_done = True

    def read_body(self, length=None):
        """handler 读取请求体的统一入口（会计入 _bytes_in）。"""
        n = self._body_len if length is None else min(length, self._body_len)
        if n <= 0 or self._body_done:
            return b""
        data = self.rfile.read(n)
        self._bytes_in += len(data)
        if len(data) < n:
            self.close_connection = True
        return data

    def do_GET(self):
        self._head_only = False
        self._dispatch_get()

    def do_HEAD(self):
        self._head_only = True
        self._dispatch_get()

    def _dispatch_get(self):
        path = self._begin()
        try:
            if path == "/login":
                return login.show(self)
            if path.startswith("/static/"):
                # CSS/JS 是公开资源，登录页也需要基础样式
                return static.handle(self, path[len("/static/"):])
            if self.role is None:
                # API / 数据类请求返回 401 JSON，避免 fetch 静默跟随 302
                # 拿到登录页 HTML 后 json() 解析失败（曾导致"搜索失败，请重试"）
                if path.startswith("/api/") or path == "/debug/requests":
                    from utils import httpjson  # noqa: E402
                    return httpjson.send_json(
                        self, 401, {"ok": False, "error": "login required"},
                        "login required",
                    )
                return self.redirect("/login", note="login required")
            if path == "/":
                rel_dir = (parse_qs(self.query).get("dir") or [""])[0]
                return listing.handle(self, rel_dir, self.role)
            if path == "/debug/requests":
                if not auth.can(self.role, "builder"):
                    return self.send_text(403, "Forbidden: builder required", note="rbac")
                return debug.handle(self)
            if path == "/api/metrics":
                return dashboard.metrics(self)
            if path == "/api/events":
                return dashboard.events(self)
            if path == "/api/search":
                return search.handle(self)
            if path.startswith("/files/"):
                return download.handle(self, path[len("/files/"):])
            return self.send_text(404, "Not Found")
        except Exception:
            return self._handle_exception()
        finally:
            metrics.conn_close()

    def do_POST(self):
        self._head_only = False
        path = self._begin()
        try:
            if path == "/login":
                return login.submit(self)
            if self.role is None:
                return self.send_text(401, "Unauthorized: login required", note="login required")
            if path == "/logout":
                return login.logout(self)
            if path in ("/upload", "/mkdir"):
                if not auth.can(self.role, "builder"):
                    return self.send_text(403, "Forbidden: builder required", note="rbac")
                return upload.handle(self) if path == "/upload" else mkdir.handle(self)
            if path == "/unlock":
                return unlock.handle(self)
            if path == "/api/demo/traffic":
                return dashboard.demo_traffic(self)
            return self.send_text(405, "Method Not Allowed", {"Allow": ALLOWED_METHODS})
        except Exception:
            return self._handle_exception()
        finally:
            metrics.conn_close()

    def do_DELETE(self):
        self._head_only = False
        path = self._begin()
        try:
            if self.role is None:
                return self.send_text(401, "Unauthorized: login required", note="login required")
            if not auth.can(self.role, "admin"):
                return self.send_text(403, "Forbidden: admin required", note="rbac")
            if path.startswith("/files/"):
                return delete.handle(self, path[len("/files/"):])
            return self.send_text(404, "Not Found")
        except Exception:
            return self._handle_exception()
        finally:
            metrics.conn_close()

    def _method_not_allowed(self):
        try:
            self._begin()
        except Exception:
            pass
        self.send_text(405, "Method Not Allowed", {"Allow": ALLOWED_METHODS})
        metrics.conn_close()

    def _client_ip(self):
        return self.client_address[0] if self.client_address else ""

    def rate_limited(self, scope=""):
        """登录/解锁失败限流；返回已发送的响应或 None。"""
        ip = self._client_ip() + scope
        if ratelimit.is_blocked(ip):
            self.send_text(
                429, "Too Many Requests: try again later",
                {"Retry-After": str(ratelimit.retry_after(ip))}, note="rate-limited",
            )
            return True
        return False

    def note_fail(self, scope=""):
        ratelimit.note_fail(self._client_ip() + scope)

    def note_ok(self, scope=""):
        ratelimit.note_ok(self._client_ip() + scope)

    # ---------- 响应工具 ----------

    def _headers_for(self, status, extra, no_store=False, page=False):
        h = {}
        h.update(SECURITY_HEADERS)
        if page:
            h["Content-Security-Policy"] = PAGE_CSP
        if no_store:
            h["Cache-Control"] = "no-store, private"
            h["Pragma"] = "no-cache"
            h["Vary"] = "*"
        h.update(extra or {})
        # keep-alive 的正确性完全由 _drain_body() 保证：它会在写响应头之前
        # 消费剩余 body；短读时自行置 close_connection，无需在此按状态判断。
        return h

    def send_text(self, status, text, extra_headers=None, note="", no_store=False):
        body = text.encode("utf-8")
        self._drain_body()
        self._send_head(status, "text/plain; charset=utf-8", len(body),
                        self._headers_for(status, extra_headers, no_store))
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)
        self.log_result(status, len(body), note)

    def redirect(self, location, extra_headers=None, note="redirect"):
        status = 303 if self.command == "POST" else 302
        headers = {"Location": location}
        headers.update(extra_headers or {})
        self._drain_body()
        self._send_head(status, "text/html; charset=utf-8", 0,
                        self._headers_for(status, headers))
        self.log_result(status, 0, note)

    def _handle_exception(self):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            self.close_connection = True
            return
        traceback.print_exc()
        if getattr(self, "_response_started", False):
            self.close_connection = True
            return
        self.send_text(500, "Internal Server Error")

    def _send_head(self, status, content_type, length, extra_headers=None,
                   no_store=False, page=False):
        """发响应头；除显式传入的头部外统一附加安全头。

        注意：调用者若已自行组装 headers，可直接传 _headers_for 的结果；
        这里再做一次合并，保证任何路径都不会漏掉 nosniff / CSP。
        """
        headers = self._headers_for(status, {}, no_store, page)
        headers.update(extra_headers or {})
        # 显式传入的 Connection / Cache-Control 优先
        self._drain_body()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self._response_started = True

    def _send_head_bare(self, status, extra_headers=None, no_store=False):
        """只发状态行与给定头部（304 不带 Content-Length）。"""
        headers = self._headers_for(status, {}, no_store)
        headers.pop("Content-Length", None)
        headers.update(extra_headers or {})
        self._drain_body()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self._response_started = True

    def send_error(self, code, message=None, explain=None):
        """覆盖标准库的 HTML 错误页，改成 text/plain。"""
        self.send_text(code, message or self.responses[code][0])

    # ---------- 日志 ----------

    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        pass

    def log_result(self, status, nbytes, note=""):
        # 只记录 path，彻底剥离 query；任何大小写/拼写的敏感参数都不会进日志
        path = urlparse(self.path).path
        if path != self.path:
            path += "?<redacted>"
        self._drain_body()
        sys.stderr.write(
            '[%s] %s "%s %s" %s %sB %s\n'
            % (self.log_date_time_string(), self.client_address[0],
               self.command, path, status, nbytes, note)
        )
        sys.stderr.flush()
        reqlog.record({
            "time": self.log_date_time_string().rsplit(" ", 1)[-1],
            "client": self.client_address[0],
            "method": self.command,
            "path": path,
            "status": status,
            "bytes": nbytes,
            "note": note,
        })
        metrics.record(self.command, path, status, nbytes, self._bytes_in)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# 其余 HTTP 方法统一 405
for _method in METHODS_NOT_ALLOWED:
    setattr(FileHandler, "do_" + _method, FileHandler._method_not_allowed)
del _method


def main():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    codes = auth.load_codes()
    print("Access codes (src/auth.json):")
    for role in auth.ROLES:
        print("  %-8s %-9s %s" % (role, auth.LABELS[role], codes[role]))

    server = Server((HOST, PORT), FileHandler)
    print("Serving on http://%s:%d  (LAN IP available)" % (HOST, PORT))
    print("Storage root: %s" % STORAGE_DIR)
    print("Dashboard: /  (实时流量仪表盘 · 协议实验室)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
