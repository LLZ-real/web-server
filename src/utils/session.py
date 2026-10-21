"""解锁令牌：密码校验通过后签发，避免把密码塞进下载 URL 或日志。

令牌与「具体文件」绑定，10 分钟过期，进程重启即失效。
"""

import secrets
import threading
import time

TTL = 600

_tokens = {}  # token -> {"real": 路径, "key": 派生密钥, "expires": 时间戳}
_lock = threading.Lock()


def issue(real: str, key: bytes) -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        _prune()
        _tokens[token] = {"real": real, "key": key, "expires": time.time() + TTL}
    return token


def key_for(token: str, real: str):
    """令牌有效且与目标文件匹配时返回密钥，否则返回 None。"""
    if not token:
        return None
    with _lock:
        entry = _tokens.get(token)
        if not entry:
            return None
        if entry["expires"] < time.time():
            _tokens.pop(token, None)
            return None
        if entry["real"] != real:
            return None
        return entry["key"]


def _prune():
    now = time.time()
    for token in [t for t, e in _tokens.items() if e["expires"] < now]:
        _tokens.pop(token, None)
