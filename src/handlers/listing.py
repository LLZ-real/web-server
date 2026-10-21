"""文件列表：扫描 storage 当前目录，服务端渲染 HTML 表格。"""

import html
import os
import time
from urllib.parse import quote

from utils import auth, crypto, pathsafe

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "public", "index.html"
)

VIDEO_EXTS = (".mp4", ".webm", ".ogg", ".mov", ".mkv", ".m4v")

ROLE_HINTS = {
    "guest": "只读访客：可浏览与下载",
    "builder": "建设者：可上传与新建文件夹",
    "admin": "管理员：可上传、删除与查看监视器",
}

# 文件类型徽标：扩展名 → CSS 类名，决定徽标配色
BADGE_GROUPS = (
    ("image", (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico")),
    ("video", VIDEO_EXTS),
    ("audio", (".mp3", ".wav", ".flac", ".aac", ".m4a")),
    ("archive", (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2")),
    ("doc", (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")),
    ("code", (".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yml", ".yaml", ".sh")),
    ("text", (".txt", ".md", ".csv", ".log")),
)

LOCK_ICON = (
    '<svg class="lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    '<rect x="4" y="10" width="16" height="10" rx="2"/>'
    '<path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>'
)


def badge(filename: str):
    """返回 (徽标文字, 颜色类名)，如 ("MP4", "video")。"""
    ext = os.path.splitext(filename)[1].lower()
    label = ext[1:].upper()[:4] if ext else "FILE"
    for cls, exts in BADGE_GROUPS:
        if ext in exts:
            return label, cls
    return label, "other"


def human_size(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return "%.1f %s" % (value, unit)
    return "%d B" % n


def scan(real_dir: str):
    """列出目录下的子目录与文件；目录在前，文件按 mtime 降序。"""
    dirs, files = [], []
    for item in os.scandir(real_dir):
        if item.name.startswith("."):  # 隐藏文件（如 .gitkeep）不进列表
            continue
        try:
            if item.is_dir():
                stat = item.stat()
                dirs.append(
                    {"type": "dir", "name": item.name, "size": 0, "mtime": stat.st_mtime}
                )
            elif item.is_file():
                stat = item.stat()
                encrypted = crypto.is_encrypted(item.path)
                files.append(
                    {
                        "type": "file",
                        "name": item.name,
                        "size": crypto.plain_size(item.path) if encrypted else stat.st_size,
                        "mtime": stat.st_mtime,
                        "encrypted": encrypted,
                    }
                )
        except OSError:
            continue  # 权限不足或文件刚被删除，跳过即可

    dirs.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["mtime"], reverse=True)
    return dirs + files


def render_crumbs(rel_dir: str) -> str:
    """面包屑：根目录 / a / b，每一段都可点击。"""
    parts = ['<a href="/">根目录</a>']
    accumulated = []
    for segment in [s for s in rel_dir.split("/") if s]:
        accumulated.append(segment)
        href = "/?dir=" + quote("/".join(accumulated))
        parts.append('<span class="sep">/</span>')
        parts.append('<a href="%s">%s</a>' % (html.escape(href, quote=True), html.escape(segment)))
    return "".join(parts)


def render_rows(entries, rel_dir: str, role: str = "guest") -> str:
    if not entries:
        hint = (
            "拖文件到上方虚线框，或点「新建文件夹」"
            if auth.can(role, "builder")
            else "这个目录暂时是空的"
        )
        return (
            '<tr class="empty-row"><td colspan="4" class="empty">'
            "<strong>这里还没有内容</strong>"
            "<span>%s</span>"
            "</td></tr>" % hint
        )

    is_admin = auth.can(role, "admin")
    rows = []
    for entry in entries:
        name = entry["name"]
        rel = (rel_dir + "/" + name) if rel_dir else name
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["mtime"]))
        encoded = quote(rel)
        escaped_name = html.escape(name)
        escaped_name_attr = html.escape(name, quote=True)
        del_btn = (
            '<button class="btn danger" type="button" data-del="%s">删除</button>'
            % html.escape(encoded, quote=True)
            if is_admin
            else ""
        )

        if entry["type"] == "dir":
            rows.append(
                '<tr class="row-dir" data-name="%s" data-dir="1">'
                '<td class="name"><span class="badge folder">DIR</span>'
                '<a href="/?dir=%s">%s</a></td>'
                '<td class="size">—</td>'
                '<td class="time">%s</td>'
                '<td class="op">%s</td>'
                "</tr>"
                % (
                    escaped_name_attr,
                    html.escape(quote(rel), quote=True),
                    escaped_name,
                    stamp,
                    del_btn,
                )
            )
            continue

        label, cls = badge(name)
        file_url = "/files/" + encoded
        encrypted = entry.get("encrypted", False)
        if encrypted:
            # 加密文件点名字会 401，统一走「下载」按钮输密码
            name_cell = '<span class="fname">%s</span>%s' % (escaped_name, LOCK_ICON)
        else:
            name_cell = (
                '<a href="%s" target="_blank" rel="noopener">%s</a>'
                % (html.escape(file_url, quote=True), escaped_name)
            )
        rows.append(
            '<tr data-name="%s" data-rel="%s" data-size="%d" data-enc="%d">'
            '<td class="name"><span class="badge %s">%s</span>%s</td>'
            '<td class="size">%s</td>'
            '<td class="time">%s</td>'
            '<td class="op">'
            '<button class="btn" type="button" data-get="%s">下载</button>%s'
            "</td></tr>"
            % (
                escaped_name_attr,
                html.escape(rel, quote=True),
                entry["size"],
                1 if encrypted else 0,
                cls,
                html.escape(label),
                name_cell,
                human_size(entry["size"]),
                stamp,
                html.escape(file_url, quote=True),
                del_btn,
            )
        )
    return "\n".join(rows)


def handle(handler, rel_dir="", role="guest"):
    try:
        real_dir = pathsafe.resolve(rel_dir)
    except pathsafe.PathEscape:
        return handler.send_text(403, "Forbidden", note="path-escape")

    if not os.path.isdir(real_dir):
        return handler.send_text(404, "Not Found", note="dir-missing")

    entries = scan(real_dir)
    rel_dir = pathsafe.relative(real_dir)
    files = [e for e in entries if e["type"] == "file"]

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        page = f.read()
    page = page.replace("<!--CRUMBS-->", render_crumbs(rel_dir))
    page = page.replace("<!--FILES-->", render_rows(entries, rel_dir, role))
    page = page.replace("<!--COUNT-->", str(len(files)))
    page = page.replace("<!--TOTAL-->", human_size(sum(e["size"] for e in files)))
    page = page.replace("<!--DIR-->", html.escape(rel_dir, quote=True))
    page = page.replace("<!--ROLE-->", role)
    page = page.replace("<!--ROLE_LABEL-->", auth.LABELS.get(role, role))
    page = page.replace("<!--ROLE_HINT-->", ROLE_HINTS.get(role, ""))
    body = page.encode("utf-8")

    handler._send_head(
        200,
        "text/html; charset=utf-8",
        len(body),
        {"Connection": "keep-alive"},
    )
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(200, len(body), "dir=%s count=%d" % (rel_dir or "/", len(entries)))
