"""multipart/form-data 切分。

字节层面的契约（最容易写错的地方）：
    原始报文：
        --BOUNDARY\\r\\n
        Content-Disposition: form-data; name="file"; filename="a.txt"\\r\\n
        Content-Type: text/plain\\r\\n
        \\r\\n
        <file content>\\r\\n
        --BOUNDARY--\\r\\n

    * 头部与正文之间是连续两个 CRLF（\\r\\n\\r\\n）
    * 字段之间是 \\r\\n--BOUNDARY\\r\\n
    * 结束标记是 --BOUNDARY--
    * 正文末尾那个 \\r\\n 属于分隔符，不属于文件内容，必须剥离
"""

import re
from urllib.parse import unquote


def parse_headers(blob: bytes) -> dict:
    """解析一个 part 的头部块。"""
    headers = {}
    for line in blob.split(b"\r\n"):
        if not line.strip():
            continue
        key, sep, value = line.partition(b":")
        if not sep:
            continue
        headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return headers


def parse(body: bytes, boundary: str):
    """按 boundary 把 body 切成 [{"headers": {...}, "body": bytes}, ...]。"""
    delimiter = b"--" + boundary.encode("latin-1")
    sections = body.split(delimiter)

    parts = []
    for section in sections[1:]:  # sections[0] 是第一个 delimiter 之前的 preamble
        if section.startswith(b"--"):  # 结束标记 --BOUNDARY--
            break
        if section.startswith(b"\r\n"):
            section = section[2:]
        if section.endswith(b"\r\n"):
            section = section[:-2]  # 剥离属于分隔符的 CRLF
        if not section:
            continue

        header_blob, sep, data = section.partition(b"\r\n\r\n")
        if not sep:
            continue
        parts.append({"headers": parse_headers(header_blob), "body": data})

    return parts


def get_boundary(content_type: str):
    """从 Content-Type 中取出 boundary，取不到返回 None。"""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "", re.I)
    return m.group(1) if m else None


def filename_of(headers: dict):
    """从 Content-Disposition 中提取文件名；没有 filename 字段返回 None。

    同时兼容 RFC 5987 的 `filename*=UTF-8''%E4%B8%AD` 与浏览器实际发送的
    "裸 UTF-8 字节被按 latin-1 解码" 这种常见形态。
    """
    cd = headers.get("content-disposition", "")

    m = re.search(r"filename\*\s*=\s*([^;]+)", cd, re.I)
    if m:
        value = m.group(1).strip().strip('"')
        charset, _, encoded = value.partition("'")
        encoded = encoded.partition("'")[2] or encoded
        return unquote(encoded, encoding=charset or "utf-8", errors="replace")

    m = re.search(r'filename\s*=\s*"([^"]*)"', cd, re.I)
    if m:
        value = m.group(1)
    else:
        m = re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
        if not m:
            return None
        value = m.group(1).strip()

    if not value:
        return None

    # 浏览器把 UTF-8 文件名按 latin-1 塞进头部，这里还原回 UTF-8
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def field_name(headers: dict):
    """取 Content-Disposition 里的 name="..."（普通表单字段用）。"""
    cd = headers.get("content-disposition", "")
    m = re.search(r'name\s*=\s*"([^"]*)"', cd, re.I)
    if m:
        return m.group(1)
    m = re.search(r"name\s*=\s*([^;]+)", cd, re.I)
    return m.group(1).strip() if m else None


def sanitize(filename: str) -> str:
    """清洗文件名：去目录成分、控制字符，空名交给调用方处理。"""
    # Windows 的 \\ 与 POSIX 的 / 都要当成路径分隔符
    name = filename.replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c != "\x00").strip()
    return name
