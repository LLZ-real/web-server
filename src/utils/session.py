"""解锁令牌：密码校验通过后签发，避免把密码塞进下载 URL 或日志。

令牌与「具体文件」+「签发它的会话」双重绑定，10 分钟过期，进程重启即失效；
会话登出时该会话签发的令牌会被立即清除（修复 AUDIT §2.1 已知限制）。
"""

import secrets
import threading
import time

TTL = 600

_tokens = {}  # token -> {"real", "keys", "expires", "owner"}
_lock = threading.Lock()


def issue(real: str, keys, owner: str = "") -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        _prune()
        _tokens[token] = {
            "real": real, "keys": keys, "owner": owner,
            "expires": time.time() + TTL,
        }
    return token


def key_for(token: str, real: str, session_token: str = ""):
    """令牌有效、与目标文件匹配、且属于当前会话时返回密钥，否则 None。"""
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
        if entry["owner"] and entry["owner"] != (session_token or ""):
            return None
        return entry["keys"]


def revoke_session(session_token: str):
    """登出时清除该会话签发的全部解锁令牌。"""
    if not session_token:
        return
    with _lock:
        for token in [t for t, e in _tokens.items() if e["owner"] == session_token]:
            _tokens.pop(token, None)


def _prune():
    now = time.time()
    for token in [t for t, e in _tokens.items() if e["expires"] < now]:
        _tokens.pop(token, None)
