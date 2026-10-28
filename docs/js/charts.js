/* ==========================================================================
   POSSIM-Web · charts.js
   职责：ECharts 折线 —— 潮位 / 增水 / 总水位 三线 + 当前帧游标线 + 系列显隐 tab
   依赖：window.echarts（ECharts 5.5.1 CDN）
   ========================================================================== */
(function (global) {
  'use strict';

  var ec = global.echarts;
  var chart = null;
  var raw = null;          // {cats:[], tide:[], surge:[], twl:[]}
  var mode = 'all';        // all | tide | surge | twl
  var cursor = 0;

  var COLOR = {
    tide: '#35e0ff',
    surge: '#ffb020',
    twl: '#2f81f7',
    axis: '#7ba7c9',
    grid: 'rgba(23,69,107,.55)'
  };

  function available() { return !!ec; }

  function init(el) {
    if (!ec || !el || chart) return !!chart;
    chart = ec.init(el, null, { renderer: 'canvas' });
    chart.setOption(baseOption(), true);
    return true;
  }

  function baseOption() {
    return {
      animationDuration: 420,
      animationEasing: 'cubicOut',
      backgroundColor: 'transparent',
      grid: { left: 36, right: 12, top: 16, bottom: 22 },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: 'rgba(6,22,38,.96)',
        borderColor: '#17456b',
        borderWidth: 1,
        padding: [6, 9],
        textStyle: { color: '#cfe6ff', fontSize: 10 },
        axisPointer: { type: 'line', lineStyle: { color: 'rgba(53,224,255,.55)', width: 1, type: 'dashed' } },
        formatter: function (ps) {
          if (!ps || !ps.length) return '';
          var s = 'Hour +' + ps[0].axisValue + '<br/>';
          for (var i = 0; i < ps.length; i++) {
            if (ps[i].value === null || ps[i].value === undefined) continue;
            s += ps[i].marker + ps[i].seriesName + ' ' +
              (Number(ps[i].value)).toFixed(2) + ' m<br/>';
          }
          return s;
        }
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: [],
        axisLine: { lineStyle: { color: 'rgba(23,69,107,.95)' } },
        axisTick: { show: false },
        axisLabel: { color: COLOR.axis, fontSize: 9, interval: 3, formatter: '{value}h' },
        splitLine: { show: false }
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: 'm',
        nameTextStyle: { color: COLOR.axis, fontSize: 9, padding: [0, 0, 0, -22] },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: COLOR.axis, fontSize: 9, formatter: function (v) { return v.toFixed(1); } },
        splitLine: { lineStyle: { color: COLOR.grid, type: 'dashed' } }
      },
      series: []
    };
  }

  function series(points) {
    return [
      {
        name: '潮位',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: points.tide,
        lineStyle: { width: 1.5, color: COLOR.tide },
        itemStyle: { color: COLOR.tide },
        areaStyle: {
          color: new ec.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(53,224,255,.28)' },
            { offset: 1, color: 'rgba(53,224,255,0)' }
          ])
        },
        emphasis: { focus: 'series' }
      },
      {
        name: '增水',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: points.surge,
        lineStyle: { width: 1.4, color: COLOR.surge, type: 'dashed' },
        itemStyle: { color: COLOR.surge }
      },
      {
        name: '总水位',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: points.twl,
        lineStyle: { width: 2, color: COLOR.twl },
        itemStyle: { color: COLOR.twl },
        markLine: {
          silent: true,
          symbol: 'none',
          animation: false,
          label: { show: false },
          lineStyle: { color: 'rgba(53,224,255,.9)', width: 1, type: 'dashed' },
          data: [{ xAxis: 0 }]
        }
      }
    ];
  }

  function visibleData() {
    if (!raw) return { tide: [], surge: [], twl: [] };
    var n = raw.cats.length, out = { tide: [], surge: [], twl: [] };
    for (var i = 0; i < n; i++) {
      out.tide.push(mode === 'all' || mode === 'tide' ? raw.tide[i] : null);
      out.surge.push(mode === 'all' || mode === 'surge' ? raw.surge[i] : null);
      out.twl.push(mode === 'all' || mode === 'twl' ? raw.twl[i] : null);
    }
    return out;
  }

  function setData(timeseries) {
    if (!chart || !timeseries) return;
    var n = timeseries.length;
    raw = { cats: [], tide: [], surge: [], twl: [] };
    for (var i = 0; i < n; i++) {
      raw.cats.push(String(i));
      raw.tide.push(Number(timeseries[i].tide) || 0);
      raw.surge.push(Number(timeseries[i].surge) || 0);
      raw.twl.push(Number(timeseries[i].twl) || 0);
    }
    chart.setOption({
      xAxis: { data: raw.cats },
      series: series(visibleData())
    }, false);
    setCursor(cursor);
  }

  function setCursor(i) {
    cursor = i;
    if (!chart || !raw) return;
    chart.setOption({
      series: [{}, {}, { markLine: { data: [{ xAxis: String(i) }] } }]
    }, false);
  }

  function setMode(m) {
    mode = m;
    if (!chart || !raw) return;
    chart.setOption({ series: series(visibleData()) }, false);
    setCursor(cursor);
  }

  function resize() { if (chart) chart.resize(); }

  global.PCharts = {
    available: available,
    init: init,
    setData: setData,
    setCursor: setCursor,
    setMode: setMode,
    resize: resize,
    chart: function () { return chart; }
  };
})(window);
