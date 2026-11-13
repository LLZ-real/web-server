"""全局搜索：GET /api/search?q=<关键词>，递归遍历 storage。

返回匹配的文件/目录列表，前端可点击结果直接跳转到其所在目录并高亮。
只做名称匹配（不做内容索引），限制递归深度与结果数，避免大目录卡顿。
"""

import json
import os
from urllib.parse import parse_qs, urlparse

from utils import crypto, pathsafe

MAX_RESULTS = 120
MAX_DEPTH = 12


def _walk(root, query, out, depth=0):
    if depth > MAX_DEPTH or len(out) >= MAX_RESULTS:
        return
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for item in entries:
        if len(out) >= MAX_RESULTS:
            return
        if item.name.startswith("."):
            continue
        if query not in item.name.lower():
            # 目录仍要递归进去找匹配的子项
            if item.is_dir(follow_symlinks=False):
                _walk(item.path, query, out, depth + 1)
            continue
        try:
            if item.is_dir(follow_symlinks=False):
                out.append({
                    "name": item.name, "rel": pathsafe.relative(item.path),
                    "dir": pathsafe.relative(item.path), "size": 0,
                    "ext": "", "kind": "dir", "encrypted": False,
                    "mtime": int(item.stat().st_mtime),
                })
                _walk(item.path, query, out, depth + 1)
            elif item.is_file(follow_symlinks=False):
                st = item.stat()
                encrypted = crypto.is_encrypted(item.path)
                out.append({
                    "name": item.name, "rel": pathsafe.relative(item.path),
                    "dir": pathsafe.relative(os.path.dirname(item.path)),
                    "size": crypto.plain_size(item.path) if encrypted else st.st_size,
                    "ext": os.path.splitext(item.name)[1].lower(),
                    "kind": "file", "encrypted": encrypted,
                    "mtime": int(st.st_mtime),
                })
        except OSError:
            continue


def handle(handler):
    q = (parse_qs(urlparse(handler.path).query).get("q") or [""])[0].strip().lower()
    if len(q) < 1:
        payload = json.dumps({"ok": True, "q": q, "results": []}).encode("utf-8")
        handler._send_head(200, "application/json; charset=utf-8", len(payload),
                           {"Cache-Control": "no-store"}, no_store=True)
        if not handler._head_only:
            handler.wfile.write(payload)
        handler.log_result(200, len(payload), "search empty")
        return
    if len(q) > 128:
        q = q[:128]
    results = []
    _walk(pathsafe.STORAGE, q, results)
    payload = json.dumps({
        "ok": True, "q": q, "results": results,
        "truncated": len(results) >= MAX_RESULTS,
    }, ensure_ascii=False).encode("utf-8")
    handler._send_head(200, "application/json; charset=utf-8", len(payload),
                       {"Cache-Control": "no-store"}, no_store=True)
    if not handler._head_only:
        handler.wfile.write(payload)
    handler.log_result(200, len(payload), "search q=%s hits=%d" % (q, len(results)))
