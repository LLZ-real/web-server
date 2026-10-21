# Web 文件服务器

零第三方依赖的 HTTP/1.1 文件服务器，浏览器即客户端：文件列表、拖拽上传、下载（Range 断点续传 + ETag 条件缓存）、局域网跨设备访问。

## 运行

```bash
cd web-server
python src/server.py
```

浏览器打开 <http://localhost:8000/>；局域网设备访问 `http://<本机局域网 IP>:8000/`。
存储根目录为 `src/storage/`（首次启动自动创建）。

环境要求：Python ≥ 3.8（实测 3.13），Windows / macOS / Linux 均可。

## 目录结构

```
web-server/
├── report.md                 # 本文件
└── src/
    ├── CONTRACT.md           # 接口契约（先改契约再改代码）
    ├── server.py             # 入口 + 路由分发 + 日志 + 异常兜底
    ├── handlers/
    │   ├── listing.py        # GET /            列表页（服务端渲染）
    │   ├── download.py       # GET /files/<name> 下载 + Range + ETag
    │   └── upload.py         # POST /upload      multipart 上传
    ├── utils/
    │   ├── mime.py           # MIME 推断
    │   ├── pathsafe.py       # 路径穿越校验（realpath + commonpath）
    │   └── multipart.py      # multipart/form-data 切分
    ├── public/index.html     # 前端（拖拽上传 + 进度条 + video 测试区）
    └── storage/              # 存储根目录
```

分层：`server.py`（协议/路由）→ `handlers/`（业务）→ `utils/`（纯函数）。每个 handler 只依赖一个 `handler` 对象，可脱离 socket 单独测试。

## 能力清单

| 能力 | 实现要点 |
|---|---|
| 文件列表 | `listdir` 按 mtime 降序；`html.escape` 转义、`quote` 编码中文名 |
| 上传 | 手写 multipart 切分；文件名 basename 化 + 冲突改名 `a (1).txt` |
| 下载 | `seek + read(64KB)` 流式发送，内存占用与文件大小无关 |
| 断点续传 | 单段 Range → `206 + Content-Range`；越界 → `416` |
| 条件缓存 | ETag = `mtime_ns-size`（十六进制）；命中 → `304` 无 body |
| 路径安全 | 所有文件输入经 `pathsafe.resolve()`，`realpath + commonpath` 校验 |
| 并发 | `ThreadingHTTPServer`，多标签页并发下载互不阻塞 |
| 日志 | 时间 / IP / 请求行 / 状态码 / 字节数 / Range 或缓存命中 |

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

协议合规验证（curl 命令集）：

```bash
# 基础 GET（-I 走 HEAD，与 GET 共用逻辑）
curl -v http://localhost:8000/files/a.txt

# Range 三种形态
curl -v -H "Range: bytes=0-99"   http://localhost:8000/files/a.mp4   # 206
curl -v -H "Range: bytes=1024-"  http://localhost:8000/files/a.mp4   # 206，到文件尾
curl -v -H "Range: bytes=-500"   http://localhost:8000/files/a.mp4   # 206，最后 500 字节
curl -v -H "Range: bytes=999999999-" http://localhost:8000/files/a.mp4   # 416

# 条件请求（先取 ETag，再带 If-None-Match）
ETAG=$(curl -sI http://localhost:8000/files/a.mp4 | awk '/ETag/ {print $2}' | tr -d '\r')
curl -v -H "If-None-Match: $ETAG" http://localhost:8000/files/a.mp4   # 304

# 路径穿越（正斜杠 / 反斜杠两种）
curl -v "http://localhost:8000/files/..%2f..%2fetc%2fpasswd"   # 403
curl -v "http://localhost:8000/files/..%5c..%5cserver.py"      # 403

# 上传
curl -v -F "file=@test.txt" http://localhost:8000/upload
```

已实测通过：上传（含 5MB 大文件、中文文件名）、下载、三种 Range 形态、416、304、403（`..%2f` 与 `..%5c` 两种写法）、404、405、keep-alive 连接复用。

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

## 已知限制

| 限制 | 影响 | 演进方向 |
|---|---|---|
| 上传全量读入内存 | 传超大文件时内存飙升（当前：几 MB ~ 百 MB 可用） | 临时文件 + 流式 multipart 解析 |
| 无认证 | 知道地址即可访问 | Basic Auth / token |
| 无 HTTPS | 明文传输 | `ssl.SSLContext` 包装 socket |
| 无磁盘配额 | 可写满磁盘 | 上传前检查剩余空间 |
| 上传不可断点续传 | 中断需重传 | 分片上传 + 合并 |
| 不支持多段 Range | 部分专业播放器受限 | 按 RFC 7233 实现 `multipart/byteranges` |
| 目录不展示 | 只有单层平铺文件列表 | 目录树 + 面包屑导航 |
| 无删除/重命名 | 只能上传和下载 | 增加 `DELETE /files/<name>` 路由 |

以上均不在本项目范围内，列出以界定设计边界。
