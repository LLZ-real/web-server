"""路径安全校验：把用户输入的文件名约束在 storage/ 目录内。

只用字符串过滤 ".." 是不可靠的：`..%2f`、`....//`、Windows 的 `..\\`、
符号链接、`\\\\?\\` 前缀都能绕过简单匹配。这里采用 realpath + commonpath：
先把路径规范化成真实绝对路径（消解 ../ 与符号链接），再校验它仍在 storage 之下。
"""

import os

# storage 根目录，相对本文件定位，保证从任何 cwd 启动都一致
STORAGE = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage")
)


class PathEscape(Exception):
    """用户输入的目标路径位于 storage 之外。"""


def _clean(user_path: str) -> str:
    """丢掉 NUL / 控制字符 / 双向控制字符（U+202A~E、U+2066~9 可用于视觉欺骗）。"""
    def ok(c):
        o = ord(c)
        return c.isprintable() and not (0x202A <= o <= 0x202E) and not (
            0x2066 <= o <= 0x2069
        )
    return "".join(c for c in user_path if ok(c))


def resolve(user_path: str) -> str:
    """把用户输入的文件名转成 storage 内的绝对路径，越权时抛 PathEscape。"""
    cleaned = _clean(user_path)
    candidate = os.path.realpath(os.path.join(STORAGE, cleaned))
    # 用 commonpath 而非 startswith，因为 "/storage-evil" 能骗过 startswith
    try:
        common = os.path.commonpath([candidate, STORAGE])
    except ValueError:  # 不同盘符（Windows）
        raise PathEscape(user_path)
    # Windows 下大小写不敏感，比较前先 normcase
    if os.path.normcase(common) != os.path.normcase(STORAGE):
        raise PathEscape(user_path)
    return candidate


def relative(real: str) -> str:
    """绝对路径 → storage 内的相对路径（统一用 / 分隔，根目录返回空串）。"""
    rel = os.path.relpath(real, STORAGE)
    return "" if rel == "." else rel.replace(os.sep, "/")


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    "COM%d" % i for i in range(1, 10)
} | {"LPT%d" % i for i in range(1, 10)}


def check_entry_name(name: str):
    """校验「新建文件夹」这类单段名称，非法时抛 ValueError。"""
    if not name or name in (".", "..") or name.rstrip(". ") != name:
        raise ValueError("empty-name")
    if "/" in name or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("separator")
    if any(ord(ch) < 32 for ch in name):
        raise ValueError("control-char")
    if name.split(".")[0].upper() in _WIN_RESERVED:
        raise ValueError("reserved-name")
    return name
