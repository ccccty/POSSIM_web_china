/* ==========================================================================
   POSSIM-Web · app.js
   职责：应用状态机（启动 → meta → 台风 → simulate → 逐帧渲染）、时间轴播放、
        指标滚动数字、村表重排、参数抽屉、导出、关于、错误/兜底态
   依赖：api.js（PAPI/PUTIL）、map.js（PMap）、charts.js（PCharts）
   ========================================================================== */
(function (global) {
  'use strict';

  var U = global.PUTIL;
  var API = global.PAPI;
  var PMap = global.PMap || {};
  var PCharts = global.PCharts || {};
  var PWIND = global.PWIND || {};

  function $(id) { return document.getElementById(id); }
  function setText(id, s) { var el = $(id); if (el) el.textContent = s; }
  function setHTML(id, s) { var el = $(id); if (el) el.innerHTML = s; }

  /* 静态演示包（GitHub Pages 等纯静态托管）下，计算结果来自预计算文件而非实时后端 */
  function isStatic() { return !!(API && API.isStatic && API.isStatic()); }

  /* ---------------- 应用状态 ---------------- */
  var S = {
    meta: null,
    mock: false,
    typhoons: [],
    tid: null,
    typhoon: null,
    sim: null,
    frames: [],
    series: [],
    idx: 0,
    playing: false,
    timer: null,
    busy: false,
    mapInited: false,
    scale: 1,
    params: {
      env_pressure: 1013,
      holland_b: 1.7,
      rmax_km: 40,
      m2_amp: 1.0,
      coarse_amp: 3.8,
      tide: { M2: [1.0, 145], S2: [0.4, 45], K1: [0.3, 75], O1: [0.2, 120] }
    },
    overrides: { vmax_kt: null, pc_mb: null, motion_kt: null, heading_deg: null },
    kernel: 'numpy',    // 'numpy' 复算 | 'fortran' 原 POSSIM 内核（storm_surge_physics.dll）
    dirty: {},          // 用户真正动过的参数 key
    ringMax: { surge: 3, twl: 4, area: 3000, pop: 200000 },
    health: null,       // 最近一次 /api/health 响应
    elapsedMs: 0,       // 最近一次 simulate 耗时（ms）
    peakIdx: 0,         // summary 解析出的峰值帧号
    /* 风场图层状态（契约 §10.3）：默认开启粒子流动 */
    wind: { on: true, mode: 'particle', intensity: 'mid' }
  };

  /* ---------------- 舞台缩放（scale-to-fit，1920×1080 基准） ---------------- */
  function fitStage() {
    var vw = window.innerWidth || 1920;
    var vh = window.innerHeight || 1080;
    var s = Math.min(vw / 1920, vh / 1080);
    if (!isFinite(s) || s <= 0) s = 1;
    var stage = $('stage');
    if (stage) stage.style.transform = 'scale(' + s + ')';
    S.scale = s;
  }

  /* ---------------- 通用 UI ---------------- */
  var toastTimer = null;
  function toast(msg, kind) {
    var el = $('toast');
    if (!el) return;
    el.className = 'toast show' + (kind === 'warn' ? ' warn' : '');
    // P6：普通提示不再带前导符号（原先会渲染成 "·已按手动参数重算（…）"）；只有告警保留 "!" 前缀
    el.innerHTML = (kind === 'warn' ? '<span class="tk">!</span>' : '') + '<span>' + U.esc(msg) + '</span>';
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = 'toast' + (kind === 'warn' ? ' warn' : ''); }, 3200);
  }
  function showErr(msg) {
    setText('errbar-msg', msg || '无法连接推演服务，请先运行 启动.cmd');
    var el = $('errbar'); if (el) el.classList.add('show');
  }
  function hideErr() { var el = $('errbar'); if (el) el.classList.remove('show'); }
  function showStatus(kind, text) {
    var el = $('tl-status');
    if (!el) return;
    el.className = 'tl-status' + (kind && kind !== 'idle' ? ' ' + kind : '');
    el.textContent = text;
  }
  function showLoading(on, text) {
    var el = $('loading');
    if (!el) return;
    if (text) setText('loading-text', text);
    if (on) el.classList.add('show'); else el.classList.remove('show');
  }
  function demoBadge(on) {
    var el = $('demo-badge');
    if (!el) return;
    if (on) el.classList.add('show'); else el.classList.remove('show');
  }

  /* ---------------- 数字滚动 ---------------- */
  var rollCache = {};
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
  function roll(key, el, to, fmt, dur) {
    if (!el) return;
    var st = rollCache[key] || (rollCache[key] = { v: 0, raf: 0 });
    if (st.raf) { cancelAnimationFrame(st.raf); st.raf = 0; }
    var from = Number(st.v) || 0;
    var target = Number(to) || 0;
    if (Math.abs(target - from) < 1e-6) { st.v = target; el.innerHTML = fmt(target); return; }
    var t0 = (global.performance && performance.now) ? performance.now() : Date.now();
    dur = dur || 460;
    function step() {
      var now = (global.performance && performance.now) ? performance.now() : Date.now();
      var t = Math.min(1, (now - t0) / dur);
      var v = from + (target - from) * easeOut(t);
      st.v = v;
      el.innerHTML = fmt(v);
      if (t < 1) st.raf = requestAnimationFrame(step);
      else { st.v = target; st.raf = 0; el.innerHTML = fmt(target); }
    }
    st.raf = requestAnimationFrame(step);
    // 兜底：某些环境（后台标签页 / headless）rAF 会被节流，超时后直接落终值
    setTimeout(function () {
      if (Math.abs((Number(st.v) || 0) - target) > 1e-6) { st.v = target; el.innerHTML = fmt(target); }
    }, dur + 80);
  }
  var RING_C = 2 * Math.PI * 16;
  function setRing(id, pct) {
    var el = $(id);
    if (!el) return;
    var p = Math.max(0, Math.min(1, pct || 0));
    el.setAttribute('stroke-dasharray', (p * RING_C).toFixed(1) + ' ' + RING_C.toFixed(1));
  }

  /* ---------------- 静态区渲染 ---------------- */
  function renderStatic() {
    var meta = S.meta;
    var site = meta.site || {};
    var dem = meta.dem || {};
    var def = meta.defaults || {};

    // 目标海域
    setText('sea-region', site.region || '--');
    setHTML('sea-area', (site.area_km2 !== undefined ? Number(site.area_km2).toLocaleString('en-US') : '--') + '<small>km²</small>');
    /* 平均高程（P4）：优先 meta.dem.mean_elev（1 位小数）；退而用 mean；
       两者都缺失时用 (min+max)/2 并加「约」字 —— 不再显示 "—" */
    var elevEl = $('sea-elev');
    if (elevEl) {
      var meanElev = null, approx = false, elevFrom = '';
      if (dem && typeof dem.mean_elev === 'number' && isFinite(dem.mean_elev)) {
        meanElev = dem.mean_elev; elevFrom = 'meta.dem.mean_elev';
      } else if (dem && typeof dem.mean === 'number' && isFinite(dem.mean)) {
        meanElev = dem.mean; elevFrom = 'meta.dem.mean';
      } else if (dem && isFinite(Number(dem.min)) && isFinite(Number(dem.max))) {
        meanElev = (Number(dem.min) + Number(dem.max)) / 2;
        approx = true; elevFrom = '(min+max)/2 估算';
      }
      if (meanElev === null) {
        elevEl.innerHTML = '--<small>m</small>';
      } else {
        elevEl.innerHTML = (approx ? '约 ' : '') + U.num(meanElev, 1) + '<small>m</small>';
        elevEl.style.color = approx ? '#7ba7c9' : '';
      }
      var elevTip = ['来源：' + (elevFrom || '--')];
      if (dem && typeof dem.median_elev === 'number') elevTip.push('中位 ' + U.num(dem.median_elev, 1) + ' m');
      if (dem && typeof dem.land_ratio === 'number') elevTip.push('陆地占比 ' + U.num(dem.land_ratio * 100, 1) + '%');
      if (dem) elevTip.push('min ' + dem.min + ' / max ' + dem.max + ' m');
      elevEl.title = elevTip.join(' · ');
    }
    setHTML('sea-res', (site.pixel_m !== undefined ? U.num(site.pixel_m, 1) : '--') + '<small>m</small>');

    // 台风下拉
    var tyList = (meta.typhoons || []).slice();
    if (!tyList.length && S.mock && API.mockTyphoonList) {
      tyList = API.mockTyphoonList().map(function (t) {
        return { id: t.id, name: t.name, name_en: t.name_en, year: t.year, vmax_kt: t.track[t.track.length - 1].vmax_kt };
      });
    }
    S.typhoons = tyList;
    var sel = $('typhoon-select');
    if (sel) {
      var opts = '';
      for (var i = 0; i < tyList.length; i++) {
        var t = tyList[i];
        opts += '<option value="' + U.esc(t.id) + '">' + U.esc((t.name || t.id) + '（' + (t.year || '') + '）') + '</option>';
      }
      sel.innerHTML = opts;
      sel.value = tyList.length ? tyList[0].id : '';
    }

    // 默认参数（对齐 meta.defaults，契约 §3.1）
    S.params.env_pressure = def.env_pressure !== undefined ? def.env_pressure : 1013;
    S.params.holland_b = def.holland_b !== undefined ? def.holland_b : 1.7;
    S.params.coarse_amp = def.coarse_amp !== undefined ? def.coarse_amp : 3.8;
    if (def.tide) {
      S.params.tide = {
        M2: [Number((def.tide.M2 || [1, 145])[0]) || 1, Number((def.tide.M2 || [1, 145])[1]) || 0],
        S2: [Number((def.tide.S2 || [0.4, 45])[0]) || 0.4, Number((def.tide.S2 || [0.4, 45])[1]) || 0],
        K1: [Number((def.tide.K1 || [0.3, 75])[0]) || 0.3, Number((def.tide.K1 || [0.3, 75])[1]) || 0],
        O1: [Number((def.tide.O1 || [0.2, 120])[0]) || 0.2, Number((def.tide.O1 || [0.2, 120])[1]) || 0]
      };
    }
    S.params.m2_amp = S.params.tide.M2[0];

    // 村表初值（静态等级）+ 分潮卡
    renderVillages(null);
    renderTideCells();
    setRangeFillAll();
    setText('tl-total', String(def.hours || 36));

    // 缩略图占位（避免空 img）
    var thumb = $('sea-thumb-img');
    if (thumb && !thumb.getAttribute('src')) {
      thumb.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
    }
  }

  function staticLevel(v) {
    var lv = v && v.level;
    if (lv === '高') return { cls: 'high', name: '高' };
    if (lv === '中') return { cls: 'mid', name: '中' };
    if (lv === '低') return { cls: 'low', name: '低' };
    return U.levelOf(0);
  }

  /* ---------------- 村表（按当前帧水深重排 + 上色） ---------------- */
  function renderVillages(frame) {
    // 同一帧重复调用（指标块 / 工况表 / 主流程）只重排一次 DOM
    if (frame && S._vilDrawnFor === frame.i && S._vilStat) return S._vilStat;
    var vs = (S.meta && S.meta.villages) || [];
    var rows = vs.map(function (v) {
      return { v: v, d: frame ? U.villageDepth(v, frame) : 0 };
    });
    if (frame) {
      rows.sort(function (a, b) { return (b.d - a.d) || (b.v.pop - a.v.pop); });
    } else {
      rows.sort(function (a, b) { return (b.v.pop || 0) - (a.v.pop || 0); });
    }
    var html = '';
    var hit = 0, popSum = 0, hhSum = 0;
    for (var i = 0; i < rows.length; i++) {
      var it = rows[i], v = it.v, d = it.d;
      var lv = frame ? U.levelOf(d) : staticLevel(v);
      var cls = '';
      if (frame && d >= 0.5) cls = ' is-danger';
      else if (frame && d >= 0.2) cls = ' is-warn';
      if (frame && d >= 0.2) { hit++; popSum += (v.pop || 0); hhSum += (v.households || 0); }
      html += '<div class="v-row' + cls + '">' +
        '<span class="vn" title="' + U.esc(v.name) + '">' + U.esc(v.name) + '</span>' +
        '<span class="nums">' + U.pop(v.pop) + '</span>' +
        '<span class="nums">' + U.pop(v.households) + '</span>' +
        '<span class="nums">' + U.num(v.dist_coast_km, 1) + '</span>' +
        '<span class="nums">' + (frame ? U.depth(d) : '--') + '</span>' +
        '<span><i class="v-lv ' + lv.cls + '">' + lv.name + '</i></span>' +
        '</div>';
    }
    setHTML('v-body', html || '<div class="v-row"><span class="vn">无村表数据</span></div>');
    setText('v-count', frame ? ('受影响 ' + hit + ' 个村') : ('村表 ' + vs.length + ' 条（静态）'));
    setText('v-pop', frame ? U.pop(popSum) : '--');
    setText('v-stamp', frame ? '按当前帧水深排序' : '按人口排序（静态）');
    S._vilStat = { hit: hit, pop: popSum, households: hhSum };
    S._vilDrawnFor = frame ? frame.i : -1;
    return S._vilStat;
  }

  /* ---------------- 分潮卡 ---------------- */
  function renderTideCells() {
    var t = S.params.tide;
    var keys = ['M2', 'S2', 'K1', 'O1'];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i], c = t[k] || [0, 0];
      setText('td-' + k.toLowerCase(), U.depth(c[0]) + ' m');
      setText('td-' + k.toLowerCase() + 'p', 'φ ' + U.num(c[1], 0) + '°');
    }
  }

  /* ---------------- 台风卡 ---------------- */
  function nearestTrack(ty, lat, lon) {
    if (!ty || !ty.track || !ty.track.length) return null;
    var best = ty.track[0], bd = Infinity;
    for (var i = 0; i < ty.track.length; i++) {
      var dx = ty.track[i].lat - lat, dy = ty.track[i].lon - lon;
      var d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = ty.track[i]; }
    }
    return best;
  }

  function updateTyphoonCard(frame, ty) {
    if (!ty) return;
    var last = ty.track && ty.track.length ? ty.track[ty.track.length - 1] : {};
    var vmax = frame ? frame.vmax_kt : (last.vmax_kt || 0);
    var pc = frame ? frame.pc_mb : (last.pc_mb || 0);
    var motion = frame ? frame.motion_kt : (last.motion_kt || 0);
    var heading = frame ? frame.heading_deg : (last.heading_deg || 0);
    var center = frame ? frame.center : [last.lat, last.lon];
    var near = nearestTrack(ty, center[0], center[1]) || last;

    setText('ty-id', String(ty.id || '--').toUpperCase());
    setText('ty-name', (ty.name || '--') + (ty.name_en ? ' / ' + ty.name_en : ''));
    setText('ty-level', U.intensity(vmax) + '（' + U.num(vmax, 0) + ' kt）');
    setText('ty-pc', U.num(pc, 0) + ' hPa');
    /* P6：最大风速行 = 当前帧值 + 路径峰值（抽屉初值口径 = 路径峰值，两处要能对上） */
    var pk = peakTrackPoint(ty) || last;
    var pkVmax = Number(pk.vmax_kt) || 0;
    setHTML('ty-vmax', U.num(vmax, 0) + ' kt（峰值 ' + U.num(pkVmax, 0) + ' kt）');
    var vmaxEl = $('ty-vmax');
    if (vmaxEl) {
      vmaxEl.title = '当前帧 ' + U.num(vmax, 0) + ' kt（' + U.num(vmax * 0.514444, 1) + ' m/s）' +
        ' · 路径峰值 ' + U.num(pkVmax, 0) + ' kt（' + U.num(pkVmax * 0.514444, 1) + ' m/s）' +
        (pk.t ? ' @ ' + pk.t : '') + ' · 参数抽屉初值取路径峰值';
    }
    var moveEl = $('ty-move');
    if (moveEl) {
      // 卡宽有限：只放「移速 · 方位 角度」，km/h 放 title
      moveEl.textContent = U.num(motion, 0) + ' kt · ' + U.bearing(heading) + ' ' + U.num(heading, 0) + '°';
      moveEl.title = U.num(motion, 0) + ' kt ≈ ' + U.num(motion * 1.852, 0) + ' km/h，移向 ' +
        U.num(heading, 0) + '°（' + U.bearing(heading) + '）';
    }
    if (near && near.r34) {
      setText('ty-r34', 'NE ' + near.r34[0] + ' / SW ' + near.r34[2] + ' nm');
    } else {
      setText('ty-r34', '--');
    }
    var al = U.alert(frame ? frame.max_surge : 0, frame ? frame.flooded_pct : 0);
    var alEl = $('ty-alert');
    if (alEl) {
      alEl.textContent = al.name === '无' ? '无预警' : (al.name + '预警');
      alEl.style.color = al.color;
    }
  }

  /* ---------------- 风场 / 工况卡 ---------------- */
  function updateWind(frame, row) {
    if (!frame) return;
    setHTML('w-wind-kt', U.num(frame.vmax_kt, 0) + '<small>kt</small>');
    setHTML('w-wind-ms', U.num(frame.vmax_kt * 0.514444, 1) + '<small>m/s</small>');
    setHTML('w-pc', U.num(frame.pc_mb, 0) + '<small>hPa</small>');
    setHTML('w-dist', U.num(row ? row.dist_km : 0, 1) + '<small>km</small>');
    setHTML('w-heading', U.num(frame.heading_deg, 0) + '<small>°</small>');
    setHTML('w-motion', U.num(frame.motion_kt, 0) + '<small>kt</small>');
    setText('wind-stamp', frame.label || '');

    var stat = renderVillages(frame);
    var total = (S.meta.villages || []).length;
    var pump = Math.max(2, Math.round(total * 0.5));
    var shelter = Math.max(1, Math.round(total * 0.4));
    var ktab = $('ktab-body');
    if (ktab) {
      ktab.innerHTML =
        '<tr><td>受影响村庄</td><td>' + stat.hit + ' / ' + total + '</td></tr>' +
        '<tr class="hl"><td>需转移人口</td><td>' + U.pop(stat.pop) + ' 人</td></tr>' +
        '<tr><td>户数合计</td><td>' + U.pop(stat.households) + '</td></tr>' +
        '<tr><td>闸泵值守点</td><td>' + pump + ' 处</td></tr>' +
        '<tr><td>避风锚地</td><td>' + shelter + ' 处</td></tr>' +
        '<tr><td>最高淹没水深</td><td>' + U.depth(frame.max_depth) + ' m</td></tr>';
    }
  }

  /* ---------------- 分潮/预警卡 ---------------- */
  function updateTide(frame) {
    if (!frame) return;
    setHTML('td-cur', U.depth(frame.tide) + '<small>m</small>');
    var next = S.series[S.idx + 1];
    var trend = '--';
    if (next) {
      var d = (Number(next.tide) || 0) - (Number(frame.tide) || 0);
      trend = d > 0.005 ? '涨潮' : (d < -0.005 ? '落潮' : '持平');
    }
    setText('td-trend', trend);
    var al = U.alert(frame.max_surge, frame.flooded_pct);
    var lvEl = $('ab-level');
    if (lvEl) { lvEl.textContent = al.name; lvEl.style.color = al.color; }
    var segs = document.querySelectorAll('#ab-seg span');
    var order = { none: -1, blue: 0, yellow: 1, orange: 2, red: 3 };
    var lv = order[al.key];
    for (var i = 0; i < segs.length; i++) {
      if (lv >= 0 && i <= lv) segs[i].classList.add('on');
      else segs[i].classList.remove('on');
    }
    setText('tide-stamp', frame.label || '');
  }

  /* ---------------- 指标块（数字滚动 + 环形进度） ---------------- */
  function updateIndicators(frame) {
    var surge = Number(frame.max_surge) || 0;
    var twl = Number(frame.max_twl) || 0;
    var area = Number(frame.flooded_ha) || 0;
    var stat = renderVillages(frame);
    var pop = stat.pop;

    roll('surge', $('ind-surge-v'), surge, function (v) { return U.depth(v) + '<small>m</small>'; });
    roll('twl', $('ind-twl-v'), twl, function (v) { return U.depth(v) + '<small>m</small>'; });
    roll('area', $('ind-area-v'), area, function (v) { return U.area(v) + '<small>ha</small>'; });
    roll('pop', $('ind-pop-v'), pop, function (v) { return U.pop(v) + '<small>人</small>'; });

    setRing('ind-surge-r', surge / Math.max(0.1, S.ringMax.surge));
    setRing('ind-twl-r', twl / Math.max(0.1, S.ringMax.twl));
    setRing('ind-area-r', area / Math.max(1, S.ringMax.area));
    setRing('ind-pop-r', pop / Math.max(1, S.ringMax.pop));

    // 指标块状态色：随当前帧水位/面积/人口变化
    var al = U.alert(surge, frame.flooded_pct);
    setIndClass('ind-surge', al.key);
    setIndClass('ind-twl', al.key);
    setIndClass('ind-area', al.key);
    setIndClass('ind-pop', pop > 0 ? (pop >= 20000 ? 'red' : 'orange') : 'none');
  }

  function setIndClass(id, key) {
    var el = $(id);
    if (!el) return;
    var cls = 'ind';
    if (key === 'red') cls += ' danger';
    else if (key === 'orange' || key === 'yellow') cls += ' warn';
    el.className = cls;
  }

  function recomputeRingMax() {
    var ms = 0.5, mt = 0.5, ma = 1, mp = 1;
    for (var i = 0; i < S.frames.length; i++) {
      var f = S.frames[i];
      ms = Math.max(ms, Number(f.max_surge) || 0);
      mt = Math.max(mt, Number(f.max_twl) || 0);
      ma = Math.max(ma, Number(f.flooded_ha) || 0);
    }
    var vs = (S.meta && S.meta.villages) || [];
    var totalPop = 0;
    for (var j = 0; j < vs.length; j++) totalPop += (vs[j].pop || 0);
    S.ringMax = { surge: ms, twl: mt, area: ma, pop: Math.max(1, totalPop) };
  }

  /* ---------------- 时间轴 ---------------- */
  function stepHours() {
    return (S.meta && S.meta.defaults && S.meta.defaults.step_hours) || 1;
  }

  function goToFrame(i) {
    var n = S.frames.length;
    if (!n) return;
    i = Math.max(0, Math.min(n - 1, Math.round(i)));
    S.idx = i;
    var f = S.frames[i];
    var row = S.series[i] || null;

    if (PMap.setFrame) PMap.setFrame(f, S.typhoon);
    // 风场按该帧 center/pc/vmax/motion/heading 重算矢量网格（粒子继续推进，不重置）
    if (PWIND.setFrame) PWIND.setFrame(f, S.params);
    if (PCharts.setCursor) PCharts.setCursor(i);

    updateIndicators(f);
    updateTyphoonCard(f, S.typhoon);
    updateWind(f, row);
    updateTide(f);

    var rng = $('tl-range');
    if (rng) {
      rng.max = String(n - 1);
      rng.value = String(i);
      var pct = n > 1 ? (i / (n - 1) * 100) : 0;
      rng.style.background = 'linear-gradient(90deg,rgba(53,224,255,.95) 0%,rgba(47,129,247,.9) ' +
        pct.toFixed(1) + '%,rgba(23,69,107,.85) ' + pct.toFixed(1) + '%)';
    }
    setText('tl-hour', '+' + U.pad3(i * stepHours()));
    setText('tl-utc', f.utc || '--');
    setText('map-caption-frame', f.label || ('Hour +' + U.pad3(i)));
    var lgT = $('lg-tide'), lgS = $('lg-surge'), lgW = $('lg-twl');
    if (lgT) lgT.textContent = U.depth(f.tide) + ' m';
    if (lgS) lgS.textContent = U.depth(f.max_surge) + ' m';
    if (lgW) lgW.textContent = U.depth(f.max_twl) + ' m';
  }

  function play() {
    if (!S.frames.length) return;
    pause();
    S.playing = true;
    var ico1 = $('ico-play'), ico2 = $('ico-pause');
    if (ico1) ico1.style.display = 'none';
    if (ico2) ico2.style.display = '';
    showStatus('ok', '播放中 · 2 s/帧');
    S.timer = setInterval(function () {
      var next = S.idx + 1;
      if (next >= S.frames.length) next = 0;   // 循环播放
      goToFrame(next);
    }, 2000);
  }
  function pause() {
    S.playing = false;
    if (S.timer) { clearInterval(S.timer); S.timer = null; }
    var ico1 = $('ico-play'), ico2 = $('ico-pause');
    if (ico1) ico1.style.display = '';
    if (ico2) ico2.style.display = 'none';
    if (S.frames.length) showStatus('ok', '就绪 · ' + S.frames.length + ' 帧');
  }
  function togglePlay() { if (S.playing) pause(); else play(); }

  /* ---------------- 推演主流程 ---------------- */
  function buildPayload() {
    var def = (S.meta && S.meta.defaults) || {};
    var tide = {
      M2: [Number(S.params.m2_amp), Number(S.params.tide.M2[1])],
      S2: [Number(S.params.tide.S2[0]), Number(S.params.tide.S2[1])],
      K1: [Number(S.params.tide.K1[0]), Number(S.params.tide.K1[1])],
      O1: [Number(S.params.tide.O1[0]), Number(S.params.tide.O1[1])]
    };
    return {
      typhoon_id: S.tid,
      kernel: S.kernel,
      fortran_amp: Number(S.params.coarse_amp),
      params: {
        env_pressure: Number(S.params.env_pressure) || 1013,
        holland_b: Number(S.params.holland_b) || 1.7,
        rmax_km: Number(S.params.rmax_km) || 40,
        wave_runup: def.wave_runup !== undefined ? def.wave_runup : 1.15,
        coarse_amp: Number(S.params.coarse_amp),
        tide: tide
      },
      hours: def.hours || 36,
      step_hours: def.step_hours || 1,
      overrides: {
        vmax_kt: S.overrides.vmax_kt,
        pc_mb: S.overrides.pc_mb,
        heading_deg: S.overrides.heading_deg,
        motion_kt: S.overrides.motion_kt
      }
    };
  }

  function changeSummary() {
    var parts = [];
    var map = { vmax_kt: ['Vmax', 'kt', 0], pc_mb: ['Pc', 'hPa', 0], motion_kt: ['移速', 'kt', 0], heading_deg: ['移向', '°', 0] };
    for (var k in map) {
      if (!Object.prototype.hasOwnProperty.call(map, k)) continue;
      if (S.overrides[k] !== null && S.overrides[k] !== undefined && S.dirty[k]) {
        parts.push(map[k][0] + '=' + U.num(S.overrides[k], map[k][2]) + map[k][1]);
      }
    }
    if (S.dirty.holland_b) parts.push('B=' + U.num(S.params.holland_b, 2));
    if (S.dirty.rmax_km) parts.push('Rmax=' + U.num(S.params.rmax_km, 0) + 'km');
    if (S.dirty.m2_amp) parts.push('M2=' + U.num(S.params.m2_amp, 2) + 'm');
    if (S.dirty.coarse_amp) parts.push('coarse_amp=' + U.num(S.params.coarse_amp, 1));
    return parts.length ? parts.join(' · ') : '参数未改动';
  }

  function runSimulate(opts) {
    opts = opts || {};
    if (S.busy) return Promise.resolve(null);
    S.busy = true;
    pause();
    showLoading(true, opts.manual ? '正在按手动参数重算…' : '正在推演 36 小时…');
    showStatus('busy', opts.manual ? '重算中…' : '推演中…');
    var t0 = Date.now();
    return API.simulate(buildPayload()).then(function (res) {
      if (!res || res.error) throw new Error((res && res.error) || '推演返回空结果');
      S.sim = res;
      S.frames = res.frames || [];
      S.series = res.timeseries || [];
      var ms = res.elapsed_ms || (Date.now() - t0);
      S.elapsedMs = ms;
      renderSimulation();
      var pk = S.frames[S.peakIdx] || S.frames[0];
      var kmTag = (res.kernel && res.kernel.kernel === 'fortran') ? '原内核' : 'numpy';
      if (isStatic()) {
        showStatus('ok', '就绪 · 静态预计算 · ' + kmTag + ' · ' + S.frames.length + ' 帧 · 峰值帧 ' + ((pk && pk.label) || ''));
      } else {
        showStatus('ok', '就绪 · ' + kmTag + ' · ' + S.frames.length + ' 帧 · ' + Math.round(ms) + ' ms · 峰值帧 ' + ((pk && pk.label) || ''));
      }
      if (!S.mock) hideErr();   // 演示兜底态保留「无法连接推演服务」提示 + 重试按钮
      setHealth(true, null);
      if (opts.manual) toast('已按手动参数重算（' + changeSummary() + '）');
      return res;
    }).catch(function (err) {
      var msg = (err && err.message) ? err.message : '推演失败，请稍后重试';
      showErr(msg);
      showStatus('err', '推演失败');
      return null;
    }).then(function (res) {
      S.busy = false;
      showLoading(false);
      return res;
    });
  }

  /* P3：默认定位峰值帧 —— 从 summary.max_area / max_depth 的 "Hour +NNN" 解析帧号 */
  function peakIndex() {
    var n = S.frames.length;
    if (!n) return 0;
    var sum = (S.sim && S.sim.summary) || {};
    var step = stepHours() || 1;
    var cands = [sum.max_area, sum.max_depth, sum.max_surge];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      if (!c) continue;
      var lab = (c.label !== undefined && c.label !== null) ? String(c.label) : '';
      var m = /Hour\s*\+?\s*(\d+)/i.exec(lab);
      if (!m) continue;
      var idx = Math.round(Number(m[1]) / step);
      if (isFinite(idx) && idx >= 0 && idx < n) return idx;
    }
    // summary 缺失：退化为「按帧求最大淹没面积」（全 0 则回到第 0 帧）
    var best = 0, bv = -1;
    for (var k = 0; k < n; k++) {
      var v = Number(S.frames[k].flooded_ha) || 0;
      if (v > bv) { bv = v; best = k; }
    }
    return bv > 0 ? best : 0;
  }

  function renderSimulation() {
    recomputeRingMax();
    if (S.series.length) {
      if (PCharts.available && PCharts.available()) {
        if (PCharts.init) PCharts.init($('chart-tide'));
        if (PCharts.setData) PCharts.setData(S.series);
      } else {
        setHTML('chart-tide', '<div style="height:100%;display:flex;align-items:center;justify-content:center;' +
          'color:#ffb020;font-size:11px;text-align:center;line-height:18px;">图表库（ECharts CDN）加载失败<br>请检查外网连接后刷新</div>');
      }
    }
    var rng = $('tl-range');
    if (rng) { rng.min = '0'; rng.max = String(Math.max(0, S.frames.length - 1)); }
    // 首屏不从头播：直接落到峰值帧（指标非 0），「从头播放」交给时间轴按钮
    S.peakIdx = peakIndex();
    goToFrame(S.peakIdx);
  }

  /* ---------------- 台风选择 ---------------- */
  function pickTyphoon(id) {
    if (!id) return Promise.resolve(null);
    S.tid = id;
    var sel = $('typhoon-select');
    if (sel) sel.value = id;
    return API.getTyphoon(id).then(function (r) {
      S.typhoon = r.data || null;
      if (!S.typhoon) throw new Error('台风 ' + id + ' 数据为空');
      if (PMap.init && !S.mapInited) {
        S.mapInited = true;
        PMap.init(S.meta);
        // 风场图层：地图就绪后挂 canvas（windPanel z-index 380：淹没 < 风场 < 村/路径）
        if (PWIND.init && PWIND.init(PMap)) setWindEnabled(S.wind.on);
      }
      if (PMap.setTrack) PMap.setTrack(S.typhoon);
      if (PMap.setVillages) PMap.setVillages((S.meta && S.meta.villages) || []);
      syncDrawer(true);
      updateTyphoonCard(null, S.typhoon);
      return r;
    });
  }

  /* ---------------- 参数抽屉 ---------------- */
  var PARAM_FMT = {
    vmax_kt: function (v) { return U.num(v, 0); },
    pc_mb: function (v) { return U.num(v, 0); },
    motion_kt: function (v) { return U.num(v, 0); },
    heading_deg: function (v) { return U.num(v, 0); },
    holland_b: function (v) { return U.num(v, 2); },
    rmax_km: function (v) { return U.num(v, 0); },
    m2_amp: function (v) { return U.num(v, 2); },
    coarse_amp: function (v) { return U.num(v, 1); }
  };
  var OVERRIDE_KEYS = { vmax_kt: 1, pc_mb: 1, motion_kt: 1, heading_deg: 1 };

  function setRangeFill(input) {
    if (!input) return;
    var min = Number(input.min), max = Number(input.max), v = Number(input.value);
    var pct = max > min ? ((v - min) / (max - min) * 100) : 0;
    input.style.background = 'linear-gradient(90deg,rgba(53,224,255,.85) 0%,rgba(47,129,247,.75) ' +
      pct.toFixed(1) + '%,rgba(23,69,107,.85) ' + pct.toFixed(1) + '%)';
  }
  function setRangeFillAll() {
    var list = document.querySelectorAll('input.p-range');
    for (var i = 0; i < list.length; i++) setRangeFill(list[i]);
  }

  /* 路径峰值点：track 里 vmax_kt 最大的那个点（同值取最早出现的那个）。
     P6：抽屉初值取这里而不是 track[0]，否则会出现"抽屉 60 kt / 台风态势 99 kt"的自打脸。 */
  function peakTrackPoint(ty) {
    var tr = (ty && ty.track) || [];
    var best = null;
    for (var i = 0; i < tr.length; i++) {
      if (!best || Number(tr[i].vmax_kt || 0) > Number(best.vmax_kt || 0)) best = tr[i];
    }
    return best;
  }

  function syncDrawer(resetOverrides) {
    var ty = S.typhoon;
    var first = ty && ty.track && ty.track.length ? ty.track[0] : {};
    var pk = peakTrackPoint(ty) || first;
    var base = {
      vmax_kt: pk.vmax_kt || 115,
      pc_mb: pk.pc_mb || 905,
      motion_kt: pk.motion_kt || 18,
      heading_deg: pk.heading_deg || 300,
      holland_b: S.params.holland_b,
      rmax_km: S.params.rmax_km,
      m2_amp: S.params.m2_amp,
      coarse_amp: S.params.coarse_amp
    };
    if (resetOverrides) {
      S.overrides = { vmax_kt: null, pc_mb: null, motion_kt: null, heading_deg: null };
      S.dirty = {};
    } else {
      // 用户手动动过的键以用户值为准（不被路径峰值覆盖）
      for (var dk in OVERRIDE_KEYS) {
        if (S.dirty[dk] && S.overrides[dk] !== null && S.overrides[dk] !== undefined) base[dk] = S.overrides[dk];
      }
    }
    var items = document.querySelectorAll('.p-item');
    for (var i = 0; i < items.length; i++) {
      var key = items[i].getAttribute('data-k');
      if (!key || base[key] === undefined) continue;
      var input = items[i].querySelector('input[type=range]');
      var pv = items[i].querySelector('.pv');
      if (input) { input.value = String(base[key]); setRangeFill(input); }
      if (pv) pv.textContent = (PARAM_FMT[key] || String)(base[key]);
    }
    var chips = document.querySelectorAll('.p-item[data-k="coarse_amp"] .switch-row .chip');
    for (var c = 0; c < chips.length; c++) {
      var ampc = Number(chips[c].getAttribute('data-amp'));
      chips[c].classList.toggle('active', Math.abs(ampc - S.params.coarse_amp) < 0.01);
    }
  }

  function applyParam(key, value, isUser) {
    if (OVERRIDE_KEYS[key]) {
      S.overrides[key] = value;
    } else if (key === 'm2_amp') {
      S.params.m2_amp = value;
      S.params.tide.M2[0] = value;
      renderTideCells();
    } else if (key === 'coarse_amp') {
      S.params.coarse_amp = value;
    } else {
      S.params[key] = value;
    }
    if (isUser) S.dirty[key] = true;
  }

  function openDrawer() {
    var d = $('drawer');
    if (d) { d.classList.add('open'); d.setAttribute('aria-hidden', 'false'); }
  }
  function closeDrawer() {
    var d = $('drawer');
    if (d) { d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); }
  }

  /* ---------------- 导出 ---------------- */
  function download(url, filename) {
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { if (a.parentNode) a.parentNode.removeChild(a); }, 200);
  }

  function exportPNG() {
    var f = S.frames[S.idx];
    if (!f) { toast('当前没有可导出的帧', 'warn'); return; }
    var url = API.frameImage ? API.frameImage(f) : '';
    if (!url) { toast('当前帧没有淹没图层', 'warn'); return; }
    var hour = 'h' + U.pad3(S.idx * stepHours());
    download(url, 'POSSIM_' + (S.tid || 'run') + '_' + hour + '_flood.png');
    toast('已导出当前帧淹没 PNG（' + (f.label || hour) + '）');
  }

  function exportJSON() {
    if (!S.sim) { toast('尚未推演，无法导出 summary', 'warn'); return; }
    var f = S.frames[S.idx] || {};
    var payload = {
      typhoon_id: S.tid,
      generated_utc: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
      current_frame: { index: S.idx, label: f.label, utc: f.utc, max_surge: f.max_surge, max_twl: f.max_twl, max_depth: f.max_depth, flooded_ha: f.flooded_ha, flooded_pct: f.flooded_pct },
      summary: (S.sim && S.sim.summary) || {},
      params: buildPayload().params,
      overrides: buildPayload().overrides,
      frame_count: S.frames.length,
      mode: S.mock ? 'demo_fallback' : 'engine'
    };
    var text = JSON.stringify(payload, null, 2);
    var blob = new Blob([text], { type: 'application/json;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    download(url, 'POSSIM_' + (S.tid || 'run') + '_summary.json');
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    toast('已导出 summary JSON');
  }

  /* ---------------- 关于 ---------------- */
  function openModal() { var m = $('modal-mask'); if (m) m.classList.add('open'); }
  function closeModal() { var m = $('modal-mask'); if (m) m.classList.remove('open'); }

  /* ---------------- 服务状态 ---------------- */
  /* P4：页脚右侧实时信息 = engine/dem 状态 + 帧数 + 本次推演耗时(ms) + 栅格尺寸 */
  function engineLine() {
    var h = S.health || null;
    var parts = [];
    if (isStatic()) {
      parts.push('engine 预计算包' + (S.sim && S.sim.kernel && S.sim.kernel.kernel === 'fortran' ? ' · fortran' : ' · numpy'));
    } else if (S.mock) parts.push('engine 演示兜底');
    else if (h) parts.push('engine ' + (h.engine ? 'ok' : 'fail') + ' · dem ' + (h.dem ? 'ok' : 'fail'));
    else parts.push('engine ' + (S.frames.length ? 'ok' : '--'));
    if (S.frames.length) parts.push(S.frames.length + ' 帧');
    if (S.elapsedMs) parts.push((isStatic() ? '离线 ' : '') + (S.elapsedMs >= 1000 ? (S.elapsedMs / 1000).toFixed(1) + ' s' : Math.round(S.elapsedMs) + ' ms'));
    if (S.sim && S.sim.nx && S.sim.ny) parts.push(S.sim.nx + '×' + S.sim.ny);
    return parts.join(' · ');
  }

  function setHealth(ok, h) {
    if (h && (h.engine !== undefined || h.dem !== undefined)) S.health = h;
    var dot = $('sb-dot');
    if (dot) dot.className = 'dot' + (ok ? '' : ' off');
    var txt;
    if (isStatic()) txt = ok ? '静态演示数据已就绪' : '静态数据加载失败';
    else txt = ok ? ('推演服务已连接' + (S.mock ? '（演示兜底）' : '')) : '推演服务未连接';
    setText('sb-health', txt);
    setText('sb-engine', engineLine());
  }

  /* ---------------- 交互绑定 ---------------- */
  function bindUI() {
    // 顶栏分段按钮
    var segL = $('seg-left');
    if (segL) segL.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!b) return;
      var act = b.getAttribute('data-act');
      setActive(segL, b);
      if (act === 'run') { runSimulate({ manual: false }); }
      else if (act === 'params') { openDrawer(); }
      else if (act === 'impact') {
        if (PMap.focusExtent) PMap.focusExtent('coast');
        toast('已聚焦重点岸段，左侧村表按当前帧水深重排');
      } else if (act === 'report') { exportJSON(); }
    });
    var segR = $('seg-right');
    if (segR) segR.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!b) return;
      var act = b.getAttribute('data-act');
      setActive(segR, b);
      if (act === 'export') { exportPNG(); setTimeout(exportJSON, 350); }
      else if (act === 'about') { openModal(); closeLayerPop(); }
      else if (act === 'layer') { openLayerPop(); }
      else if (act === 'compare') { jumpToPeak(); closeLayerPop(); }
    });

    // 底图 chips（矢量 / 影像 / 地形 / 纯色 + 地形底衬开关 + 复位）
    var tools = document.querySelector('.map-tools');
    if (tools) tools.addEventListener('click', function (e) {
      var chip = e.target.closest ? e.target.closest('.chip') : null;
      if (!chip) return;
      // P2：DEM 范围底衬开关
      if (chip.getAttribute('data-toggle') === 'dem') {
        var on = !chip.classList.contains('active');
        chip.classList.toggle('active', on);
        if (PMap.setDemOn) PMap.setDemOn(on);
        toast(on ? '已开启 DEM 范围底衬（0.18 透明度）' : '已关闭 DEM 范围底衬');
        return;
      }
      var b = chip.getAttribute('data-base');
      if (b) {
        setBaseChip(b);
        if (PMap.setBaseMode) PMap.setBaseMode(b);
        S.baseMode = b;
        toast('底图：' + BASE_LABEL[b]);
      }
      if (chip.getAttribute('data-map') === 'reset' && PMap.focusExtent) PMap.focusExtent('all');
    });

    // 图层弹层：台风风场总开关 + 粒子/箭头单选 + 换底图
    var pop = $('layer-pop');
    if (pop) {
      pop.addEventListener('click', function (e) {
        var t = e.target;
        if (t && t.id === 'lp-wind') {
          var on = setWindEnabled(!S.wind.on);
          toast(on ? '台风风场已开启（' + (S.wind.mode === 'arrow' ? '箭头网格' : '粒子流动') + '）' : '台风风场已关闭');
          return;
        }
        if (t && t.id === 'lp-base') { cycleBase(); return; }
        var radio = t && t.closest ? t.closest('input[name="lp-wind-mode"]') : null;
        if (radio) {
          setWindMode(radio.value);
          toast('风场形态：' + (S.wind.mode === 'arrow' ? '箭头网格' : '粒子流动'));
          return;
        }
        var ir = t && t.closest ? t.closest('input[name="lp-wind-int"]') : null;
        if (ir) {
          setWindIntensity(ir.value);
          toast('风场强度：' + WIND_INT_LABEL[S.wind.intensity]);
          return;
        }
      });
      // radio 的 change 事件（键盘/点击都覆盖）
      pop.addEventListener('change', function (e) {
        var t = e.target;
        if (t && t.name === 'lp-wind-mode') {
          setWindMode(t.value);
          toast('风场形态：' + (S.wind.mode === 'arrow' ? '箭头网格' : '粒子流动'));
        }
        if (t && t.name === 'lp-wind-int') {
          setWindIntensity(t.value);
          toast('风场强度：' + WIND_INT_LABEL[S.wind.intensity]);
        }
      });
    }
    // 点击弹层外关闭
    document.addEventListener('click', function (e) {
      var p = $('layer-pop');
      if (!p || !p.classList.contains('open')) return;
      var b = $('btn-layer');
      if (p.contains(e.target) || (b && b.contains(e.target))) return;
      openLayerPop(false);
    }, true);

    // 缩略图视角切换
    var thumbs = document.querySelector('.thumbs');
    if (thumbs) thumbs.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('.thumb') : null;
      if (!t) return;
      var all = thumbs.querySelectorAll('.thumb');
      for (var i = 0; i < all.length; i++) all[i].classList.remove('active');
      t.classList.add('active');
      var v = t.getAttribute('data-view');
      if (PMap.focusExtent) PMap.focusExtent(v === 'coast' ? 'coast' : 'all');
    });

    // 播放 / 时间轴
    var btnPlay = $('btn-play');
    if (btnPlay) btnPlay.addEventListener('click', togglePlay);
    // P3：保留「从头播放」——回到 Hour +000 再连续播放
    var btnRestart = $('btn-restart');
    if (btnRestart) btnRestart.addEventListener('click', restartPlay);
    var rng = $('tl-range');
    if (rng) {
      rng.addEventListener('input', function () {
        if (S.playing) pause();
        goToFrame(Number(rng.value));
      });
    }

    // 曲线 tab
    var tabs = $('chart-tabs');
    if (tabs) tabs.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.tab') : null;
      if (!b) return;
      var all = tabs.querySelectorAll('.tab');
      for (var i = 0; i < all.length; i++) all[i].classList.remove('on');
      b.classList.add('on');
      if (PCharts.setMode) PCharts.setMode(b.getAttribute('data-series') || 'all');
    });

    // 台风下拉
    var sel = $('typhoon-select');
    if (sel) sel.addEventListener('change', function () {
      pickTyphoon(sel.value).then(function () { return runSimulate({ manual: false }); });
    });

    // 参数滑块
    var items = document.querySelectorAll('.p-item');
    for (var i = 0; i < items.length; i++) {
      (function (item) {
        var key = item.getAttribute('data-k');
        var input = item.querySelector('input[type=range]');
        var pv = item.querySelector('.pv');
        if (!key || !input) return;
        input.addEventListener('input', function () {
          var v = Number(input.value);
          if (pv) pv.textContent = (PARAM_FMT[key] || String)(v);
          setRangeFill(input);
          applyParam(key, v, true);
        });
      })(items[i]);
    }
    // coarse_amp 开关
    var chips = document.querySelectorAll('.p-item[data-k="coarse_amp"] .switch-row .chip');
    for (var c = 0; c < chips.length; c++) {
      (function (chip) {
        chip.addEventListener('click', function () {
          var v = Number(chip.getAttribute('data-amp'));
          var row = chip.parentNode.querySelectorAll('.chip');
          for (var k = 0; k < row.length; k++) row[k].classList.remove('active');
          chip.classList.add('active');
          applyParam('coarse_amp', v, true);
          var pv = chip.parentNode.parentNode.querySelector('.pv');
          if (pv) pv.textContent = U.num(v, 1);
        });
      })(chips[c]);
    }

    // 计算内核切换（numpy 复算 / 原 POSSIM Fortran 内核）
    var kChips = document.querySelectorAll('#kernel-row .chip');
    for (var kc = 0; kc < kChips.length; kc++) {
      (function (chip) {
        chip.addEventListener('click', function () {
          var v = chip.getAttribute('data-kernel') || 'numpy';
          if (v === S.kernel) return;
          for (var k = 0; k < kChips.length; k++) kChips[k].classList.remove('active');
          chip.classList.add('active');
          S.kernel = v;
          var pv = $('kernel-pv');
          if (pv) pv.textContent = (v === 'fortran' ? 'fortran' : 'numpy');
          S.busy = false;
          runSimulate({ manual: true });
          toast(v === 'fortran' ? '已切到原 POSSIM Fortran 内核，正在重算（约 7 s）' : '已切回 numpy 复算');
        });
      })(kChips[kc]);
    }

    // 抽屉动作
    var drawer = $('drawer');
    if (drawer) drawer.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-act]') : null;
      if (!b) return;
      var act = b.getAttribute('data-act');
      if (act === 'close-drawer') closeDrawer();
      else if (act === 'resim') { closeDrawer(); runSimulate({ manual: true }); }
      else if (act === 'reset-params') {
        syncDrawer(true);
        renderTideCells();
        closeDrawer();
        runSimulate({ manual: true });
      }
    });

    // 关于模态
    var modal = $('modal-mask');
    if (modal) modal.addEventListener('click', function (e) {
      if (e.target === modal) { closeModal(); return; }
      var b = e.target.closest ? e.target.closest('[data-act]') : null;
      if (b && b.getAttribute('data-act') === 'close-modal') closeModal();
    });

    // 错误条重试
    var retry = $('btn-retry');
    if (retry) retry.addEventListener('click', function () { boot(true); });

    // 键盘：空格播放/暂停，左右步进
    document.addEventListener('keydown', function (e) {
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
      else if (e.code === 'ArrowRight') { pause(); goToFrame(S.idx + 1); }
      else if (e.code === 'ArrowLeft') { pause(); goToFrame(S.idx - 1); }
      else if (e.code === 'Escape') { closeDrawer(); closeModal(); }
    });

    // 自适应缩放
    var rt = null;
    window.addEventListener('resize', function () {
      fitStage();
      if (rt) clearTimeout(rt);
      rt = setTimeout(function () {
        if (PMap.invalidate) PMap.invalidate();
        if (PCharts.resize) PCharts.resize();
        if (PWIND.resize) PWIND.resize();
      }, 120);
    });
  }

  /* ---------------- 图层弹层 / 台风风场开关（契约 §10.3） ---------------- */
  function openLayerPop(on) {
    var pop = $('layer-pop'), btn = $('btn-layer');
    if (!pop) return;
    var v = (on === undefined) ? !pop.classList.contains('open') : !!on;
    pop.classList.toggle('open', v);
    pop.setAttribute('aria-hidden', v ? 'false' : 'true');
    if (btn) btn.classList.toggle('active', v);
  }
  function closeLayerPop() { openLayerPop(false); }

  function setWindEnabled(on) {
    S.wind.on = !!on;
    var sw = $('lp-wind');
    if (sw) {
      sw.classList.toggle('on', S.wind.on);
      sw.setAttribute('aria-checked', S.wind.on ? 'true' : 'false');
    }
    if (PWIND.setEnabled) PWIND.setEnabled(S.wind.on);
    return S.wind.on;
  }

  function setWindMode(m) {
    S.wind.mode = (m === 'arrow') ? 'arrow' : 'particle';
    var rs = document.querySelectorAll('input[name="lp-wind-mode"]');
    for (var i = 0; i < rs.length; i++) rs[i].checked = (rs[i].value === S.wind.mode);
    if (PWIND.setMode) PWIND.setMode(S.wind.mode);
    return S.wind.mode;
  }

  /* 风场强度：淡 / 中（默认）/ 浓 —— 只改笔迹不透明度，配色与粒子数不变 */
  var WIND_INT_LABEL = { light: '淡', mid: '中', strong: '浓' };
  function setWindIntensity(v) {
    S.wind.intensity = (v === 'light' || v === 'strong') ? v : 'mid';
    var rs = document.querySelectorAll('input[name="lp-wind-int"]');
    for (var i = 0; i < rs.length; i++) rs[i].checked = (rs[i].value === S.wind.intensity);
    if (PWIND.setIntensity) PWIND.setIntensity(S.wind.intensity);
    return S.wind.intensity;
  }

  function setActive(seg, btn) {
    var all = seg.querySelectorAll('button');
    for (var i = 0; i < all.length; i++) all[i].classList.remove('active');
    btn.classList.add('active');
  }
  function setBaseChip(kind) {
    var chips = document.querySelectorAll('.map-tools .chip[data-base]');
    for (var i = 0; i < chips.length; i++) {
      chips[i].classList.toggle('active', chips[i].getAttribute('data-base') === kind);
    }
  }
  /* P6：默认影像底图换成 ArcGIS World_Imagery（WGS84，与淹没栅格对齐），高德影像降为兜底 */
  var BASE_LABEL = { img: 'ArcGIS 影像', vec: '高德矢量', dem: 'DEM 地形晕渲', none: '纯色' };
  var baseOrder = ['img', 'vec', 'dem', 'none'];
  var baseIdx = 0;
  function cycleBase() {
    baseIdx = (baseIdx + 1) % baseOrder.length;
    var k = baseOrder[baseIdx];
    setBaseChip(k);
    if (PMap.setBaseMode) PMap.setBaseMode(k);
    S.baseMode = k;
    toast('底图切换：' + BASE_LABEL[k]);
  }

  /* P3：从头播放（0 帧起播，2 s/帧） */
  function restartPlay() {
    if (!S.frames.length) { toast('尚未推演', 'warn'); return; }
    pause();
    goToFrame(0);
    play();
  }
  function jumpToPeak() {
    if (!S.frames.length) { toast('尚未推演', 'warn'); return; }
    var best = 0, bv = -1;
    for (var i = 0; i < S.frames.length; i++) {
      var v = Number(S.frames[i].flooded_ha) || 0;
      if (v > bv) { bv = v; best = i; }
    }
    pause();
    goToFrame(best);
    toast('已跳转到淹没面积峰值帧（' + (S.frames[best].label || '') + '，' + U.area(bv) + ' ha）');
  }

  /* ---------------- 启动 ---------------- */
  /* URL 深链：?tid=saola&kernel=fortran&amp=1.0&frame=20  —— 方便把某个具体画面直接分享出去 */
  function parseQuery() {
    var q = {};
    var s = (global.location && global.location.search) || '';
    if (!s) return q;
    s = s.replace(/^\?/, '').split('&');
    for (var i = 0; i < s.length; i++) {
      if (!s[i]) continue;
      var p = s[i].split('=');
      try { q[decodeURIComponent(p[0])] = decodeURIComponent((p[1] || '').replace(/\+/g, ' ')); } catch (e) { }
    }
    return q;
  }
  function applyQuery() {
    var q = parseQuery();
    S._query = q;
    if (q.tid) {
      for (var i = 0; i < S.typhoons.length; i++) {
        if (S.typhoons[i].id === q.tid) { S.tid = q.tid; break; }
      }
    }
    if (q.kernel === 'fortran' || q.kernel === 'numpy') {
      S.kernel = q.kernel;
      var kChips = document.querySelectorAll('#kernel-row .chip');
      for (var k = 0; k < kChips.length; k++) {
        kChips[k].classList.toggle('active', kChips[k].getAttribute('data-kernel') === q.kernel);
      }
      var pv = $('kernel-pv');
      if (pv) pv.textContent = q.kernel;
    }
    if (q.amp) {
      var amp = Number(q.amp);
      if (isFinite(amp) && amp > 0) S.params.coarse_amp = (amp >= 2 ? 3.8 : 1.0);
    }
  }
  function applyFrameQuery() {
    var q = S._query || {};
    if (q.frame === undefined || !S.frames.length) return;
    var idx = (q.frame === 'peak') ? S.peakIdx : Math.round(Number(q.frame));
    if (isFinite(idx) && idx >= 0 && idx < S.frames.length) { pause(); goToFrame(idx); }
  }

  var booting = false;
  function boot(force) {
    if (booting) return Promise.resolve();
    booting = true;
    fitStage();
    if (!force) bindUI();
    demoBadge(false);
    showStatus('busy', '正在连接推演服务…');
    setHealth(false, null);

    API.health().then(function (h) {
      if (h && h.ok) setHealth(true, h);
    });

    return API.getMeta(!!force).then(function (r) {
      S.meta = r.data || {};
      S.mock = !!r.mock;
      demoBadge(S.mock);
      if (S.mock) {
        showErr('无法连接推演服务，请先运行 启动.cmd');
      } else {
        hideErr();
      }
      renderStatic();
      applyQuery();
      S.mapInited = false;
      var firstId = S.tid || ((S.typhoons && S.typhoons.length) ? S.typhoons[0].id : 'mangkhut');
      return pickTyphoon(firstId);
    }).then(function () {
      return runSimulate({ first: true });
    }).then(function () {
      applyFrameQuery();
    }).catch(function (err) {
      var msg = (err && err.message) ? err.message : '初始化失败';
      showErr(msg);
      showStatus('err', '初始化失败');
      if (global.console) console.error('[POSSIM] boot failed:', err);
    }).then(function () {
      booting = false;
      if (PMap.invalidate) PMap.invalidate();
    });
  }

  /* 兜底：任何脚本异常都不允许白屏，统一进错误条 */
  window.addEventListener('error', function (e) {
    var msg = e && e.message ? e.message : '未知脚本错误';
    showErr('页面脚本异常：' + msg);
    if (global.console) console.error('[POSSIM] error:', e);
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    var msg = (r && r.message) ? r.message : String(r || '未知 Promise 异常');
    if (msg && msg.indexOf('无法连接推演服务') === 0) return;  // 已由错误条处理
    showErr('推演异常：' + msg);
    if (global.console) console.error('[POSSIM] rejection:', r);
  });

  /* ---------------- 自检（?selftest=1）：把布局溢出/尺寸结论写进 document.title ---------------- */
  function selftest() {
    setTimeout(function () {
      var rep = { scale: Number(S.scale.toFixed(4)), frames: S.frames.length, idx: S.idx, mock: S.mock, problems: [] };
      function probe(el, tag) {
        if (!el) return;
        var dh = el.scrollHeight - el.clientHeight;
        var dw = el.scrollWidth - el.clientWidth;
        if (dh > 2) rep.problems.push(tag + ':H+' + dh);
        if (dw > 2) rep.problems.push(tag + ':W+' + dw);
      }
      probe(document.getElementById('stage'), 'stage');
      probe(document.querySelector('.col-left'), 'col-left');
      probe(document.querySelector('.col-center'), 'col-center');
      probe(document.querySelector('.col-right'), 'col-right');
      var cards = document.querySelectorAll('.card-in');
      for (var i = 0; i < cards.length; i++) probe(cards[i], 'card' + i);
      var bodies = document.querySelectorAll('.card-body');
      for (var j = 0; j < bodies.length; j++) probe(bodies[j], 'body' + j);
      rep.doc = {
        sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
        sh: document.documentElement.scrollHeight, ch: document.documentElement.clientHeight
      };
      rep.map = (global.PMap && PMap.map && PMap.map()) ? 'leaflet-ok' : 'leaflet-missing';
      // P6：底图主机名 + 瓦片 loaded/err（验收要求）+ 贴边吸附标记状态
      rep.tiles = (global.PMap && PMap.tileStats) ? PMap.tileStats() : null;
      rep.tint = (function () {
        function probe(sel) {
          var t = document.querySelector(sel);
          if (!t) return null;
          var cs = getComputedStyle(t);
          return {
            z: cs.zIndex, blend: cs.mixBlendMode, pe: cs.pointerEvents,
            w: t.offsetWidth, h: t.offsetHeight,
            parent: t.parentNode ? t.parentNode.className : ''
          };
        }
        return { tint: probe('.map-tint'), shade: probe('.map-shade') };
      })();
      rep.center = (function () {
        var c = (S.frames[S.idx] || {}).center || null;
        var tyb = document.querySelector('.tyb'), tye = document.querySelector('.tye');
        return {
          frame: (S.frames[S.idx] || {}).label || '', latlng: c,
          bubbleShown: !!(tyb && tyb.style.display !== 'none'),
          edgeShown: !!(tye && tye.style.display !== 'none'),
          edgeSide: tye ? tye.className.replace(/.*?(side-[nsew]).*/, '$1') : '',
          edgeText: tye ? (tye.textContent || '').replace(/\s+/g, ' ').trim() : ''
        };
      })();
      rep.drawerInit = (function () {
        var o = {};
        var items = document.querySelectorAll('.p-item');
        for (var i = 0; i < items.length; i++) {
          var k = items[i].getAttribute('data-k');
          var inp = items[i].querySelector('input[type=range]');
          if (k && inp) o[k] = Number(inp.value);
        }
        return o;
      })();
      rep.toastText = (document.getElementById('toast') || {}).textContent || '';
      // P6 自检：中心在 bbox 内走常规气泡、框外走贴边吸附 —— 两条路径都要能切（同步 setFrame，跑完还原）
      try {
        var real = S.frames[S.idx];
        var fake = {};
        for (var fk in real) if (Object.prototype.hasOwnProperty.call(real, fk)) fake[fk] = real[fk];
        fake.center = [22.60, 113.70];            // bbox 中心（框内）
        PMap.setFrame(fake, S.typhoon);
        var tb = document.querySelector('.tyb'), te = document.querySelector('.tye');
        rep.centerInside = {
          bubbleShown: !!(tb && tb.style.display !== 'none'),
          edgeShown: !!(te && te.style.display !== 'none')
        };
        PMap.setFrame(real, S.typhoon);           // 还原真实帧
      } catch (e2) { rep.centerInside = 'probe-failed'; }
      rep.chart = (global.PCharts && PCharts.chart && PCharts.chart()) ? 'echarts-ok' : 'echarts-missing';
      rep.indicator = (document.getElementById('ind-surge-v') || {}).textContent || '';
      rep.villageRows = document.querySelectorAll('.v-row').length;
      // 几何校验：错误条与右上指标块不得重叠；抽屉必须贴在 1920 右边界内
      var eb = document.getElementById('errbar');
      var ir = document.querySelector('.ind-row');
      if (eb && ir) {
        var rb = eb.getBoundingClientRect(), ri = ir.getBoundingClientRect();
        rep.errbar_right = Math.round(rb.right);
        rep.indrow_left = Math.round(ri.left);
        rep.overlap = rb.right > ri.left + 1 && rb.top < ri.bottom && rb.bottom > ri.top;
      }
      var dr = document.getElementById('drawer');
      if (dr) {
        var rd2 = dr.getBoundingClientRect();
        rep.drawer = { offsetLeft: dr.offsetLeft, offsetWidth: dr.offsetWidth, open: dr.classList.contains('open') };
        rep.drawer_right = Math.round(rd2.right);
        rep.stage_right = Math.round(document.getElementById('stage').getBoundingClientRect().right);
        rep.viewport = [window.innerWidth, window.innerHeight];
      }
      /* ---- 图例/浮层几何（契约 §10.4：风速图例不得与淹没图例/缩略图/指标块重叠） ---- */
      function rectOf(sel) {
        var e = (sel === '.mapwrap') ? document.querySelector('.mapwrap') : document.querySelector('.mapwrap ' + sel);
        if (!e) e = document.querySelector(sel);
        if (!e) return null;
        var r = e.getBoundingClientRect();
        if (!r.width && !r.height) return null;
        return { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) };
      }
      function hit(a, b) { return !!(a && b) && a.l < b.r - 1 && a.r > b.l + 1 && a.t < b.b - 1 && a.b > b.t + 1; }
      rep.geo = (function () {
        var o = {};
        o.map = rectOf('.mapwrap');
        o.floodLegend = rectOf('.legend:not(.legend-wind)');
        o.windLegend = rectOf('.legend-wind');
        o.thumbs = rectOf('.thumbs');
        o.mapTools = rectOf('.map-tools');
        o.indRow = rectOf('.ind-row');
        o.gapLegend = (o.floodLegend && o.windLegend) ? (o.windLegend.t - o.floodLegend.b) : null;
        o.overlap = {
          floodLegend_vs_windLegend: hit(o.floodLegend, o.windLegend),
          legend_vs_thumbs: (hit(o.floodLegend, o.thumbs) || hit(o.windLegend, o.thumbs)),
          legend_vs_mapTools: (hit(o.floodLegend, o.mapTools) || hit(o.windLegend, o.mapTools)),
          legend_vs_indRow: (hit(o.floodLegend, o.indRow) || hit(o.windLegend, o.indRow))
        };
        var m = o.map, w = o.windLegend;
        o.windLegendInsideMap = !!(m && w) && w.b <= m.b + 1 && w.t >= m.t - 1 && w.l >= m.l - 1 && w.r <= m.r + 1;
        o.thumbs_right_to_windLegend_left = (o.thumbs && w) ? (w.l - o.thumbs.r) : null;
        return o;
      })();

      /* ---- 风场自检：帧耗时基准 → 37 帧 sweep → 关开关验证 rAF 停止 + canvas 清空 ---- */
      function fin(windRep) {
        rep.wind = windRep;
        // 不被「回参重算」自检（SELFTEST2）的结果覆盖
        if (String(document.title || '').indexOf('SELFTEST2') === 0) return;
        document.title = 'SELFTEST ' + JSON.stringify(rep);
      }
      if (!PWIND.available || !PWIND.available()) { fin('wind-not-mounted'); return; }
      /* 帧数据就绪后再跑风场基准（真实时钟下 simulate 可能晚于本自检） */
      (function waitFrames(cb) {
        var t0 = Date.now();
        (function poll() {
          if ((S.frames && S.frames.length && S.sim) || (Date.now() - t0) > 20000) { cb(); return; }
          setTimeout(poll, 200);
        })();
      })(runWindSelfTest);

      function runWindSelfTest() {
      var probe0 = PWIND.probe();
      var windDone = false;
      function windReport(b) {
        if (windDone) return;
        windDone = true;
        var wr;
        try {
          wr = {
            mount: probe0,
            rafBench: b,
            syncBench: PWIND.benchSync ? PWIND.benchSync(30) : null,
            // 37 帧逐帧切换（含风速网格重建 + 一帧绘制）的真实开销
            sweep: PWIND.sweep ? PWIND.sweep(goToFrame, S.frames.length) : null,
            peakLabel: '',
            indicator: ''
          };
          goToFrame(S.peakIdx);
          wr.playing = PWIND.probe();
          wr.peakLabel = (S.frames[S.idx] || {}).label || '';
          wr.indicator = (document.getElementById('ind-surge-v') || {}).textContent || '';
          var cen = ((S.frames[S.peakIdx] || {}).center) || [22.6, 113.7];
          wr.heading_000 = PWIND.sampleAt(cen[0] + 0.45, cen[1] + 0.45, { heading_deg: 0, motion_kt: 20 });
          wr.heading_180 = PWIND.sampleAt(cen[0] + 0.45, cen[1] + 0.45, { heading_deg: 180, motion_kt: 20 });
        } catch (e5) { wr = { error: String((e5 && e5.message) || e5) }; }
        setWindEnabled(false);
        setTimeout(function () {
          try {
            var off = PWIND.probe();
            wr.afterOff = off;
            wr.canvasClearedOnOff = off.alphaSum === 0;
            wr.rafStoppedOnOff = off.running === false;
          } catch (e6) { wr.afterOff = String((e6 && e6.message) || e6); }
          setWindEnabled(true);
          if (S.wind.mode === 'arrow') setWindMode('arrow');
          fin(wr);
        }, 340);
      }
      // 兜底：headless 下 rAF 可能被节流，9 s 后无论如何都写出结论
      setTimeout(function () { windReport(null); }, 9000);
      (PWIND.bench ? PWIND.bench(30) : Promise.resolve(null)).then(function (b) { windReport(b); },
        function () { windReport(null); });
      }
    }, 2500);
  }

  global.PAPP = {
    state: S,
    boot: boot,
    goToFrame: goToFrame,
    play: play,
    pause: pause,
    fitStage: fitStage
  };

  /* 自检二段：?selftest=1&act=resim 时自动改 Vmax 重算，把差异写进 title（验证"调参真算 + 提示条"） */
  function selftestResim() {
    setTimeout(function () {
      var before = JSON.parse(JSON.stringify((S.sim && S.sim.summary) || {}));
      var pkp = peakTrackPoint(S.typhoon) || (S.typhoon && S.typhoon.track && S.typhoon.track[0]) || {};
      var base = pkp.vmax_kt;   // 抽屉初值口径 = 路径峰值
      S.overrides.vmax_kt = 140;
      S.dirty.vmax_kt = true;
      var pv = document.querySelector('.p-item[data-k="vmax_kt"] .pv');
      if (pv) pv.textContent = '140';
      runSimulate({ manual: true }).then(function (res) {
        setTimeout(function () {
          var peakSurge = 0, peakArea = 0;
          for (var i = 0; i < S.frames.length; i++) {
            peakSurge = Math.max(peakSurge, Number(S.frames[i].max_surge) || 0);
            peakArea = Math.max(peakArea, Number(S.frames[i].flooded_ha) || 0);
          }
          var t = document.getElementById('toast') || {};
          document.title = 'SELFTEST2 ' + JSON.stringify({
            ok: !!res, base_vmax: base, override_vmax: S.overrides.vmax_kt,
            before_summary: before, after_summary: (S.sim && S.sim.summary) || {},
            peak_surge: Number(peakSurge.toFixed(2)), peak_area: Number(peakArea.toFixed(1)),
            toast: t.textContent || '', toastClass: t.className || '',
            frames: S.frames.length,
            export_png: (function () {
              var u = API.frameImage ? API.frameImage(S.frames[S.idx]) : '';
              return { prefix: u.slice(0, 22), bytes: u.length };
            })(),
            export_json_bytes: JSON.stringify({ summary: S.sim.summary, params: buildPayload().params }).length
          });
        }, 700);
      });
    }, 2600);
  }

  /* ---------------- 风场 URL 钩子（自检/截图用，正常访问不受影响） ----------------
     ?wind=off          关闭风场开关
     ?wind=arrows       箭头网格（实时）
     ?wind=hold         粒子模式：预热 N 步后"定帧"，保证 headless 静态截图能拍到形态
     ?wind=holdarrows   箭头网格 + 定帧
     &hour=NN           先跳到 Hour +0NN 的帧（默认落在峰值帧 Hour +020）
     &windsteps=N       预热步数（默认 240 步 ≈ 4 s 动画量）
     &windintensity=light|mid|strong   风场强度档位（默认中） */
  function windOpts() {
    var q = '';
    try { q = location.search || ''; } catch (e) { q = ''; }
    var o = { on: true, mode: 'particle', hold: false, hour: null, steps: 240, q: q };
    if (/[?&]wind=off/.test(q)) o.on = false;
    var wi = /[?&]windintensity=(light|mid|strong)/.exec(q);
    if (wi) o.intensity = wi[1];
    if (/[?&]wind=arrows/.test(q) || /[?&]wind=holdarrows/.test(q)) o.mode = 'arrow';
    if (/[?&]wind=hold/.test(q) || /[?&]wind=holdarrows/.test(q)) o.hold = true;
    var m = /[?&]hour=(\d+)/.exec(q);
    if (m) o.hour = Number(m[1]);
    var s = /[?&]windsteps=(\d+)/.exec(q);
    if (s) o.steps = Number(s[1]);
    var v = /[?&]view=(coast|all)/.exec(q);
    if (v) o.view = v[1];
    var pd = /[?&]probeDelay=(\d+)/.exec(q);
    if (pd) o.probeDelay = Number(pd[1]);
    return o;
  }
  function applyWindOpts(o) {
    if (!o || !PWIND.available || !PWIND.available()) return;
    if (o.mode === 'arrow') setWindMode('arrow');
    if (o.intensity) setWindIntensity(o.intensity);
    setWindEnabled(o.on);
    if (o.hour !== null && S.frames.length) {
      var step = stepHours() || 1;
      var idx = Math.round(o.hour / step);
      if (idx >= 0 && idx < S.frames.length) { pause(); goToFrame(idx); }
    }
    if (o.hold && o.on && PWIND.hold) PWIND.hold(o.steps);
    // 视角漂移测试：&view=coast 触发一次真实的 pan+zoom 动画
    if (o.view && PMap.focusExtent) setTimeout(function () { PMap.focusExtent(o.view); }, 200);
    if (/[?&]probe=wind/.test(o.q || '')) {
      setTimeout(function () {
        document.title = 'WINDPROBE ' + JSON.stringify({ url: o.q, view: o.view || '', probe: PWIND.probe() });
      }, o.probeDelay || 700);
    }
  }

  function start() {
    fitStage();
    var bootP = boot();
    try {
      var wo = windOpts();
      if (bootP && bootP.then) bootP.then(function () { try { applyWindOpts(wo); } catch (e3) { /* 钩子失败不影响主流程 */ } });
    } catch (e4) { /* ignore */ }
    try {
      if (/[?&]selftest=1/.test(location.search)) {
        selftest();
        if (/[?&]panel=params/.test(location.search)) {
          setTimeout(function () {
            // 自检时关掉过渡（headless 下 CSS 过渡时钟不可靠），只验证布局几何
            var dr = $('drawer');
            if (dr) dr.style.transition = 'none';
            openDrawer();
          }, 2000);
        }
        if (/[?&]panel=layer/.test(location.search)) {
          setTimeout(function () { try { openLayerPop(true); } catch (e7) { } }, 2100);
        }
        if (/[?&]act=resim/.test(location.search)) selftestResim();
      }
    } catch (err) { /* 自检不影响正常渲染 */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})(window);
