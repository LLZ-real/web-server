"""最近请求的内存环形缓冲，供页面内的请求监视器读取。"""

import threading
from collections import deque

MAX_EVENTS = 200

_events = deque(maxlen=MAX_EVENTS)
_lock = threading.Lock()


def record(event):
    with _lock:
        _events.appendleft(event)


def snapshot(limit=20):
    with _lock:
        return list(_events)[:limit]
