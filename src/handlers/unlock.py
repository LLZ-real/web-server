"""解锁加密文件：POST /unlock，body 为 JSON {"name": "路径", "password": "..."}。

返回一次性令牌（10 分钟有效，且只能用于该文件），后续下载用 ?token=。
"""

import os

from utils import crypto, httpjson, pathsafe, session


def handle(handler):
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

    key = crypto.unlock(real, password)
    if key is None:
        httpjson.send_json(handler, 401, {"ok": False, "error": "bad-password"}, "unlock denied")
        return

    token = session.issue(real, key)
    httpjson.send_json(
        handler, 200, {"ok": True, "token": token}, "unlock ok file=%s" % pathsafe.relative(real)
    )
