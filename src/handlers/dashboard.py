"""实时仪表盘数据：/api/metrics（快照）、/api/events（SSE 推流）、
POST /api/demo/traffic（一键生成演示流量，方便现场展示曲线）。

SSE 使用标准 text/event-stream；不写访问日志，避免每秒刷屏。
"""

import json
import threading
import time

from utils import auth, metrics as mstore, reqlog

_DEMO_LOCK = threading.Lock()
_demo_running = False


def metrics_snapshot():
    """给页面用的合并快照：指标 + 最近请求 + 存储概况。"""
    snap = mstore.snapshot(60)
    snap["events"] = reqlog.snapshot(12)
    from utils import pathsafe
    import os
    files, total, dirs = 0, 0, 0
    for root, dnames, fnames in os.walk(pathsafe.STORAGE):
        dirs += len(dnames)
        for fn in fnames:
            if fn.startswith("."):
                continue
            files += 1
            try:
                total += os.stat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    snap["storage"] = {"files": files, "dirs": dirs, "bytes": total}
    snap["roles"] = auth.LABELS
    return snap


def metrics(handler):
    payload = json.dumps(metrics_snapshot(), ensure_ascii=False).encode("utf-8")
    handler._send_head(
        200, "application/json; charset=utf-8", len(payload),
        {"Cache-Control": "no-store"}, no_store=True,
    )
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(200, len(payload), "metrics snapshot")


def events(handler):
    """SSE：每秒推一帧指标快照；客户端断开时自动退出。"""
    handler._drain_body()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache, no-store")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    for k, v in handler._headers_for(200, {}, no_store=True).items():
        if k not in ("Cache-Control", "Connection", "Content-Length", "Vary", "Pragma"):
            handler.send_header(k, v)
    handler.end_headers()
    handler._response_started = True
    handler.close_connection = True
    try:
        for _ in range(300):  # 最多 5 分钟，前端会重连
            data = json.dumps(metrics_snapshot(), ensure_ascii=False)
            handler.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
            handler.wfile.flush()
            time.sleep(1)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    # SSE 不计入普通访问日志，避免自身刷屏；仅登记一次指标
    mstore.record("GET", "/api/events", 200, 0, 0)


def demo_traffic(handler):
    """一键演示：后台生成一段带随机状态码/字节数的请求记录。

    只写入指标与请求日志缓冲区，不产生真实网络请求、不触碰用户文件。
    现场展示时点一下即可看到曲线、状态分布与监视器同时动起来。
    """
    global _demo_running
    with _DEMO_LOCK:
        if _demo_running:
            payload = b'{"ok": false, "error": "already running"}'
            handler._send_head(200, "application/json; charset=utf-8", len(payload))
            if not handler._head_only:
                handler.wfile.write(payload)
            handler.log_result(200, len(payload), "demo already running")
            return
        _demo_running = True

    def run():
        global _demo_running
        import random
        import datetime
        methods = ["GET", "GET", "GET", "POST", "GET", "HEAD", "DELETE"]
        paths = ["/", "/files/demo.mp4", "/files/report.pdf", "/api/metrics",
                 "/files/photo.png", "/debug/requests", "/files/data.zip"]
        statuses = [200, 200, 200, 206, 304, 404, 403]
        ip = "demo.local"
        try:
            for i in range(45):
                m = random.choice(methods)
                p = random.choice(paths)
                s = random.choice(statuses)
                n = random.randint(0, 900000) if s in (200, 206) else 0
                note = ("range=%d-%d" % (n // 2, n)) if s == 206 else (
                    "cache-hit" if s == 304 else "demo")
                now = datetime.datetime.now().strftime("%H:%M:%S")
                mstore.record(m, p, s, n, random.randint(0, 4000) if m == "POST" else 0)
                reqlog.record({
                    "time": now, "client": ip, "method": m, "path": p,
                    "status": s, "bytes": n, "note": note,
                })
                time.sleep(0.22)
        finally:
            with _DEMO_LOCK:
                _demo_running = False

    threading.Thread(target=run, daemon=True).start()
    payload = b'{"ok": true, "seconds": 10}'
    handler._send_head(200, "application/json; charset=utf-8", len(payload))
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(200, len(payload), "demo traffic started")
