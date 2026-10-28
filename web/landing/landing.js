/* ==========================================================================
   POSSIM-Web · 项目介绍页脚本
   1) 导航滚动态  2) Hero 粒子风场  3) 从 manifest 渲染情景卡与截图  4) 滚动入场
   无框架、无依赖；数据来自构建产物 data/build/manifest.json
   ========================================================================== */
(function () {
  'use strict';

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- 导航 ---------------- */
  var nav = document.getElementById('nav');
  function onScroll() {
    if (!nav) return;
    nav.classList.toggle('solid', window.scrollY > 24);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------------- Hero 粒子风场 ---------------- */
  (function windCanvas() {
    var cv = document.getElementById('hero-canvas');
    if (!cv || !cv.getContext) return;
    var ctx = cv.getContext('2d');
    var W = 0, H = 0, dpr = 1;
    var parts = [];
    var raf = 0, running = false, t = 0;
    var eye = { x: .5, y: .46 };

    function resize() {
      var host = cv.parentNode;
      dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      W = host.clientWidth; H = host.clientHeight;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      seed();
    }

    function seed() {
      var n = Math.round(Math.min(820, Math.max(220, W * H / 2200)));
      parts = [];
      for (var i = 0; i < n; i++) {
        parts.push({
          x: Math.random() * W,
          y: Math.random() * H,
          life: Math.random() * 120,
          max: 90 + Math.random() * 140,
          w: .6 + Math.random() * 1.1
        });
      }
    }

    /* 简化涡旋：切向速度随半径衰减，叠加向心分量，制造台风式环流 */
    function field(x, y) {
      var cx = W * eye.x, cy = H * eye.y;
      var dx = x - cx, dy = y - cy;
      var r = Math.sqrt(dx * dx + dy * dy) + 1e-3;
      var R = Math.min(W, H) * .46;
      var vt = 120 * (r / R) / (1 + Math.pow(r / R, 2)) * 2.6;
      var vx = -dy / r * vt - dx / r * vt * .22;
      var vy = dx / r * vt - dy / r * vt * .22;
      // 缓慢东移的副热带高压引导气流
      vx += 16 + Math.sin(t * .0006 + y * .004) * 7;
      vy += Math.cos(t * .0005 + x * .003) * 6;
      return [vx, vy];
    }

    function step() {
      t += 16;
      // 尾迹：低透明明暗叠加，形成长曝光感
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = 'rgba(4,16,31,0.075)';
      ctx.fillRect(0, 0, W, H);

      ctx.globalCompositeOperation = 'lighter';
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        var v = field(p.x, p.y);
        var nx = p.x + v[0] * .016;
        var ny = p.y + v[1] * .016;
        var sp = Math.sqrt(v[0] * v[0] + v[1] * v[1]);
        var k = Math.min(1, sp / 150);
        var r = Math.round(46 + 160 * k);
        var g = Math.round(120 + 120 * k);
        var b = Math.round(200 + 55 * k);
        ctx.strokeStyle = 'rgba(' + r + ',' + g + ',' + b + ',' + (0.16 + 0.46 * k).toFixed(3) + ')';
        ctx.lineWidth = p.w;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(nx, ny);
        ctx.stroke();

        p.x = nx; p.y = ny; p.life++;
        if (p.x < -40 || p.x > W + 40 || p.y < -40 || p.y > H + 40 || p.life > p.max) {
          p.x = Math.random() * W; p.y = Math.random() * H; p.life = 0;
          p.max = 90 + Math.random() * 140;
        }
      }
    }

    function loop() {
      step();
      raf = requestAnimationFrame(loop);
    }
    function start() { if (!running) { running = true; loop(); } }
    function stop() { running = false; if (raf) { cancelAnimationFrame(raf); raf = 0; } }

    resize();
    window.addEventListener('resize', function () { resize(); if (reduce) drawOnce(); });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop(); else if (reduce) return; else start();
    });

    function drawOnce() {
      ctx.fillStyle = 'rgba(4,16,31,1)';
      ctx.fillRect(0, 0, W, H);
      for (var i = 0; i < 40; i++) step();
    }

    if (reduce) { drawOnce(); return; }

    // 只在场时跑，滚出视口就停，避免白烧 CPU
    var hero = document.querySelector('.hero');
    if (window.IntersectionObserver && hero) {
      new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) start(); else stop(); });
      }, { threshold: .05 }).observe(hero);
    } else {
      start();
    }
  })();

  /* ---------------- 滚动入场 ---------------- */
  function observeReveal(nodes) {
    if (!nodes.length) return;
    nodes.forEach(function (n) { if (!n.classList.contains('reveal')) n.classList.add('reveal'); });
    if (reduce || !window.IntersectionObserver) {
      nodes.forEach(function (n) { n.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        var el = e.target;
        var i = Number(el.getAttribute('data-i') || 0);
        setTimeout(function () { el.classList.add('in'); }, i * 90);
        io.unobserve(el);
      });
    }, { threshold: .12, rootMargin: '0px 0px -8% 0px' });
    nodes.forEach(function (n) { io.observe(n); });
  }

  /* ---------------- 数据渲染 ---------------- */
  function num(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return '--';
    var s = Number(v).toFixed(digits || 0);
    var p = s.split('.');
    p[0] = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return p.join('.');
  }

  var TY_META = {
    mangkhut: { name: '山竹', en: 'MANGKHUT', year: 2018 },
    saola: { name: '苏拉', en: 'SAOLA', year: 2023 },
    hato: { name: '天鸽', en: 'HATO', year: 2017 }
  };
  var AMP_HINT = {
    '3.8': '对齐原厂在粗网格上的经验放大，观感接近 POSSIM 桌面版',
    '1.0': '不放大，纯物理档：增水更保守，淹没面积落到千公顷量级'
  };

  var MANIFEST = null;

  function renderCases(amp) {
    var grid = document.getElementById('case-grid');
    if (!grid || !MANIFEST) return;
    amp = amp || '3.8';
    var cases = (MANIFEST.cases || []).filter(function (c) {
      return c.kernel === 'numpy' && c.amp === amp;
    });
    if (!cases.length) {
      grid.innerHTML = '<p style="color:#7ba7c9;font-size:13px;">静态数据包尚未生成，请先运行 <code>python scripts/build_site.py</code>。</p>';
      return;
    }
    var order = ['mangkhut', 'saola', 'hato'];
    cases.sort(function (a, b) { return order.indexOf(a.tid) - order.indexOf(b.tid); });
    var maxArea = Math.max.apply(null, cases.map(function (c) { return c.peak.area_ha || 0; })) || 1;

    var html = '';
    cases.forEach(function (c, i) {
      var m = TY_META[c.tid] || { name: c.name, en: c.tid, year: c.year };
      var p = c.peak || {};
      var pct = Math.max(6, Math.min(100, (p.area_ha || 0) / maxArea * 100));
      html +=
        '<article class="case reveal in" data-i="' + i + '">' +
          '<div class="case-in">' +
            '<div class="case-top">' +
              '<div class="case-name">' + m.name + '<small>' + m.en + ' · ' + c.tid + '</small></div>' +
              '<div class="case-year">' + (m.year || '') + '</div>' +
            '</div>' +
            '<div class="case-metrics">' +
              '<div class="case-metric"><span>峰值增水</span><b>' + num(p.surge_m, 2) + '<em>m</em></b></div>' +
              '<div class="case-metric hi"><span>最大淹没</span><b>' + num(p.area_ha, 0) + '<em>ha</em></b></div>' +
              '<div class="case-metric"><span>最大水深</span><b>' + num(p.depth_m, 2) + '<em>m</em></b></div>' +
              '<div class="case-metric"><span>影响行政村</span><b>' + num(p.villages, 0) + '<em>个</em></b></div>' +
            '</div>' +
            '<div class="case-bar"><i style="width:' + pct.toFixed(1) + '%"></i></div>' +
            '<div class="case-sub">峰值出现在 <b>' + (p.area_at || '--') + '</b> · 影响人口 <b>' + num(p.pop, 0) + '</b> 人</div>' +
            '<a class="case-go" href="app.html?tid=' + c.tid + '&#38;amp=' + amp + '">进入这一场' +
              '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M12 5l7 7-7 7"/></svg>' +
            '</a>' +
          '</div>' +
        '</article>';
    });
    grid.innerHTML = html;

    var fast = cases.reduce(function (m, c) { return Math.min(m, (c.elapsed_ms || 1e9)); }, 1e9);
    var el = document.getElementById('stat-fastest');
    if (el && isFinite(fast) && fast < 1e9) {
      el.innerHTML = (fast / 1000).toFixed(1) + '<span>s</span>';
    }
    var hint = document.getElementById('amp-hint');
    if (hint) hint.textContent = AMP_HINT[amp] || '';
  }

  /* ---------------- 参数响应曲线（Vmax → 增水 / 面积） ---------------- */
  function renderSweep(sens) {
    var svg = document.getElementById('sweep-svg');
    if (!svg || !sens || !sens.rows || sens.rows.length < 2) return;
    var rows = sens.rows;
    var W = 720, H = 348;
    var padL = 58, padR = 62, padT = 30, padB = 68;
    var iw = W - padL - padR, ih = H - padT - padB;

    var sVals = rows.map(function (r) { return r.surge_m || 0; });
    var aVals = rows.map(function (r) { return r.area_ha || 0; });
    function rng(arr, pad) {
      var lo = Math.min.apply(null, arr), hi = Math.max.apply(null, arr);
      var d = (hi - lo) || 1;
      return [lo - d * pad, hi + d * pad];
    }
    var sR = rng(sVals, .18), aR = rng(aVals, .18);

    function X(i) { return padL + iw * (i / (rows.length - 1)); }
    function Ys(v) { return padT + ih * (1 - (v - sR[0]) / (sR[1] - sR[0])); }
    function Ya(v) { return padT + ih * (1 - (v - aR[0]) / (aR[1] - aR[0])); }

    var s = '';
    // 网格 + 左轴刻度（增水）
    var ticks = 4;
    for (var t = 0; t <= ticks; t++) {
      var y = padT + ih * (t / ticks);
      var sv = sR[1] - (sR[1] - sR[0]) * (t / ticks);
      var av = aR[1] - (aR[1] - aR[0]) * (t / ticks);
      s += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) +
        '" stroke="rgba(23,69,107,.55)" stroke-width="1" stroke-dasharray="3 4"/>';
      s += '<text x="' + (padL - 9) + '" y="' + (y + 3.5).toFixed(1) + '" text-anchor="end" font-size="10" fill="#6f9ab5">' + sv.toFixed(1) + '</text>';
      s += '<text x="' + (W - padR + 9) + '" y="' + (y + 3.5).toFixed(1) + '" text-anchor="start" font-size="10" fill="#8a6f3f">' +
        Math.round(av / 1000) + 'k</text>';
    }
    // 轴标题
    s += '<text x="' + padL + '" y="' + 14 + '" font-size="10.5" fill="#35e0ff" letter-spacing=".5">增水 m</text>';
    s += '<text x="' + (W - padR) + '" y="' + 14 + '" text-anchor="end" font-size="10.5" fill="#ffb020" letter-spacing=".5">面积 ha</text>';

    // 面积（面积填充 + 线）
    var aPts = rows.map(function (r, i) { return X(i).toFixed(1) + ',' + Ya(r.area_ha || 0).toFixed(1); }).join(' ');
    s += '<polyline points="' + aPts + '" fill="none" stroke="#ffb020" stroke-width="1.8" stroke-linejoin="round" opacity=".9"/>';

    // 增水（渐变面积 + 线）
    var sPts = rows.map(function (r, i) { return X(i).toFixed(1) + ',' + Ys(r.surge_m || 0).toFixed(1); }).join(' ');
    var areaPath = 'M' + X(0).toFixed(1) + ',' + (padT + ih) + ' L' + rows.map(function (r, i) {
      return X(i).toFixed(1) + ',' + Ys(r.surge_m || 0).toFixed(1);
    }).join(' L') + ' L' + X(rows.length - 1).toFixed(1) + ',' + (padT + ih) + ' Z';
    s += '<path d="' + areaPath + '" fill="url(#gSurge)" opacity=".55"/>';
    s += '<polyline points="' + sPts + '" fill="none" stroke="#35e0ff" stroke-width="2.2" stroke-linejoin="round"/>';
    s += '<defs><linearGradient id="gSurge" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#35e0ff" stop-opacity=".45"/>' +
      '<stop offset="100%" stop-color="#35e0ff" stop-opacity="0"/></linearGradient></defs>';

    // 数据点 + x 轴
    rows.forEach(function (r, i) {
      var x = X(i);
      s += '<circle cx="' + x.toFixed(1) + '" cy="' + Ys(r.surge_m || 0).toFixed(1) + '" r="3.4" fill="#04101f" stroke="#35e0ff" stroke-width="2"/>';
      s += '<circle cx="' + x.toFixed(1) + '" cy="' + Ya(r.area_ha || 0).toFixed(1) + '" r="2.6" fill="#ffb020" opacity=".95"/>';
      s += '<text x="' + x.toFixed(1) + '" y="' + (padT + ih + 18) + '" text-anchor="middle" font-size="10.5" fill="#6f9ab5">' + r.vmax_kt + '</text>';
      s += '<title>Vmax ' + r.vmax_kt + ' kt → 增水 ' + (r.surge_m || 0).toFixed(2) + ' m，淹没 ' + Math.round(r.area_ha || 0) + ' ha</title>';
    });
    s += '<text x="' + (padL + iw / 2) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="10.5" fill="#5e88a8" letter-spacing=".4">覆盖整条路径的最大风速 Vmax（kt）</text>';

    svg.innerHTML = s;
  }

  function bindAmpToggle() {
    var box = document.getElementById('amp-toggle');
    if (!box) return;
    box.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-amp]') : null;
      if (!b) return;
      var all = box.querySelectorAll('button');
      for (var i = 0; i < all.length; i++) all[i].classList.remove('on');
      b.classList.add('on');
      renderCases(b.getAttribute('data-amp'));
    });
  }

  function renderGallery(mf) {
    var box = document.getElementById('gallery');
    if (!box || !mf.gallery || !mf.gallery.length) return;
    var html = '';
    mf.gallery.slice(0, 2).forEach(function (g, i) {
      html += '<figure class="shot reveal" data-i="' + (i + 1) + '">' +
        '<img src="' + g.file + '" alt="' + (g.caption || '') + '" loading="lazy">' +
        '<figcaption>' + (g.caption || '') + '</figcaption></figure>';
    });
    box.innerHTML = html;
    observeReveal([].slice.call(box.querySelectorAll('.reveal')));
  }

  function loadManifest() {
    fetch('data/build/manifest.json', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error('manifest ' + r.status); return r.json(); })
      .then(function (mf) {
        MANIFEST = mf;
        renderCases('3.8');
        renderSweep(mf.sensitivity);
        renderGallery(mf);
        bindAmpToggle();
      })
      .catch(function (err) {
        if (window.console) console.warn('[POSSIM] manifest 未就绪：', err);
        var grid = document.getElementById('case-grid');
        if (grid) {
          grid.innerHTML = '<p style="color:#7ba7c9;font-size:13px;line-height:1.9;">' +
            '静态数据包未加载（' + (err && err.message ? err.message : '文件缺失') + '）。<br>' +
            '本地开发时先执行 <code>python scripts/build_site.py</code> 生成 <code>data/build/</code>。</p>';
        }
      });
  }
  loadManifest();

  // 静态区块的入场
  observeReveal([].slice.call(document.querySelectorAll('.sec-head, .flow li, .scard, .dt-row, .knote, .ktable, .flowline')));
})();
