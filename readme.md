# Web File Server

一个**零第三方依赖**的纯 Python HTTP/1.1 文件服务器。核心只用标准库 `http.server` + `socket`，
浏览器就是客户端：目录浏览、拖拽上传、断点续传、密码加密存储、三级角色权限、
页内视频播放、全局搜索，以及一个实时流量仪表盘和 HTTP 协议实验室。

接口契约见 [`src/CONTRACT.md`](src/CONTRACT.md)，本文档描述**当前代码的实际行为**
（所有状态码、响应头、文件格式均在 Python 3.13 / Windows 11 上实测核对过）。

---

## 1. 功能总览

| 能力 | 说明 |
|---|---|
| 目录管理 | 面包屑导航、新建文件夹、按目录上传、递归删除（管理员） |
| 下载 | 64 KB 分块流式输出；单段 `Range` → `206`；越界 → `416`；`ETag` 命中 → `304` |
| 上传 | 手写 `multipart/form-data` 解析、拖拽、多选、XHR 实时进度 + 速率/剩余时间估算 |
| 加密存储 | 上传时可选密码；PBKDF2-HMAC-SHA256（600 000 轮）+ SHAKE-256 计数器流；密文落盘，明文不落盘 |
| 加密访问 | `POST /unlock` 用密码换**文件+会话绑定**令牌，10 分钟有效，后续用 `?token=` 下载/删除 |
| 认证 | 三个访问码 → `guest` / `builder` / `admin`，每个路由在服务端强制校验 |
| 视频播放 | 页内 `<video>`，加密视频按 64 KB 块独立解密，因此拖动进度条可用 |
| 全局搜索 | `GET /api/search?q=` 递归匹配**文件名**（不做内容索引），结果卡片直达所在目录 |
| 请求监视器 | 最近 20 条请求（方法/路径/状态/字节/备注），环形缓冲上限 200 条 |
| 实时仪表盘 | SSE 每秒推送；4 个 KPI + 请求数折线图 + 状态码环图，Canvas 手绘无图表库 |
| 存储画像 | 按文件类型统计字节占比；图片画廊 + 灯箱预览 |
| 协议实验室 | 在页面上直接发 `Range` / `If-None-Match` / `HEAD` 请求，查看原始响应头 |
| 演示流量 | `POST /api/demo/traffic` 生成约 10 秒假流量，**不产生真实请求、不触碰用户文件** |

### 角色模型

| 角色 | 访问码字段 | 权限 |
|---|---|---|
| 访客 `guest` | `guest` | 浏览目录、下载、播放视频、解锁加密文件、全局搜索 |
| 建设者 `builder` | `builder` | 访客权限 + 上传、新建文件夹、请求监视器 |
| 管理员 `admin` | `admin` | 建设者权限 + 删除文件与文件夹（含非空目录） |

权限在 [`src/server.py`](src/server.py) 中于 handler 执行**之前**校验，前端 `[data-need]` 的
隐藏只是界面效果，`curl` 直接请求同样会被 `401` / `403` 拒绝。

两条边界值得单独说明：

- **上传永不覆盖。** 同名文件按 `name (1).ext` → `name (2).ext` 递增，100 次后追加随机后缀；
  写入用 `O_EXCL` 原子创建，并发上传不会互相覆盖。
- **目录递归删除不做逐文件校验。** 管理员删除文件夹时 `shutil.rmtree` 整棵子树移除，
  其中的加密文件**不会**再逐个索要解锁令牌。加密保护的是「内容机密性」，不是「防管理员删除」。

---

## 2. 快速开始

### 环境要求

- Python **3.8+**（实测 Python 3.13，Windows 11）
- 无第三方包、无需 `pip install`、无数据库
- `src/storage/` 可写（首次启动自动创建）

### 启动

```bash
cd web-server
python src/server.py
```

首次运行会创建 `src/auth.json` 并打印三个访问码：

```
Access codes (src/auth.json):
  guest    访客        <guest code>
  builder  建设者       <builder code>
  admin    管理员       <admin code>
Serving on http://0.0.0.0:8000  (LAN IP available)
Storage root: .../src/storage
```

> 访问码是随机生成的，三个码互不相同。**不要把 `auth.json` 提交进仓库**（已在 `.gitignore` 中），
> 也不要把真实访问码贴进任何文档或截图。

打开 **<http://localhost:8000/>**，用任一访问码登录。改码请编辑 `src/auth.json` 后重启
（该文件已在 `.gitignore` 中，不会入库）。

### 同一局域网内的手机 / 其他电脑

1. 查本机 IPv4（`ipconfig` / `ifconfig` / `ip addr`）。
2. 另一台设备打开 `http://<本机IP>:8000/`。
3. 若不通，放行入站 TCP 8000（Windows 管理员 PowerShell）：

```powershell
New-NetFirewallRule -DisplayName "Web File Server 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

校园网 / 企业 Wi-Fi 常开客户端隔离，同 SSID 设备互不可达，此时改用手机热点或下面的隧道。

> **Windows 编码坑（实测）**：把启动输出重定向到文件或管道时（例如 `python src/server.py > out.log`），
> 打印中文角色名会让启动在 `print` 处抛 `UnicodeEncodeError` 并退出——重定向后 Python 用系统代码页
> （cp1252 / cp936）编码 stdout。用 UTF-8 模式启动即可：
>
> ```bash
> python -X utf8 src/server.py          # 或 set PYTHONIOENCODING=utf-8
> ```
>
> 直接在终端里运行不受影响（控制台走 Unicode API）。若部署成服务、把日志交给进程管理器，
> 这一条是必须的。

### 可选：通过隧道公网访问

服务本身仍跑在本机，不需要公网 IP：

```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflare 会给出临时 `https://….trycloudflare.com` 地址，自动带 HTTPS。
快速隧道不保证可用时长且每次重启换域名；要固定地址需配置命名隧道 + 自定义域名。

> 走 HTTPS 时请设置环境变量 `WFS_HTTPS=1`，会话 Cookie 才会追加 `Secure` 属性。

---

## 3. 目录结构

```
web-server/
├── readme.md                    # 本文档
├── .gitignore                   # 排除 auth.json、storage/*、__pycache__
└── src/
    ├── server.py                # 入口：路由分发、鉴权门禁、请求体排空、日志与指标
    ├── CONTRACT.md              # 接口契约（先写契约再改代码）
    ├── auth.json                # 访问码（首次启动生成，已 gitignore）
    ├── handlers/                # 一个文件一个功能域
    │   ├── login.py             #   GET/POST /login、POST /logout
    │   ├── listing.py           #   GET /  目录列表（服务端渲染 HTML）
    │   ├── download.py          #   GET /files/*  Range / ETag / 加密流式解密
    │   ├── upload.py            #   POST /upload  multipart 解析 + 原子落盘
    │   ├── delete.py            #   DELETE /files/*  文件与递归目录
    │   ├── mkdir.py             #   POST /mkdir
    │   ├── unlock.py            #   POST /unlock  密码换令牌
    │   ├── search.py            #   GET /api/search
    │   ├── dashboard.py         #   GET /api/metrics、GET /api/events(SSE)、POST /api/demo/traffic
    │   ├── debug.py             #   GET /debug/requests  监视器数据源
    │   └── static.py            #   GET /static/*  白名单 CSS/JS + 内容哈希版本戳
    ├── utils/                   # 无状态工具，副作用只发生在显式调用处
    │   ├── auth.py              #   访问码、会话令牌、Cookie、角色比较
    │   ├── session.py           #   解锁令牌（文件 + 会话双绑定）
    │   ├── crypto.py            #   文件加密格式 v1/v2、按块解密迭代器
    │   ├── pathsafe.py          #   realpath + commonpath 路径越权防御
    │   ├── multipart.py         #   multipart 切分（只在 \r\n--boundary 处切）
    │   ├── ratelimit.py         #   滑动窗口失败限流
    │   ├── metrics.py           #   线程安全计数器与秒级时间序列
    │   ├── reqlog.py            #   请求环形缓冲（deque，上限 200）
    │   ├── httpjson.py          #   JSON 读写助手（body 上限 8 KB）
    │   └── mime.py              #   MIME 推断，文本类型补 charset
    ├── public/
    │   ├── index.html           #   主界面（服务端注入 <!--CRUMBS--> / <!--FILES--> 等占位）
    │   ├── login.html           #   登录页
    │   ├── dash.css             #   仪表盘 / 画廊 / 实验室 / 搜索样式，含亮暗主题
    │   └── app.js               #   演示增强层：SSE 图表、搜索、画廊、协议实验室
    └── storage/                 # 用户文件（内容不入库）
```

---

## 4. 界面模块

主页面由 `index.html` 的骨架 + `app.js` 的增强层组成，`app.js` 是独立 IIFE，不污染内联脚本的全局作用域。

- **顶部工具栏**：搜索框（当前目录过滤 + 回车全局搜索）、排序（时间/名称/大小）、紧凑视图、
  模块导航、画廊、分享（一键复制含 `?dir=` 的当前地址）、主题切换（`localStorage` 记忆）。
- **存储内容**：面包屑 + 表格。目录在前，文件按 mtime 降序；类型徽标按扩展名分色；
  加密文件显示锁图标且不提供直链（直链会 401），只能走「下载」按钮输密码。
- **上传卡**（仅 builder/admin）：拖拽或选择文件，可填可选密码；进度条下方实时显示速率与剩余秒数。
- **视频播放**：下拉选择当前目录的视频；加密视频选择时才弹密码框，令牌仅在这一次播放的连续
  `Range` 请求之间复用（`<video>` 自身持有带 token 的 URL）。
- **数据洞察区**（追加在页面末尾）：① 实时流量仪表盘（SSE + Canvas）② 存储画像与图片画廊
  ③ HTTP 协议实验室 ④ 请求监视器。
- **搜索结果卡**：插在「存储内容」之前，命中项可「定位到所在目录」并按 `#f=<文件名>` 高亮该行。

前端刻意**不缓存解锁令牌**：每次下载、删除、播放都要重新输入密码，令牌 TTL 只是泄漏后的兜底。

---

## 5. 接口一览

完整字段与错误矩阵见 [`src/CONTRACT.md`](src/CONTRACT.md)，这里是速查表。

| 方法 | 路径 | 最低角色 | 说明 |
|---|---|---|---|
| `GET` | `/login` | 公开 | 登录页；已登录 `302 → /` |
| `POST` | `/login` | 公开 | 表单 `code=`，成功 `303 → /` + `Set-Cookie` |
| `POST` | `/logout` | 已登录 | 清 Cookie，并撤销该会话签发的所有解锁令牌 |
| `GET`/`HEAD` | `/` | 访客 | 目录列表，`?dir=` 指定子目录 |
| `GET`/`HEAD` | `/files/<path>` | 访客 | 下载；加密文件需 `?token=` |
| `POST` | `/upload?dir=` | 建设者 | multipart 上传，可选 `password` 字段 |
| `POST` | `/mkdir` | 建设者 | JSON `{"dir","name"}` |
| `POST` | `/unlock` | 访客 | JSON `{"name","password"}` → `{"ok":true,"token":…}` |
| `DELETE` | `/files/<path>` | 管理员 | 文件或递归目录；加密文件需 `?token=` |
| `GET` | `/debug/requests` | 建设者 | 最近 20 条请求（自身不写日志） |
| `GET` | `/api/metrics` | 已登录 | 指标快照（含 60 秒序列、存储概况、角色标签） |
| `GET` | `/api/events` | 已登录 | SSE，每秒一帧，单连接最长 5 分钟后由前端重连 |
| `GET` | `/api/search?q=` | 已登录 | 文件名递归搜索，深度 ≤ 12、结果 ≤ 120 条 |
| `POST` | `/api/demo/traffic` | 已登录 | 生成演示流量；重复调用返回 `already running` |
| `GET` | `/static/<name>` | 公开 | 仅白名单 `dash.css` / `app.js`，带 ETag 与内容哈希版本戳 |

其余方法（`PUT`/`PATCH`/`OPTIONS`/`TRACE`/`CONNECT`）统一 `405` 并带 `Allow: GET, HEAD, POST, DELETE`。

### 实测状态码

| 场景 | 结果 |
|---|---|
| 未登录 `GET /`、`GET /files/…` | `302 → /login` |
| 未登录 `GET /api/metrics`、`/api/search`、`/debug/requests` | `401`（JSON 接口返回 JSON，不跟随 302） |
| 访客 `POST /mkdir`、`DELETE /files/…`、`GET /debug/requests` | `403` |
| 未知路由 `/nope` | `404` |
| `GET /static/../server.py` | `404`（白名单之外一律不提供） |
| `GET /files/..%2fserver.py` | `403`（path-escape） |
| 非法方法 `PUT /` | `405` |
| `POST /upload` 非 multipart | `400` |
| `POST /mkdir` 同名 | `409 {"ok":false,"error":"exists"}` |
| `DELETE /files/`（storage 根） | `403 Forbidden: cannot delete storage root` |
| `POST /unlock` 密码错误 | `401 {"ok":false,"error":"bad-password"}` |
| 加密文件无令牌下载 / 删除 | `401 Unauthorized: password required` |
| 登录 60 秒内第 9 次失败 | `429` + `Retry-After`（实测 29 秒） |

---

## 6. 协议实现细节

- **HTTP/1.1 keep-alive**。带 body 的响应都有 `Content-Length`；`304` 只发状态行与 `ETag`，
  **不带** `Content-Length`。所有分支在写响应头前统一排空未读请求体（`_drain_body`），
  否则残留字节会被当成下一个请求行——这是手写 keep-alive 最容易踩的坑。
- **Range**：`bytes=0-1023`、`bytes=1024-`、`bytes=-500` 三种形式；多段 Range 只处理第一段；
  语法非法按 RFC 7233 忽略并退化为 `200`；**请求整个文件时返回 `200` 而非 `206`**。
  越界返回 `416` + `Content-Range: bytes */<size>`。
- **条件请求**：`ETag` 为 `"<mtime_ns 十六进制>-<size 十六进制>"`，零 I/O 开销；
  `If-None-Match` 支持 `*`、`W/` 前缀与逗号分隔多值（弱比较）→ `304`。
- **`If-Range` 未实现**：文件在断点续传期间被改动，客户端可能拼出两个版本的字节。
- **路径安全**：所有用户路径经 `realpath + commonpath` 归一化后校验仍在 `storage/` 内
  （不用 `startswith`，因为 `storage-evil` 能骗过它）；Windows 下先 `normcase` 再比较；
  上传的文件名还要二次复核最终路径。
- **流式输出**：下载与解密都按 64 KB 块推进，内存占用与文件大小无关。

---

## 7. 加密格式

新写入一律使用 v2；v1 文件仍可解锁读取（无完整性校验）。

```
v2: magic "WFSENC02" | salt 16B | verifier 32B | rounds 4B | size 8B | header_mac 32B
    | (ciphertext ≤64KB + tag 16B) × N          头部合计 100 字节
v1: magic "WFSENC01" | salt 16B | verifier 32B | size 8B | ciphertext
```

- **密钥**：`PBKDF2-HMAC-SHA256(password, salt, 600000)` → 32 B；
  再分出 `enc_key = HMAC(key,"enc")`、`mac_key = HMAC(key,"mac")`。
- **密钥流**：第 *i* 块 = `SHAKE-256(enc_key ‖ "blk" ‖ uint64_be(i))`，取 64 KB。
  每块独立派生，所以任意偏移都能单独解密——这正是 `Range` 与视频拖动在加密文件上可用的原因。
- **完整性**：`header_mac` 防伪造头部，每块 16 B tag 防篡改；校验失败抛 `IntegrityError`，
  响应头已发出时直接断连接（traceback 只进服务端日志）。
- **PBKDF2 只在解锁时跑一次**：派生密钥缓存在令牌条目里，后续每个 `Range` 请求复用，
  拖动进度条不会重复跑 60 万轮。
- **头部防御**：`rounds` 限制在 `1..10 000 000`，`size` 不得超过磁盘实际容量，
  否则视为损坏并返回空列表 / `500`，避免伪造头导致死循环或超大输出。
- **加密文件的所有响应**（200/206/304/401/416）都带 `Cache-Control: no-store, private`、
  `Pragma: no-cache`、`Vary: *`。令牌校验发生在 `If-None-Match` 比较**之前**，
  所以无令牌请求得到 `401` 而不是 `304`。
- **范围语义**：`size`、`Content-Range` 一律指**明文长度**。
- **只加密正文**：文件名、明文长度、密文的 size/mtime 都是可见的；
  密码只参与内存计算，从不进 URL、日志或浏览器历史。

---

## 8. 会话与令牌

- **登录会话**：`POST /login` → 随机令牌写入 `Set-Cookie: wfs_session=…; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200`，
  12 小时滑动过期，**只存内存**，重启即失效。`WFS_HTTPS=1` 时追加 `Secure`。
- **解锁令牌**：`POST /unlock` 签发，与「具体文件 + 签发它的会话」双重绑定，10 分钟 TTL；
  只能用于该文件的 `GET`/`DELETE`，跨文件无效；`POST /logout` 立即撤销该会话的全部令牌。
  令牌本身不是独立凭据——请求仍要过登录门禁，`DELETE` 还要求管理员。
- **令牌传输**：放在查询串 `?token=` 里，因此会出现在浏览器历史、DevTools 和隧道访问日志中。
  应用日志做了兜底：**只记录 `urlparse().path`，任何 query 一律剥离并替换为 `?<redacted>`**，
  从根本上避免大小写变体绕过脱敏正则。
- **限流**：`/login` 与 `/unlock` 各自按 IP 统计，60 秒内 8 次失败锁 30 秒，返回 `429` + `Retry-After`。
  这同时挡住暴力破解和「每个 `/unlock` 跑一次 60 万轮 PBKDF2」的 CPU 耗尽攻击。

---

## 9. 安全设计

| 措施 | 实现位置 |
|---|---|
| 全局安全响应头 | `nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、CSP（`object-src 'none'`、`frame-ancestors 'none'`） |
| 同源存储型 XSS 防护 | `.html/.htm/.svg/.js/.xml/...` 等主动类型强制 `Content-Disposition: attachment; filename*=UTF-8''…` |
| 访问码比较 | `hmac.compare_digest` 常量时间比较；可选 `hash_codes()` 把 `auth.json` 改写为 `sha256$` 哈希版 |
| 路径越权 | `realpath + commonpath`，并过滤 NUL、控制字符与双向控制字符（U+202A~E、U+2066~9） |
| Windows 保留名 | 拒绝 `CON/PRN/AUX/NUL/COM1-9/LPT1-9`，去尾随点与空格 |
| 请求体上限 | multipart 256 MB（超出 `413`）、JSON 8 KB、登录表单 4 KB |
| 日志脱敏 | 只记 path，query 全部丢弃；密码永不入日志 |
| 错误页 | 统一 `text/plain`，不用标准库的 HTML 错误页，避免反射 |

---

## 10. 实测验证

以下结果由脚本直接发原始 HTTP 请求（不跟随重定向、不规范化路径）在 Python 3.13 上逐条核对：

| # | 测试 | 期望 | 实测 |
|---|---|---|---|
| 1 | 未登录访问 `/` | `302 → /login` | ✅ |
| 2 | 未登录访问 `/api/metrics` | `401` JSON | ✅ |
| 3 | 未登录访问 `/static/dash.css` | `200`（登录页也要样式） | ✅ |
| 4 | 访客 mkdir / delete / 监视器 | 全部 `403` | ✅ |
| 5 | 管理员 mkdir → 重复 mkdir → 删除 | `200` / `409` / `200` | ✅ |
| 6 | 删除 storage 根 | `403 cannot delete storage root` | ✅ |
| 7 | 完整下载 2.3 MB PDF | `200`，长度 `2334977` | ✅ |
| 8 | `Range: bytes=0-1023` | `206`，`Content-Range: bytes 0-1023/2334977` | ✅ |
| 9 | `Range: bytes=-500` | `206`，`bytes 2334477-2334976/2334977` | ✅ |
| 10 | `Range` 越界 | `416`，`bytes */2334977` | ✅ |
| 11 | `Range` 覆盖整个文件 | `200`（不是 `206`） | ✅ |
| 12 | `If-None-Match` 命中 | `304`，无 body，无 `Content-Length` | ✅ |
| 13 | `HEAD /files/…` | `200`，`Content-Length` 完整，body 0 字节 | ✅ |
| 14 | `GET /files/..%2fserver.py` | `403` | ✅ |
| 15 | `GET /static/../server.py` | `404` | ✅ |
| 16 | `PUT /` | `405` + `Allow` | ✅ |
| 17 | 加密上传 200 KB 随机数据 | `200`，磁盘 `204964` = 100 头 + 204800 + 4×16 tag，magic `WFSENC02` | ✅ |
| 18 | 加密文件无令牌下载 | `401` + `Cache-Control: no-store, private` | ✅ |
| 19 | 错误密码解锁 | `401 {"error":"bad-password"}` | ✅ |
| 20 | 正确密码解锁并下载 | 明文 SHA-256 与源文件一致 | ✅ |
| 21 | 加密文件 `Range: bytes=70000-70099` | `206`，`bytes 70000-70099/204800`，片段逐字节一致 | ✅ |
| 22 | 加密文件越界 Range | `416` + `bytes */204800`（按明文算） | ✅ |
| 23 | 非加密文件调用 `/unlock` | `400` | ✅ |
| 24 | 加密文件无令牌删除 → 带令牌删除 | `401` → `200`，文件已移除 | ✅ |
| 25 | 同名上传两次 | `zz_dup.txt` / `zz_dup (1).txt`，内容各自保留 | ✅ |
| 26 | 连续 8 次错误登录后第 9 次 | `429` + `Retry-After: 29`；锁定期内正确码也被拦 | ✅ |
| 27 | `GET /api/events` | `200`，`text/event-stream`，首帧 `data: {...}` | ✅ |
| 28 | `POST /api/demo/traffic` 连点两次 | `{"ok":true}` → `{"ok":false,"error":"already running"}` | ✅ |
| 29 | `/api/metrics` 字段 | 13 个顶层键，`series` 长度 60，`storage` / `roles` 齐全 | ✅ |
| 30 | 日志格式 | `[时间] IP "方法 路径" 状态 字节B 备注`，query 显示为 `?<redacted>` | ✅ |

---

## 11. 调试记录

开发过程中真正卡住过的几个问题，以及最终的处理方式。

1. **密码只在第一次生效。** 前端把解锁令牌缓存在 `dataset.token` 上复用，导致密码校验形同虚设。
   改为完全不缓存：每次下载 / 删除 / 播放都重新 `POST /unlock`，`401` 一律重新弹密码框。

2. **删除总报「目标已不存在」。** 删除按钮的 `data-del` 少了 `/files/` 前缀，
   `DELETE /中文目录` 落到未知路由返回 `404`。改为在 `listing.py` 里直接生成 `/files/` + 编码路径，
   用中文文件名和非空中文目录复测通过。

3. **`POST /mkdir` 从 PowerShell 发总 `400`。** PowerShell 5 的 `Set-Content -Encoding utf8`
   会写 BOM，JSON 解析失败；把带引号的 JSON 传给 `curl.exe` 又会丢内层引号。
   改用 `UTF8Encoding($false)` 写 body，再 `--data-binary @file` 发送。

4. **加密文件谁都能删。** `DELETE` 原本不校验密码。补上「先 `POST /unlock` 拿令牌，再带 `?token=` 删除」，
   无令牌返回 `401`。注意这只覆盖单文件路径，管理员的递归删目录仍是设计上的信任边界。

5. **手机在校园 Wi-Fi 连不上。** 报 `ERR_ADDRESS_UNREACHABLE`，但 PC 自己能访问 LAN IP。
   原因是 AP 的客户端隔离，不是防火墙（Windows 防火墙里 `python.exe` 的放行规则本来就在）。
   临时方案：Cloudflare 快速隧道，顺带拿到 HTTPS。

6. **改了 `app.js` 浏览器仍跑旧代码。** 静态资源原来没有强校验。现在默认
   `no-cache, must-revalidate` + `ETag` 协商，页面引用时再带 `?v=<内容哈希>`，命中版本戳的请求才长缓存。

7. **keep-alive 下前一个请求的 body 污染下一个请求。** 大量 `400/401/403/404` 分支从未读过 body 就返回，
   残留字节被当成下一个请求行。统一在写响应头前 `_drain_body()` 补齐；短读时置 `close_connection`。

8. **二进制文件被静默截断。** 裸 `body.split(b"--"+boundary)` 会把文件内容里偶然出现的边界字节当成新 part。
   改为只在 `\r\n--boundary`（后跟 CRLF 或 `--`）处切分，并剥离属于分隔符的尾随 CRLF。

9. **Windows 下 `python src/server.py > out.log` 启动即崩。** 重定向后 stdout 用系统代码页编码，
   打印中文角色名抛 `UnicodeEncodeError`。用 `python -X utf8 src/server.py` 或
   `set PYTHONIOENCODING=utf-8` 解决（已写进第 2 节）。

---

## 12. 已知限制与后续方向

| 限制 | 影响 | 改进方向 |
|---|---|---|
| 共享访问码 | 无法区分具体用户；换码影响整个角色 | 每用户账号 + 加盐哈希口令 |
| 会话与令牌只在内存 | 重启后需重新登录、重新解锁 | 签名 Cookie 或持久化会话存储 |
| 默认无 HTTPS | 局域网内口令与明文可被嗅探 | `ssl.SSLContext` 包装 socket，或走隧道 |
| 解锁令牌走查询串 | 进入浏览器历史、DevTools、隧道日志；仅应用日志做了剥离 | 改用 `X-Unlock-Token` 请求头 |
| 无 CSRF token / Origin 校验 | `SameSite=Lax` 挡住主要跨站 POST 向量，但不够强 | 增加显式 `Origin` / `Referer` 校验 |
| 无 `If-Range` | 文件改动后续传可能拼接两个版本 | 基于现有 ETag 实现 `If-Range` |
| 管理员递归删除 | 不校验子树内加密文件的令牌 | 设计边界；可为加密子树加可选口令门 |
| 多段 Range 不支持 | 少数播放器需要 `multipart/byteranges` | 实现 RFC 7233 多段响应 |
| 上传整体读入内存 | 256 MB 上限内大文件会占用同等内存；加密时还要额外一份密文 | 落临时文件流式处理，加密改为流式分块写 |
| v1 文件无完整性校验 | 旧文件仍可读，但篡改不可检测 | 提供 v1 → v2 迁移脚本 |
| 请求监视器是全局的 | 建设者/管理员能看到其他客户端的路径 | 按会话过滤或脱敏 |
| 无重命名 / 移动 / 回收站 | 管理能力有限，删除不可恢复 | 增加 move 接口与软删除 |
| 丢失密码不可恢复 | 密钥只由口令派生 | 设计使然，需用户自行保管 |
| 指标只在内存 | 重启后统计归零 | 落盘或接入外部时序库 |

---

## 13. 结语

这个项目用标准库实现了完整的文件服务，并把 HTTP 协议本身当作可观察对象：持久连接、
范围请求、条件缓存、multipart 上传、路径越权防御、会话与角色、口令加密，都能在页面上
「协议实验室」和「请求监视器」里直接看到原始响应头与状态码。

加密是**保密性 + 完整性**的：v2 头部与每个密文块都有 HMAC，篡改会被检测到；
但它不是 AEAD，也保护不了元数据，解锁令牌仍走查询串。这些边界连同同源内容风险、
内存占用等限制都写在 §9 与 §12 里，而不是当作已解决的问题。

> 改代码前请先更新 [`src/CONTRACT.md`](src/CONTRACT.md) —— 契约先行是这个项目能保持前后端一致的原因。
