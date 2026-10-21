"""访问码登录与角色权限。

角色从低到高：guest（访客，只能浏览下载）→ builder（建设者，可上传新建目录）
→ admin（管理员，可删除、可看请求监视器）。

访问码存在 src/auth.json（已在 .gitignore 中忽略），首次启动自动生成随机码
并打印到控制台。登录成功后签发随机会话令牌，放在 HttpOnly Cookie 里，
令牌只存内存，服务重启即失效。
"""

import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie

ROLES = ("guest", "builder", "admin")
LEVEL = {"guest": 1, "builder": 2, "admin": 3}
LABELS = {"guest": "访客", "builder": "建设者", "admin": "管理员"}

CONFIG_PATH = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auth.json")
)

COOKIE_NAME = "wfs_session"
SESSION_TTL = 12 * 3600  # 12 小时，每次请求滑动续期

_codes = {}
_sessions = {}  # token -> {"role": str, "expires": float}
_lock = threading.Lock()


# ---------- 访问码 ----------

def _write_config(codes):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_codes():
    """读取 auth.json；缺字段或读不到时生成随机码并写回。返回 {role: code}。"""
    global _codes
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}

    codes, missing = {}, False
    for role in ROLES:
        value = str(raw.get(role, "") or "").strip() if isinstance(raw, dict) else ""
        if not value:
            value = secrets.token_urlsafe(9)
            missing = True
        codes[role] = value

    if missing or not os.path.exists(CONFIG_PATH):
        _write_config(codes)
    _codes = codes
    return codes


def role_for_code(code: str):
    """访问码 → 角色；不匹配返回 None。同码时按最高角色裁决。"""
    code = (code or "").strip()
    if not code:
        return None
    for role in ("admin", "builder", "guest"):
        expected = _codes.get(role, "")
        if expected and hmac.compare_digest(code, expected):
            return role
    return None


# ---------- 登录会话 ----------

def login(role: str) -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        _prune()
        _sessions[token] = {"role": role, "expires": time.time() + SESSION_TTL}
    return token


def role_for_token(token: str):
    if not token:
        return None
    with _lock:
        entry = _sessions.get(token)
        if not entry:
            return None
        now = time.time()
        if entry["expires"] < now:
            _sessions.pop(token, None)
            return None
        entry["expires"] = now + SESSION_TTL  # 滑动续期
        return entry["role"]


def logout(token: str):
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def _prune():
    now = time.time()
    for token in [t for t, e in _sessions.items() if e["expires"] < now]:
        _sessions.pop(token, None)


# ---------- Cookie ----------

def cookie_token(header: str):
    if not header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(header)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


def role_from_cookie(header: str):
    return role_for_token(cookie_token(header))


def session_cookie(token: str) -> str:
    return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
        COOKIE_NAME,
        token,
        SESSION_TTL,
    )


def cleared_cookie() -> str:
    return "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE_NAME


# ---------- 权限 ----------

def can(role, need: str) -> bool:
    return LEVEL.get(role or "", 0) >= LEVEL[need]
