/* ==========================================================================
   POSSIM-Web · map.js
   职责：Leaflet 地图 —— 底图切换 / DEM 预览叠加 / 逐帧淹没 PNG / 台风路径与风圈 / 影响村
   pane 顺序（契约 §7.1）：底图 < dem_preview < 淹没PNG < 影响村 < 台风路径与中心
   依赖：window.L（Leaflet 1.9.4 CDN）、window.PAPI（api.js）
   ========================================================================== */
(function (global) {
  'use strict';

  var L = global.L;
  var PAPI = global.PAPI;
  var U = global.PUTIL;

  var map = null;
  var panes = {};
  var meta = null;
  var bounds = null;          // [[S,W],[N,E]]
  var st = {
    tile: null,                 // 当前生效的瓦片底图（高德矢量 / 影像）
    tileVec: null,              // 矢量瓦片（常驻实例，切换不重复建）
    tileImg: null,              // 影像瓦片
    dem: null,                  // DEM 预览叠加（范围底衬，P2）
    flood: null,                // 当前帧淹没 PNG
    edge: null,                 // 推演范围（bbox）发光边框
    baseMode: 'img',            // 'vec' | 'img' | 'dem' | 'none'（默认影像，P1）
    demOn: true,                // 「地形底衬」开关
    bubble: null,               // 台风中心标注气泡
    villages: [],               // [{data, marker}]
    villageLayer: null,
    trackLayer: null,
    windLayer: null,
    centerMarker: null,
    edgeMarker: null,               // 中心在 bbox 外时的贴边吸附标记
    edgeVisible: false,             // 贴边标记当前是否可见
    edgeCenter: null,               // 最近一次贴边标记的台风中心 [lat,lon]（move/zoom 后重算用）
    edgeName: '',                   // 最近一次的台风名（贴边标记文字）
    frame: null,
    track: null,
    demReady: false
  };

  function available() { return !!L; }

  /* ---------------- 初始化 ---------------- */
  function init(m) {
    meta = m;
    bounds = (m.map && m.map.bbox_leaflet) || [[22.20, 113.20], [23.00, 114.20]];

    var el = document.getElementById('map');
    if (!el) return false;
    if (!L) {
      el.innerHTML = '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
        'color:#ffb020;font-size:13px;letter-spacing:1px;">地图库（Leaflet CDN）加载失败，请检查外网连接后刷新</div>';
      return false;
    }

    map = L.map(el, {
      center: (m.map && m.map.center) || [22.60, 113.70],
      zoom: (m.map && m.map.zoom) || 11,
      zoomControl: false,
      attributionControl: false,
      minZoom: 8,
      maxZoom: 15,
      preferCanvas: true,
      zoomSnap: 0,            // 允许分数缩放（叠加层可精确铺满，不做整级吸附）
      wheelPxPerZoomLevel: 90
    });

    // 自定义 pane（z-index 严格按契约：底图 < dem_preview < 淹没PNG < 边界 < 影响村 < 台风）
    panes.dem = map.createPane('demPane');       panes.dem.style.zIndex = 250;
    panes.flood = map.createPane('floodPane');   panes.flood.style.zIndex = 350;
    panes.edge = map.createPane('edgePane');     panes.edge.style.zIndex = 400;
    panes.vil = map.createPane('vilPane');       panes.vil.style.zIndex = 450;
    panes.ty = map.createPane('tyPane');         panes.ty.style.zIndex = 550;
    panes.dem.style.pointerEvents = 'none';
    panes.flood.style.pointerEvents = 'none';
    panes.edge.style.pointerEvents = 'none';
    panes.ty.style.pointerEvents = 'none';

    /* 底图源（P6 调整，实测 200）：
         影像 = ArcGIS World_Imagery（EPSG:3857/WGS84 底图，与 WGS84 淹没栅格、村点严格对齐）
                —— 高德影像为 GCJ-02 加偏，与 WGS84 叠图有 200~500 m 偏移，降为二级兜底；
         矢量 = 高德 webrd（style=8，中文地名/道路，仅作"矢量"模式）；
       任一层连续 3 次 tileerror 自动切备用源，备用源再连续 3 次失败则降级 DEM 地形底图。 */
    st.tileVec = makeTileLayer('vec');
    st.tileImg = makeTileLayer('img');
    st.tile = st.tileImg;
    st.tile.addTo(map);
    setBaseClass('img');

    /* 底图暗化遮罩（P6）：绝对定位、指向穿透，z-index 300 夹在瓦片层(200)与淹没层(350)之间，
       只压暗底图，不影响淹没斑块、村庄标签与台风路径（见 style.css 注释）。 */
    ensureTintLayers();

    st.villageLayer = L.layerGroup().addTo(map);
    st.trackLayer = L.layerGroup().addTo(map);
    st.windLayer = L.layerGroup().addTo(map);

    drawDomainEdge(bounds);

    setDemPreview(m.map && m.map.preview_png ? m.map.preview_png : '/api/dem/preview.png', bounds);

    // 以契约 bbox 铺满画面，但留 12px 内边距 —— 这样 P5 的 bbox 青色发光边框
    // 与范围外的影像上下文都能露出来（原先 +0.4 级 cover 会把边框整条裁掉）
    try {
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [12, 12], animate: false, maxZoom: 12 });
    } catch (e) { /* 尺寸未就绪时保留 center/zoom */ }

    // 容器尺寸就绪后刷新一次（遮罩尺寸/反平移也跟着走）
    setTimeout(function () { if (map) { map.invalidateSize(); syncTintLayers(); } }, 60);
    return true;
  }

  /* ---------------- 底图暗化遮罩（P6） ----------------
     必须挂在 .leaflet-map-pane 内部：Leaflet 给地图容器加了 transform，pane 的 z-index
     只在那个层叠上下文里比较，所以遮罩放在 #map 直属子级会永远压在所有 pane（含淹没/标签）之上。
     挂在 pane 内 + z-index 300/301，就能精确夹在"瓦片 200"和"淹没 350"之间；
     再用 containerPointToLayerPoint 反向平移，抵消地图位移，视觉上始终铺满视口。 */
  function ensureTintLayers() {
    var pane = (map.getPane && map.getPane('mapPane')) || map.getContainer();
    var c = map.getContainer();
    ['map-tint', 'map-shade'].forEach(function (cls) {
      var old = c.querySelector('.' + cls);
      if (old && old.parentNode) old.parentNode.removeChild(old);
      var d = document.createElement('div');
      d.className = cls;
      pane.appendChild(d);
    });
    syncTintLayers();
    map.on('move zoom viewreset resize moveend zoomend', syncTintLayers);
  }

  function syncTintLayers() {
    if (!map) return;
    var size = map.getSize();
    var lp = map.containerPointToLayerPoint([0, 0]);
    var list = map.getContainer().querySelectorAll('.map-tint,.map-shade');
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      e.style.width = size.x + 'px';
      e.style.height = size.y + 'px';
      e.style.transform = 'translate3d(' + Math.round(lp.x) + 'px,' + Math.round(lp.y) + 'px,0)';
    }
  }

  /* ---------------- 底图（ArcGIS 影像 = 默认；高德矢量；连续失败自动降级） ---------------- */
  var BASE_SRC = {
    /* 矢量模式：高德 webrd（中文注记），失败切 ArcGIS 街道图 */
    vec: {
      label: '高德矢量',
      url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
      subdomains: ['1', '2', '3', '4'], maxNativeZoom: 18,
      fallback: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
      fallbackLabel: 'ArcGIS 街道图'
    },
    /* 影像模式：默认 ArcGIS World_Imagery（WGS84，与淹没 PNG/村点对齐；
       高德影像 webst 是 GCJ-02 加偏底图，叠图会偏 200~500 m，只作二级兜底） */
    img: {
      label: 'ArcGIS World_Imagery（WGS84）',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      subdomains: ['1', '2', '3', '4'], maxNativeZoom: 18,
      fallback: 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
      fallbackLabel: '高德影像（GCJ-02）'
    }
  };

  /* 用当前 url 反推主机名（验收要报"底图主机名"） */
  function hostOf(url) {
    var m = /^https?:\/\/([^/]+)\//.exec(String(url || ''));
    return m ? m[1] : '';
  }

  /* 一级源连续 3 次 tileerror -> 切二级备用源；备用源再连续 3 次 -> 降级 DEM 地形底图。
     "连续"= 任意一次 tileload 成功即把计数清零，避免偶发抖动误切。 */
  function makeTileLayer(kind) {
    var src = BASE_SRC[kind];
    var layer = L.tileLayer(src.url, {
      subdomains: src.subdomains,
      minZoom: 8, maxZoom: 16, maxNativeZoom: src.maxNativeZoom,
      opacity: 1, pane: 'tilePane', crossOrigin: true,
      keepBuffer: 3, updateWhenIdle: false, detectRetina: false
    });
    var stat = { kind: kind, stage: 0, loaded: 0, err: 0, lastErrUrl: '' };
    layer._stat = stat;
    layer._src = src;
    layer.on('tileload', function () {
      stat.loaded++;
      stat.err = 0;                       // 成功即清零"连续失败"计数
    });
    layer.on('tileerror', function (e) {
      stat.err++;
      try { stat.lastErrUrl = (e && e.tile && e.tile.src) || ''; } catch (x) { stat.lastErrUrl = ''; }
      if (stat.stage >= 2) return;
      if (stat.stage === 0 && stat.err >= 3) {
        stat.stage = 1; stat.err = 0;
        layer.setUrl(src.fallback);
        if (global.console) console.warn('[POSSIM] ' + src.label + ' 连续 3 次瓦片失败，切备用源 ' + src.fallbackLabel);
      } else if (stat.stage === 1 && stat.err >= 3) {
        stat.stage = 2;
        if (global.console) console.warn('[POSSIM] 备用源仍连续失败，降级 DEM 地形底图');
        setBaseMode('dem');
        if (global.PAPP && global.PAPP.state) global.PAPP.state.baseMode = 'dem';
      }
    });
    return layer;
  }

  /* 验收/自检用：当前生效底的加载统计（loaded / err / stage / host） */
  function tileStats() {
    var l = st.tile;
    var stt = (l && l._stat) || { kind: st.baseMode, stage: 0, loaded: 0, err: 0 };
    var url = (l && l._url) || '';
    return {
      mode: st.baseMode, host: hostOf(url) || '(DEM/纯色无瓦片)',
      url: url, stage: stt.stage, loaded: stt.loaded, err: stt.err,
      vec: st.tileVec && st.tileVec._stat ? { loaded: st.tileVec._stat.loaded, err: st.tileVec._stat.err, stage: st.tileVec._stat.stage } : null,
      img: st.tileImg && st.tileImg._stat ? { loaded: st.tileImg._stat.loaded, err: st.tileImg._stat.err, stage: st.tileImg._stat.stage } : null
    };
  }

  function setBaseClass(kind) {
    var c = map ? map.getContainer() : null;
    if (!c || !c.className) return;
    c.className = String(c.className).replace(/\s*base-[a-z]+/g, '');
    c.classList.add('base-' + kind);
  }

  /* P2：DEM 预览默认只做 0.22 透明度的范围底衬（mix-blend-mode: multiply），可一键关闭 */
  function applyDemOpacity() {
    if (!st.dem) return;
    var o = 0;
    if (st.demOn) {
      if (st.baseMode === 'dem') o = 0.95;      // 「地形」底图模式：全量显示
      else if (st.baseMode === 'none') o = 0;   // 「纯色」模式：不显示
      else o = 0.18;                            // 矢量/影像底图：只当范围底衬
    }
    st.dem.setOpacity(o);
    var el = st.dem.getElement ? st.dem.getElement() : null;
    if (el) {
      el.classList.toggle('dem-underlay', o > 0 && st.baseMode !== 'dem');
      el.style.mixBlendMode = '';
    }
  }

  function setBaseMode(kind) {
    if (['vec', 'img', 'dem', 'none'].indexOf(kind) < 0) kind = 'img';
    st.baseMode = kind;
    if (!map) return;
    var want = kind === 'vec' ? st.tileVec : (kind === 'img' ? st.tileImg : null);
    if (st.tile && st.tile !== want && map.hasLayer(st.tile)) map.removeLayer(st.tile);
    if (want) {
      if (!map.hasLayer(want)) want.addTo(map);
      st.tile = want;
    } else {
      st.tile = null;
    }
    setBaseClass(kind);
    applyDemOpacity();
    // 切底图时保证淹没层 / 村庄层 / 台风层仍在最上层（pane z-index 已固定，这里再兜一次顺序）
    ['floodPane', 'vilPane', 'tyPane'].forEach(function (p) {
      var el = map.getPane(p);
      if (el && el.parentNode) el.parentNode.appendChild(el);
    });
    ['edgePane'].forEach(function (p) {
      var el = map.getPane(p);
      if (el && el.parentNode) el.parentNode.appendChild(el);
    });
  }

  /* P1：瓦片去色压暗后，再罩一层深青蓝（普通 alpha 叠加，不依赖 mix-blend-mode）
     只覆盖推演范围 bbox，pan/zoom 由 Leaflet 负责，淹没层/村庄/台风都在其上层 pane。 */
  function setDemOn(on) {
    st.demOn = !!on;
    applyDemOpacity();
  }
  function demOn() { return st.demOn; }

  /* P5：bbox 青色描边发光线（参考图的"流域边界"观感） */
  function drawDomainEdge(b) {
    if (!map || !b) return;
    var S = b[0][0], W = b[0][1], N = b[1][0], E = b[1][1];
    // 三层描边叠出"外发光"（不用 CSS filter，几何确定、缩放不抖）
    L.rectangle(b, { pane: 'edgePane', color: '#35e0ff', weight: 12, opacity: 0.07, fill: false, interactive: false }).addTo(map);
    L.rectangle(b, { pane: 'edgePane', color: '#35e0ff', weight: 5, opacity: 0.20, fill: false, interactive: false }).addTo(map);
    L.rectangle(b, { pane: 'edgePane', color: '#7ef0ff', weight: 1.4, opacity: 0.92, fill: false, interactive: false, dashArray: '11 7' }).addTo(map);
    st.edge = b;
    // 四角加粗短线（角标）
    var dLat = (N - S) * 0.07, dLon = (E - W) * 0.07;
    var segs = [
      [[N, W], [N - dLat, W]], [[N, W], [N, W + dLon]],
      [[N, E], [N - dLat, E]], [[N, E], [N, E - dLon]],
      [[S, W], [S + dLat, W]], [[S, W], [S, W + dLon]],
      [[S, E], [S + dLat, E]], [[S, E], [S, E - dLon]]
    ];
    segs.forEach(function (p) {
      L.polyline(p, { pane: 'edgePane', color: '#b8f6ff', weight: 2.6, opacity: 0.95, interactive: false }).addTo(map);
    });
  }

  /* ---------------- DEM 预览叠加 ---------------- */
  function setDemPreview(url, bnds) {
    if (!map) return;
    var b = bnds || bounds;
    // 先用探测机制确认 png 可用，不可用且是演示态则用 canvas 兜底
    var probe = new Image();
    probe.onload = function () { attachDem(url, b); };
    probe.onerror = function () {
      var fallback = (PAPI && PAPI.isDevHost && PAPI.isDevHost()) ? PAPI.mockDemPreview() : '';
      if (fallback) attachDem(fallback, b);
    };
    probe.src = url;
  }

  function attachDem(url, b) {
    if (!map || !url) return;
    if (st.dem) {
      st.dem.setUrl(url);
    } else {
      st.dem = L.imageOverlay(url, b, { pane: 'demPane', opacity: 0, interactive: false, className: 'dem-layer' });
      st.dem.addTo(map);
    }
    st.demReady = true;
    applyDemOpacity();
    // 地形底图也用作「目标海域」卡缩略图
    var thumb = document.getElementById('sea-thumb-img');
    if (thumb && !thumb.getAttribute('data-set')) {
      thumb.setAttribute('data-set', '1');
      thumb.src = url;
    }
  }

  /* ---------------- 淹没 PNG（逐帧） ---------------- */
  function setFlood(dataUrl, bnds) {
    if (!map || !dataUrl) return;
    var b = bnds || bounds;
    if (st.flood) {
      st.flood.setBounds(b);
      st.flood.setUrl(dataUrl);
      st.flood.setOpacity(1);
      if (!map.hasLayer(st.flood)) st.flood.addTo(map);
    } else {
      st.flood = L.imageOverlay(dataUrl, b, { pane: 'floodPane', opacity: 1, interactive: false });
      st.flood.addTo(map);
    }
  }

  /* ---------------- 影响村 ---------------- */
  function setVillages(villages) {
    if (!map) return;
    st.villageLayer.clearLayers();
    st.villages = [];
    (villages || []).forEach(function (v) {
      var mk = L.circleMarker([v.lat, v.lon], {
        pane: 'vilPane', radius: 4, color: '#0a2a40', weight: 1,
        fillColor: '#35e0ff', fillOpacity: 0.95
      });
      var tip = '<b>' + U.esc(v.name) + '</b><br>人口 ' + U.pop(v.pop) + ' 人 · 户数 ' + U.pop(v.households) +
        '<br>距岸 ' + U.num(v.dist_coast_km, 1) + ' km · 高程 ' + U.num(v.elev_m, 1) + ' m' +
        '<br>策略 ' + U.esc(v.strategy || '—');
      mk.bindPopup(tip, { closeButton: true, autoPan: false });
      if ((Number(v.dist_coast_km) || 99) <= 2.0) {
        mk.bindTooltip(U.esc(v.name), {
          permanent: true, direction: 'right', offset: [5, 0],
          className: 'vil-label', opacity: 0.9
        });
      }
      mk.addTo(st.villageLayer);
      st.villages.push({ data: v, marker: mk });
    });
  }

  // 按当前帧的估算水深给村点上色
  function styleVillages(frame) {
    if (!map) return;
    st.villages.forEach(function (it) {
      var d = U.villageDepth(it.data, frame);
      it.marker.setStyle({
        fillColor: d > 0.001 ? U.depthColor(d) : '#35e0ff',
        radius: d >= 0.5 ? 5.5 : (d >= 0.2 ? 4.6 : 4),
        color: d >= 0.5 ? '#ff5c5c' : '#0a2a40',
        weight: d >= 0.5 ? 1.6 : 1
      });
    });
  }

  /* ---------------- 台风路径 / 风圈 / 中心 ---------------- */
  function nmToDelta(rNm, lat) {
    var m = rNm * 1852;
    var dLat = m / 111320;
    var dLon = m / (111320 * Math.cos(lat * Math.PI / 180) || 111320);
    return { dLat: dLat, dLon: dLon };
  }

  // 四象限风圈（NE,SE,SW,NW）插值成多边形
  function windPolygon(center, radii) {
    if (!center || !radii || radii.length < 4) return null;
    var pts = [];
    for (var b = 0; b < 360; b += 6) {
      var i0 = Math.floor(b / 90) % 4;
      var i1 = (i0 + 1) % 4;
      var f = (b % 90) / 90;
      var r = (Number(radii[i0]) || 0) * (1 - f) + (Number(radii[i1]) || 0) * f;
      if (!(r > 0)) continue;
      var dd = nmToDelta(r, center[0]);
      var rad = b * Math.PI / 180;
      pts.push([center[0] + dd.dLat * Math.cos(rad), center[1] + dd.dLon * Math.sin(rad)]);
    }
    return pts.length >= 3 ? pts : null;
  }

  function setTrack(typhoon) {
    if (!map || !typhoon) return;
    st.track = typhoon;
    st.trackLayer.clearLayers();
    var pts = (typhoon.track || []).map(function (p) { return [p.lat, p.lon]; });
    if (pts.length > 1) {
      L.polyline(pts, {
        pane: 'tyPane', color: '#ff5c5c', weight: 2, opacity: 0.9, dashArray: '7 6',
        lineJoin: 'round'
      }).addTo(st.trackLayer);
    }
    pts.forEach(function (p, i) {
      L.circleMarker(p, {
        pane: 'tyPane', radius: i === pts.length - 1 ? 3.4 : 2.4,
        color: '#ff9a9a', weight: 1, fillColor: '#ff5c5c', fillOpacity: 0.85
      }).addTo(st.trackLayer);
    });
    if (typhoon.landfall && typhoon.landfall.lat) {
      L.circleMarker([typhoon.landfall.lat, typhoon.landfall.lon], {
        pane: 'tyPane', radius: 6, color: '#ffb020', weight: 1.4,
        fillColor: 'rgba(255,176,32,.25)', fillOpacity: 0.8, dashArray: '3 3'
      }).bindTooltip('登陆点（示意）', { direction: 'top', className: 'vil-label' }).addTo(st.trackLayer);
    }
    /* P5：台风中心标注气泡（红点 + 引线 + 文本，纯 DOM/CSS，不用图片）
       P6：另备一个"贴边吸附"标记，中心跑到 bbox 外时用它（见 updateCenter） */
    ensureCenterMarkers(pts.length ? pts[pts.length - 1] : [22.6, 113.7]);
    st.bubble = st.centerMarker;
    updateBubble(null, typhoon);
  }

  function ensureCenterMarkers(ll) {
    if (st.centerMarker) return;
    var icon = L.divIcon({
      className: 'tyb',
      html: '<span class="tyb-dot"></span><span class="tyb-lead"></span>' +
        '<span class="tyb-box"><b id="tyb-name">台风中心</b><i id="tyb-val">--</i></span>',
      iconSize: [0, 0], iconAnchor: [0, 0]
    });
    st.centerMarker = L.marker(ll || [22.6, 113.7], {
      icon: icon, pane: 'tyPane', interactive: false, zIndexOffset: 800, keyboard: false
    }).addTo(map);

    var eicon = L.divIcon({
      className: 'tye',
      html: '<span class="tye-lead"></span><span class="tye-arrow"></span>' +
        '<span class="tye-box"><b class="tye-name">台风中心</b><i class="tye-val">--</i></span>',
      iconSize: [0, 0], iconAnchor: [0, 0]
    });
    st.edgeMarker = L.marker(ll || [22.6, 113.7], {
      icon: eicon, pane: 'tyPane', interactive: false, zIndexOffset: 900, keyboard: false
    }).addTo(map);
    var ee = st.edgeMarker.getElement();
    if (ee) ee.style.display = 'none';
    /* 平移/缩放/尺寸变化后，容器像素坐标系变了 —— 贴边标记必须重算一次，
       否则会停在旧位置的像素上（贴到底边时尤其容易被裁） */
    map.on('moveend zoomend resize', function () {
      if (!st.edgeVisible || !st.edgeCenter) return;
      placeEdgeMarker(st.edgeCenter, st.edgeName);
    });
  }

  function inBbox(lat, lon) {
    var S = bounds[0][0], W = bounds[0][1], N = bounds[1][0], E = bounds[1][1];
    return lat >= S && lat <= N && lon >= W && lon <= E;
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    var R = 6371, rad = Math.PI / 180;
    var dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  /* P6 贴边吸附：从 bbox 中心朝台风中心方向的射线与 bbox 边求交（像素空间求交，
     比例与视野一致），标记落在"台风所在一侧"的那条边上；返回边、锚点像素、箭头角度与距离 */
  function snapToEdge(lat, lon) {
    var S = bounds[0][0], W = bounds[0][1], N = bounds[1][0], E = bounds[1][1];
    if (lat >= S && lat <= N && lon >= W && lon <= E) return null;
    var pc = map.latLngToContainerPoint([(S + N) / 2, (W + E) / 2]);
    var po = map.latLngToContainerPoint([lat, lon]);
    var p0 = map.latLngToContainerPoint([N, W]);
    var p1 = map.latLngToContainerPoint([S, E]);
    var x0 = Math.min(p0.x, p1.x), x1 = Math.max(p0.x, p1.x);
    var y0 = Math.min(p0.y, p1.y), y1 = Math.max(p0.y, p1.y);
    var dx = po.x - pc.x, dy = po.y - pc.y;
    var tx = dx > 0 ? (x1 - pc.x) / dx : (dx < 0 ? (x0 - pc.x) / dx : Infinity);
    var ty = dy > 0 ? (y1 - pc.y) / dy : (dy < 0 ? (y0 - pc.y) / dy : Infinity);
    if (!isFinite(tx) && !isFinite(ty)) return null;
    var t = Math.min(tx, ty);
    var side = (ty <= tx) ? (dy > 0 ? 's' : 'n') : (dx > 0 ? 'e' : 'w');
    var nLat = Math.min(N, Math.max(S, lat)), nLon = Math.min(E, Math.max(W, lon));
    return {
      side: side,
      px: pc.x + dx * t, py: pc.y + dy * t,
      angle: Math.atan2(dx, -dy) * 180 / Math.PI,     // CSS rotate：0=向上，顺时针为正
      distKm: haversineKm(lat, lon, nLat, nLon)      // 中心到 bbox 最近点的真实距离
    };
  }

  /* 地图内需要避让的浮层：右下图例 / 底部两个视角缩略图 / 左下工具条 / 右上指标块 / 左上角标注。
     返回值统一换算成"地图容器像素坐标"（与 map.getSize() / containerPoint 同一坐标系）：
     #stage 用 transform:scale 整体缩放，所以 getBoundingClientRect 的差必须除以真实舞台缩放 k。 */
  function avoidRects() {
    var size = map.getSize();
    var m = map.getContainer().getBoundingClientRect();
    var k = (m.width > 0 && size.x > 0) ? (m.width / size.x) : 1;
    if (!isFinite(k) || !(k > 0)) k = 1;
    var out = [];
    var sels = ['.legend', '.thumbs', '.map-tools', '.ind-row', '.map-caption'];
    for (var i = 0; i < sels.length; i++) {
      var e = document.querySelector('.mapwrap ' + sels[i]);
      if (!e) continue;
      var r = e.getBoundingClientRect();
      if (!r.width && !r.height) continue;
      out.push({
        l: (r.left - m.left) / k, t: (r.top - m.top) / k,
        r: (r.right - m.left) / k, b: (r.bottom - m.top) / k
      });
    }
    return out;
  }

  function rectHit(a, b) {
    return a.l < b.r - 1 && a.r > b.l + 1 && a.t < b.b - 1 && a.b > b.t + 1;
  }

  /* 贴边标记的几何常量 */
  var EDGE_PAD = 18;     // 标记整体距地图容器四边的最小距离（含文字框高度）
  var ARROW_R = 7;       // 箭头图形半径（.tye-arrow 为 14×14、以锚点为中心）
  var EDGE_GAP = 8;      // 箭头 ↔ 文字框的间隙

  function clampNum(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  /* 给定箭头锚点，算文字框落位。框压到浮层时朝容器内侧推（s 往上 / n 往下 / e 往左 / w 往右）；
     推不动（会顶出安全区）返回 null，交给外层换一个锚点。
     返回 {l,t,push}：push = 相对"紧贴箭头"的推移量，用来在候选里挑代价最小的。 */
  function resolveBox(side, ax, ay, bw, bh, avoid, size) {
    var P = EDGE_PAD, G = EDGE_GAP, i, j, hit, nt, nl, rc;
    var l, t, ideal, push = 0;
    if (side === 's' || side === 'n') {
      l = clampNum(ax - bw / 2, P, Math.max(P, size.x - P - bw));
      ideal = (side === 's') ? (ay - G - bh) : (ay + G);
      t = ideal;
      for (i = 0; i < 12; i++) {
        hit = null;
        rc = { l: l, r: l + bw, t: t, b: t + bh };
        for (j = 0; j < avoid.length; j++) {
          if (!rectHit(rc, avoid[j])) continue;
          if (!hit) hit = avoid[j];
          else if (side === 's') { if (avoid[j].t < hit.t) hit = avoid[j]; }
          else if (avoid[j].b > hit.b) hit = avoid[j];
        }
        if (!hit) break;
        nt = (side === 's') ? (hit.t - G - bh) : (hit.b + G);
        if (side === 's' ? (nt >= t - 1) : (nt <= t + 1)) break;   // 推不动，防死循环
        t = nt;
      }
      if (t < P) return null;                        // 顶到容器上沿
      if (t + bh > size.y - P) {                     // 顶到容器下沿（仅 side=n 会走到）
        if (side === 'n') return null;
        t = size.y - P - bh;
      }
      push = Math.abs(t - ideal);
      return { l: l, t: t, push: push };
    }
    t = clampNum(ay - bh / 2, P, Math.max(P, size.y - P - bh));
    ideal = (side === 'e') ? (ax - G - bw) : (ax + G);
    l = ideal;
    for (i = 0; i < 12; i++) {
      hit = null;
      rc = { l: l, r: l + bw, t: t, b: t + bh };
      for (j = 0; j < avoid.length; j++) {
        if (!rectHit(rc, avoid[j])) continue;
        if (!hit) hit = avoid[j];
        else if (side === 'e') { if (avoid[j].l < hit.l) hit = avoid[j]; }
        else if (avoid[j].r > hit.r) hit = avoid[j];
      }
      if (!hit) break;
      nl = (side === 'e') ? (hit.l - G - bw) : (hit.r + G);
      if (side === 'e' ? (nl >= l - 1) : (nl <= l + 1)) break;
      l = nl;
    }
    if (l < P) return null;
    if (l + bw > size.x - P) {
      if (side === 'w') return null;
      l = size.x - P - bw;
    }
    return { l: l, t: t, push: Math.abs(l - ideal) };
  }

  /* 箭头（锚点 ±(ARROW_R+4)）到最近浮层的净距：<0 表示箭头被浮层压住，正值表示还有多少 px 余量。
     贴边标记是"箭头 + 文字框"一个整体，箭头被缩略图/工具条压住同样算不合格。 */
  function arrowClearance(ax, ay, avoid) {
    var r = ARROW_R + 4, d = Infinity;
    for (var i = 0; i < avoid.length; i++) {
      var a = avoid[i];
      var q = Math.max(a.l - (ax + r), (ax - r) - a.r, a.t - (ay + r), (ay - r) - a.b);
      if (q < d) d = q;
    }
    return d === Infinity ? 999 : d;
  }

  /* 引线：文字框被推离箭头时（底边拥挤 → 整框上移），画一条细线连回箭头，避免"框飘在半空" */
  function setLeader(ld, side, ax, ay, bl, bt, bw, bh) {
    if (!ld) return;
    var R = ARROW_R, x, y, w, h, bg, gap;
    if (side === 's' || side === 'n') {
      gap = (side === 's') ? (ay - R - (bt + bh)) : (bt - (ay + R));
      if (!(gap > 4)) { ld.style.display = 'none'; return; }
      x = clampNum(ax, bl + 4, bl + bw - 4) - ax - 0.5; w = 1;
      y = (side === 's') ? (bt + bh - ay) : (ay + R); h = gap;
      bg = (side === 's')
        ? 'linear-gradient(180deg,rgba(255,92,92,.16),rgba(255,92,92,.8))'
        : 'linear-gradient(0deg,rgba(255,92,92,.16),rgba(255,92,92,.8))';
    } else {
      gap = (side === 'e') ? (ax - R - (bl + bw)) : (bl - (ax + R));
      if (!(gap > 4)) { ld.style.display = 'none'; return; }
      y = clampNum(ay, bt + 4, bt + bh - 4) - ay - 0.5; h = 1;
      x = (side === 'e') ? (bl + bw - ax) : (ax + R); w = gap;
      bg = (side === 'e')
        ? 'linear-gradient(90deg,rgba(255,92,92,.16),rgba(255,92,92,.8))'
        : 'linear-gradient(90deg,rgba(255,92,92,.8),rgba(255,92,92,.16))';
    }
    ld.style.display = 'block';      // CSS 里默认 display:none，必须显式覆盖
    ld.style.left = x + 'px';
    ld.style.top = y + 'px';
    ld.style.width = w + 'px';
    ld.style.height = h + 'px';
    ld.style.background = bg;
  }

  /* P6 贴边吸附标记落位（风场契约 v1.2 修正：整框必须落在可见地图容器内）
     1) 箭头锚点先夹进"容器内缩 18px + 箭头半径"的安全区 —— 箭头本身永远不被裁；
     2) 文字框贴在箭头内侧；若压到左下底图工具条 / 底部两个视角缩略图 / 右下图例 / 右上指标块，
        就整框往容器内侧推（底边拥挤时即整框上移），并画引线连回箭头；
     3) 沿边以 16~300px 步长滑动找"不压控件"的位置，代价 = 滑移量 + 0.6×推移量
        （先保"位置真实"，其次才沿边滑动）。全部候选不可行时退化为"只夹进安全区"，仍不越界。 */
  function placeEdgeMarker(c, name) {
    var ee = st.edgeMarker ? st.edgeMarker.getElement() : null;
    if (!ee) return;
    var snap = snapToEdge(c[0], c[1]);
    if (!snap) { st.edgeMarker.setLatLng(c); return; }
    st.edgeCenter = c;
    st.edgeName = name || '台风中心';
    st.edgeVisible = true;
    ee.style.display = '';
    ee.classList.remove('side-n', 'side-s', 'side-e', 'side-w');
    ee.classList.add('side-' + snap.side);
    var arrow = ee.querySelector('.tye-arrow');
    if (arrow) arrow.style.transform = 'rotate(' + snap.angle.toFixed(1) + 'deg)';
    var nb = ee.querySelector('.tye-name') || ee.querySelector('#tye-name');
    var nv = ee.querySelector('.tye-val') || ee.querySelector('#tye-val');
    if (nb) nb.textContent = st.edgeName;
    if (nv) nv.textContent = '在范围外 ' + Math.round(snap.distKm) + ' km';

    var box = ee.querySelector('.tye-box');
    var lead = ee.querySelector('.tye-lead');
    var bw = (box && box.offsetWidth) || 150, bh = (box && box.offsetHeight) || 20;
    var size = map.getSize();
    var avoid = avoidRects();
    var side = snap.side;
    var alongX = (side === 's' || side === 'n');

    var loX = EDGE_PAD + ARROW_R, hiX = size.x - EDGE_PAD - ARROW_R;
    var loY = EDGE_PAD + ARROW_R, hiY = size.y - EDGE_PAD - ARROW_R;
    var ax0 = clampNum(snap.px, loX, hiX), ay0 = clampNum(snap.py, loY, hiY);
    var steps = [0, 16, -16, 32, -32, 50, -50, 72, -72, 98, -98, 128, -128,
                 164, -164, 205, -205, 250, -250, 300, -300];
    var best = null, bestCost = Infinity;
    for (var i = 0; i < steps.length; i++) {
      var ax = clampNum(ax0 + (alongX ? steps[i] : 0), loX, hiX);
      var ay = clampNum(ay0 + (alongX ? 0 : steps[i]), loY, hiY);
      var clear = arrowClearance(ax, ay, avoid);
      if (clear < 0) continue;                       // 箭头被工具条/缩略图压住 → 换位置
      var rc = resolveBox(side, ax, ay, bw, bh, avoid, size);
      if (!rc) continue;
      var cost = Math.abs(steps[i]) + rc.push * 0.6 + Math.max(0, 26 - clear) * 1.6;
      if (cost < bestCost) { bestCost = cost; best = { ax: ax, ay: ay, l: rc.l, t: rc.t }; }
      if (bestCost <= 0) break;
    }
    if (!best) {   /* 极端拥挤：只保证整体落在安全区内 */
      var bl = clampNum(ax0 - bw / 2, EDGE_PAD, Math.max(EDGE_PAD, size.x - EDGE_PAD - bw));
      var bt = (side === 's' || side === 'n')
        ? clampNum(side === 's' ? ay0 - EDGE_GAP - bh : ay0 + EDGE_GAP, EDGE_PAD, Math.max(EDGE_PAD, size.y - EDGE_PAD - bh))
        : clampNum(ay0 - bh / 2, EDGE_PAD, Math.max(EDGE_PAD, size.y - EDGE_PAD - bh));
      best = { ax: ax0, ay: ay0, l: bl, t: bt };
    }
    st.edgeMarker.setLatLng(map.containerPointToLatLng([best.ax, best.ay]));
    if (box) {
      box.style.left = (best.l - best.ax) + 'px';
      box.style.top = (best.t - best.ay) + 'px';
      box.style.right = 'auto';
      box.style.bottom = 'auto';
      box.style.transform = 'none';
    }
    setLeader(lead, side, best.ax, best.ay, best.l, best.t, bw, bh);
  }

  /* 逐帧的中心处理：框内走常规气泡；框外在最近的那条 bbox 边上画吸附标记 */
  function updateCenter(c, frame, typhoon) {
    if (!map || !c) return;
    ensureCenterMarkers(c);
    var ce = st.centerMarker.getElement();
    var ee = st.edgeMarker.getElement();
    if (inBbox(c[0], c[1])) {
      st.centerMarker.setLatLng(c);
      st.edgeVisible = false;
      if (ce) ce.style.display = '';
      if (ee) ee.style.display = 'none';
      updateBubble(frame, typhoon);
    } else {
      if (ce) ce.style.display = 'none';
      placeEdgeMarker(c, (typhoon && typhoon.name) || '台风中心');
    }
  }

  /* 气泡文本：台风名 · Vmax(kt) · 中心气压(hPa) */
  function updateBubble(frame, typhoon) {
    var nm = (typhoon && typhoon.name) ? typhoon.name : '台风中心';
    var vmax = frame ? Math.round(Number(frame.vmax_kt) || 0) : 0;
    var pc = frame ? Math.round(Number(frame.pc_mb) || 0) : 0;
    if (!vmax && typhoon && typhoon.track && typhoon.track.length) {
      vmax = Math.round(Number(typhoon.track[typhoon.track.length - 1].vmax_kt) || 0);
    }
    if (!pc && typhoon && typhoon.track && typhoon.track.length) {
      pc = Math.round(Number(typhoon.track[typhoon.track.length - 1].pc_mb) || 0);
    }
    var n = document.getElementById('tyb-name');
    var v = document.getElementById('tyb-val');
    if (n) n.textContent = nm;
    if (v) v.textContent = (vmax ? vmax + 'kt' : '--') + ' · ' + (pc ? pc + 'hPa' : '--');
  }

  /* ---------------- 逐帧更新 ---------------- */
  function setFrame(frame, typhoon) {
    if (!map || !frame) return;
    st.frame = frame;
    var b = frame.bbox || bounds;
    var img = PAPI && PAPI.frameImage ? PAPI.frameImage(frame) : '';
    if (img) setFlood(img, b);

    var c = frame.center || null;
    if (c) updateCenter(c, frame, typhoon);

    // 风圈用最近路径点的 r34/r50（帧响应不含风圈半径，契约 §6.2）
    if (typhoon && c) {
      var near = nearestTrackPoint(typhoon.track || [], c);
      st.windLayer.clearLayers();
      if (near && near.r34) {
        var p34 = windPolygon(c, near.r34);
        if (p34) {
          L.polygon(p34, {
            pane: 'tyPane', color: '#ffb020', weight: 1.1, opacity: 0.55,
            fillColor: '#ffb020', fillOpacity: 0.022, dashArray: '6 6'
          }).addTo(st.windLayer);
        }
        var p50 = windPolygon(c, near.r50);
        if (p50) {
          L.polygon(p50, {
            pane: 'tyPane', color: '#ffb020', weight: 1, opacity: 0.42,
            fillColor: '#ffb020', fillOpacity: 0.028, dashArray: '2 4'
          }).addTo(st.windLayer);
        }
      }
    }
    styleVillages(frame);
  }

  function nearestTrackPoint(track, c) {
    if (!track || !track.length) return null;
    var best = track[0], bd = Infinity;
    for (var i = 0; i < track.length; i++) {
      var dx = track[i].lat - c[0], dy = track[i].lon - c[1];
      var d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = track[i]; }
    }
    return best;
  }

  /* ---------------- 视野 ---------------- */
  function focusExtent(kind) {
    if (!map) return;
    if (kind === 'coast') {
      map.flyToBounds([[22.24, 113.38], [22.58, 113.80]], { duration: 0.8, padding: [10, 10] });
    } else {
      map.flyToBounds(bounds, { duration: 0.8, padding: [8, 8] });
    }
  }

  function invalidate() { if (map) map.invalidateSize(); }

  global.PMap = {
    available: available,
    init: init,
    setBaseMode: setBaseMode,
    tileStats: tileStats,
    setDemOn: setDemOn,
    demOn: demOn,
    setDemPreview: setDemPreview,
    setFlood: setFlood,
    setVillages: setVillages,
    setTrack: setTrack,
    setFrame: setFrame,
    focusExtent: focusExtent,
    invalidate: invalidate,
    map: function () { return map; }
  };
})(window);
