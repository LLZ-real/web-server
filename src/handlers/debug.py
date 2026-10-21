"""调试接口：GET /debug/requests，返回最近的请求记录给页面内的监视器。"""

import json

from utils import reqlog


def handle(handler):
    events = reqlog.snapshot(20)
    payload = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
    handler._send_head(
        200,
        "application/json; charset=utf-8",
        len(payload),
        {"Connection": "keep-alive", "Cache-Control": "no-store"},
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    # 不写访问日志：监视器每秒轮询一次，写日志会刷屏且污染监视器自身的记录
