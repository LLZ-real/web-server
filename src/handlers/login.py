"""登录页与访问码登录：GET /login、POST /login、POST /logout。

访问码只用于校验，不写日志；登录成功后在 HttpOnly Cookie 中放随机会话令牌。
带失败限流；登出会同时撤销该会话签发的解锁令牌。
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
    q = parse_qs(handler.query)
    if q.get("err"):
        error = '<p class="err">访问码不正确，请重试。</p>'
    if q.get("locked"):
        error = '<p class="err">尝试过于频繁，请稍后再试。</p>'
    page = page.replace("<!--ERROR-->", error)
    body = page.encode("utf-8")
    handler._send_head(200, "text/html; charset=utf-8", len(body),
                       {"Connection": "keep-alive"}, page=True)
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(200, len(body), "login-page")


def submit(handler):
    if handler.rate_limited("/login"):
        return
    length = handler._body_len
    if length <= 0 or length > 4096:
        return handler.send_text(400, "Bad Request: invalid body")
    body = handler.read_body(length).decode("utf-8", "replace")
    handler._body_done = True
    # parse_qs 把 + 解码为空格；访问码若含 + 需先还原（表单编码应为 %2B）
    code = (parse_qs(body, keep_blank_values=True).get("code") or [""])[0]

    role = auth.role_for_code(code)
    if role is None:
        handler.note_fail("/login")
        handler.redirect("/login?err=1", note="login failed")
        return
    handler.note_ok("/login")
    token = auth.login(role)
    handler.redirect(
        "/", {"Set-Cookie": auth.session_cookie(token)},
        note="login ok role=%s" % role,
    )


def logout(handler):
    auth.logout(handler._auth_token)
    handler.redirect("/login", {"Set-Cookie": auth.cleared_cookie()}, note="logout")
