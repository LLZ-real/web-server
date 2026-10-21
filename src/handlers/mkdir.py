"""新建文件夹：POST /mkdir，body 为 JSON {"dir": "当前目录", "name": "新目录名"}。"""

import os

from utils import httpjson, pathsafe


def handle(handler):
    data = httpjson.read_json(handler)
    if data is None:
        return handler.send_text(400, "Bad Request: invalid json")

    raw_name = str(data.get("name", ""))
    rel_dir = str(data.get("dir", ""))
    try:
        name = pathsafe.check_entry_name(raw_name.strip())
    except ValueError:
        return handler.send_text(400, "Bad Request: invalid folder name")

    try:
        real_dir = pathsafe.resolve(rel_dir)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")
    if not os.path.isdir(real_dir):
        return handler.send_text(404, "Not Found", note="dir-missing")

    target = os.path.join(real_dir, name)
    if os.path.exists(target):
        return httpjson.send_json(
            handler, 409, {"ok": False, "error": "exists"}, "mkdir-exists"
        )

    try:
        os.makedirs(target)
    except OSError:
        return handler.send_text(500, "Internal Server Error", note="mkdir-failed")

    rel = pathsafe.relative(target)
    httpjson.send_json(handler, 200, {"ok": True, "dir": rel}, "mkdir dir=%s" % rel)
