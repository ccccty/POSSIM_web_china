/* ==========================================================================
   POSSIM-Web · wind.js
   职责：台风风场图层（风速配色 + 流向粒子/箭头），纯前端本地计算，后端零改动
   契约：CONTRACT.md §10「风场图层契约 v1.2」，公式与 §5.2 Holland 梯度风同式
   依赖：window.L（Leaflet 1.9.4 CDN）；由 app.js 在 PMap.init 之后调用 init()
   对外：window.PWIND
   ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------- 契约常量（§10.2 / §10.3） ---------------- */
  var RHO_A = 1.15;                 // 空气密度
  var OMEGA_E = 7.292115e-5;        // 地球自转角速度
  var INFLOW_DEG = 22;              // 入流角 α
  var COS_A = Math.cos(INFLOW_DEG * Math.PI / 180);
  var SIN_A = Math.sin(INFLOW_DEG * Math.PI / 180);
  var DEG = Math.PI / 180;
  var MS_PER_KT = 0.514444;

  var GRID_NX = 96, GRID_NY = 72;   // 风速矢量缓存网格（一次/帧）
  var ARROW_NX = 24, ARROW_NY = 18; // 箭头网格
  var FADE = 0.075;                 // 尾迹每帧衰减系数（约 30 帧可见，尾迹 50–90 px）
  var SPEED_K = 6.0;               // 粒子速度比例：px / (m·s⁻¹ · s)，再乘画布高度系数
  var MIN_PARTS = 600;              // 粒子下限（契约 §10.3）
  var MAX_PARTS = 2400;
  var PX_PER_PART = 665;            // 每多少个"屏幕上真实的"像素一个粒子

  /* 风场"强度"三档（图层弹层里的 淡/中/浓）：只调笔迹不透明度，不动配色与粒子数。
     默认 mid = 0.78 —— 比 v1.2 最初的 alpha=1 略淡一档，密集帧（Hour +024 近中心一片红橙）
     不至于把下面的淹没斑块压掉；箭头模式跟随同一系数。 */
  var INTENSITY = { light: 0.55, mid: 0.78, strong: 1 };

  /* 风速配色（m/s，契约 §10.3，与 style.css / index.html 图例一致） */
  var STOPS = [
    { max: 5, c: '#4b6b8a' },
    { max: 10, c: '#3ba0d8' },
    { max: 17, c: '#35e0ff' },
    { max: 25, c: '#27d17a' },
    { max: 33, c: '#ffb020' },
    { max: 45, c: '#ff7a45' },
    { max: 1e9, c: '#ff2d55' }
  ];
  function colorIdx(w) {
    for (var i = 0; i < STOPS.length; i++) { if (w < STOPS[i].max) return i; }
    return STOPS.length - 1;
  }
  /* 强度档位 → 不透明度系数（未知/缺省一律回落"中"） */
  function intensityK() { return INTENSITY[intensity] || INTENSITY.mid; }

  /* ---------------- 模块状态 ---------------- */
  var map = null, pane = null, canvas = null, ctx = null;
  var enabled = false, mode = 'particle';
  var intensity = 'mid';           // 'light' 淡 / 'mid' 中（默认）/ 'strong' 浓
  var raf = 0, running = false, holdMode = false, holdSteps = 240;
  var viewDirty = true, gridDirty = true, lastT = 0;
  var parts = [];
  var curFrame = null;

  /* 当前帧气象参数（来自 /api/simulate 的 frames 与 payload.params） */
  var st = {
    center: null, pc: 1013, vmaxMs: 0, motionKt: 0, headingRad: 0,
    envP: 1013, B: 1.7, rmaxKm: 40
  };
  var _w = { u: 0, v: 0, W: 0 };
  var _s = { u: 0, v: 0 };
  var G = { nx: 0, ny: 0, u: null, v: null, wpx: 0, hpx: 0, scaleK: 1, lineW: 1.4 };
  var stat = {
    frames: 0, sumMs: 0, maxMs: 0, lastMs: 0, gridNodes: 0,
    drawCalls: 0, cleared: 0, warmSteps: 0, builtFrames: 0
  };

  /* 确定性伪随机（xorshift32）：截图可复现，粒子布局每次一致 */
  var rngState = 20180915;
  function rnd() {
    var x = rngState;
    x ^= x << 13; x >>>= 0;
    x ^= x >>> 17;
    x ^= x << 5; x >>>= 0;
    rngState = x;
    return x / 4294967296;
  }
  function nowMs() {
    return (global.performance && global.performance.now) ? global.performance.now() : Date.now();
  }

  /* ---------------- 风场公式（契约 §10.2，与 §5.2 同式） ---------------- */
  function windAt(lat, lon) {
    _w.u = 0; _w.v = 0; _w.W = 0;
    var c = st.center;
    if (!c) return _w;

    /* P 相对台风中心的距离（米）与方位角（北=0，顺时针） */
    var dN = (lat - c[0]) * 111320;                              // 北向分量 m
    var dE = (lon - c[1]) * 111320 * Math.cos((lat + c[0]) * 0.5 * DEG); // 东向分量 m
    var r = Math.sqrt(dN * dN + dE * dE);
    if (!(r > 1)) r = 1;

    var Rm = st.rmaxKm * 1000;
    var X = Math.pow(Rm / r, st.B);
    var dP = (st.envP - st.pc) * 100;                            // Pa
    var f = 2 * OMEGA_E * Math.sin(lat * DEG);
    var cor = 0.5 * f * r;

    var W2 = st.B * dP / RHO_A * X * Math.exp(-X) + cor * cor;
    var W = Math.sqrt(W2 > 0 ? W2 : 0) - cor;
    if (r < Rm * 0.15) W = 0;                                    // 眼区奇点规避
    if (!(W > 0)) W = 0;
    var cap = 1.15 * st.vmaxMs;
    if (cap > 0 && W > cap) W = cap;                             // 风速封顶

    /* 移行非对称（北半球）：km = 1 + 0.35*min(motion,50)/50*cos(θ-heading) */
    var theta = Math.atan2(dE, dN);                              // 弧度
    var km = 1 + 0.35 * Math.min(st.motionKt, 50) / 50 * Math.cos(theta - st.headingRad);
    if (km < 0.6) km = 0.6; else if (km > 1.5) km = 1.5;
    W *= km;

    /* 矢量方向：r_hat=(sinθ,cosθ)  t_hat=(-cosθ,sinθ)
       v = W*(cosα*t_hat − sinα*r_hat)：切向 + 向内（气旋式旋转并向中心收拢） */
    var sn = Math.sin(theta), cs = Math.cos(theta);
    _w.u = W * (COS_A * (-cs) - SIN_A * sn);                     // 东向 m/s
    _w.v = W * (COS_A * (sn) - SIN_A * cs);                      // 北向 m/s
    _w.W = W;
    return _w;
  }

  /* 自检/探针用：给定经纬度（可临时覆盖 heading）返回风矢量 */
  function sampleAtLatLng(lat, lon, override) {
    var bak = { h: st.headingRad, c: st.center, m: st.motionKt };
    if (override) {
      if (override.heading_deg !== undefined) st.headingRad = Number(override.heading_deg) * DEG;
      if (override.center) st.center = [Number(override.center[0]), Number(override.center[1])];
      if (override.motion_kt !== undefined) st.motionKt = Number(override.motion_kt);
    }
    var w = windAt(lat, lon);
    var out = {
      lat: +lat.toFixed(4), lon: +lon.toFixed(4),
      W: +w.W.toFixed(2), u: +w.u.toFixed(2), v: +w.v.toFixed(2),
      toward_deg: +(Math.atan2(w.u, w.v) / DEG).toFixed(1)      // 风"吹向"的方位角（0=向北）
    };
    st.headingRad = bak.h; st.center = bak.c; st.motionKt = bak.m;
    return out;
  }

  /* ---------------- Leaflet 挂载 ---------------- */
  function init(PMap) {
    if (canvas) return true;
    if (!global.L || !PMap || typeof PMap.map !== 'function') return false;
    map = PMap.map();
    if (!map) return false;
    pane = map.createPane('windPane');
    pane.style.zIndex = '380';            // 淹没 350 < 风场 380 < 边界 400 < 村/路径/中心
    pane.style.pointerEvents = 'none';
    canvas = document.createElement('canvas');
    canvas.className = 'wind-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    pane.appendChild(canvas);
    ctx = canvas.getContext('2d');
    syncCanvas();
    map.on('move zoom viewreset resize moveend zoomend', onView);
    return true;
  }

  function stageScale() {
    var vw = global.innerWidth || 1920, vh = global.innerHeight || 1080;
    var s = Math.min(vw / 1920, vh / 1080);
    return (isFinite(s) && s > 0) ? s : 1;
  }

  function targetCount() {
    var s = stageScale();
    var n = Math.round((canvas.width * s) * (canvas.height * s) / PX_PER_PART);
    if (n < MIN_PARTS) n = MIN_PARTS;
    if (n > MAX_PARTS) n = MAX_PARTS;
    return n;
  }

  /* 用 containerPoint 换算重新对齐 canvas（禁止 CSS transform 硬缩放） */
  function syncCanvas() {
    if (!map || !canvas) return;
    var size = map.getSize();
    var changed = false;
    if (canvas.width !== size.x || canvas.height !== size.y) {
      canvas.width = size.x;
      canvas.height = size.y;
      canvas.style.width = size.x + 'px';
      canvas.style.height = size.y + 'px';
      changed = true;
    }
    var origin = map.containerPointToLayerPoint([0, 0]);
    global.L.DomUtil.setPosition(canvas, origin);
    if (changed) { resizeParticles(); viewDirty = true; }
  }

  function newPart() {
    var p = {
      x: rnd() * canvas.width,
      y: rnd() * canvas.height,
      age: rnd() * 1.6,
      life: 1.1 + rnd() * 2.0
    };
    return p;
  }
  function respawn(p) {
    p.x = rnd() * canvas.width;
    p.y = rnd() * canvas.height;
    p.age = 0;
    p.life = 1.1 + rnd() * 2.0;
  }
  function resizeParticles() {
    if (!canvas) return;
    var want = targetCount();
    G.lineW = Math.max(1.0, 1.7 * stageScale());
    while (parts.length > want) parts.pop();
    while (parts.length < want) parts.push(newPart());
    stat.count = parts.length;
  }
  function reseed() {
    parts.length = 0;
    resizeParticles();
  }

  /* ---------------- 风速矢量网格（缓存，一次/帧 或 视图变化时重建） ---------------- */
  function rebuildGrid() {
    if (!map || !canvas || canvas.width < 8 || canvas.height < 8) return;
    var w = canvas.width, h = canvas.height;
    var nx = GRID_NX, ny = GRID_NY;
    if (!G.u || G.nx !== nx || G.ny !== ny) {
      G.nx = nx; G.ny = ny;
      var n = (nx + 1) * (ny + 1);
      G.u = new Float32Array(n);
      G.v = new Float32Array(n);
    }
    G.wpx = w; G.hpx = h;
    G.scaleK = SPEED_K * (h / 800);
    var k = 0;
    for (var j = 0; j <= ny; j++) {
      var py = h * j / ny;
      for (var i = 0; i <= nx; i++) {
        var px = w * i / nx;
        var ll = map.containerPointToLatLng([px, py]);
        windAt(ll.lat, ll.lng);
        G.u[k] = _w.u;
        G.v[k] = _w.v;
        k++;
      }
    }
    stat.gridNodes = k;
    stat.builtFrames++;
    gridDirty = false;
  }

  /* 双线性插值取样（容器像素坐标 → 风矢量 m/s） */
  function sample(px, py, out) {
    var nx = G.nx, ny = G.ny;
    var gx = px / G.wpx * nx, gy = py / G.hpx * ny;
    if (gx < 0) gx = 0; else if (gx > nx - 1e-6) gx = nx - 1e-6;
    if (gy < 0) gy = 0; else if (gy > ny - 1e-6) gy = ny - 1e-6;
    var i = gx | 0, j = gy | 0;
    var fx = gx - i, fy = gy - j;
    var row = nx + 1;
    var k0 = j * row + i, k1 = k0 + 1, k2 = k0 + row, k3 = k2 + 1;
    var a = G.u, b = G.v;
    out.u = (a[k0] * (1 - fx) + a[k1] * fx) * (1 - fy) + (a[k2] * (1 - fx) + a[k3] * fx) * fy;
    out.v = (b[k0] * (1 - fx) + b[k1] * fx) * (1 - fy) + (b[k2] * (1 - fx) + b[k3] * fx) * fy;
    return out;
  }

  /* ---------------- 渲染：粒子 ---------------- */
  function drawParticles(dt) {
    if (!ctx || !canvas) return;
    var w = canvas.width, h = canvas.height;
    var ka = intensityK();                 // 强度系数（淡 0.55 / 中 0.78 / 浓 1.0）
    /* 尾迹透明衰减：衰减率与强度无关，保持尾迹长度在各档一致 */
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = 'rgba(0,0,0,' + FADE + ')';
    ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'source-over';

    var segs = [[], [], [], [], [], [], []];
    var k = G.scaleK;
    var n = parts.length;
    for (var i = 0; i < n; i++) {
      var p = parts[i];
      sample(p.x, p.y, _s);
      var wx = _s.u, wy = _s.v;
      var ww = Math.sqrt(wx * wx + wy * wy);
      if (!(ww > 0.6) || !isFinite(ww)) { respawn(p); continue; }
      var x0 = p.x, y0 = p.y;
      var x1 = x0 + wx * k * dt;
      var y1 = y0 - wy * k * dt;         // 屏幕 y 向南为正，风矢量 v 向北为正
      p.x = x1; p.y = y1;
      p.age += dt;
      segs[colorIdx(ww)].push(x0, y0, x1, y1);
      if (x1 < -6 || x1 > w + 6 || y1 < -6 || y1 > h + 6 || p.age > p.life) respawn(p);
    }
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var c = 0; c < segs.length; c++) {
      var arr = segs[c];
      if (!arr.length) continue;
      ctx.strokeStyle = STOPS[c].c;
      /* 第一遍：宽而淡的辉光，让细尾迹在亮底图（卫星影像/深蓝底衬）上也清楚 */
      ctx.globalAlpha = 0.09 * ka;
      ctx.lineWidth = G.lineW * 2.2;
      ctx.beginPath();
      for (var q = 0; q < arr.length; q += 4) {
        ctx.moveTo(arr[q], arr[q + 1]);
        ctx.lineTo(arr[q + 2], arr[q + 3]);
      }
      ctx.stroke();
      /* 第二遍：实线本体 */
      ctx.globalAlpha = ka;
      ctx.lineWidth = G.lineW;
      ctx.beginPath();
      for (var q2 = 0; q2 < arr.length; q2 += 4) {
        ctx.moveTo(arr[q2], arr[q2 + 1]);
        ctx.lineTo(arr[q2 + 2], arr[q2 + 3]);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    stat.drawCalls++;
  }

  /* ---------------- 渲染：箭头网格 ---------------- */
  function drawArrows() {
    if (!ctx || !canvas) return;
    var w = canvas.width, h = canvas.height;
    var ka = intensityK();
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, w, h);
    var segs = [[], [], [], [], [], [], []];
    var maxLen = Math.min(w / ARROW_NX, h / ARROW_NY) * 0.86;
    var ca = Math.cos(0.42), sa = Math.sin(0.42);
    for (var j = 0; j < ARROW_NY; j++) {
      for (var i = 0; i < ARROW_NX; i++) {
        var px = w * (i + 0.5) / ARROW_NX;
        var py = h * (j + 0.5) / ARROW_NY;
        sample(px, py, _s);
        var u = _s.u, v = _s.v;
        var W = Math.sqrt(u * u + v * v);
        if (!(W > 0.3)) continue;
        var dx = u / W, dy = -v / W;                 // 屏幕方向
        var len = Math.min(maxLen, 6 + Math.min(W, 60) * 0.5);   // 长度 ∝ W
        var x1 = px + dx * len * 0.5, y1 = py + dy * len * 0.5;
        var x0 = px - dx * len * 0.5, y0 = py - dy * len * 0.5;
        var arr = segs[colorIdx(W)];
        arr.push(x0, y0, x1, y1);
        var hl = Math.min(7.5, len * 0.42);
        var bx = -dx, by = -dy;
        arr.push(x1, y1, x1 + (bx * ca - by * sa) * hl, y1 + (bx * sa + by * ca) * hl);
        arr.push(x1, y1, x1 + (bx * ca + by * sa) * hl, y1 + (-bx * sa + by * ca) * hl);
      }
    }
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.globalAlpha = ka;                 // 箭头跟随同一强度系数
    ctx.lineWidth = Math.max(1, 1.5 * stageScale());
    for (var c = 0; c < segs.length; c++) {
      var a = segs[c];
      if (!a.length) continue;
      ctx.strokeStyle = STOPS[c].c;
      ctx.beginPath();
      for (var q = 0; q < a.length; q += 4) {
        ctx.moveTo(a[q], a[q + 1]);
        ctx.lineTo(a[q + 2], a[q + 3]);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    stat.drawCalls++;
  }

  /* ---------------- rAF 循环 ---------------- */
  function loop(t) {
    if (!running) return;
    var dt = (t - lastT) / 1000;
    if (!(dt > 0) || dt > 0.12) dt = 1 / 60;
    lastT = t;
    var t0 = nowMs();
    if (viewDirty || gridDirty) { syncCanvas(); rebuildGrid(); viewDirty = false; }
    drawParticles(dt > 0.06 ? 0.06 : dt);
    var el = nowMs() - t0;
    stat.frames++;
    stat.lastMs = el;
    stat.sumMs += el;
    if (el > stat.maxMs) stat.maxMs = el;
    raf = global.requestAnimationFrame(loop);
  }

  function start() {
    if (!enabled || running || !canvas) return;
    if (mode === 'arrow') { drawPush(); return; }
    running = true;
    lastT = nowMs();
    raf = global.requestAnimationFrame(loop);
  }
  function stop() {
    running = false;
    if (raf) { global.cancelAnimationFrame(raf); raf = 0; }
  }
  function clearCanvas() {
    if (!ctx || !canvas) return;
    ctx.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    stat.cleared++;
  }

  function drawPush() {
    syncCanvas();
    rebuildGrid();
    viewDirty = false;
    if (mode === 'arrow') drawArrows();
    else drawParticles(1 / 60);
  }

  function onView() {
    viewDirty = true;
    syncCanvas();
    if (!enabled) return;
    if (mode === 'arrow') { rebuildGrid(); drawArrows(); viewDirty = false; return; }
    if (holdMode) { rebuildGrid(); viewDirty = false; warm(holdSteps, true); return; }
    if (!running) start();
  }

  /* ---------------- 开关 / 模式 ---------------- */
  function setEnabled(on) {
    enabled = !!on;
    if (!canvas) return enabled;
    if (!enabled) {
      stop();
      holdMode = false;
      clearCanvas();
      return enabled;
    }
    holdMode = false;
    syncCanvas();
    rebuildGrid();
    viewDirty = false;
    if (mode === 'arrow') drawArrows();
    else start();
    return enabled;
  }

  function setMode(m) {
    mode = (m === 'arrow') ? 'arrow' : 'particle';
    if (!canvas || !enabled) return mode;
    stop();
    holdMode = false;
    syncCanvas();
    rebuildGrid();
    viewDirty = false;
    if (mode === 'arrow') drawArrows();
    else { clearCanvas(); start(); }
    return mode;
  }

  /* 强度档位：淡 / 中 / 浓（只换笔迹不透明度，配色/粒子数/速度不变）
     - 定帧（截图）模式：清屏后按同一步数重新预热，保证截图与实时观感一致
     - 箭头模式：立刻重绘
     - 实时粒子：让下一次 rAF 用新系数续画，旧尾迹自然衰减，不闪 */
  function setIntensity(v) {
    intensity = (v === 'light' || v === 'strong') ? v : 'mid';
    if (!canvas || !enabled) return intensity;
    if (mode === 'arrow') { syncCanvas(); rebuildGrid(); viewDirty = false; drawArrows(); return intensity; }
    if (holdMode) { clearCanvas(); warm(holdSteps, true); return intensity; }
    return intensity;
  }

  /* ---------------- 自检：预热 + 定帧（截图用） ---------------- */
  function warm(steps, keepTrails) {
    if (!canvas || !enabled) return stat;
    if (!keepTrails) clearCanvas();
    var n = steps || holdSteps;
    for (var i = 0; i < n; i++) drawParticles(1 / 60);
    stat.warmSteps += n;
    return stat;
  }

  function hold(steps) {
    if (!canvas) return stat;
    holdSteps = steps || holdSteps || 240;
    stop();
    holdMode = true;
    enabled = true;
    syncCanvas();
    rebuildGrid();
    viewDirty = false;
    if (mode === 'arrow') { drawArrows(); return stat; }
    reseed();
    clearCanvas();
    warm(holdSteps, true);
    countAlpha();
    return stat;
  }

  /* 同步重绘一帧（自检计时用） */
  function renderNow() {
    if (!canvas || !enabled) return 0;
    var t0 = nowMs();
    if (viewDirty || gridDirty) { syncCanvas(); rebuildGrid(); viewDirty = false; }
    if (mode === 'arrow') drawArrows(); else drawParticles(1 / 60);
    flush();
    return nowMs() - t0;
  }

  function countAlpha() {
    var sum = 0;
    if (ctx && canvas && canvas.width) {
      try {
        var d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        for (var i = 3; i < d.length; i += 4 * 37) sum += d[i];
      } catch (e) { sum = -1; }
    }
    return sum;
  }

  /* ---------------- 逐帧入口 ---------------- */
  function setFrame(frame, params) {
    var f = frame || {};
    curFrame = f;
    st.center = (f.center && f.center.length >= 2) ? [Number(f.center[0]), Number(f.center[1])] : null;
    st.pc = Number(f.pc_mb) || 1013;
    st.vmaxMs = (Number(f.vmax_kt) || 0) * MS_PER_KT;
    st.motionKt = Number(f.motion_kt) || 0;
    st.headingRad = (Number(f.heading_deg) || 0) * DEG;
    if (params) {
      if (params.env_pressure !== undefined) st.envP = Number(params.env_pressure) || 1013;
      if (params.holland_b !== undefined) st.B = Number(params.holland_b) || 1.7;
      if (params.rmax_km !== undefined) st.rmaxKm = Number(params.rmax_km) || 40;
    }
    gridDirty = true;
    viewDirty = true;
    if (!enabled) return;
    if (mode === 'arrow') { syncCanvas(); rebuildGrid(); viewDirty = false; drawArrows(); }
    else if (holdMode) { syncCanvas(); rebuildGrid(); viewDirty = false; warm(holdSteps, true); }
  }

  /* ---------------- 探针 / 基准 ---------------- */
  function probe() {
    return {
      enabled: enabled, mode: mode, intensity: intensity, intensityK: +intensityK().toFixed(2),
      running: running, hold: holdMode,
      particles: parts.length,
      grid: [G.nx, G.ny], gridNodes: stat.gridNodes,
      canvas: canvas ? [canvas.width, canvas.height] : null,
      frames: stat.frames,
      avgDrawMs: stat.frames ? +(stat.sumMs / stat.frames).toFixed(2) : 0,
      maxDrawMs: +stat.maxMs.toFixed(2),
      lastDrawMs: +stat.lastMs.toFixed(2),
      warmSteps: stat.warmSteps,
      gridBuilds: stat.builtFrames,
      drawCalls: stat.drawCalls,
      alphaSum: countAlpha(),
      frame: curFrame ? (curFrame.label || '') : '',
      center: st.center, heading: +(st.headingRad / DEG).toFixed(1),
      speedK: +G.scaleK.toFixed(3),
      /* 对齐探针：canvas 必须正好覆盖地图容器（move/zoom 后仍为 0） */
      align: (function () {
        if (!canvas || !map) return null;
        var cr = canvas.getBoundingClientRect(), mr = map.getContainer().getBoundingClientRect();
        return {
          dLeft: Math.round(cr.left - mr.left), dTop: Math.round(cr.top - mr.top),
          dWidth: Math.round(cr.width - mr.width), dHeight: Math.round(cr.height - mr.height)
        };
      })(),
      stops: STOPS.map(function (s) { return s.c; })
    };
  }

  /* 连续 n 帧的 rAF 间隔（播放流畅度）。
     headless 下 rAF 可能被节流甚至不触发，所以带 4 s 超时兜底并标记 rafThrottled。 */
  function bench(n) {
    n = n || 30;
    return new Promise(function (resolve) {
      if (!enabled) { resolve(null); return; }
      var times = [], prev = 0, i = 0, done = false;
      function finish(v) { if (done) return; done = true; resolve(v); }
      function pack(throttled) {
        var sum = 0, mx = 0;
        for (var q = 0; q < times.length; q++) { sum += times[q]; if (times[q] > mx) mx = times[q]; }
        return {
          frames: times.length,
          rafThrottled: !!throttled,
          avgIntervalMs: times.length ? +(sum / times.length).toFixed(2) : null,
          maxIntervalMs: times.length ? +mx.toFixed(2) : null,
          avgDrawMs: stat.frames ? +(stat.sumMs / stat.frames).toFixed(2) : 0,
          maxDrawMs: +stat.maxMs.toFixed(2)
        };
      }
      function step(t) {
        if (i > 0) times.push(t - prev);
        prev = t; i++;
        if (i <= n) global.requestAnimationFrame(step);
        else finish(pack(false));
      }
      global.requestAnimationFrame(step);
      global.setTimeout(function () { finish(pack(true)); }, 4000);
    });
  }

  /* 强制把 canvas 的绘制队列刷到底（否则 Skia 可能延迟栅格化，计时失真） */
  function flush() {
    try { ctx.getImageData(0, 0, 1, 1); } catch (e) { /* ignore */ }
  }

  /* 连续 n 帧的同步绘制耗时（不依赖 rAF；整块计时再均摊，规避 now() 精度问题） */
  function benchSync(n) {
    n = n || 30;
    if (!enabled) return null;
    syncCanvas();
    rebuildGrid();
    viewDirty = false;
    var times = [], sum = 0, mx = 0;
    var blockT0 = nowMs();
    for (var i = 0; i < n; i++) {
      var t0 = nowMs();
      if (mode === 'arrow') drawArrows(); else drawParticles(1 / 60);
      flush();
      var el = nowMs() - t0;
      times.push(+el.toFixed(2));
      sum += el;
      if (el > mx) mx = el;
    }
    var blockMs = nowMs() - blockT0;
    return {
      frames: n,
      avgMs: +(blockMs / n).toFixed(2),      // 含 raster 的真实均摊成本（最可信）
      maxMs: +mx.toFixed(2),
      blockMs: +blockMs.toFixed(2),
      particles: parts.length, grid: [G.nx, G.ny]
    };
  }

  /* 37 帧逐帧切换的真实开销（含网格重建 + 一帧绘制） */
  function sweep(goToFrame, n) {
    var out = { frames: 0, avgMs: 0, maxMs: 0, maxAt: -1, samples: [] };
    if (typeof goToFrame !== 'function') return out;
    var total = 0;
    for (var i = 0; i < n; i++) {
      var t0 = nowMs();
      goToFrame(i);
      renderNow();
      flush();
      var el = nowMs() - t0;
      total += el;
      if (el > out.maxMs) { out.maxMs = +el.toFixed(2); out.maxAt = i; }
      out.samples.push(+el.toFixed(2));
    }
    out.frames = n;
    out.avgMs = +(total / n).toFixed(2);
    return out;
  }

  global.PWIND = {
    init: init,
    setFrame: setFrame,
    setEnabled: setEnabled,
    setMode: setMode,
    setIntensity: setIntensity,
    resize: syncCanvas,
    hold: hold,
    warm: warm,
    renderNow: renderNow,
    probe: probe,
    bench: bench,
    benchSync: benchSync,
    sweep: sweep,
    sampleAt: sampleAtLatLng,
    available: function () { return !!canvas; },
    mode: function () { return mode; },
    enabled: function () { return enabled; },
    intensity: function () { return intensity; },
    stops: STOPS
  };
})(window);
