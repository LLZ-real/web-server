"""实时指标采集：线程安全的计数器与秒级时间序列，供仪表盘 / SSE 使用。

零第三方依赖；所有写入都在一把锁内完成，SSE 与轮询读取互不影响。
"""

import threading
import time

WINDOW = 180  # 内存中保留的秒级样本数

_lock = threading.Lock()
_started = time.time()
_total_requests = 0
_total_out = 0
_total_in = 0
_status = {"2": 0, "3": 0, "4": 0, "5": 0}
_methods = {}
_active = 0
_peak_rps = 0
_series = {}  # epoch 秒 -> {"req", "out", "in"}


def _bucket(t):
    return _series.setdefault(t, {"req": 0, "out": 0, "in": 0, "err": 0})


def conn_open():
    global _active
    with _lock:
        _active += 1


def conn_close():
    global _active
    with _lock:
        _active = max(0, _active - 1)


def record(method, path, status, nbytes, bytes_in=0):
    """每次响应结束后调用一次。"""
    global _total_requests, _total_out, _total_in, _peak_rps
    now = int(time.time())
    with _lock:
        _total_requests += 1
        _total_out += max(0, nbytes)
        _total_in += max(0, bytes_in)
        key = str(status)[:1]
        if key in _status:
            _status[key] += 1
        _methods[method] = _methods.get(method, 0) + 1
        b = _bucket(now)
        b["req"] += 1
        b["out"] += max(0, nbytes)
        b["in"] += max(0, bytes_in)
        if status >= 400:
            b["err"] += 1
        if b["req"] > _peak_rps:
            _peak_rps = b["req"]
        for t in [t for t in _series if t < now - WINDOW]:
            del _series[t]


def snapshot(series_len=60):
    now = int(time.time())
    with _lock:
        series = []
        for t in range(now - series_len + 1, now + 1):
            d = _series.get(t, {"req": 0, "out": 0, "in": 0, "err": 0})
            series.append({"t": t, "req": d["req"], "out": d["out"],
                           "in": d["in"], "err": d["err"]})
        return {
            "uptime": max(0, int(time.time() - _started)),
            "total_requests": _total_requests,
            "total_out": _total_out,
            "total_in": _total_in,
            "status": dict(_status),
            "methods": dict(_methods),
            "active": _active,
            "peak_rps": _peak_rps,
            "now": now,
            "series": series,
        }
