"""删除：DELETE /files/<path>。文件直接删，目录仅在为空时删。

加密文件需要先 POST /unlock 拿到 token，再用 `?token=` 删除；
密码本身永远不出现在 URL 与日志里。
"""

import json
import os
from urllib.parse import parse_qs, unquote, urlparse

from utils import crypto, pathsafe, session


def handle(handler, raw_name):
    name = unquote(raw_name)
    try:
        real = pathsafe.resolve(name)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")

    if not os.path.exists(real):
        return handler.send_text(404, "Not Found", note="missing")

    rel = pathsafe.relative(real)

    if os.path.isdir(real):
        if os.listdir(real):
            return handler.send_text(
                409, "Conflict: directory not empty", note="dir-not-empty"
            )
        try:
            os.rmdir(real)
        except OSError:
            return handler.send_text(500, "Internal Server Error", note="delete-failed")
        kind = "dir"
    else:
        # 加密文件是「有密码才能看」的资源，删除同样要密码，否则拿到列表的人可以直接毁掉它
        if crypto.is_encrypted(real):
            token = (parse_qs(urlparse(handler.path).query).get("token") or [""])[0]
            if session.key_for(token, real) is None:
                return handler.send_text(
                    401, "Unauthorized: password required", {"Cache-Control": "no-store"}
                )
        try:
            os.remove(real)
        except OSError:
            return handler.send_text(500, "Internal Server Error", note="delete-failed")
        kind = "file"

    payload = json.dumps({"ok": True, "filename": rel, "kind": kind}).encode("utf-8")
    handler._send_head(
        200, "application/json; charset=utf-8", len(payload), {"Connection": "keep-alive"}
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(200, len(payload), "delete %s=%s" % (kind, rel))
