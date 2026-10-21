# Web 文件服务器

零第三方依赖的 HTTP/1.1 文件服务器，浏览器即客户端：访问码分级鉴权（访客 / 建设者 / 管理员）、目录浏览与新建、拖拽上传、下载（Range 断点续传 + ETag 条件缓存）、加密存储（上传时设密码，下载时必须输入同一密码）、局域网跨设备访问。

## 运行

```bash
cd web-server
python src/server.py
```

首次启动会在 `src/auth.json` 生成三个随机访问码并打印到控制台（该文件已被 `.gitignore` 忽略）。
浏览器打开 <http://localhost:8000/>，用对应的访问码登录；局域网设备访问 `http://<本机局域网 IP>:8000/`。
存储根目录为 `src/storage/`（首次启动自动创建）。

环境要求：Python ≥ 3.8（实测 3.13），Windows / macOS / Linux 均可。

## 目录结构

```
web-server/
├── report.md                 # 本文件
└── src/
    ├── CONTRACT.md           # 接口契约（先改契约再改代码）
    ├── auth.json             # 访问码（运行期生成，已 gitignore）
    ├── server.py             # 入口 + 路由分发 + 鉴权拦截 + 日志 + 异常兜底
    ├── handlers/
    │   ├── login.py          # GET /login、POST /login、POST /logout
    │   ├── listing.py        # GET /            列表页（服务端渲染，支持 ?dir=，按角色渲染）
    │   ├── download.py       # GET /files/<path> 下载 + Range + ETag + 加密解密
    │   ├── upload.py         # POST /upload      multipart 上传（可加密）
    │   ├── delete.py         # DELETE /files/<path> 删除文件 / 递归删除目录
    │   ├── mkdir.py          # POST /mkdir       新建文件夹
    │   ├── unlock.py         # POST /unlock      校验密码，签发下载令牌
    │   └── debug.py          # GET /debug/requests 请求监视器数据
    ├── utils/
    │   ├── auth.py           # 访问码、登录会话、角色模型
    │   ├── mime.py           # MIME 推断
    │   ├── pathsafe.py       # 路径穿越校验（realpath + commonpath）
    │   ├── multipart.py      # multipart/form-data 切分
    │   ├── crypto.py         # PBKDF2 + SHAKE-256 计数器流加解密
    │   ├── session.py        # 解锁令牌（绑定文件、10 分钟过期）
    │   ├── httpjson.py       # JSON 请求读取 / 响应
    │   └── reqlog.py         # 最近请求的环形缓冲（供监视器读取）
    ├── public/
    │   ├── login.html        # 登录页
    │   └── index.html        # 前端（目录导航 + 拖拽上传 + 密码对话框 + 视频播放 + 请求监视器）
    └── storage/              # 存储根目录
```

分层：`server.py`（协议/路由）→ `handlers/`（业务）→ `utils/`（纯函数）。每个 handler 只依赖一个 `handler` 对象，可脱离 socket 单独测试。

## 能力清单

| 能力 | 实现要点 |
|---|---|
| 鉴权 | 三个共享访问码（`src/auth.json`）对应 `guest` < `builder` < `admin`；登录签发 HttpOnly Cookie 会话，未登录一律跳登录页 |
| 目录管理 | `GET /?dir=` 浏览子目录，面包屑逐段可点；`POST /mkdir` 新建；上传目标为当前目录 |
| 目录删除 | `DELETE /files/<path>` 递归删除（含非空目录），仅管理员；storage 根目录拒绝删除 |
| 加密文件 | 上传时填密码 → PBKDF2-HMAC-SHA256(150000) 派生密钥 + SHAKE-256 计数器流加密落盘 |
| 加密下载 | 密码经 `POST /unlock` 换取与文件绑定的令牌（10 分钟），再用 `?token=` 下载；错误密码 `401`；前端不缓存令牌，每次操作都重新输密码 |
| 加密 + Range | 密钥流按 64KB 块独立派生，任意偏移可单独解密，加密视频也能拖动进度条 |
| 文件列表 | `scandir` 枚举，目录在前、文件按 mtime 降序；`html.escape` 转义、`quote` 编码中文名 |
| 上传 | 手写 multipart 切分；文件名 basename 化 + 冲突改名 `a (1).txt` |
| 删除 | `DELETE /files/<path>`，仅管理员，二次确认；加密文件需先输密码换令牌；路径同样过 `pathsafe` |
| 界面 | 角色徽标与退出登录、目录/加密徽标、文件数/总大小统计、原生 `<dialog>` 输入与确认、Toast、删除行内动画、移动端隐藏次要列 |
| 视频播放 | 下拉框从当前文件列表动态生成，可自由切换视频；加密视频走令牌；删除后同步移除选项 |
| 请求监视器 | 页面底部实时展示最近 20 条请求（方法/路径/状态码/字节数/Range 备注），令牌自动脱敏；可折叠；仅 `builder` 及以上可见 |
| 下载 | `seek + read(64KB)` 流式发送，内存占用与文件大小无关 |
| 断点续传 | 单段 Range → `206 + Content-Range`；越界 → `416` |
| 条件缓存 | ETag = `mtime_ns-size`（十六进制）；命中 → `304` 无 body |
| 路径安全 | 所有文件输入经 `pathsafe.resolve()`，`realpath + commonpath` 校验 |
| 并发 | `ThreadingHTTPServer`，多标签页并发下载互不阻塞 |
| 日志 | 时间 / IP / 请求行 / 状态码 / 字节数 / Range 或缓存命中；密码不落日志 |

## 验证

功能用例（对应设计文档 §10.1）：

| # | 用例 | 预期 |
|---|---|---|
| T1 | 打开 `http://localhost:8000/` | 200，显示文件列表 |
| T2 | 拖拽上传文本 | 文件出现在 storage 与列表页 |
| T3 | 下载刚上传的文件 | 200，内容一致 |
| T4 | 上传大文件（实测 5MB） | 200，落盘字节数与源文件一致 |
| T5 | `<video>` 拖动进度条 | DevTools 出现 206 序列 |
| T6 | 带 ETag 再次请求同一文件 | 304，Size 列 0 B |
| T7 | `curl "http://localhost:8000/files/..%2f..%2fetc%2fpasswd"` | 403 |
| T8 | `curl -I` 检查响应头 | 含 `Content-Type` / `Content-Length` / `ETag` / `Accept-Ranges` |
| T9 | 手机访问 `http://<LAN-IP>:8000` | 页面正常 |
| T10 | 手机上传 → 电脑下载 | 内容一致 |
| T11 | 点「删除」并确认 | 行消失、统计数字同步减少、Toast 提示 |
| T12 | 删除不存在的文件 / 越权路径 | 404 / 403 |
| T13 | 点「新建文件夹」输入 `docs` | 目录行出现，带 DIR 徽标；进入后面包屑为「根目录 / docs」 |
| T14 | 在 `docs` 目录上传文件 | 落盘到 `storage/docs/`，列表只在子目录可见，根目录不出现 |
| T15 | 管理员删除非空目录 / 空目录 | 200（连同内容递归删除） / 200 |
| T16 | 上传时填写密码 | 文件名旁显示锁图标，磁盘文件中不含明文 |
| T17 | 错误密码解锁 / 正确密码解锁 | 401「密码错误」 / 200 返回 token，带 token 下载内容与源文件一致 |
| T18 | 加密文件带 token 请求 Range | 206，解密片段与明文对应区间一致 |
| T19 | 加密文件不带 token 直接访问 | 401 |
| T20 | 删除加密文件（无令牌 / 有令牌） | 401 / 200，文件消失 |
| T21 | 未登录访问 `/`、`/files/x` | 302 → `/login`，只显示登录页 |
| T22 | 错误访问码登录 | 303 → `/login?err=1`，页面提示「访问码不正确」 |
| T23 | 访客登录 | 200 列表页，无上传区/新建按钮/删除按钮/监视器；`POST /upload`、`POST /mkdir`、`DELETE` 均 403 |
| T24 | 建设者登录 | 可上传、新建目录、看监视器；`DELETE` 仍 403 |
| T25 | 管理员登录 | 可上传、新建、删除文件与非空目录、看监视器 |
| T26 | 退出登录后再访问 | 302 → `/login`，旧 Cookie 失效 |

协议合规验证（curl 命令集）：

```bash
# 鉴权：未登录只能看到登录页；登录后把 Cookie 存进 cookies.txt
curl -v http://localhost:8000/                                   # 302 → /login
curl -c cookies.txt -X POST -d "code=<访问码>" http://localhost:8000/login   # 303 → /
curl -b cookies.txt "http://localhost:8000/"                     # 200
curl -v -X POST http://localhost:8000/upload                     # 401（未登录，接口类）
curl -v -X POST -b cookies.txt http://localhost:8000/upload      # 403（访客无上传权限）
curl -v -X POST -b cookies.txt http://localhost:8000/logout      # 303 → /login

# 以下请求都需带 -b cookies.txt（DELETE 需管理员码，上传/新建需建设者及以上）

# 基础 GET（-I 走 HEAD，与 GET 共用逻辑）
curl -v -b cookies.txt http://localhost:8000/files/a.txt

# Range 三种形态
curl -v -b cookies.txt -H "Range: bytes=0-99"   http://localhost:8000/files/a.mp4   # 206
curl -v -b cookies.txt -H "Range: bytes=1024-"  http://localhost:8000/files/a.mp4   # 206，到文件尾
curl -v -b cookies.txt -H "Range: bytes=-500"   http://localhost:8000/files/a.mp4   # 206，最后 500 字节
curl -v -b cookies.txt -H "Range: bytes=999999999-" http://localhost:8000/files/a.mp4   # 416

# 条件请求（先取 ETag，再带 If-None-Match）
ETAG=$(curl -sI -b cookies.txt http://localhost:8000/files/a.mp4 | awk '/ETag/ {print $2}' | tr -d '\r')
curl -v -b cookies.txt -H "If-None-Match: $ETAG" http://localhost:8000/files/a.mp4   # 304

# 路径穿越（正斜杠 / 反斜杠两种）
curl -v -b cookies.txt "http://localhost:8000/files/..%2f..%2fetc%2fpasswd"   # 403
curl -v -b cookies.txt "http://localhost:8000/files/..%5c..%5cserver.py"      # 403

# 目录：新建 → 进入 → 上传到子目录 → 管理员递归删除（含非空）
curl -X POST -b cookies.txt -H "Content-Type: application/json" \
     -d '{"dir":"","name":"docs"}' http://localhost:8000/mkdir          # 200
curl -v -b cookies.txt "http://localhost:8000/?dir=docs"                # 200
curl -F "file=@test.txt" -b cookies.txt "http://localhost:8000/upload?dir=docs"   # 200
curl -X DELETE -b cookies.txt http://localhost:8000/files/docs          # 200（管理员递归删除，连内容一起）
curl -X DELETE -b cookies.txt http://localhost:8000/files/              # 403（storage 根目录不可删）

# 上传（普通 / 加密）
curl -v -b cookies.txt -F "file=@test.txt" http://localhost:8000/upload
curl -b cookies.txt -F "file=@test.txt" -F "password=secret" http://localhost:8000/upload   # encrypted: true

# 加密下载：错误密码 401 → 正确密码换 token → 带 token 下载
curl -X POST -b cookies.txt -H "Content-Type: application/json" \
     -d '{"name":"test.txt","password":"wrong"}' http://localhost:8000/unlock    # 401
TOKEN=$(curl -s -X POST -b cookies.txt -H "Content-Type: application/json" \
     -d '{"name":"test.txt","password":"secret"}' http://localhost:8000/unlock \
     | sed -E 's/.*"token": ?"([^"]+)".*/\1/')
curl -v -b cookies.txt "http://localhost:8000/files/test.txt?token=$TOKEN"                     # 200，明文
curl -v -b cookies.txt "http://localhost:8000/files/test.txt"                                  # 401

# 删除（加密文件同样要密码：无令牌 401，带令牌 200；均需管理员）
curl -v -X DELETE -b cookies.txt http://localhost:8000/files/test.txt   # 200（未加密文件）
curl -v -X DELETE -b cookies.txt "http://localhost:8000/files/secret.txt?token=$TOKEN"   # 200（加密文件）
curl -v -X DELETE -b cookies.txt http://localhost:8000/files/secret.txt                   # 401
curl -v -X DELETE -b cookies.txt http://localhost:8000/files/..%2fserver.py   # 403
```

已实测通过：未登录跳转登录页、三个访问码对应权限（访客只读、建设者可上传、管理员可删除）、错误访问码提示、退出登录、访客上传 403、管理员递归删除非空目录、storage 根目录拒删；上传（含 5MB 大文件、中文文件名）、下载、三种 Range 形态、416、304、403（`..%2f` 与 `..%5c` 两种写法）、404、405、删除（200/404/403）、新建文件夹（200/400/409）、加密上传与解密下载（哈希一致）、加密文件 Range 206、错误密码 401、无令牌 401、加密文件删除需令牌（401/200）、keep-alive 连接复用、中文目录与面包屑。

日志示例（这张截图本身就是「服务器真的在处理协议」的证据）：

```
[2026-10-20 15:30:40] 127.0.0.1 "POST /upload" 200 99B multipart file=中文测试.txt size=18
[2026-10-20 15:30:49] 127.0.0.1 "GET /files/big.bin" 206 1048576B range=0-1048575
[2026-10-20 15:27:59] 127.0.0.1 "GET /files/hello.txt" 304 0B cache-hit
```

## 关键设计决策

| 决策点 | 选择 | 备选 | 取舍理由 |
|---|---|---|---|
| 协议层 | `BaseHTTPRequestHandler` | Flask / 裸 socket | 框架把协议藏起来，裸 socket 要自己处理粘包 |
| 并发 | `ThreadingHTTPServer` | 单线程 / asyncio | 改动一行，I/O 阻塞场景下线程模型够用 |
| ETag | `mtime_ns + size` | 文件 hash | 零 I/O 开销，取 `stat` 即可算出 |
| Range | 单段 | 多段 `multipart/byteranges` | 浏览器视频播放只用单段 |
| multipart | 全量读内存 | 流式解析 | 演示场景文件小，流式解析复杂一个数量级 |
| 路径安全 | `realpath + commonpath` | 字符串过滤 `..` | `..%2f`、`..\`、符号链接都能绕过字符串匹配 |
| 上传进度 | XHR `upload.onprogress` | fetch | fetch 无标准上传进度事件 |
| 大文件下载 | `seek + read(64KB)` | 全量 `read()` | 内存占用恒定 64KB |
| 加密算法 | PBKDF2 + SHAKE-256 计数器流 | AES-GCM / 第三方库 | 零依赖约束下用标准库实现；计数器流可按块随机访问 |
| 密钥派生参数 | PBKDF2-SHA256 150000 轮 | 单次 SHA256 | 提高离线爆破成本 |
| 加密下载凭据 | `POST /unlock` 换令牌 + `?token=` | 把密码放 URL | 密码不进 URL / 日志 / 浏览器历史 |
| 鉴权方式 | 三个共享访问码 + HttpOnly Cookie 会话 | 账号密码库 / Basic Auth | 零依赖、无需注册流程，三种角色各记一个码即可 |
| 权限模型 | 角色等级 `guest < builder < admin` | 逐接口 ACL | 三个角色线性覆盖全部操作，判定只需比较等级 |
| 目录删除 | 管理员递归删除 | 仅空目录 / 逐层删 | 管理员本就有全部权限，递归删除省去先清空子项的繁琐操作，误删由二次确认与角色限制兜底 |

## 已知限制

| 限制 | 影响 | 演进方向 |
|---|---|---|
| 上传全量读入内存 | 传超大文件时内存飙升（当前：几 MB ~ 百 MB 可用） | 临时文件 + 流式 multipart 解析 |
| 访问码是共享的 | 无法区分同角色内的具体用户，泄露后需手工改 `auth.json` 并重启 | 账号体系 / 每用户会话 |
| 无 HTTPS | 访问码、密码与明文在局域网中可被嗅探 | `ssl.SSLContext` 包装 socket |
| 会话只存内存 | 服务重启后需重新登录 | 持久化会话或签名 Cookie |
| 无磁盘配额 | 可写满磁盘 | 上传前检查剩余空间 |
| 上传不可断点续传 | 中断需重传 | 分片上传 + 合并 |
| 不支持多段 Range | 部分专业播放器受限 | 按 RFC 7233 实现 `multipart/byteranges` |
| 目录操作有限 | 不能移动/重命名 | 增加移动、重命名接口 |
| 无重命名 | 只能上传、下载、删除 | 增加 `PATCH /files/<name>` |
| 删除不可撤销 | 递归删除没有回收站，误删整棵子树无法恢复 | 软删除 + 定期清理 |
| 加密文件忘记密码无法恢复 | 密钥只由密码派生，服务端不保存 | 设计上无解，需用户自行保管密码 |

以上均不在本项目范围内，列出以界定设计边界。
