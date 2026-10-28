/* ==========================================================================
   POSSIM-Web · static.js
   职责：把「实时推演后端（Python + numpy/Fortran）」替换成「预计算静态数据包」，
        让整套大屏能纯静态托管（GitHub Pages），无需任何服务端。
   启用条件：构建产物在 index.html/app.html 里注入 window.POSSIM_STATIC = true。
   数据约定（由 scripts/build_site.py 生成）：
     data/build/manifest.json                       全部可用工况 + 指标
     data/build/<tid>/<kernel>_<amp>/sim.json       一整套推演结果（帧元数据 + 时序 + summary）
     data/build/<tid>/<kernel>_<amp>/f000.png ...   逐帧淹没栅格（RGBA 叠加图）
   ========================================================================== */
(function (global) {
  'use strict';

  if (!global.POSSIM_STATIC) return;
  var API = global.PAPI;
  if (!API) return;

  var BUILD = 'data/build/';
  var state = { manifest: null, loading: null, meta: null, typhoons: {} };

  function pad3(n) { return ('000' + n).slice(-3); }

  function getJSON(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (res) {
      if (!res.ok) {
        var e = new Error('静态数据缺失：' + url + '（HTTP ' + res.status + '）');
        e.status = res.status;
        e.network = true;
        throw e;
      }
      return res.json();
    }, function () {
      var e = new Error('静态数据加载失败：' + url);
      e.network = true;
      throw e;
    });
  }

  function loadManifest() {
    if (state.manifest) return Promise.resolve(state.manifest);
    if (state.loading) return state.loading;
    state.loading = getJSON(BUILD + 'manifest.json').then(function (m) {
      state.manifest = m;
      return m;
    });
    return state.loading;
  }

  function ampKey(amp) {
    return (Number(amp) >= 2) ? '3.8' : '1.0';
  }

  function findCase(m, tid, kernel, amp) {
    var a = ampKey(amp);
    var list = (m && m.cases) || [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.tid === tid && c.kernel === kernel && String(c.amp) === a) return c;
    }
    // 兜底：同内核 + 同增量档的其他台风不糊弄，直接报错，避免「看起来在算其实没算」
    return null;
  }

  function decorate(sim, c) {
    var dir = c.dir;
    var frames = sim.frames || [];
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      f.png_b64 = '';                       // 不再内联，改走独立文件，浏览器可缓存
      f.img = dir + 'f' + pad3(Number(f.i != null ? f.i : i)) + '.png';
    }
    sim.static = true;
    sim.elapsed_ms = sim.elapsed_ms || c.elapsed_ms || 0;
    sim.source_label = '静态预计算包';
    return sim;
  }

  /* ---------------- 覆盖 PAPI ---------------- */
  API.isStatic = function () { return true; };

  API.health = function () {
    return Promise.resolve({ ok: true, engine: true, dem: true, static: true });
  };

  API.getMeta = function (force) {
    if (state.meta && !force) return Promise.resolve(state.meta);
    return Promise.all([getJSON('data/meta.json'), loadManifest()]).then(function (arr) {
      var m = arr[0] || {};
      var mf = arr[1] || {};
      m.map = m.map || {};
      m.map.preview_png = 'data/dem_preview.webp';
      m.build = { utc: mf.built_utc, cases: (mf.cases || []).length, static: true };
      state.meta = { data: m, mock: false };
      return state.meta;
    });
  };

  API.getTyphoon = function (id) {
    if (state.typhoons[id]) return Promise.resolve(state.typhoons[id]);
    return getJSON('data/typhoons/' + encodeURIComponent(id) + '.json').then(function (t) {
      state.typhoons[id] = { data: t, mock: false };
      return state.typhoons[id];
    });
  };

  API.simulate = function (payload) {
    var p = payload || {};
    var tid = p.typhoon_id || 'mangkhut';
    var kernel = p.kernel === 'fortran' ? 'fortran' : 'numpy';
    var amp = (p.params && p.params.coarse_amp) != null ? p.params.coarse_amp : (p.fortran_amp || 3.8);

    return loadManifest().then(function (m) {
      var c = findCase(m, tid, kernel, amp);
      if (!c) {
        throw new Error('线上演示版未预计算该组合（' + tid + ' / ' + kernel + ' / ' + ampKey(amp) + '）');
      }
      return getJSON(c.dir + 'sim.json').then(function (sim) { return decorate(sim, c); });
    });
  };

  /* 帧叠加图：直接给相对路径，Leaflet 与 <a download> 都能用 */
  API.frameImage = function (frame) {
    if (!frame) return '';
    return frame.img || '';
  };

  API.staticInfo = function () { return state.manifest; };
  API.ampKey = ampKey;

  /* ---------------- 静态模式 UI 调整 ---------------- */
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  ready(function () {
    document.body.classList.add('static-mode');
    var badge = document.getElementById('demo-badge');
    if (badge) {
      badge.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" ' +
        'stroke-width="2.2" stroke-linecap="round"><path d="M4 7h16M4 12h10M4 17h7"/></svg>静态预计算帧';
      badge.classList.add('show', 'is-static');
    }
    // 小屏提示：大屏本体是 1920×1080 桌面版，手机上给一个明确的入口，而不是塞一张看不清的缩略图
    if (window.innerWidth < 980 && !document.getElementById('mobile-gate')) {
      var gate = document.createElement('div');
      gate.id = 'mobile-gate';
      gate.className = 'mobile-gate';
      gate.innerHTML =
        '<div class="mg-card">' +
          '<div class="mg-ico">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">' +
              '<rect x="2.5" y="4" width="19" height="12.5" rx="1.8"/><path d="M8 20h8M12 16.5V20"/></svg>' +
          '</div>' +
          '<h3>这是 1920×1080 的桌面大屏</h3>' +
          '<p>手机屏宽不够铺开三栏（台风态势 / 淹没地图 / 增长曲线）。建议用电脑打开本页；' +
          '也可以先看项目介绍页，里面有三场台风的完整指标与截图。</p>' +
          '<div class="mg-acts">' +
            '<a class="mg-btn primary" href="index.html">看项目介绍</a>' +
            '<button type="button" class="mg-btn" id="mg-view">仍要看缩略</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(gate);
      gate.querySelector('#mg-view').addEventListener('click', function () { gate.remove(); });
    }

    var drawerBody = document.querySelector('#drawer .drawer-body');
    if (drawerBody && !document.getElementById('static-note')) {
      var note = document.createElement('div');
      note.id = 'static-note';
      note.className = 'static-note';
      note.innerHTML = '<b>线上演示版 = 预计算帧</b><br>' +
        '本站 12 组工况（3 场台风 × 2 套内核 × 2 个增幅档）全部离线算好后以静态文件发布，' +
        '所以能零后端运行。可切换「计算内核」和「增幅档」，其余连续参数（Vmax / Pc / 潮汐）' +
        '需在本地 <code>python server.py</code> 下实时重算。';
      drawerBody.insertBefore(note, drawerBody.firstChild);
    }
  });

  if (global.console) {
    console.info('[POSSIM] 静态演示模式：数据来自预计算包 ' + BUILD + 'manifest.json');
  }
})(window);
