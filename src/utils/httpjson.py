"""小型 JSON 请求/响应助手：只服务于本项目的 POST 接口。"""

import json


def read_json(handler):
    """读取 JSON body；长度非法或解析失败返回 None。"""
    try:
        length = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        return None
    if length <= 0 or length > 8192:
        return None
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def send_json(handler, status, payload, note=""):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler._send_head(status, "application/json; charset=utf-8", len(body))
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(status, len(body), note)
