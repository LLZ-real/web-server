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


def resolve(user_path: str) -> str:
    """把用户输入的文件名转成 storage 内的绝对路径，越权时抛 PathEscape。"""
    # 1. 丢掉 NUL 与控制字符
    cleaned = "".join(c for c in user_path if c.isprintable())

    # 2. 拼接后取 realpath，消解 ../ 与符号链接
    candidate = os.path.realpath(os.path.join(STORAGE, cleaned))

    # 3. 前缀校验：必须仍在 STORAGE 下
    #    用 commonpath 而非 startswith，因为 "/storage-evil" 能骗过 startswith("/storage")
    try:
        common = os.path.commonpath([candidate, STORAGE])
    except ValueError:  # 不同盘符（Windows）
        raise PathEscape(user_path)

    # Windows 下大小写不敏感，比较前先 normcase
    if os.path.normcase(common) != os.path.normcase(STORAGE):
        raise PathEscape(user_path)

    return candidate
