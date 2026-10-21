"""文件列表：扫描 storage/，服务端渲染 HTML 表格。"""

import html
import os
import time
from urllib.parse import quote

from utils import pathsafe

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "public", "index.html"
)

VIDEO_EXTS = (".mp4", ".webm", ".ogg", ".mov", ".mkv", ".m4v")


def human_size(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return "%.1f %s" % (value, unit)
    return "%d B" % n


def scan():
    """返回 storage 下的文件条目，按 mtime 降序。目录跳过。"""
    entries = []
    for name in os.listdir(pathsafe.STORAGE):
        if name.startswith("."):  # 隐藏文件（如 .gitkeep）不进列表
            continue
        real = os.path.join(pathsafe.STORAGE, name)
        if not os.path.isfile(real):
            continue
        stat = os.stat(real)
        entries.append(
            {"name": name, "size": stat.st_size, "mtime": stat.st_mtime}
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def render_rows(entries) -> str:
    if not entries:
        return '<tr><td colspan="4" class="empty">暂无文件，拖拽或点上方按钮上传</td></tr>'

    rows = []
    for entry in entries:
        name = entry["name"]
        href = "/files/" + quote(name)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["mtime"]))
        rows.append(
            "<tr>"
            '<td class="name">%s</td>'
            '<td class="size">%s</td>'
            '<td class="time">%s</td>'
            '<td class="op"><a href="%s" download>下载</a></td>'
            "</tr>"
            % (html.escape(name), human_size(entry["size"]), stamp, html.escape(href))
        )
    return "\n".join(rows)


def render_video(entries) -> str:
    for entry in entries:
        if entry["name"].lower().endswith(VIDEO_EXTS):
            return (
                '<video id="player" controls preload="metadata" src="/files/%s"></video>\n'
                '<p class="hint">拖动进度条，看 DevTools 里的 206 Range 请求</p>'
                % quote(entry["name"])
            )
    return '<p class="hint">storage 里放一个 mp4 文件，这里会出现视频测试区</p>'


def handle(handler):
    entries = scan()
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        page = f.read()
    page = page.replace("<!--FILES-->", render_rows(entries))
    page = page.replace("<!--VIDEO-->", render_video(entries))
    body = page.encode("utf-8")

    handler._send_head(
        200,
        "text/html; charset=utf-8",
        len(body),
        {"Connection": "keep-alive"},
    )
    if not handler._head_only:
        handler.wfile.write(body)
    handler.log_result(200, len(body), "count=%d" % len(entries))
