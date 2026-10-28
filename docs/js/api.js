/* ==========================================================================
   POSSIM-Web · api.js
   职责：所有与后端（server.py）的通信 + 数字格式化 + 演示兜底数据
   - 一律相对路径 /api/...
   - fetch 失败抛中文 Error
   - 开发态（localhost / ?mock=1）且后端不可达时用内置样例渲染，页面打「演示兜底数据」角标
   契约：CONTRACT.md §6（请求/响应体）、§5（物理量纲）
   ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------- 通用格式化 ---------------- */
  function pad(n, w) {
    var s = String(n);
    while (s.length < w) s = '0' + s;
    return s;
  }
  // 千分位（不依赖 locale，避免不同浏览器输出不一致）
  function thousands(v) {
    var n = Math.round(Number(v) || 0);
    var neg = n < 0;
    var s = String(Math.abs(n));
    var out = '';
    for (var i = 0; i < s.length; i++) {
      if (i > 0 && (s.length - i) % 3 === 0) out += ',';
      out += s.charAt(i);
    }
    return (neg ? '-' : '') + out;
  }
  var UTIL = {
    // 深度：两位小数 + m（契约要求统一格式）
    depth: function (v) { return (Number(v) || 0).toFixed(2); },
    // 面积：整数 + ha
    area: function (v) { return thousands(v); },
    // 人口：带千分位
    pop: function (v) { return thousands(v); },
    num: function (v, d) { return (Number(v) || 0).toFixed(d === undefined ? 2 : d); },
    pad3: function (n) { return pad(Math.round(n), 3); },
    // 16 方位名（移向展示）
    bearing: function (deg) {
      var names = ['北', '北北东', '东北', '东北东', '东', '东南东', '东南', '南南东',
        '南', '南南西', '西南', '西南西', '西', '西北西', '西北', '北北西'];
      var d = ((Number(deg) || 0) % 360 + 360) % 360;
      return names[Math.round(d / 22.5) % 16];
    },
    // 强度等级（按最大风速 kt）
    intensity: function (kt) {
      var v = Number(kt) || 0;
      if (v >= 100) return '超强台风';
      if (v >= 81) return '强台风';
      if (v >= 64) return '台风';
      if (v >= 48) return '强热带风暴';
      if (v >= 34) return '热带风暴';
      return '热带低压';
    },
    // 预警级别：按当前帧最大增水 + 淹没占比（演示口径）
    alert: function (surge, pct) {
      var s = Number(surge) || 0, p = Number(pct) || 0;
      if (s >= 2.0 || p >= 8) return { key: 'red', name: '红色', color: '#ff5c5c' };
      if (s >= 1.2 || p >= 4) return { key: 'orange', name: '橙色', color: '#ffb020' };
      if (s >= 0.6 || p >= 1.5) return { key: 'yellow', name: '黄色', color: '#ffd166' };
      if (s > 0.05) return { key: 'blue', name: '蓝色', color: '#2f81f7' };
      return { key: 'none', name: '无', color: '#7ba7c9' };
    },
    // 村水深估算（后端 frames 未逐村给值，按 总水位-高程 做近岸衰减估算）
    villageDepth: function (v, frame) {
      if (!frame) return 0;
      var maxD = Number(frame.max_depth) || 0;
      var twl = Number(frame.max_twl) || 0;
      if (maxD <= 0.001 || twl <= 0) return 0;
      var elev = Number(v.elev_m) || 0;
      var dist = Number(v.dist_coast_km) || 0;
      var raw = (twl - elev) / (1 + dist / 5);
      if (!(raw > 0)) return 0;
      var d = Math.min(maxD * 1.05, raw);
      return d > 0 ? d : 0;
    },
    levelOf: function (d) {
      var x = Number(d) || 0;
      if (x >= 0.5) return { cls: 'high', name: '高' };
      if (x >= 0.2) return { cls: 'mid', name: '中' };
      if (x > 0.001) return { cls: 'low', name: '低' };
      return { cls: 'none', name: '无' };
    },
    esc: function (s) {
      return String(s === undefined || s === null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
    // 水深分级配色（与 style.css 图例一致）
    DEPTH_STOPS: [
      { max: 0.33, color: '#7ee8ff' },
      { max: 0.5, color: '#35e0ff' },
      { max: 1.0, color: '#2f81f7' },
      { max: 2.0, color: '#ffd166' },
      { max: 3.0, color: '#ffb020' },
      { max: 5.0, color: '#ff5c5c' },
      { max: 999, color: '#c026d3' }
    ],
    depthColor: function (d) {
      var x = Number(d) || 0;
      for (var i = 0; i < UTIL.DEPTH_STOPS.length; i++) {
        if (x <= UTIL.DEPTH_STOPS[i].max) return UTIL.DEPTH_STOPS[i].color;
      }
      return UTIL.DEPTH_STOPS[UTIL.DEPTH_STOPS.length - 1].color;
    }
  };

  /* ---------------- 演示兜底数据（后端未就绪时） ---------------- */
  var MOCK_VILLAGES = [
    { name: '南沙区龙穴街', lat: 22.70, lon: 113.62, pop: 12000, households: 4300, dist_coast_km: 1.2, elev_m: 2.1, strategy: '转移', level: '高' },
    { name: '珠海香洲唐家湾', lat: 22.37, lon: 113.60, pop: 38000, households: 12800, dist_coast_km: 2.4, elev_m: 3.4, strategy: '加固', level: '中' },
    { name: '中山南朗崖口村', lat: 22.44, lon: 113.55, pop: 8600, households: 2900, dist_coast_km: 1.8, elev_m: 1.6, strategy: '转移', level: '高' },
    { name: '东莞沙田镇', lat: 22.72, lon: 113.63, pop: 22000, households: 7400, dist_coast_km: 3.1, elev_m: 2.8, strategy: '加固', level: '中' },
    { name: '深圳宝安沙井街', lat: 22.73, lon: 113.75, pop: 95000, households: 31000, dist_coast_km: 4.6, elev_m: 4.2, strategy: '巡查', level: '低' },
    { name: '深圳宝安福永街', lat: 22.68, lon: 113.80, pop: 62000, households: 21000, dist_coast_km: 3.9, elev_m: 3.9, strategy: '巡查', level: '低' },
    { name: '珠海斗门白蕉镇', lat: 22.30, lon: 113.35, pop: 41000, households: 13000, dist_coast_km: 5.2, elev_m: 2.6, strategy: '加固', level: '中' },
    { name: '澳门路环', lat: 22.12, lon: 113.56, pop: 26000, households: 8600, dist_coast_km: 0.9, elev_m: 3.1, strategy: '转移', level: '高' },
    { name: '珠海金湾三灶镇', lat: 22.06, lon: 113.36, pop: 33000, households: 9800, dist_coast_km: 1.5, elev_m: 1.9, strategy: '转移', level: '高' },
    { name: '香港北区上水', lat: 22.50, lon: 114.13, pop: 88000, households: 27000, dist_coast_km: 6.8, elev_m: 8.4, strategy: '巡查', level: '低' },
    { name: '南沙万顷沙', lat: 22.61, lon: 113.50, pop: 18000, households: 5600, dist_coast_km: 2.0, elev_m: 1.4, strategy: '转移', level: '高' },
    { name: '中山坦洲镇', lat: 22.28, lon: 113.48, pop: 52000, households: 16000, dist_coast_km: 4.0, elev_m: 3.0, strategy: '加固', level: '中' },
    { name: '东莞长安镇', lat: 22.79, lon: 113.72, pop: 76000, households: 24000, dist_coast_km: 5.5, elev_m: 5.1, strategy: '巡查', level: '低' },
    { name: '珠海香洲湾仔', lat: 22.20, lon: 113.53, pop: 15000, households: 5200, dist_coast_km: 1.1, elev_m: 2.3, strategy: '转移', level: '高' },
    { name: '深圳南山蛇口', lat: 22.48, lon: 113.92, pop: 68000, households: 22000, dist_coast_km: 1.6, elev_m: 4.8, strategy: '加固', level: '中' }
  ];

  var MOCK_TYPHOONS = {
    mangkhut: {
      id: 'mangkhut', name: '山竹', name_en: 'Mangkhut', year: 2018,
      track: makeTrack('2018-09-15T00:00Z', [
        [19.50, 118.20, 915, 115, 18, 300, [180, 160, 140, 170]],
        [20.00, 117.35, 913, 115, 18, 302, [180, 160, 140, 170]],
        [20.45, 116.55, 910, 113, 18, 305, [180, 160, 140, 168]],
        [20.90, 115.75, 905, 115, 19, 308, [178, 158, 138, 166]],
        [21.30, 114.95, 900, 118, 20, 310, [176, 156, 136, 164]],
        [21.70, 114.15, 905, 112, 19, 312, [172, 152, 132, 160]],
        [22.00, 113.40, 915, 100, 17, 315, [160, 140, 120, 148]],
        [22.25, 112.70, 935, 80, 16, 320, [140, 120, 100, 128]],
        [22.55, 112.10, 970, 50, 14, 330, [110, 95, 80, 100]]
      ]),
      landfall: { t: '2018-09-16T00:00Z', lat: 22.00, lon: 113.40 }
    },
    hato: {
      id: 'hato', name: '天鸽', name_en: 'Hato', year: 2017,
      track: makeTrack('2017-08-22T00:00Z', [
        [20.30, 116.80, 985, 55, 20, 295, [140, 120, 110, 130]],
        [20.80, 115.90, 975, 65, 21, 297, [150, 130, 115, 140]],
        [21.30, 115.00, 955, 85, 22, 300, [160, 140, 120, 150]],
        [21.80, 114.10, 940, 95, 23, 302, [170, 150, 130, 158]],
        [22.10, 113.40, 945, 100, 22, 305, [170, 150, 128, 156]],
        [22.40, 112.60, 975, 70, 20, 310, [140, 125, 110, 130]],
        [22.80, 111.80, 995, 45, 18, 315, [110, 100, 90, 100]]
      ]),
      landfall: { t: '2017-08-23T04:00Z', lat: 22.10, lon: 113.40 }
    },
    hagibis: {
      id: 'hagibis', name: '海贝思', name_en: 'Hagibis', year: 2019,
      track: makeTrack('2019-10-10T00:00Z', [
        [18.00, 126.50, 915, 115, 22, 300, [190, 170, 150, 180]],
        [18.60, 125.20, 910, 118, 23, 302, [195, 175, 155, 185]],
        [19.20, 123.90, 905, 120, 23, 305, [200, 180, 160, 190]],
        [19.80, 122.60, 910, 115, 22, 308, [195, 175, 155, 185]],
        [20.40, 121.30, 915, 112, 21, 310, [190, 170, 150, 180]],
        [21.00, 120.00, 920, 108, 20, 312, [185, 165, 145, 175]],
        [21.60, 118.80, 930, 100, 19, 315, [175, 155, 135, 165]]
      ]),
      landfall: { t: '2019-10-12T00:00Z', lat: 22.60, lon: 114.50 }
    }
  };

  function makeTrack(startIso, rows) {
    var t0 = Date.parse(startIso);
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var hh = i * 3; // 3 小时间隔（契约允许 3–6 小时）
      out.push({
        t: isoHours(t0, hh),
        lat: r[0], lon: r[1], pc_mb: r[2], vmax_kt: r[3],
        motion_kt: r[4], heading_deg: r[5],
        r34: r[6],
        r50: [Math.round(r[6][0] * 0.6), Math.round(r[6][1] * 0.6), Math.round(r[6][2] * 0.6), Math.round(r[6][3] * 0.6)],
        r64: [Math.round(r[6][0] * 0.33), Math.round(r[6][1] * 0.33), Math.round(r[6][2] * 0.33), Math.round(r[6][3] * 0.33)]
      });
    }
    return out;
  }
  function isoHours(t0, hours) {
    var d = new Date(t0 + hours * 3600 * 1000);
    return d.toISOString().replace('.000Z', 'Z');
  }

  function mockMeta() {
    return {
      site: {
        name: '珠江口风暴潮淹没推演平台', region: '珠江口-伶仃洋', crs: 'EPSG:4326',
        bbox: [113.20, 22.20, 114.20, 23.00], nx: 1188, ny: 864, pixel_deg: 0.000925926,
        pixel_m: 102.7, area_km2: 12345.6, note: '数据：SRTM 90m · 台风参数为历史/示意情景'
      },
      dem: { file: 'dem.npy', nodata: -9999, min: -2.0, max: 512.3, land_pixels: 452310 },
      map: {
        center: [22.60, 113.70], zoom: 11,
        bbox_leaflet: [[22.20, 113.20], [23.00, 114.20]],
        preview_png: '/api/dem/preview.png'
      },
      defaults: {
        env_pressure: 1013.0, holland_b: 1.7, wave_runup: 1.15, coarse_amp: 3.8,
        tide: { M2: [1.0, 145.0], S2: [0.4, 45.0], K1: [0.3, 75.0], O1: [0.2, 120.0] },
        hours: 36, step_hours: 1
      },
      typhoons: [
        { id: 'mangkhut', name: '山竹', name_en: 'Mangkhut', year: 2018, vmax_kt: 115, pc_mb: 905 },
        { id: 'hato', name: '天鸽', name_en: 'Hato', year: 2017, vmax_kt: 100, pc_mb: 945 },
        { id: 'hagibis', name: '海贝思', name_en: 'Hagibis', year: 2019, vmax_kt: 120, pc_mb: 905 }
      ],
      villages: MOCK_VILLAGES,
      _mock: true
    };
  }

  /* 调和分潮：eta = Σ A·cos(ω·h + φ)，ω 用 deg/hr（契约 §4） */
  var OMEGA = { M2: 28.9841042, S2: 30.0, K1: 15.0410686, O1: 13.9430356 };
  function tideAt(hour, tideCfg) {
    var sum = 0;
    for (var k in OMEGA) {
      if (!Object.prototype.hasOwnProperty.call(OMEGA, k)) continue;
      var c = (tideCfg && tideCfg[k]) || [0, 0];
      sum += (Number(c[0]) || 0) * Math.cos((OMEGA[k] * hour + (Number(c[1]) || 0)) * Math.PI / 180);
    }
    return sum;
  }

  function haversine(lat1, lon1, lat2, lon2) {
    var R = 6371, rad = Math.PI / 180;
    var dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  /* 路径点插值到逐小时（mock 用线性插值，engine 同职责） */
  function interpTrack(track, hourFrom) {
    var pts = track.map(function (p) { return { t: Date.parse(p.t), p: p }; });
    var t0 = pts[0].t;
    var target = t0 + hourFrom * 3600 * 1000;
    if (target <= pts[0].t) return pts[0].p;
    var last = pts[pts.length - 1];
    if (target >= last.t) return last.p;
    for (var i = 0; i < pts.length - 1; i++) {
      var a = pts[i], b = pts[i + 1];
      if (target >= a.t && target <= b.t) {
        var f = (target - a.t) / (b.t - a.t || 1);
        function mix(k) { return a.p[k] + (b.p[k] - a.p[k]) * f; }
        return {
          t: new Date(target).toISOString().replace('.000Z', 'Z'),
          lat: mix('lat'), lon: mix('lon'), pc_mb: mix('pc_mb'), vmax_kt: mix('vmax_kt'),
          motion_kt: mix('motion_kt'), heading_deg: mix('heading_deg'),
          r34: a.p.r34, r50: a.p.r50, r64: a.p.r64
        };
      }
    }
    return last.p;
  }

  /* 演示用淹没 PNG（canvas 生成，非外部图片；按帧缓存） */
  var mockOverlayCache = {};
  function mockOverlay(frame) {
    var key = 'f' + frame.i;
    if (mockOverlayCache[key]) return mockOverlayCache[key];
    if (typeof document === 'undefined') return '';
    var W = 594, H = 432;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var g = cv.getContext('2d');
    var depth = Number(frame.max_depth) || 0;
    if (depth > 0.01) {
      // 经纬度 -> 画布像素（bbox 113.20–114.20 / 22.20–23.00，行序自北向南）
      function px(lon) { return (lon - 113.20) / 1.0 * W; }
      function py(lat) { return (23.00 - lat) / 0.8 * H; }
      // 演示淹没形态：沿珠江口岸线叠三层带状色带（浅->深），比圆斑更像淹没图
      var westShore = [[113.27, 22.64], [113.33, 22.68], [113.40, 22.62], [113.47, 22.53],
                       [113.44, 22.44], [113.38, 22.34], [113.31, 22.25]];
      var eastShore = [[113.89, 22.62], [113.84, 22.53], [113.81, 22.44], [113.76, 22.34],
                       [113.72, 22.25], [113.78, 22.18]];
      var headBand = [[113.33, 22.73], [113.44, 22.71], [113.55, 22.73], [113.66, 22.71],
                      [113.79, 22.73], [113.87, 22.70]];
      var lines = [westShore, eastShore, headBand];

      // 分级取色：浅层 / 中层 / 当前帧最深色
      var stops = UTIL.DEPTH_STOPS;
      var di = 0;
      while (di < stops.length - 1 && depth > stops[di].max) di++;
      var cShallow = stops[0].color;
      var cMid = stops[Math.min(1, di)].color;
      var cDeep = stops[di].color;

      var scale = Math.min(1.7, Math.max(0.4, Math.sqrt((Number(frame.flooded_ha) || 0) / 1800)));
      var base = 15 * scale;                 // 最外层带宽（画布像素）

      function strokeBand(pts, width, color, alpha) {
        g.globalAlpha = alpha;
        g.strokeStyle = color;
        g.lineWidth = width;
        g.lineCap = 'round';
        g.lineJoin = 'round';
        g.beginPath();
        for (var k = 0; k < pts.length; k++) {
          var X = px(pts[k][0]), Y = py(pts[k][1]);
          if (k === 0) g.moveTo(X, Y); else g.lineTo(X, Y);
        }
        g.stroke();
      }

      g.globalCompositeOperation = 'source-over';
      g.save();
      g.shadowColor = cMid;
      g.shadowBlur = 10 * scale;
      for (var li = 0; li < lines.length; li++) {
        strokeBand(lines[li], base, cShallow, 0.55);
      }
      g.restore();
      for (var lj = 0; lj < lines.length; lj++) {
        strokeBand(lines[lj], base * 0.62, cMid, 0.85);
        strokeBand(lines[lj], base * 0.30, cDeep, 0.95);
      }
      // 河口顶部几个深水斑块（避免色带过于均匀）
      var blobs = [[113.62, 22.66, 0.034], [113.46, 22.57, 0.038], [113.74, 22.44, 0.034]];
      g.globalAlpha = 1;
      for (var bi = 0; bi < blobs.length; bi++) {
        var cx = px(blobs[bi][0]), cy = py(blobs[bi][1]);
        var r = blobs[bi][2] * scale * W;
        var grad = g.createRadialGradient(cx, cy, r * 0.1, cx, cy, r);
        grad.addColorStop(0, cDeep);
        grad.addColorStop(0.6, cMid);
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        g.fillStyle = grad;
        g.beginPath();
        g.ellipse(cx, cy, r, r * 0.7, 0, 0, Math.PI * 2);
        g.fill();
      }
      g.globalAlpha = 1;
    }
    var url = cv.toDataURL('image/png');
    mockOverlayCache[key] = url;
    return url;
  }

  /* 演示用 DEM 底图（后端 /api/dem/preview.png 缺失时 canvas 兜底，非外部图片） */
  function mockDemPreview() {
    if (typeof document === 'undefined') return '';
    var W = 594, H = 432;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var g = cv.getContext('2d');

    // 1) 水面：由北向南的深蓝渐变
    var wg = g.createLinearGradient(0, 0, 0, H);
    wg.addColorStop(0, '#11465f');
    wg.addColorStop(0.55, '#0a2d44');
    wg.addColorStop(1, '#071e2f');
    g.fillStyle = wg;
    g.fillRect(0, 0, W, H);

    // 2) 陆地：西岸 / 东岸 / 北岸（珠江口喇叭口形态）
    var landGrad = g.createLinearGradient(0, 0, W, H);
    landGrad.addColorStop(0, '#2f7996');
    landGrad.addColorStop(0.45, '#23617c');
    landGrad.addColorStop(1, '#1a4a63');
    g.fillStyle = landGrad;

    function landPath() {
      g.beginPath();
      g.moveTo(0, 0); g.lineTo(W * 0.34, 0);
      g.bezierCurveTo(W * 0.27, H * 0.30, W * 0.37, H * 0.52, W * 0.28, H);
      g.lineTo(0, H); g.closePath();
      g.moveTo(W, 0); g.lineTo(W * 0.62, 0);
      g.bezierCurveTo(W * 0.70, H * 0.26, W * 0.58, H * 0.54, W * 0.70, H);
      g.lineTo(W, H); g.closePath();
      g.moveTo(0, 0); g.lineTo(W, 0); g.lineTo(W, H * 0.09);
      g.bezierCurveTo(W * 0.72, H * 0.17, W * 0.30, H * 0.05, 0, H * 0.15);
      g.closePath();
    }
    // 西岸 / 东岸
    g.beginPath();
    g.moveTo(0, 0); g.lineTo(W * 0.34, 0);
    g.bezierCurveTo(W * 0.27, H * 0.30, W * 0.37, H * 0.52, W * 0.28, H);
    g.lineTo(0, H); g.closePath();
    g.moveTo(W, 0); g.lineTo(W * 0.62, 0);
    g.bezierCurveTo(W * 0.70, H * 0.26, W * 0.58, H * 0.54, W * 0.70, H);
    g.lineTo(W, H); g.closePath();
    g.fill();
    // 北岸条带：向下渐隐，避免硬边
    var ng = g.createLinearGradient(0, 0, 0, H * 0.2);
    ng.addColorStop(0, 'rgba(47,121,150,1)');
    ng.addColorStop(0.62, 'rgba(35,97,124,.92)');
    ng.addColorStop(1, 'rgba(26,74,99,0)');
    g.fillStyle = ng;
    g.beginPath();
    g.moveTo(0, 0); g.lineTo(W, 0); g.lineTo(W, H * 0.09);
    g.bezierCurveTo(W * 0.72, H * 0.17, W * 0.30, H * 0.05, 0, H * 0.15);
    g.closePath();
    g.fill();
    g.fillStyle = landGrad;

    // 3) 地形纹理（用固定种子的伪随机点模拟晕渲明暗）
    var seed = 20180915;
    function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
    g.save();
    landPath(); g.clip();
    for (var k = 0; k < 2600; k++) {
      var x = rnd() * W, y = rnd() * H;
      var a = 0.03 + rnd() * 0.07;
      g.fillStyle = rnd() > 0.5 ? 'rgba(120,196,236,' + a.toFixed(3) + ')'
        : 'rgba(3,18,30,' + (a * 0.9).toFixed(3) + ')';
      g.fillRect(x, y, 1 + rnd() * 2, 1 + rnd() * 2);
    }
    g.restore();

    // 4) 河道（伶仃洋主槽 + 两条支流）
    g.strokeStyle = 'rgba(9,38,58,.85)';
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(W * 0.50, 0);
    g.bezierCurveTo(W * 0.47, H * 0.30, W * 0.55, H * 0.55, W * 0.48, H);
    g.stroke();
    g.lineWidth = 1.6;
    g.globalAlpha = 0.5;
    g.beginPath();
    g.moveTo(W * 0.18, H * 0.30);
    g.bezierCurveTo(W * 0.30, H * 0.40, W * 0.40, H * 0.46, W * 0.47, H * 0.58);
    g.stroke();
    g.beginPath();
    g.moveTo(W * 0.84, H * 0.34);
    g.bezierCurveTo(W * 0.74, H * 0.42, W * 0.64, H * 0.48, W * 0.56, H * 0.60);
    g.stroke();
    g.globalAlpha = 1;

    // 5) 岸线高亮（带发光）
    g.strokeStyle = 'rgba(53,224,255,.8)';
    g.shadowColor = 'rgba(53,224,255,.85)';
    g.shadowBlur = 7;
    g.lineWidth = 1.6;
    g.beginPath();
    g.moveTo(W * 0.34, 0);
    g.bezierCurveTo(W * 0.27, H * 0.30, W * 0.37, H * 0.52, W * 0.28, H);
    g.stroke();
    g.beginPath();
    g.moveTo(W * 0.62, 0);
    g.bezierCurveTo(W * 0.70, H * 0.26, W * 0.58, H * 0.54, W * 0.70, H);
    g.stroke();

    return cv.toDataURL('image/png');
  }

  function mockSimulate(payload) {
    var ty = MOCK_TYPHOONS[payload.typhoon_id] || MOCK_TYPHOONS.mangkhut;
    var meta = mockMeta();
    var def = meta.defaults;
    var params = payload.params || {};
    var ov = payload.overrides || {};
    var hours = Number(payload.hours) || def.hours;
    var step = Number(payload.step_hours) || def.step_hours;
    var n = Math.floor(hours / step) + 1;
    var env = Number(params.env_pressure) || def.env_pressure;
    var hollandB = Number(params.holland_b) || def.holland_b;
    var rmax = Number(params.rmax_km) || 40;
    var coarse = params.coarse_amp === undefined || params.coarse_amp === null ? def.coarse_amp : Number(params.coarse_amp);
    var tideCfg = params.tide || def.tide;
    var SITE = [22.60, 113.70];

    var frames = [], series = [];
    for (var i = 0; i < n; i++) {
      var h = i * step;
      var p = interpTrack(ty.track, h);
      var vmax = ov.vmax_kt === null || ov.vmax_kt === undefined ? p.vmax_kt : Number(ov.vmax_kt);
      var pc = ov.pc_mb === null || ov.pc_mb === undefined ? p.pc_mb : Number(ov.pc_mb);
      var motion = ov.motion_kt === null || ov.motion_kt === undefined ? p.motion_kt : Number(ov.motion_kt);
      var heading = ov.heading_deg === null || ov.heading_deg === undefined ? p.heading_deg : Number(ov.heading_deg);

      var dist = haversine(p.lat, p.lon, SITE[0], SITE[1]);
      var tide = tideAt(h, tideCfg);

      // 反气压增水（h_p = dP*100/(rho_w*g)）
      var dP = Math.max(0, env - pc);
      var hP = dP * 100 / (1025 * 9.81);
      // 风增水（与 Vmax 单调增，Holland B / Rmax / coarse_amp 参与）
      var vms = vmax * 0.514444;
      var windTerm = 1.13 * Math.pow(vms / 59.2, 1.6) *
        Math.pow(1.7 / Math.max(0.8, hollandB), 0.5) *
        Math.pow(rmax / 40, 0.15) * (coarse / 3.8);
      // 距离衰减：中心越近增水越大
      var decay = Math.max(0.03, Math.min(1, 1 - (dist - 70) / 300));
      var surge = Math.max(0, (hP + windTerm) * decay);
      if (dist > 800) surge = 0;
      var twl = surge + tide;
      var maxDepth = Math.max(0, (twl - 1.45) * 1.35);
      if (maxDepth > surge * 2.5) maxDepth = surge * 2.5;
      var floodedHa = maxDepth > 0.01 ? Math.round(160 * maxDepth * maxDepth * 18 * 10) / 10 : 0;
      var floodedPct = Math.round(floodedHa / 100 / 4770 * 100 * 100) / 100;

      var cats = { '0-0.33': 0, '0.33-0.5': 0, '0.5-1': 0, '1-2': 0, '2-3': 0, '3-5': 0, '>5': 0 };
      if (floodedHa > 0) {
        var share = [0.52, 0.16, 0.15, 0.10, 0.05, 0.015, 0.005];
        var keys = Object.keys(cats);
        var tot = 0;
        for (var ci = 0; ci < keys.length; ci++) { cats[keys[ci]] = Math.round(floodedHa * 100 * share[ci]); tot += cats[keys[ci]]; }
        if (tot === 0) cats['0-0.33'] = 1;
      }

      var frame = {
        i: i, label: 'Hour +' + UTIL.pad3(h), utc: p.t,
        center: [Math.round(p.lat * 1000) / 1000, Math.round(p.lon * 1000) / 1000],
        pc_mb: Math.round(pc), vmax_kt: Math.round(vmax),
        motion_kt: Math.round(motion), heading_deg: Math.round(heading),
        tide: Math.round(tide * 100) / 100,
        max_surge: Math.round(surge * 100) / 100,
        max_twl: Math.round(twl * 100) / 100,
        max_depth: Math.round(maxDepth * 100) / 100,
        flooded_ha: floodedHa, flooded_pct: floodedPct,
        categories: cats,
        png_b64: null, png_w: 594, png_h: 432, _mockOverlay: true
      };
      frames.push(frame);
      series.push({
        utc: p.t, tide: frame.tide, surge: frame.max_surge, twl: frame.max_twl,
        wind_ms: Math.round(vms * 10) / 10, wind_kt: frame.vmax_kt,
        pc_mb: frame.pc_mb, dist_km: Math.round(dist * 10) / 10, flooded_ha: floodedHa
      });
    }

    // 汇总（按帧取极值）
    function argmax(key) {
      var bi = 0, bv = -Infinity;
      for (var k = 0; k < frames.length; k++) if (frames[k][key] > bv) { bv = frames[k][key]; bi = k; }
      return { value: bv, label: frames[bi].label, i: bi };
    }
    var md = argmax('max_depth'), ma = argmax('flooded_ha'), ms = argmax('max_surge');
    // 影响人口：按提示帧的村水深估算
    var peaked = frames[md.i];
    var popSum = 0, vilCount = 0;
    for (var vi = 0; vi < MOCK_VILLAGES.length; vi++) {
      var d = UTIL.villageDepth(MOCK_VILLAGES[vi], peaked);
      if (d >= 0.2) { popSum += MOCK_VILLAGES[vi].pop; vilCount++; }
    }

    return {
      ok: true, elapsed_ms: 0, _mock: true,
      bbox: [[22.20, 113.20], [23.00, 114.20]], nx: 1188, ny: 864,
      frames: frames, timeseries: series,
      summary: {
        max_depth: { value: Math.round(md.value * 100) / 100, label: md.label },
        max_area: { value: Math.round(ma.value * 10) / 10, label: ma.label },
        max_surge: { value: Math.round(ms.value * 100) / 100, label: ms.label },
        affected_pop: popSum, affected_villages: vilCount
      }
    };
  }

  /* ---------------- 网络层 ---------------- */
  var state = {
    mode: 'unknown',     // 'live' | 'mock'
    reason: '',
    metaCache: null,
    typhoonCache: {}
  };

  function isDevHost() {
    if (typeof location === 'undefined') return true;
    if (location.protocol === 'file:') return true;
    var h = location.hostname;
    if (!h) return true;
    if (/^(localhost|127\.|0\.0\.0\.0|\[::1\])/.test(h)) return true;
    return false;
  }
  var FORCE_MOCK = (function () {
    try { return /[?&]mock=1/.test(location.search); } catch (e) { return false; }
  })();

  function netError(msg) {
    var e = new Error(msg || '无法连接推演服务，请先运行 启动.cmd');
    e.network = true;
    return e;
  }

  function request(url, opts) {
    return fetch(url, opts).then(function (res) {
      var ct = (res.headers.get('content-type') || '').toLowerCase();
      if (!res.ok) {
        if (ct.indexOf('json') >= 0) {
          return res.json().then(function (j) {
            var e = new Error((j && j.error) ? j.error : ('推演服务返回 HTTP ' + res.status));
            e.status = res.status;
            e.network = res.status === 404 || res.status >= 500;
            throw e;
          }, function () {
            var e2 = new Error('推演服务返回 HTTP ' + res.status);
            e2.status = res.status; e2.network = res.status === 404 || res.status >= 500;
            throw e2;
          });
        }
        var e3 = new Error('推演服务返回 HTTP ' + res.status + '（' + url + '）');
        e3.status = res.status; e3.network = res.status === 404 || res.status >= 500;
        throw e3;
      }
      if (ct.indexOf('json') >= 0) return res.json();
      return res.text();
    }, function () {
      throw netError();
    });
  }

  var API = {
    util: UTIL,

    mode: function () { return state.mode; },
    isMock: function () { return state.mode === 'mock'; },
    mockReason: function () { return state.reason; },
    isDevHost: isDevHost,

    /* GET /api/health */
    health: function () {
      return request('/api/health').then(function (j) { return j; }, function () { return null; });
    },

    /* GET /api/meta —— 失败时（开发态）回落到内置样例 */
    getMeta: function (forceRefresh) {
      if (state.metaCache && !forceRefresh) return Promise.resolve(state.metaCache);
      if (FORCE_MOCK) {
        state.mode = 'mock'; state.reason = 'URL 指定 ?mock=1';
        state.metaCache = { data: mockMeta(), mock: true };
        return Promise.resolve(state.metaCache);
      }
      return request('/api/meta').then(function (j) {
        state.mode = 'live'; state.reason = '';
        state.metaCache = { data: j, mock: false };
        return state.metaCache;
      }, function (err) {
        if (!isDevHost()) throw err;   // 生产态不给兜底，直接暴露错误
        state.mode = 'mock'; state.reason = err && err.message ? err.message : '后端不可达';
        state.metaCache = { data: mockMeta(), mock: true };
        return state.metaCache;
      });
    },

    /* GET /api/typhoon/{id} */
    getTyphoon: function (id) {
      if (state.typhoonCache[id]) return Promise.resolve(state.typhoonCache[id]);
      if (state.mode === 'mock') {
        var t = MOCK_TYPHOONS[id] || mockTyphoonFallback(id);
        state.typhoonCache[id] = { data: t, mock: true };
        return Promise.resolve(state.typhoonCache[id]);
      }
      return request('/api/typhoon/' + encodeURIComponent(id)).then(function (j) {
        state.typhoonCache[id] = { data: j, mock: false };
        return state.typhoonCache[id];
      }, function (err) {
        if (state.mode === 'mock') {
          var tf = MOCK_TYPHOONS[id] || mockTyphoonFallback(id);
          state.typhoonCache[id] = { data: tf, mock: true };
          return state.typhoonCache[id];
        }
        throw err;
      });
    },

    /* POST /api/simulate */
    simulate: function (payload) {
      if (state.mode === 'mock') {
        return new Promise(function (resolve) {
          setTimeout(function () {
            var t0 = Date.now();
            var res = mockSimulate(payload || {});
            res.elapsed_ms = Date.now() - t0;
            resolve(res);
          }, 260); // 模拟一点耗时，让加载态可见
      });
      }
      return request('/api/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      }).then(function (j) {
        if (j && j.error) {
          var e = new Error(j.error);
          e.fromServer = true;
          throw e;
        }
        return j;
      });
    },

    /* 帧叠加图 data URL：真实后端给 png_b64，演示兜底走 canvas 现场渲染 */
    frameImage: function (frame) {
      if (!frame) return '';
      if (frame._mockOverlay) return mockOverlay(frame);
      if (frame.png_b64) return 'data:image/png;base64,' + frame.png_b64;
      return '';
    },

    mockDemPreview: mockDemPreview,
    mockTyphoonList: function () {
      return [MOCK_TYPHOONS.mangkhut, MOCK_TYPHOONS.hato, MOCK_TYPHOONS.hagibis];
    }
  };

  function mockTyphoonFallback(id) {
    var t = MOCK_TYPHOONS.mangkhut;
    var copy = JSON.parse(JSON.stringify(t));
    copy.id = id;
    return copy;
  }

  global.PAPI = API;
  global.PUTIL = UTIL;
})(window);
