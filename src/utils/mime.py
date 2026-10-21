"""MIME 类型推断。补全标准库缺失的常用类型，文本类型自动补 charset。"""

import mimetypes

mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("application/octet-stream", "")

DEFAULT = "application/octet-stream"


def guess(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if mime is None:
        return DEFAULT
    if mime.startswith("text/"):
        return mime + "; charset=utf-8"
    return mime
