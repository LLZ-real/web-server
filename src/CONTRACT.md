# 接口契约（CONTRACT）

本文件定义服务器对外可见的 HTTP 行为，任何改动都必须先更新此契约再改代码。
实现见 `server.py` 与 `handlers/`。

## 通用约定

- 协议：HTTP/1.1，默认开启 keep-alive（`protocol_version = "HTTP/1.1"`）
- 所有响应都带 `Content-Length`（304 除外），否则连接无法复用
- 存储根：`storage/`，一切文件路径必须经 `utils/pathsafe.resolve()` 校验

## 1. `GET /`（含 `HEAD /`）

| 项 | 值 |
|---|---|
| 响应状态 | `200` |
| 响应头 | `Content-Type: text/html; charset=utf-8`、`Content-Length`、`Connection: keep-alive` |
| 响应体 | 文件列表 HTML（按 mtime 降序；隐藏文件不进列表） |

## 2. `GET /files/<name>`（含 `HEAD /files/<name>`）

请求头（均可选）：`Range: bytes=start-end`、`If-None-Match: "<etag>"`

| 状态 | 触发条件 | 关键响应头 |
|---|---|---|
| `200` | 无 Range、无命中缓存 | `Content-Type`、`Content-Length`、`Accept-Ranges: bytes`、`ETag` |
| `206` | Range 合法 | 同上 + `Content-Range: bytes s-e/size`，`Content-Length: e-s+1` |
| `304` | `If-None-Match` 等于当前 ETag | `ETag`；无 body、**无 `Content-Length`** |
| `403` | 路径越权（含 `..`、反斜杠、符号链接逃逸） | `Content-Type: text/plain` |
| `404` | 文件不存在或目标是目录 | `Content-Type: text/plain` |
| `416` | Range 越界 | `Content-Range: bytes */<size>`，`Content-Length: 0` |

- ETag 定义：`"<mtime_ns 十六进制>-<size 十六进制>"`，零 I/O 开销
- Range 语法：`bytes=0-1023` / `bytes=1024-` / `bytes=-500`；多段 Range 只处理第一段
- 语法非法的 Range 按 RFC 7233 忽略，退化为 `200`

## 3. `POST /upload`

| 项 | 值 |
|---|---|
| 请求头 | `Content-Type: multipart/form-data; boundary=...`、`Content-Length` |
| 请求体 | multipart 格式，文件字段需带 `filename` |
| 成功响应 | `200`，`application/json`：`{"ok": true, "filename": "a.txt", "files": ["a.txt"]}` |
| `400` | `Content-Type` 非 multipart / boundary 缺失 / body 为空 / 无文件字段 |
| `403` | 文件名越权 |

- 文件名处理：取 basename（`\` 与 `/` 都视为分隔符）→ 去控制字符 → 空名用 uuid
- 同名冲突：`a.txt` → `a (1).txt` → `a (2).txt`，超过 100 次追加 uuid 后缀
- 响应体为 JSON 时前端自行 `location.reload()`，不使用 302

## 4. 错误处理矩阵

| 场景 | 状态码 | 响应体 |
|---|---|---|
| 路径不存在 | `404` | `Not Found` |
| 路径越权 | `403` | `Forbidden` |
| Range 越界 | `416` | 空 |
| ETag 匹配 | `304` | 空 |
| Content-Type 非 multipart | `400` | `Bad Request: expected multipart` |
| boundary 缺失 | `400` | `Bad Request: missing boundary` |
| multipart 解析失败 | `400` | `Bad Request: malformed multipart` |
| 未知路由 | `404` | `Not Found` |
| 方法不支持 | `405` | `Method Not Allowed`，带 `Allow: GET, POST` |
| handler 内部异常 | `500` | `Internal Server Error`（traceback 只进服务端日志） |

所有错误响应均为 `text/plain; charset=utf-8`，不使用标准库默认的 HTML 错误页。

## 5. 日志契约

每行一条，字段固定为「时间 / 客户端 IP / 请求行 / 状态码 / 字节数 / 备注」：

```
[2025-01-15 14:32:01] 192.168.1.7 "GET /files/a.mp4" 206 5242880B range=5242880-10485759
[2025-01-15 14:32:03] 192.168.1.7 "POST /upload" 200 61B multipart file=a.txt size=1024
[2025-01-15 14:32:05] 192.168.1.7 "GET /files/a.mp4" 304 0B cache-hit
```
