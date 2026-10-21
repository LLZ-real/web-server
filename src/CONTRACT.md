# 接口契约（CONTRACT）

本文件定义服务器对外可见的 HTTP 行为，任何改动都必须先更新此契约再改代码。
实现见 `server.py` 与 `handlers/`。

## 通用约定

- 协议：HTTP/1.1，默认开启 keep-alive（`protocol_version = "HTTP/1.1"`）
- 所有响应都带 `Content-Length`（304 除外），否则连接无法复用
- 存储根：`storage/`，一切文件路径必须经 `utils/pathsafe.resolve()` 校验

## 0. 鉴权与角色

三级角色，权限只升不降：

| 角色 | 访问码字段 | 权限 |
|---|---|---|
| `guest` 访客 | `guest` | 浏览目录、下载文件（含播放视频、解锁加密文件） |
| `builder` 建设者 | `builder` | 访客权限 + 上传、新建文件夹、查看请求监视器 |
| `admin` 管理员 | `admin` | 建设者权限 + 删除文件/文件夹（含非空目录） |

- 访问码存于 `src/auth.json`（已在 `.gitignore` 中忽略）；启动时缺失则生成随机码并写回，
  同时打印到控制台。三个访问码应互不相同。
- 登录成功后签发随机令牌，写入 `Set-Cookie: wfs_session=<token>; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200`；
  令牌只存服务端内存，12 小时滑动过期，进程重启即失效。
- **未登录**时：页面类请求（`GET /`、`GET /files/*`、`GET /debug/requests`）一律 `302 Location: /login`；
  接口类请求（`POST` / `DELETE`）返回 `401 Unauthorized: login required`。
- **角色不足**时返回 `403`（`Forbidden: builder required` / `Forbidden: admin required`）。
- 权限在服务端强制校验；前端的隐藏只是界面效果，不是安全边界。

### 0.1 `GET /login`

| 项 | 值 |
|---|---|
| 响应 | `200` 登录页 HTML；已登录时 `302 Location: /` |
| 查询参数 | `err=1` 时显示「访问码不正确」 |

### 0.2 `POST /login`

| 项 | 值 |
|---|---|
| 请求头 | `Content-Type: application/x-www-form-urlencoded`，body 上限 4KB |
| 请求体 | `code=<访问码>` |
| 成功响应 | `303 Location: /` + `Set-Cookie: wfs_session=...` |
| 失败响应 | `303 Location: /login?err=1`（不回显访问码） |
| `400` | `Content-Length` 缺失/非法/超限 |

### 0.3 `POST /logout`

清除 Cookie（`Max-Age=0`）并 `303 Location: /login`；未登录时先返回 `401`。

## 1. `GET /`（含 `HEAD /`）

| 项 | 值 |
|---|---|
| 响应状态 | `200` |
| 查询参数 | `dir=<子目录>`（可选，缺省为根目录；用 `/` 分隔并逐段 URL 编码） |
| 响应头 | `Content-Type: text/html; charset=utf-8`、`Content-Length`、`Connection: keep-alive` |
| 响应体 | 当前目录的文件列表 HTML（目录在前；文件按 mtime 降序；隐藏文件不进列表） |
| `403` | `dir` 越出 `storage/` |
| `404` | `dir` 不存在或不是目录 |

- 每行带 `data-rel`（相对路径，供前端调用其他接口）与 `data-enc`（`1` 表示加密文件）
- 加密文件名旁显示锁图标，且不提供直链（直链会 401），只能走「下载」按钮输密码

## 2. `GET /files/<path>`（含 `HEAD /files/<path>`）

请求头（均可选）：`Range: bytes=start-end`、`If-None-Match: "<etag>"`

| 状态 | 触发条件 | 关键响应头 |
|---|---|---|
| `200` | 无 Range、无命中缓存 | `Content-Type`、`Content-Length`、`Accept-Ranges: bytes`、`ETag` |
| `206` | Range 合法 | 同上 + `Content-Range: bytes s-e/size`，`Content-Length: e-s+1` |
| `304` | `If-None-Match` 等于当前 ETag | `ETag`；无 body、**无 `Content-Length`** |
| `401` | 目标是加密文件，且未带有效 `?token=` | `Cache-Control: no-store` |
| `403` | 路径越权（含 `..`、反斜杠、符号链接逃逸） | `Content-Type: text/plain` |
| `404` | 文件不存在或目标是目录 | `Content-Type: text/plain` |
| `416` | Range 越界 | `Content-Range: bytes */<size>`，`Content-Length: 0` |

- ETag 定义：`"<mtime_ns 十六进制>-<size 十六进制>"`，零 I/O 开销
- Range 语法：`bytes=0-1023` / `bytes=1024-` / `bytes=-500`；多段 Range 只处理第一段
- 语法非法的 Range 按 RFC 7233 忽略，退化为 `200`
- 加密文件：`size` 一律指**明文长度**（Range / `Content-Range` 都按明文计算），
  服务端按 64KB 块独立派生密钥流，可随机解密任意区间；`Content-Type` 仍按原扩展名推断
- 密码只用于 `POST /unlock` 换取令牌，**从不出现在 URL 与日志**；下载 URL 里的
  `token` 在写日志前会脱敏为 `token=***`

## 3. `POST /upload?dir=<子目录>`

| 项 | 值 |
|---|---|
| 请求头 | `Content-Type: multipart/form-data; boundary=...`、`Content-Length` |
| 请求体 | multipart 格式，文件字段需带 `filename`；可选普通字段 `password` |
| 成功响应 | `200`，`application/json`：`{"ok": true, "filename": "a.txt", "files": ["a.txt"], "encrypted": false}` |
| `400` | `Content-Type` 非 multipart / boundary 缺失 / body 为空 / 无文件字段 |
| `403` | 目录或文件名越权 |
| `404` | 目标目录不存在 |

- 文件名处理：取 basename（`\` 与 `/` 都视为分隔符）→ 去控制字符 → 空名用 uuid
- 同名冲突：`a.txt` → `a (1).txt` → `a (2).txt`，超过 100 次追加 uuid 后缀
- `password` 非空时：文件用 PBKDF2 + SHAKE-256 计数器流加密后落盘，返回 `"encrypted": true`
- 密码只参与内存计算，不写日志、不回显；`filename` / `files` 为相对 `storage/` 的路径
- 响应体为 JSON 时前端自行 `location.reload()`，不使用 302

## 4. `DELETE /files/<path>`（仅管理员）

| 项 | 值 |
|---|---|
| 请求体 | 无 |
| 查询参数 | `token=<unlock token>`（目标为加密文件时必填） |
| 成功响应 | `200`，`application/json`：`{"ok": true, "filename": "a/b.txt", "kind": "file"}`（目录时 `kind: "dir"`） |
| `401` | 未登录（`Unauthorized: login required`）；或目标是加密文件且未带有效令牌（`Unauthorized: password required`） |
| `403` | 角色不是管理员（`Forbidden: admin required`）；路径越权；目标是 storage 根目录 |
| `404` | 目标不存在 |

- 目录**递归删除**：连同其中所有文件与子目录一并移除（`shutil.rmtree`），不再返回 `409`
- 删除 `storage/` 根本身始终拒绝（`403 Forbidden: cannot delete storage root`）
- 加密文件必须先 `POST /unlock` 输密码换令牌，再带 `?token=` 删除，与下载共用同一令牌
- 删除后不可恢复，前端必须二次确认

## 5. `POST /mkdir`

| 项 | 值 |
|---|---|
| 请求头 | `Content-Type: application/json` |
| 请求体 | `{"dir": "当前子目录（可为空串）", "name": "新目录名"}` |
| 成功响应 | `200`：`{"ok": true, "dir": "当前子目录/新目录名"}` |
| `400` | JSON 非法 / 目录名为空、含 `/` `\` 或控制字符 |
| `403` `404` | 当前目录越权 / 不存在 |
| `409` | 同名文件夹已存在：`{"ok": false, "error": "exists"}` |

- 只创建一层目录，不递归；目录名由 `pathsafe.check_entry_name()` 校验
- 请求体上限 8KB

## 6. `POST /unlock`

| 项 | 值 |
|---|---|
| 请求头 | `Content-Type: application/json` |
| 请求体 | `{"name": "文件相对路径", "password": "访问密码"}` |
| 成功响应 | `200`：`{"ok": true, "token": "<24 字节 urlsafe 令牌>"}` |
| `400` | JSON 非法 / 密码为空 / 目标不是加密文件 |
| `401` | 密码错误：`{"ok": false, "error": "bad-password"}` |
| `403` `404` | 路径越权 / 文件不存在 |

- 令牌与「具体文件路径」绑定，10 分钟过期，进程重启即失效；只存内存
- 令牌只能用于对应文件的 `GET /files/<path>?token=` 与 `DELETE /files/<path>?token=`，不能跨文件使用
- 前端**不缓存令牌**：每次下载、删除、播放加密视频都要重新输入密码；
  令牌仅在单次视频播放的连续 Range 请求之间复用（`<video>` 自身持有该 URL）

## 7. `GET /debug/requests`（需 `builder` 及以上）

页面内「请求监视器」的数据源，返回最近 20 条请求记录：

```json
{"events": [
  {"time": "22:17:53", "client": "192.168.1.7", "method": "GET",
   "path": "/files/a.mp4", "status": 206, "bytes": 5242880,
   "note": "range=5242880-10485759"}
]}
```

- 时间倒序（最新在前），环形缓冲上限 200 条，进程重启即清空
- 本接口自身**不写访问日志、不计入缓冲**，否则每秒轮询会刷屏
- 响应头带 `Cache-Control: no-store`

## 8. 错误处理矩阵

| 场景 | 状态码 | 响应体 |
|---|---|---|
| 未登录访问页面 / 文件 | `302` | 空，`Location: /login` |
| 未登录调用接口 | `401` | `Unauthorized: login required` |
| 角色不足（访客上传 / 非管理员删除） | `403` | `Forbidden: builder required` / `Forbidden: admin required` |
| 删除 storage 根目录 | `403` | `Forbidden: cannot delete storage root` |
| 路径不存在 | `404` | `Not Found` |
| 路径越权 | `403` | `Forbidden` |
| 加密文件缺少/失效令牌 | `401` | `Unauthorized: password required` |
| 解锁密码错误 | `401` | `{"ok": false, "error": "bad-password"}` |
| 同名文件夹已存在 | `409` | `{"ok": false, "error": "exists"}` |
| Range 越界 | `416` | 空 |
| ETag 匹配 | `304` | 空 |
| Content-Type 非 multipart | `400` | `Bad Request: expected multipart` |
| boundary 缺失 | `400` | `Bad Request: missing boundary` |
| multipart 解析失败 | `400` | `Bad Request: malformed multipart` |
| JSON 非法 / 密码为空 / 非法目录名 | `400` | `Bad Request: ...` |
| 未知路由 | `404` | `Not Found` |
| 方法不支持 | `405` | `Method Not Allowed`，带 `Allow: GET, HEAD, POST, DELETE` |
| handler 内部异常 | `500` | `Internal Server Error`（traceback 只进服务端日志） |

所有错误响应均为 `text/plain; charset=utf-8`，不使用标准库默认的 HTML 错误页；
业务类冲突（mkdir / unlock）额外返回 JSON，便于前端区分提示。

## 9. 日志契约

每行一条，字段固定为「时间 / 客户端 IP / 请求行 / 状态码 / 字节数 / 备注」：

```
[2025-01-15 14:32:01] 192.168.1.7 "GET /files/a.mp4" 206 5242880B range=5242880-10485759
[2025-01-15 14:32:03] 192.168.1.7 "POST /upload" 200 61B multipart file=a.txt size=1024
[2025-01-15 14:32:05] 192.168.1.7 "GET /files/a.mp4" 304 0B cache-hit
[2025-01-15 14:32:07] 192.168.1.7 "POST /unlock" 200 72B unlock ok file=secret.txt
[2025-01-15 14:32:08] 192.168.1.7 "GET /files/secret.txt?token=***" 200 4096B full encrypted
```

- 上传备注会追加 `encrypted`，下载备注追加 `encrypted`，便于核对加密路径
- 密码永不入日志；令牌统一脱敏为 `token=***`
