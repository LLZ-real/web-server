"""解锁加密文件：POST /unlock，body 为 JSON {"name": "路径", "password": "..."}。

返回与「文件 + 当前会话」绑定的令牌（10 分钟有效），后续下载用 ?token=。
带失败限流，防止暴力破密码与 PBKDF2 CPU 耗尽（AUDIT §3.1）。
"""

import os

from utils import crypto, httpjson, pathsafe, session


def handle(handler):
    if handler.rate_limited("/unlock"):
        return
    data = httpjson.read_json(handler)
    if data is None:
        return handler.send_text(400, "Bad Request: invalid json")
    name = str(data.get("name", ""))
    password = str(data.get("password", ""))
    if not password:
        return handler.send_text(400, "Bad Request: empty password")

    try:
        real = pathsafe.resolve(name)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")
    if not os.path.isfile(real):
        return handler.send_text(404, "Not Found", note="missing")
    if not crypto.is_encrypted(real):
        return handler.send_text(400, "Bad Request: file is not encrypted")

    keys = crypto.unlock(real, password)
    if keys is None:
        handler.note_fail("/unlock")
        httpjson.send_json(
            handler, 401, {"ok": False, "error": "bad-password"}, "unlock denied"
        )
        return

    handler.note_ok("/unlock")
    token = session.issue(real, keys, handler._auth_token)
    httpjson.send_json(
        handler, 200, {"ok": True, "token": token},
        "unlock ok file=%s" % pathsafe.relative(real),
    )
