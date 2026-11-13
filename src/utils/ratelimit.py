"""轻量滑动窗口限流：按客户端 IP 统计失败次数，超限后短暂锁定。

用于登录（/login）与解锁（/unlock）：既挡暴力破访问码/密码，也挡
「每次 /unlock 都跑一次 PBKDF2」造成的 CPU 耗尽。
"""

import threading
import time

WINDOW = 60          # 统计窗口（秒）
MAX_FAILS = 8        # 窗口内允许的失败次数
LOCKOUT = 30         # 触发后的锁定时长（秒）

_lock = threading.Lock()
_fails = {}          # ip -> [失败时间戳]
_blocked = {}        # ip -> 解封时间戳


def _now():
    return time.time()


def is_blocked(ip):
    with _lock:
        until = _blocked.get(ip, 0)
        if until and until > _now():
            return True
        if until:
            _blocked.pop(ip, None)
            _fails.pop(ip, None)
        return False


def note_fail(ip):
    """记录一次失败；返回本次是否刚触发锁定。"""
    now = _now()
    with _lock:
        arr = [t for t in _fails.get(ip, []) if t > now - WINDOW]
        arr.append(now)
        _fails[ip] = arr
        if len(arr) >= MAX_FAILS:
            _blocked[ip] = now + LOCKOUT
            _fails[ip] = []
            return True
    return False


def note_ok(ip):
    with _lock:
        _fails.pop(ip, None)
        _blocked.pop(ip, None)


def retry_after(ip):
    with _lock:
        return max(0, int(_blocked.get(ip, 0) - _now()))
