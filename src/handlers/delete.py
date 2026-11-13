"""删除：DELETE /files/<path>（仅管理员）。文件直接删，目录连同内容递归删除。

加密文件需要先 POST /unlock 拿到 token，再用 `?token=` 删除；
密码本身永远不出现在 URL 与日志里。
"""

import json
import os
import shutil
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
    if os.path.realpath(real) == os.path.realpath(pathsafe.STORAGE):
        return handler.send_text(403, "Forbidden: cannot delete storage root", note="root")

    rel = pathsafe.relative(real)
    if os.path.isdir(real):
        # 目录递归删除仍为 admin 特权；若子树内含加密文件，不做逐文件 token 校验
        # （契约 §4 已声明的边界），但这里记录一条审计备注便于展示时说明
        try:
            shutil.rmtree(real)
        except OSError:
            return handler.send_text(500, "Internal Server Error", note="delete-failed")
        kind = "dir"
    else:
        if crypto.is_encrypted(real):
            token = (parse_qs(urlparse(handler.path).query).get("token") or [""])[0]
            if session.key_for(token, real, handler._auth_token) is None:
                return handler.send_text(
                    401, "Unauthorized: password required",
                    note="password required", no_store=True,
                )
        try:
            os.remove(real)
        except OSError:
            return handler.send_text(500, "Internal Server Error", note="delete-failed")
        kind = "file"

    payload = json.dumps({"ok": True, "filename": rel, "kind": kind}).encode("utf-8")
    handler._send_head(200, "application/json; charset=utf-8", len(payload),
                       {"Connection": "keep-alive"})
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(200, len(payload), "delete %s=%s" % (kind, rel))
