"""登录页与访问码登录：GET /login、POST /login、POST /logout。

访问码只用于校验，不写日志；登录成功后在 HttpOnly Cookie 中放随机会话令牌。
"""

import os
from urllib.parse import parse_qs

from utils import auth

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "public", "login.html"
)


def show(handler):
    if handler.role is not None:
        return handler.redirect("/", note="already-logged-in")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        page = f.read()

    error = ""
    if parse_qs(handler.query).get("err"):
        error = '<p class="err">访问码不正确，请重试。</p>'
    page = page.replace("<!--ERROR-->", error)

    body = page.encode("utf-8")
    handler._send_head(
        200, "text/html; charset=utf-8", len(body), {"Connection": "keep-alive"}
    )
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(200, len(body), "login-page")


def submit(handler):
    try:
        length = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        return handler.send_text(400, "Bad Request: missing Content-Length")
    if length <= 0 or length > 4096:
        return handler.send_text(400, "Bad Request: invalid body")

    body = handler.rfile.read(length).decode("utf-8", "replace")
    code = (parse_qs(body).get("code") or [""])[0]

    role = auth.role_for_code(code)
    if role is None:
        handler.redirect("/login?err=1", note="login failed")
        return

    token = auth.login(role)
    handler.redirect(
        "/",
        {"Set-Cookie": auth.session_cookie(token)},
        note="login ok role=%s" % role,
    )


def logout(handler):
    auth.logout(auth.cookie_token(handler.headers.get("Cookie")))
    handler.redirect("/login", {"Set-Cookie": auth.cleared_cookie()}, note="logout")
