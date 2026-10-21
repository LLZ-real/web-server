"""Web 文件服务器入口：进程启动、路由分发、全局异常兜底、日志。

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
    debug,
    delete,
    download,
    listing,
    login,
    mkdir,
    unlock,
    upload,
)
from utils import auth, pathsafe, reqlog  # noqa: E402

HOST = "0.0.0.0"
PORT = 8000

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
STORAGE_DIR = pathsafe.STORAGE

METHODS_NOT_ALLOWED = ("PUT", "PATCH", "OPTIONS", "TRACE", "CONNECT")
ALLOWED_METHODS = "GET, HEAD, POST, DELETE"


class FileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # 启用 keep-alive
    server_version = "WebFileServer/1.0"

    # ---------- 路由 ----------

    def _begin(self):
        """每次请求的公共初始化：解析 query、从 Cookie 还原角色。"""
        self._response_started = False
        parsed = urlparse(self.path)
        self.query = parsed.query
        self.role = auth.role_from_cookie(self.headers.get("Cookie"))
        return parsed.path

    def do_GET(self):
        self._head_only = False
        self._dispatch_get()

    def do_HEAD(self):
        # curl -I 走 HEAD，与 GET 共用同一套逻辑，只是不写 body
        self._head_only = True
        self._dispatch_get()

    def _dispatch_get(self):
        path = self._begin()
        try:
            if path == "/login":
                return login.show(self)
            if self.role is None:
                return self.redirect("/login", note="login required")
            if path == "/":
                rel_dir = (parse_qs(self.query).get("dir") or [""])[0]
                return listing.handle(self, rel_dir, self.role)
            if path == "/debug/requests":
                if not auth.can(self.role, "builder"):
                    return self.send_text(403, "Forbidden: builder required", note="rbac")
                return debug.handle(self)
            if path.startswith("/files/"):
                return download.handle(self, path[len("/files/"):])
            return self.send_text(404, "Not Found")
        except Exception:
            return self._handle_exception()

    def do_POST(self):
        self._head_only = False
        path = self._begin()
        try:
            if path == "/login":
                return login.submit(self)
            if self.role is None:
                return self.send_text(
                    401, "Unauthorized: login required", note="login required"
                )
            if path == "/logout":
                return login.logout(self)
            if path in ("/upload", "/mkdir"):
                if not auth.can(self.role, "builder"):
                    return self.send_text(
                        403, "Forbidden: builder required", note="rbac"
                    )
                return upload.handle(self) if path == "/upload" else mkdir.handle(self)
            if path == "/unlock":
                return unlock.handle(self)
            return self.send_text(405, "Method Not Allowed", {"Allow": ALLOWED_METHODS})
        except Exception:
            return self._handle_exception()

    def do_DELETE(self):
        self._head_only = False
        path = self._begin()
        try:
            if self.role is None:
                return self.send_text(
                    401, "Unauthorized: login required", note="login required"
                )
            if not auth.can(self.role, "admin"):
                return self.send_text(403, "Forbidden: admin required", note="rbac")
            if path.startswith("/files/"):
                return delete.handle(self, path[len("/files/"):])
            return self.send_text(404, "Not Found")
        except Exception:
            return self._handle_exception()

    def _method_not_allowed(self):
        self.send_text(405, "Method Not Allowed", {"Allow": ALLOWED_METHODS})

    # ---------- 响应工具 ----------

    def send_text(self, status, text, extra_headers=None, note=""):
        """发送 text/plain 响应，始终带 Content-Length（keep-alive 的前提）。"""
        body = text.encode("utf-8")
        self._send_head(status, "text/plain; charset=utf-8", len(body), extra_headers)
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)
        self.log_result(status, len(body), note)

    def redirect(self, location, extra_headers=None, note="redirect"):
        """临时跳转：GET 用 302，POST 用 303（避免刷新时重复提交表单）。"""
        status = 303 if self.command == "POST" else 302
        headers = {"Location": location}
        headers.update(extra_headers or {})
        self._send_head(status, "text/html; charset=utf-8", 0, headers)
        self.log_result(status, 0, note)

    def _handle_exception(self):
        """未捕获异常兜底：traceback 只进服务端日志，不回给客户端。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            # 客户端主动断开（常见于视频拖动时取消上一段 Range 请求），不是服务端错误
            self.close_connection = True
            return
        traceback.print_exc()
        if getattr(self, "_response_started", False):
            # 响应头已发出，无法再改成 500，只能断连接
            self.close_connection = True
            return
        self.send_text(500, "Internal Server Error")

    def _send_head(self, status, content_type, length, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self._response_started = True

    def _send_head_bare(self, status, extra_headers=None):
        """只发状态行与给定头部（304 不带 Content-Length）。"""
        self.send_response(status)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self._response_started = True

    def send_error(self, code, message=None, explain=None):
        """覆盖标准库的 HTML 错误页，改成 text/plain。"""
        self.send_text(code, message or self.responses[code][0])

    # ---------- 日志 ----------

    def log_message(self, fmt, *args):
        pass  # 用 log_result 统一输出，默认日志不含状态码和字节数

    def log_request(self, code="-", size="-"):
        pass

    def log_result(self, status, nbytes, note=""):
        # 解锁令牌等同于临时密码，写日志前统一脱敏
        path = re.sub(r"(token=)[^&]*", r"\1***", self.path)
        sys.stderr.write(
            '[%s] %s "%s %s" %s %sB %s\n'
            % (
                self.log_date_time_string(),
                self.client_address[0],
                self.command,
                path,
                status,
                nbytes,
                note,
            )
        )
        sys.stderr.flush()
        reqlog.record(
            {
                "time": self.log_date_time_string().rsplit(" ", 1)[-1],
                "client": self.client_address[0],
                "method": self.command,
                "path": path,
                "status": status,
                "bytes": nbytes,
                "note": note,
            }
        )


# 其余 HTTP 方法统一 405，符合错误处理矩阵
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

    server = ThreadingHTTPServer((HOST, PORT), FileHandler)
    print("Serving on http://%s:%d  (LAN IP available)" % (HOST, PORT))
    print("Storage root: %s" % STORAGE_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
