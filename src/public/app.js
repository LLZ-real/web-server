/* ============================================================
   Web File Server · 演示增强层 app.js
   独立 IIFE，不污染原有 index.html 内联脚本的全局作用域。
   依赖：index.html 里的 #wrap / 表格行 / 原有 toast()、formatSize()
   ============================================================ */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var wrap = $("#wrap");
  if (!wrap) { return; }
  var role = wrap.dataset.role || "guest";
  var isBuilder = role !== "guest";

  /* ---------------- 工具 ---------------- */
  function fmt(n) {
    if (n < 1024) { return n + " B"; }
    var u = ["KB", "MB", "GB", "TB"], v = n, i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
    return v.toFixed(1) + " " + u[i];
  }
  function fmtRate(n) { return fmt(n) + "/s"; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  }); }
  function toast(msg, err) {
    if (typeof window.toast === "function") { return window.toast(msg, err); }
    console.log("[wfs]", msg);
  }

  /* 统一的 API 请求封装。
     服务端重启后内存会话失效时，API 不再返回 302 登录页 HTML 让 fetch 静默跟随，
     而是返回 401 JSON；这里显式处理，给出可操作的提示，避免"搜索失败，请重试"
     这类误导性报错。fetch redirect 设为 manual，兜底识别被跟随的登录页。 */
  var sessionExpiredNotified = false;
  function notifySessionExpired() {
    if (sessionExpiredNotified) { return; }
    sessionExpiredNotified = true;
    toast("登录已失效（服务可能重启过），请刷新页面重新登录", true);
    var box = $("#search-body");
    if (box) {
      box.innerHTML = '<div class="sr-empty">登录已失效，请 <a href="/login">重新登录</a> 后再搜索。</div>';
    }
  }
  function apiFetch(url, opts) {
    opts = opts || {};
    opts.cache = "no-store";
    opts.redirect = "manual";
    return fetch(url, opts).then(function (res) {
      if (res.status === 401 || res.type === "opaqueredirect") {
        notifySessionExpired();
        throw new Error("login-required");
      }
      if (res.redirected && /\/login/.test(res.url)) {
        notifySessionExpired();
        throw new Error("login-required");
      }
      if (res.status === 404) {
        // 404 通常意味着「正在运行的服务进程是旧版本，没有这个接口」，
        // 而不是网络错误。给出可操作的诊断，避免误导成"搜索失败"。
        throw new Error("接口不存在（404）：正在运行的服务器可能是旧版本，请重启 python src/server.py");
      }
      var ct = res.headers.get("content-type") || "";
      if (ct.indexOf("json") < 0) {
        throw new Error("服务器返回了非 JSON 响应（HTTP " + res.status + "）");
      }
      return res.json();
    });
  }

  /* ---------------- 主题 ---------------- */
  var THEME_KEY = "wfs_theme";
  function applyTheme(t) {
    document.body.classList.toggle("wfs-light", t === "light");
    var btn = $("#theme-btn");
    if (btn) { btn.classList.toggle("on", t === "light"); btn.title = t === "light" ? "切换暗色主题" : "切换亮色主题"; }
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
    drawCharts();
  }
  try { applyTheme(localStorage.getItem(THEME_KEY) || "dark"); } catch (e) { applyTheme("dark"); }

  /* ============================================================
     顶部工具栏：模块导航 + 全局搜索 + 视图工具
     插在 header 之后；所有「展示模块」统一追加到 wrap 末尾，
     保证【存储内容】列表始终紧跟上传卡，处于首屏核心位置。
     ============================================================ */
  var host = document.createElement("div");
  host.className = "wfs-toolbar";
  host.id = "wfs-toolbar";
  host.innerHTML =
    '<div class="wfs-search-wrap">' +
      '<span class="tb-label">搜索</span>' +
      '<input class="wfs-search" id="wfs-search" type="search" ' +
        'placeholder="搜当前目录，或输入关键词回车全局搜…" aria-label="搜索文件" autocomplete="off">' +
    '</div>' +
    '<button class="wfs-btn" id="sort-btn" title="切换排序">⇅ 时间</button>' +
    '<button class="wfs-btn" id="compact-btn" title="紧凑视图">▤ 紧凑</button>' +
    '<span class="spacer"></span>' +
    '<nav class="wfs-nav" aria-label="模块导航">' +
      '<button class="wfs-btn" data-jump="card-files">▣ 文件</button>' +
      '<button class="wfs-btn" data-jump="card-video">▶ 视频</button>' +
      '<button class="wfs-btn" data-jump="sec-insight">◈ 数据洞察</button>' +
      '<button class="wfs-btn" data-jump="card-monitor">≡ 监视器</button>' +
    '</nav>' +
    '<button class="wfs-btn" id="gallery-btn" title="图片画廊">▦ 画廊</button>' +
    '<button class="wfs-btn" id="share-btn" title="分享此页">⤴ 分享</button>' +
    '<button class="wfs-btn" id="theme-btn" title="切换主题">◐ 主题</button>' +
    (isBuilder ? '<button class="wfs-btn primary" id="demo-btn" title="生成演示流量">▶ 演示流量</button>' : "");
  var header = $("header", wrap) || wrap.firstChild;
  if (header && header.nextSibling) { wrap.insertBefore(host, header.nextSibling); }
  else { wrap.appendChild(host); }

  /* ---------------- 数据洞察总容器（追加到末尾） ---------------- */
  var section = document.createElement("section");
  section.id = "sec-insight";
  section.className = "wfs-section";
  section.innerHTML =
    '<div class="wfs-section-head">' +
      '<div><h2>数据洞察</h2><p>服务器运行状态、协议行为与存储画像 · 全部实时</p></div>' +
      '<span class="pill">自动刷新</span>' +
    '</div>';
  wrap.appendChild(section);

  /* ---------------- 卡片 1：实时流量仪表盘 ---------------- */
  var dash = document.createElement("div");
  dash.className = "card";
  dash.id = "dash-card";
  dash.innerHTML =
    '<div class="card-head">' +
      '<span class="card-title">① 实时流量仪表盘 <span class="dim">· Server-Sent Events</span></span>' +
      '<div class="head-tools">' +
        '<span class="live-badge off" id="sse-badge"><i></i><span>SSE 连接中…</span></span></div>' +
    '</div>' +
     '<div class="card-body">' +
      '<div class="kpi-grid">' +
        '<div class="kpi k1"><div class="k-label">总请求</div><div class="k-value" id="k-req">0</div><div class="k-sub" id="k-uptime">运行 0s</div></div>' +
        '<div class="kpi k2"><div class="k-label">出站流量</div><div class="k-value" id="k-out">0 B</div><div class="k-sub" id="k-in">入站 0 B</div></div>' +
        '<div class="kpi k3"><div class="k-label">当前速率</div><div class="k-value" id="k-rate">0 B/s</div><div class="k-sub" id="k-peak">峰值 0 req/s</div></div>' +
        '<div class="kpi k4"><div class="k-label">存储 / 连接</div><div class="k-value" id="k-files">0</div><div class="k-sub" id="k-active">0 个活跃连接</div></div>' +
      '</div>' +
      '<div class="chart-grid">' +
        '<div class="chart-box">' +
          '<div class="chart-title">每秒请求数 · 最近 60 秒</div>' +
          '<canvas class="wfs-canvas" id="cv-line" height="150"></canvas>' +
          '<div class="legend"><span><i style="background:#4c8bf5"></i>请求 / 秒</span>' +
          '<span><i style="background:#3ecf8e"></i>出站 / 秒（独立刻度）</span>' +
          '<span><i style="background:#ff9f43"></i>该秒有 4xx/5xx</span></div>' +
        '</div>' +
        '<div class="chart-box">' +
          '<div class="chart-title">状态码分布</div>' +
          '<canvas class="wfs-canvas" id="cv-ring" height="118"></canvas>' +
          '<div class="status-bars" id="status-bars"></div>' +
        '</div>' +
      '</div>' +
    '</div>';
  section.appendChild(dash);

  /* ---------------- 卡片 2：存储画像 + 图片画廊 ---------------- */
  var extra = document.createElement("div");
  extra.className = "card";
  extra.id = "card-profile";
  extra.innerHTML =
    '<div class="card-head"><span class="card-title">② 存储画像与图片画廊</span>' +
    '<div class="head-tools">' +
    '<span class="pill"><b id="type-total">0</b> 个文件</span></div></div>' +
     '<div class="card-body">' +
      '<div class="chart-title">按类型分布（字节）</div>' +
      '<div class="type-bars" id="type-bars"></div>' +
      '<div class="chart-title" style="margin-top:18px">图片画廊 <span class="dim">· 点击放大</span></div>' +
      '<div class="gallery" id="gallery"></div>' +
    '</div>';
  section.appendChild(extra);

  /* ---------------- 卡片 3：HTTP 协议实验室 ---------------- */
  var lab = document.createElement("div");
  lab.className = "card";
  lab.id = "card-lab";
  lab.innerHTML =
    '<div class="card-head"><span class="card-title">③ HTTP 协议实验室</span>' +
    '<div class="head-tools">' +
    '<span class="pill">直接观察 Range / 缓存 / HEAD</span></div></div>' +
     '<div class="card-body"><div class="lab-grid">' +
      '<div class="lab-form">' +
        '<label>目标文件</label><select id="lab-file"></select>' +
        '<label>请求方法</label><select id="lab-method">' +
          '<option value="GET">GET</option><option value="HEAD">HEAD</option></select>' +
        '<label>Range 头（可留空）</label><input id="lab-range" placeholder="bytes=0-1023">' +
        '<label>If-None-Match（可留空）</label><input id="lab-etag" placeholder="&quot;mtime-size&quot;">' +
        '<div class="lab-presets">' +
          '<button class="wfs-btn" data-preset="first">首 1KB</button>' +
          '<button class="wfs-btn" data-preset="suffix">末 500B</button>' +
          '<button class="wfs-btn" data-preset="bad">越界 → 416</button>' +
          '<button class="wfs-btn" data-preset="head">HEAD</button>' +
          '<button class="wfs-btn" data-preset="cond">条件请求 → 304</button>' +
        '</div>' +
        '<button class="wfs-btn primary" id="lab-send" style="justify-content:center;margin-top:4px">发送请求</button>' +
      '</div>' +
      '<div><div class="chart-title">响应 <span class="dim">· 状态行 + 原始响应头</span></div>' +
        '<div class="lab-out" id="lab-out">选择一个文件，点「发送请求」观察原始响应头。<br><br>' +
        '<span class="ln-note">试试右侧（下方）的预设按钮 →</span></div>' +
    '</div></div>';
  section.appendChild(lab);

  /* 把「请求监视器」纳入洞察区，便于统一导航 */
  var monitor = $("#card-monitor");
  if (monitor) { section.appendChild(monitor); }


  /* ---------------- 灯箱 / 分享弹层 ---------------- */
  var lb = document.createElement("div");
  lb.id = "lightbox";
  lb.innerHTML =
    '<button class="lb-close" id="lb-close" aria-label="关闭">×</button>' +
    '<button class="lb-nav prev" id="lb-prev" aria-label="上一张">‹</button>' +
    '<img id="lb-img" alt="">' +
    '<button class="lb-nav next" id="lb-next" aria-label="下一张">›</button>' +
    '<div class="lb-cap" id="lb-cap"></div><div class="lb-hint">← → 翻页 · Esc 关闭</div>';
  document.body.appendChild(lb);

  var sp = document.createElement("div");
  sp.id = "share-pop";
  sp.innerHTML =
    '<div class="chart-title">手机 / 局域网访问</div>' +
    '<div style="font-size:26px;margin:6px 0 2px">📡</div>' +
    '<div style="color:var(--fg);font-size:12.5px;font-weight:600">同一 Wi-Fi 下打开此地址</div>' +
    '<div class="sp-url" id="sp-url"></div>' +
    '<button class="wfs-btn primary" id="sp-copy" style="width:100%;justify-content:center;margin-top:12px">复制链接</button>' +
    '<div style="color:var(--dim);font-size:10.5px;margin-top:9px;line-height:1.6">' +
    '若手机无法访问，检查 Windows 防火墙<br>或改用 Cloudflare Tunnel 暴露服务</div>';
  document.body.appendChild(sp);

  /* ============================================================
     仪表盘：数据 + SSE + Canvas
     ============================================================ */
  var latest = null;
  var prevSeriesKey = "";

  function palette() {
    var light = document.body.classList.contains("wfs-light");
    return {
      grid: light ? "#e6eaf2" : "#232833",
      text: light ? "#6b7590" : "#6b7385",
      a: "#4c8bf5", b: "#3ecf8e", c: "#ff9f43", d: "#a078ff", e: "#ef5b5b",
      fillA: light ? "rgba(76,139,245,.14)" : "rgba(76,139,245,.20)",
      track: light ? "#e9edf5" : "#1b1f27",
    };
  }

  function setupCanvas(cv, h) {
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth || cv.parentElement.clientWidth || 400;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.height = h + "px";
    var ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }

  function drawLine(data) {
    var cv = $("#cv-line"); if (!cv) { return; }
    var s = data.series || [], p = palette();
    var g = setupCanvas(cv, 150), ctx = g.ctx, W = g.w, H = g.h;
    var pad = { l: 34, r: 8, t: 8, b: 18 };
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    ctx.clearRect(0, 0, W, H);
    var maxReq = 1, maxOut = 1;
    s.forEach(function (d) {
      if (d.req > maxReq) { maxReq = d.req; }
      if (d.out > maxOut) { maxOut = d.out; }
    });
    // 网格
    ctx.strokeStyle = p.grid; ctx.lineWidth = 1; ctx.font = "10px ui-monospace, monospace";
    ctx.fillStyle = p.text; ctx.textAlign = "right";
    for (var i = 0; i <= 3; i++) {
      var y = pad.t + ih * i / 3;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillText(String(Math.round(maxReq * (3 - i) / 3)), pad.l - 6, y + 3);
    }
    if (!s.length) { return; }
    var X = function (i) { return pad.l + iw * (i / Math.max(1, s.length - 1)); };
    var Y = function (v, m) { return pad.t + ih - ih * (v / m); };
    // 告警柱
    s.forEach(function (d, i) {
      if ((d.err || 0) > 0) {
        ctx.fillStyle = "rgba(255,159,67,.28)";
        ctx.fillRect(X(i) - 1, pad.t, Math.max(2, iw / s.length - 1), ih);
      }
    });
    // 出站面积
    ctx.beginPath(); ctx.moveTo(X(0), pad.t + ih);
    s.forEach(function (d, i) { ctx.lineTo(X(i), Y(d.out, maxOut)); });
    ctx.lineTo(X(s.length - 1), pad.t + ih); ctx.closePath();
    ctx.fillStyle = "rgba(62,207,142,.10)"; ctx.fill();
    ctx.beginPath();
    s.forEach(function (d, i) { i ? ctx.lineTo(X(i), Y(d.out, maxOut)) : ctx.moveTo(X(i), Y(d.out, maxOut)); });
    ctx.strokeStyle = p.b; ctx.lineWidth = 1.4; ctx.stroke();
    // 请求面积
    ctx.beginPath(); ctx.moveTo(X(0), pad.t + ih);
    s.forEach(function (d, i) { ctx.lineTo(X(i), Y(d.req, maxReq)); });
    ctx.lineTo(X(s.length - 1), pad.t + ih); ctx.closePath();
    ctx.fillStyle = p.fillA; ctx.fill();
    ctx.beginPath();
    s.forEach(function (d, i) { i ? ctx.lineTo(X(i), Y(d.req, maxReq)) : ctx.moveTo(X(i), Y(d.req, maxReq)); });
    ctx.strokeStyle = p.a; ctx.lineWidth = 2; ctx.stroke();
    // 最后一个点
    var last = s[s.length - 1];
    ctx.beginPath(); ctx.arc(X(s.length - 1), Y(last.req, maxReq), 3.2, 0, Math.PI * 2);
    ctx.fillStyle = p.a; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.4; ctx.stroke();
    // 时间轴
    ctx.fillStyle = p.text; ctx.textAlign = "center";
    [0, Math.floor(s.length / 2), s.length - 1].forEach(function (i) {
      var d = new Date((data.now - (s.length - 1 - i)) * 1000);
      ctx.fillText(("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + ":" + ("0" + d.getSeconds()).slice(-2), X(i), H - 4);
    });
  }

  function drawRing(data) {
    var cv = $("#cv-ring"); if (!cv) { return; }
    var p = palette(), g = setupCanvas(cv, 118), ctx = g.ctx, W = g.w, H = g.h;
    ctx.clearRect(0, 0, W, H);
    var st = data.status || {}, keys = ["2", "3", "4", "5"];
    var colors = { "2": p.b, "3": p.a, "4": p.c, "5": p.e };
    var total = keys.reduce(function (a, k) { return a + (st[k] || 0); }, 0) || 1;
    var cx = W / 2, cy = H / 2, r = Math.min(W, H) / 2 - 8, lw = 13;
    // 底环
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = p.track; ctx.lineWidth = lw; ctx.stroke();
    var start = -Math.PI / 2;
    keys.forEach(function (k) {
      var v = st[k] || 0; if (!v) { return; }
      var end = start + Math.PI * 2 * v / total;
      ctx.beginPath(); ctx.arc(cx, cy, r, start, end);
      ctx.strokeStyle = colors[k]; ctx.lineWidth = lw; ctx.lineCap = "butt"; ctx.stroke();
      start = end;
    });
    ctx.fillStyle = p.text; ctx.textAlign = "center";
    ctx.font = "600 19px ui-sans-serif, system-ui, sans-serif";
    ctx.fillStyle = document.body.classList.contains("wfs-light") ? "#1b2231" : "#e8ebf2";
    ctx.fillText(String(data.total_requests || 0), cx, cy + 2);
    ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
    ctx.fillStyle = p.text; ctx.fillText("总请求", cx, cy + 16);

    var bars = $("#status-bars"); if (!bars) { return; }
    bars.innerHTML = keys.map(function (k) {
      var v = st[k] || 0, pct = (v / total * 100).toFixed(1);
      return '<div class="sb-row"><span class="sb-tag">' + k + 'xx</span>' +
             '<span class="sb-track"><i style="width:' + pct + '%;background:' + colors[k] + '"></i></span>' +
             '<span class="sb-num">' + v + '</span></div>';
    }).join("");
  }

  function drawTypes() {
    var rows = $$("#list tbody tr[data-ext]"), box = $("#type-bars");
    if (!box) { return; }
    var groups = {};
    var LABEL = {
      image: ["图片", ".png .jpg .jpeg .gif .webp .bmp .svg .ico"],
      video: ["视频", ".mp4 .webm .ogg .mov .mkv .m4v"],
      audio: ["音频", ".mp3 .wav .flac .aac .m4a"],
      archive: ["压缩包", ".zip .rar .7z .tar .gz .bz2"],
      doc: ["文档", ".pdf .doc .docx .xls .xlsx .ppt .pptx"],
      code: ["代码", ".py .js .ts .html .css .json .xml .yml .yaml .sh"],
      text: ["文本", ".txt .md .csv .log"],
    };
    var sets = {};
    Object.keys(LABEL).forEach(function (k) { sets[k] = LABEL[k][1].split(" "); });
    rows.forEach(function (tr) {
      var ext = tr.dataset.ext || "", size = Number(tr.dataset.size) || 0, k = "other";
      Object.keys(sets).forEach(function (g) { if (sets[g].indexOf(ext) >= 0) { k = g; } });
      groups[k] = (groups[k] || 0) + size;
    });
    var COLORS = { image: "#3ecf8e", video: "#a078ff", audio: "#ff9f43",
                   archive: "#ffd166", doc: "#ef5b5b", code: "#4c8bf5",
                   text: "#8b93a7", other: "#5c6478" };
    var keys = Object.keys(groups).sort(function (a, b) { return groups[b] - groups[a]; });
    var max = Math.max.apply(null, keys.map(function (k) { return groups[k]; }).concat([1]));
    $("#type-total").textContent = rows.length;
    if (!keys.length) { box.innerHTML = '<div class="gal-empty">当前目录没有文件</div>'; return; }
    box.innerHTML = keys.map(function (k) {
      var name = LABEL[k] ? LABEL[k][0] : "其他";
      return '<div class="tb-row"><span>' + name + '</span>' +
             '<span class="tb-track"><i style="width:' + (groups[k] / max * 100).toFixed(1) + '%;background:' + (COLORS[k] || COLORS.other) + '"></i></span>' +
             '<span class="tb-num">' + fmt(groups[k]) + '</span></div>';
    }).join("");
  }

  function drawCharts() { if (latest) { drawLine(latest); drawRing(latest); } drawTypes(); }

  function renderKPI(d) {
    latest = d;
    $("#k-req").textContent = d.total_requests.toLocaleString();
    $("#k-out").textContent = fmt(d.total_out);
    $("#k-in").textContent = "入站 " + fmt(d.total_in);
    var s = d.series || [], last = s.length ? s[s.length - 1] : { out: 0, req: 0 };
    $("#k-rate").textContent = fmtRate(last.out);
    $("#k-peak").textContent = "峰值 " + d.peak_rps + " req/s";
    $("#k-uptime").textContent = "运行 " + d.uptime + "s";
    $("#k-files").textContent = (d.storage && d.storage.files != null) ? d.storage.files : "—";
    $("#k-active").textContent = d.active + " 个活跃连接";
    drawLine(d); drawRing(d); drawTypes();
  }

  var es = null, sseErrors = 0;
  function connectSSE() {
    if (!window.EventSource) { return; }
    try { if (es) { es.close(); } } catch (e) {}
    es = new EventSource("/api/events");
    var badge = $("#sse-badge");
    es.onopen = function () {
      sseErrors = 0;
      badge.classList.remove("off");
      badge.querySelector("span").textContent = "SSE 实时";
    };
    es.onmessage = function (e) {
      try { renderKPI(JSON.parse(e.data)); } catch (err) {}
    };
    es.onerror = function () {
      sseErrors++;
      badge.classList.add("off");
      badge.querySelector("span").textContent = "重连中…";
      // 连续失败（常见于服务重启后旧会话失效）则停止自动重连并提示，
      // 避免 EventSource 每几秒无限重连
      if (sseErrors >= 3) {
        try { es.close(); } catch (e) {}
        badge.querySelector("span").textContent = "连接已断开";
        notifySessionExpired();
      }
    };
  }
  // 首次也拉一次快照，保证立刻有数据
  apiFetch("/api/metrics")
    .then(renderKPI).catch(function () {});
  connectSSE();
  window.addEventListener("resize", drawCharts);

  /* ============================================================
     演示流量按钮
     ============================================================ */
  var demoBtn = $("#demo-btn");
  if (demoBtn) {
    demoBtn.addEventListener("click", function () {
      demoBtn.disabled = true;
      demoBtn.textContent = "▶ 生成中…";
      apiFetch("/api/demo/traffic", { method: "POST" })
        .then(function (d) {
          toast(d.ok ? "演示流量已启动，观察曲线与监视器" : "演示流量已在运行");
          setTimeout(function () { demoBtn.disabled = false; demoBtn.textContent = "▶ 演示流量"; }, 10000);
        })
        .catch(function () { demoBtn.disabled = false; demoBtn.textContent = "▶ 演示流量"; toast("启动失败", true); });
    });
  }

  /* ============================================================
     搜索：结果统一呈现在页面内独立的「搜索结果」卡片中。
     不再筛选当前目录表格，也不整页跳走，避免"搜了却看不到结果"。
     ============================================================ */
  var search = $("#wfs-search");
  var searchTimer = null, searchSeq = 0;

  var resultCard = document.createElement("div");
  resultCard.className = "card";
  resultCard.id = "card-search";
  resultCard.innerHTML =
    '<div class="card-head">' +
      '<div><div class="card-title">搜索结果 <span class="dim" id="search-summary"></span></div>' +
        '<div class="crumbs" id="search-crumbs"></div></div>' +
      '<button class="wfs-btn" id="search-clear" type="button">✕ 清除搜索</button>' +
    '</div>' +
    '<div class="card-body" id="search-body"></div>';
  // 放在「存储内容」之前，搜索时一眼就能看到
  var filesCard = $("#card-files");
  if (filesCard) { wrap.insertBefore(resultCard, filesCard); }
  else { wrap.appendChild(resultCard); }
  resultCard.style.display = "none";

  function hideResults() {
    resultCard.style.display = "none";
    $("#search-body").innerHTML = "";
  }

  function renderResults(q, results, truncated) {
    var sum = $("#search-summary"), crumbs = $("#search-crumbs"), box = $("#search-body");
    sum.textContent = "· 全盘 " + results.length + " 条" + (truncated ? "（已截断）" : "");
    crumbs.innerHTML = '<span>关键词「' + esc(q) + '」</span><span class="sep">/</span>' +
                       '<span>整个存储目录</span>';
    if (!results.length) {
      box.innerHTML = '<div class="sr-empty">没有找到名称包含「' + esc(q) + '」的文件或目录。</div>';
    } else {
      var rows = results.map(function (r, i) {
        var label = r.kind === "dir" ? "DIR" : (r.ext ? r.ext.slice(1).toUpperCase() : "FILE");
        var loc = r.dir ? r.dir : "根目录";
        var rel = r.rel || r.name;
        var openUrl = "/?dir=" + encodeURIComponent(r.dir || "") +
                      "#f=" + encodeURIComponent(r.name);
        var fileUrl = "/files/" + rel.split("/").map(encodeURIComponent).join("/");
        var actions = r.kind === "dir"
          ? '<a class="wfs-btn" href="' + esc(openUrl) + '">进入目录</a>'
          : '<a class="wfs-btn" href="' + esc(openUrl) + '">定位到所在目录</a>' +
            '<a class="wfs-btn" href="' + esc(fileUrl) + '" download>下载</a>';
        return '<div class="sr-row" data-i="' + i + '">' +
          '<span class="sr-badge ' + (r.kind === "dir" ? "dir" : "") + '">' + esc(label) + '</span>' +
          '<div class="sr-main"><div class="sr-name">' + esc(r.name) +
            (r.encrypted ? ' <span class="sp-lock">🔒</span>' : '') + '</div>' +
            '<div class="sr-path">' + esc(loc) + '/' + (r.kind === "file" ? esc(r.name) : "") +
            (r.kind === "file" ? ' <span class="dim">· ' + fmt(r.size) + '</span>' : '') + '</div></div>' +
          '<div class="sr-actions">' + actions + '</div></div>';
      }).join("");
      box.innerHTML = rows;
    }
    resultCard.style.display = "";
    if (resultCard.scrollIntoView) {
      resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function globalSearch(q) {
    var seq = ++searchSeq;
    $("#search-body").innerHTML = '<div class="sr-empty">正在搜索整个存储…</div>';
    resultCard.style.display = "";
    apiFetch("/api/search?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (seq !== searchSeq) { return; }   // 丢弃过期响应
        renderResults(q, d.results || [], d.truncated);
      })
      .catch(function (err) {
        if (seq !== searchSeq) { return; }
        if (err && err.message === "login-required") { return; }  // 已由 notifySessionExpired 提示
        $("#search-body").innerHTML = '<div class="sr-empty">' +
          esc(err && err.message ? err.message : "搜索失败，请重试") + '</div>';
      });
  }

  search.addEventListener("input", function () {
    var q = search.value.trim();
    clearTimeout(searchTimer);
    if (!q) { hideResults(); return; }
    searchTimer = setTimeout(function () { globalSearch(q); }, 350);
  });
  search.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { search.value = ""; hideResults(); search.blur(); }
    if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(searchTimer);
      var q = search.value.trim();
      if (q) { globalSearch(q); } else { hideResults(); }
    }
  });
  $("#search-clear").addEventListener("click", function () {
    search.value = ""; hideResults(); search.focus();
  });

  /* URL 带 #f=文件名时（从结果点「定位」过来），在目录表格里高亮该行 */
  try {
    var hash = decodeURIComponent(location.hash || "");
    if (hash.indexOf("#f=") === 0) {
      var wanted = hash.slice(3);
      var hit = $$("#list tbody tr[data-name]").filter(function (tr) {
        return tr.dataset.name === wanted;
      })[0];
      if (hit) {
        hit.classList.add("wfs-hit");
        setTimeout(function () {
          if (hit.scrollIntoView) { hit.scrollIntoView({ block: "center", behavior: "smooth" }); }
        }, 120);
      }
    }
  } catch (err) {}

  /* 模块导航：点击平滑滚动到对应卡片 */
  $$("[data-jump]").forEach(function (b) {
    b.addEventListener("click", function () {
      var el = document.getElementById(b.dataset.jump);
      if (el && el.scrollIntoView) { el.scrollIntoView({ behavior: "smooth", block: "start" }); }
    });
  });
  var sortMode = "time";
  $("#sort-btn").addEventListener("click", function () {
    var tbody = $("#list tbody"), rows = $$("tr[data-name]", tbody);
    sortMode = sortMode === "time" ? "name" : sortMode === "name" ? "size" : "time";
    var label = { time: "⇅ 时间", name: "⇅ 名称", size: "⇅ 大小" }[sortMode];
    $("#sort-btn").textContent = label;
    var dirs = rows.filter(function (r) { return r.dataset.dir === "1"; });
    var files = rows.filter(function (r) { return r.dataset.dir !== "1"; });
    files.sort(function (a, b) {
      if (sortMode === "name") { return a.dataset.name.localeCompare(b.dataset.name, "zh"); }
      if (sortMode === "size") { return (Number(b.dataset.size) || 0) - (Number(a.dataset.size) || 0); }
      return (Number(b.dataset.mtime) || 0) - (Number(a.dataset.mtime) || 0);
    });
    dirs.concat(files).forEach(function (r) { tbody.appendChild(r); });
  });
  $("#compact-btn").addEventListener("click", function () {
    var t = $("#list"); t.classList.toggle("compact");
    $("#compact-btn").classList.toggle("on", t.classList.contains("compact"));
  });
  $("#theme-btn").addEventListener("click", function () {
    applyTheme(document.body.classList.contains("wfs-light") ? "dark" : "light");
  });

  /* ============================================================
     图片画廊 + 灯箱
     ============================================================ */
  var IMG_EXT = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"];
  var images = [];
  function collectImages() {
    images = [];
    $$("#list tbody tr[data-ext]").forEach(function (tr) {
      if (IMG_EXT.indexOf(tr.dataset.ext) >= 0 && tr.dataset.enc !== "1") {
        var getter = $("button[data-get]", tr);
        if (getter) { images.push({ url: getter.dataset.get, name: tr.dataset.name }); }
      }
    });
    var box = $("#gallery");
    if (!images.length) { box.innerHTML = '<div class="gal-empty">当前目录没有可直接预览的图片</div>'; return; }
    box.innerHTML = images.map(function (im, i) {
      return '<div class="gal-item" data-i="' + i + '" title="' + esc(im.name) + '">' +
             '<img loading="lazy" src="' + esc(im.url) + '" alt="' + esc(im.name) + '">' +
             '<div class="gal-cap">' + esc(im.name) + '</div></div>';
    }).join("");
    $$(".gal-item", box).forEach(function (el) {
      el.addEventListener("click", function () { openLB(Number(el.dataset.i)); });
    });
  }
  var lbIndex = 0;
  function openLB(i) {
    if (!images.length) { return; }
    lbIndex = (i + images.length) % images.length;
    $("#lb-img").src = images[lbIndex].url;
    $("#lb-cap").textContent = images[lbIndex].name + "  (" + (lbIndex + 1) + "/" + images.length + ")";
    lb.classList.add("on");
  }
  function closeLB() { lb.classList.remove("on"); $("#lb-img").removeAttribute("src"); }
  $("#lb-close").addEventListener("click", closeLB);
  $("#lb-prev").addEventListener("click", function () { openLB(lbIndex - 1); });
  $("#lb-next").addEventListener("click", function () { openLB(lbIndex + 1); });
  lb.addEventListener("click", function (e) { if (e.target === lb) { closeLB(); } });
  document.addEventListener("keydown", function (e) {
    if (!lb.classList.contains("on")) { return; }
    if (e.key === "Escape") { closeLB(); }
    if (e.key === "ArrowLeft") { openLB(lbIndex - 1); }
    if (e.key === "ArrowRight") { openLB(lbIndex + 1); }
  });
  $("#gallery-btn").addEventListener("click", function () {
    collectImages();
    if (images.length) { openLB(0); }
    else { toast("当前目录没有可预览的图片", true); }
  });
  collectImages();

  /* ============================================================
     分享卡片：一键复制当前页面地址（含 dir），供手机 / 同局域网访问
     不依赖任何外部二维码服务，离线演示同样可用
     ============================================================ */
  function showShare() {
    var pop = $("#share-pop");
    if (pop.classList.contains("on")) { pop.classList.remove("on"); return; }
    var url = location.href;
    $("#sp-url").textContent = url;
    pop.classList.add("on");
  }
  $("#share-btn").addEventListener("click", showShare);
  $("#sp-copy").addEventListener("click", function () {
    var url = location.href, btn = this;
    function done(ok) {
      btn.textContent = ok ? "✓ 已复制" : "复制失败";
      setTimeout(function () { btn.textContent = "复制链接"; }, 1600);
      if (ok) { toast("链接已复制，可在手机浏览器打开"); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { done(true); }, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = url; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) {}
      ta.remove(); done(ok);
    }
  });
  document.addEventListener("click", function (e) {
    var pop = $("#share-pop");
    if (pop.classList.contains("on") && !pop.contains(e.target) && e.target.id !== "share-btn") {
      pop.classList.remove("on");
    }
  });

  /* ============================================================
     协议实验室
     ============================================================ */
  var labFiles = $$("#list tbody tr[data-ext]").map(function (tr) {
    var g = $("button[data-get]", tr);
    return g ? { url: g.dataset.get, name: tr.dataset.name } : null;
  }).filter(Boolean);
  var sel = $("#lab-file");
  sel.innerHTML = labFiles.length
    ? labFiles.map(function (f, i) { return '<option value="' + i + '">' + esc(f.name) + '</option>'; }).join("")
    : '<option value="-1">（当前目录无文件）</option>';
  var lastEtag = "";
  $$(".lab-presets button").forEach(function (b) {
    b.addEventListener("click", function () {
      var p = b.dataset.preset;
      if (p === "first") { $("#lab-range").value = "bytes=0-1023"; $("#lab-etag").value = ""; $("#lab-method").value = "GET"; }
      if (p === "suffix") { $("#lab-range").value = "bytes=-500"; $("#lab-etag").value = ""; $("#lab-method").value = "GET"; }
      if (p === "bad") { $("#lab-range").value = "bytes=999999999-"; $("#lab-etag").value = ""; $("#lab-method").value = "GET"; }
      if (p === "head") { $("#lab-range").value = ""; $("#lab-etag").value = ""; $("#lab-method").value = "HEAD"; }
      if (p === "cond") { $("#lab-range").value = ""; $("#lab-etag").value = lastEtag || "*"; $("#lab-method").value = "GET"; }
    });
  });
  /* 逐行解释响应头：把「原始报文」翻译成人话 */

  $("#lab-send").addEventListener("click", function () {
    var idx = Number(sel.value);
    if (idx < 0 || !labFiles[idx]) { toast("请先选择一个文件", true); return; }
    var f = labFiles[idx], method = $("#lab-method").value;
    var headers = {};
    var rg = $("#lab-range").value.trim(), et = $("#lab-etag").value.trim();
    if (rg) { headers["Range"] = rg; }
    if (et) { headers["If-None-Match"] = et; }
    var out = $("#lab-out");
    out.innerHTML = '<span class="ln-note">' + esc(method + " " + f.url) + '</span>\n' +
                    Object.keys(headers).map(function (k) {
                      return '<span class="ln-key">' + esc(k) + '</span>: ' + esc(headers[k]);
                    }).join("\n") + "\n\n... 等待响应 ...";
    var t0 = performance.now();
    fetch(f.url, { method: method, headers: headers, cache: "no-store" })
      .then(function (res) {
        var dt = (performance.now() - t0).toFixed(1);
        var lines = [];
        lines.push('<span class="ln-status">' + esc(res.status + " " + res.statusText) + '</span>  <span class="ln-note">' + dt + ' ms</span>');
        res.headers.forEach(function (v, k) {
          lines.push('<span class="ln-key">' + esc(k) + '</span>: ' + esc(v));
        });
        var cr = res.headers.get("content-range"), cl = res.headers.get("content-length");
        if (cr) { lines.push('\n<span class="ln-note">▸ Content-Range = ' + esc(cr) + "（本段 " + esc(cl) + " 字节）</span>"); }
        if (res.status === 304) { lines.push('\n<span class="ln-note">▸ 304：缓存命中，本次 0 字节传输</span>'); }
        if (res.status === 416) { lines.push('\n<span class="ln-note">▸ 416：Range 越界，服务器给出完整大小</span>'); }
        if (res.headers.get("etag")) { lastEtag = res.headers.get("etag"); }
        out.innerHTML = lines.join("\n");
      })
      .catch(function (e) { out.innerHTML = '<span class="ln-err">请求失败：' + esc(e.message) + "</span>"; });
  });

  /* ============================================================
     上传速率 / ETA：包装原有 XHR 行为较侵入，这里用独立监听器
     读取原脚本创建的进度条文本并补充速率信息
     ============================================================ */
  var statusEl = $("#status");
  if (statusEl) {
    var obs = new MutationObserver(function () {
      var txt = statusEl.textContent || "";
      if (!txt || txt.indexOf("上传 ") < 0 || txt.indexOf("MB") < 0) { return; }
      // 从原有文本 "上传 x（i/n）a / b MB" 里解析进度，用时间差估速率
      var m = txt.match(/([\d.]+)\s*\/\s*([\d.]+)\s*MB/);
      if (!m) { return; }
      var done = parseFloat(m[1]) * 1048576, total = parseFloat(m[2]) * 1048576;
      var now = performance.now();
      if (!window._wfsUp) { window._wfsUp = { t: now, d: done }; }
      var el = window._wfsUp, dt = (now - el.t) / 1000;
      if (dt > 0.4) {
        var rate = (done - el.d) / dt;
        if (rate > 0) {
          var eta = (total - done) / rate;
          var sp = $("#wfs-speed");
          if (!sp) { sp = document.createElement("span"); sp.id = "wfs-speed"; sp.className = "wfs-speed"; statusEl.appendChild(sp); }
          sp.textContent = " · " + fmtRate(rate) + " · 剩余 " + eta.toFixed(0) + "s";
        }
        el.t = now; el.d = done;
      }
    });
    obs.observe(statusEl, { childList: true, characterData: true, subtree: true });
  }

  // 首屏绘制
  setTimeout(drawCharts, 60);
})();
