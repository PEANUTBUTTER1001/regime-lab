// ECharts 래퍼 (design.md §9). 축·단위·범례·툴팁을 차트 가까이에 두고, 색 외에 선 종류·마커 모양으로도 구분한다.
// 색은 CSS 토큰(:root, 라이트 테마는 [data-theme="light"])에서 그릴 때마다 읽는다 → 테마를 바꾸면 화면을 다시 그려 새 색을 쓴다.
import { t } from './i18n.js';

// 한국식 손익 색 (E18 ④): 상승·이익·매수 = --up(빨강), 하락·손실·매도 = --down(파랑). 강조 선은 --accent
function pal() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return {
    accent: v('--accent'), amber: v('--amber'), up: v('--up'), down: v('--down'), text: v('--text'), muted: v('--muted'),
    line: v('--line'), lineStrong: v('--line-strong'), surface: v('--surface'), control: v('--control'), canvas: v('--canvas'),
    shadeUp: v('--shade-up'), shadeDown: v('--shade-down'),
  };
}
const live = new Map(); // chart → ResizeObserver

// 열 폭(조절바·자리 바꾸기·서랍)이 바뀌어도 차트가 칸에 맞게 다시 그려지도록 요소마다 크기를 지켜본다
export function disposeAll() { live.forEach((ro, c) => { ro.disconnect(); c.dispose(); }); live.clear(); }
// 열 안의 내용만 다시 그릴 때(탭 전환 등) 그 안의 차트만 정리한다
export function disposeIn(root) {
  live.forEach((ro, c) => { if (c.isDisposed() || root.contains(c.getDom())) { ro.disconnect(); if (!c.isDisposed()) c.dispose(); live.delete(c); } });
}

function base(el, ariaLabel) {
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', ariaLabel);
  const chart = window.echarts.init(el, null, { renderer: 'canvas' });
  const ro = new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); });
  ro.observe(el);
  live.set(chart, ro);
  return chart;
}

const axisCommon = (C) => ({
  axisLine: { lineStyle: { color: C.lineStrong } }, axisLabel: { color: C.muted, fontSize: 11 },
  splitLine: { lineStyle: { color: C.line } },
});
// 기간 조절 막대(x축 아래): 끌기 쉬운 높이 (2026-09-22 사용자 요청)
const zoomSlider = (C) => ({ type: 'slider', height: 28, bottom: 8, borderColor: C.lineStrong, textStyle: { color: C.muted, fontSize: 11 }, handleSize: '110%',
  fillerColor: 'rgba(128,128,128,.12)', dataBackground: { lineStyle: { color: C.lineStrong }, areaStyle: { color: C.control } } });
const tooltip = (C) => ({ trigger: 'axis', backgroundColor: C.surface, borderColor: C.lineStrong, textStyle: { color: C.text, fontSize: 12 } });

export function histogram(el, bins, ariaLabel) {
  const C = pal();
  const ax = axisCommon(C);
  const pc = (x) => (x * 100).toFixed(0);
  const label = (b) => (b.lo == null ? `< ${pc(b.hi)}%` : b.hi == null ? `≥ ${pc(b.lo)}%` : `${pc(b.lo)}%`);
  const chart = base(el, ariaLabel);
  chart.setOption({
    grid: { left: 48, right: 12, top: 18, bottom: 48 },
    tooltip: { ...tooltip(C), formatter: (p) => {
      const b = bins[p[0].dataIndex];
      const rng = b.lo == null ? t('c.binBelow', { a: pc(b.hi) }) : b.hi == null ? t('c.binAbove', { a: pc(b.lo) }) : t('c.binRange', { a: pc(b.lo), b: pc(b.hi) });
      return t('c.binTip', { r: rng, n: b.count.toLocaleString('en-US') });
    } },
    xAxis: { type: 'category', data: bins.map(label), name: t('c.netReturn'), nameLocation: 'middle', nameGap: 32, nameTextStyle: { color: C.muted }, ...ax, axisLabel: { ...ax.axisLabel, interval: 4 } },
    yAxis: { type: 'value', name: t('c.trades'), nameTextStyle: { color: C.muted }, ...ax },
    series: [{ type: 'bar', data: bins.map((b) => ({ value: b.count, itemStyle: { color: (b.hi != null && b.hi <= 0) ? C.down : C.up } })), barCategoryGap: '8%' }],
  });
  return chart;
}

export function lines(el, series, { ariaLabel, yName, percentBase = null } = {}) {
  const C = pal();
  const ax = axisCommon(C);
  const chart = base(el, ariaLabel);
  const styles = [{ color: C.accent, type: 'solid', width: 1.5 }, { color: C.muted, type: 'dashed', width: 1 }, { color: C.amber, type: 'dotted', width: 1 }];
  chart.setOption({
    // 범례(맨 위 한 줄)와 y축 이름이 겹치지 않게 차트 영역을 범례 아래로 내린다
    grid: { left: 58, right: 16, top: 56, bottom: 72 },
    legend: { type: 'scroll', top: 0, left: 8, right: 8, textStyle: { color: C.muted, fontSize: 11 }, pageTextStyle: { color: C.muted }, pageIconColor: C.muted, pageIconInactiveColor: C.lineStrong },
    tooltip: { ...tooltip(C), valueFormatter: (v) => (v == null ? '—' : percentBase != null ? `${((v / percentBase - 1) * 100).toFixed(2)}%` : v.toFixed(3)) },
    xAxis: { type: 'time', ...ax, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: yName, nameTextStyle: { color: C.muted, align: 'left' }, nameGap: 12, ...ax,
      axisLabel: { ...ax.axisLabel, formatter: (v) => (percentBase != null ? `${((v / percentBase - 1) * 100).toFixed(0)}%` : v) } },
    dataZoom: [{ type: 'inside' }, zoomSlider(C)],
    series: series.map((s, i) => ({ name: s.name, type: 'line', showSymbol: false, data: s.data, lineStyle: styles[i % styles.length], itemStyle: { color: styles[i % styles.length].color } })),
  });
  return chart;
}

// 봉 예측 (P2-10, E17): 직전 종가 선 + 다음 h 봉의 회색 띠(10~90%)만 그린다. 예상 캔들·가운데 선은 그리지 않는다.
// x축은 봉 순서(밤·휴장 공백 없이). 실제 종가가 있으면 점으로 겹친다. 띠는 --muted 를 옅게 칠한다(라이트·다크 모두 회색).
export function forecastBand(el, d, ariaLabel) {
  const C = pal();
  const ax = axisCommon(C);
  const cats = [...d.history.map((p) => p.label), ...d.steps.map((s) => t('fc.step', { h: s.h }))];
  const nh = d.history.length;
  const pad = (arr) => [...Array(nh).fill(null), ...arr];
  const chart = base(el, ariaLabel);
  chart.setOption({
    // 범례(맨 위 한 줄, 좁으면 넘겨 보기)와 y축 이름이 겹치지 않게 차트 영역을 범례 아래로 내린다 (price 와 같은 배치)
    grid: { left: 64, right: 16, top: 56, bottom: 48 },
    legend: { type: 'scroll', top: 0, left: 8, right: 8, textStyle: { color: C.muted, fontSize: 11 }, pageTextStyle: { color: C.muted },
      pageIconColor: C.muted, pageIconInactiveColor: C.lineStrong, data: [t('fc.hist'), t('fc.band'), t('fc.actual')] },
    tooltip: { ...tooltip(C), valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 })) },
    xAxis: { type: 'category', data: cats, ...ax, splitLine: { show: false }, axisLabel: { ...ax.axisLabel, hideOverlap: true } },
    yAxis: { type: 'value', scale: true, name: t('c.price'), nameTextStyle: { color: C.muted, align: 'left' }, nameGap: 12, ...ax },
    series: [
      { name: t('fc.hist'), type: 'line', showSymbol: false, data: [...d.history.map((p) => p.close), ...Array(d.steps.length).fill(null)],
        lineStyle: { color: C.text, width: 1.6 }, itemStyle: { color: C.text } },
      // 띠: 아래값(투명) 위에 (위값 − 아래값)을 쌓는다
      { name: 'low', type: 'line', stack: 'band', showSymbol: false, silent: true, tooltip: { show: false },
        data: pad(d.steps.map((s) => s.low)), lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 } },
      { name: t('fc.band'), type: 'line', stack: 'band', showSymbol: false, data: pad(d.steps.map((s) => s.high - s.low)),
        lineStyle: { opacity: 0 }, itemStyle: { color: C.muted }, areaStyle: { color: C.muted, opacity: 0.3 },
        tooltip: { valueFormatter: () => '' } },
      { name: t('fc.actual'), type: 'scatter', symbol: 'diamond', symbolSize: 9, data: pad(d.steps.map((s) => s.actual)),
        itemStyle: { color: C.amber, borderColor: C.canvas, borderWidth: 1 } },
    ],
  });
  return chart;
}

export function price(el, d, ariaLabel) {
  const C = pal();
  const ax = axisCommon(C);
  const regimeBg = { bull: C.shadeUp, bear: C.shadeDown };
  const s = d.series;
  const pts = (arr) => s.date.map((x, i) => [x, arr[i]]);
  const areas = d.stock_regime_segments.filter((g) => regimeBg[g.regime])
    .map((g) => [{ xAxis: g.start, itemStyle: { color: regimeBg[g.regime] }, name: g.regime }, { xAxis: g.end }]);
  const entries = d.markers.filter((m) => m.type === 'entry').map((m) => [m.date, m.price]);
  const exits = d.markers.filter((m) => m.type === 'exit').map((m) => [m.date, m.price, m.reason]);
  const chart = base(el, ariaLabel);
  chart.setOption({
    grid: { left: 60, right: 12, top: 56, bottom: 72 },
    legend: { type: 'scroll', top: 0, left: 8, right: 8, textStyle: { color: C.muted, fontSize: 11 }, pageTextStyle: { color: C.muted }, pageIconColor: C.muted, pageIconInactiveColor: C.lineStrong },
    tooltip: { ...tooltip(C), valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 })) },
    xAxis: { type: 'time', ...ax, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: t('c.price'), nameTextStyle: { color: C.muted, align: 'left' }, nameGap: 12, ...ax },
    dataZoom: [{ type: 'inside' }, zoomSlider(C)],
    series: [
      { name: t('c.close'), type: 'line', showSymbol: false, data: pts(s.close), lineStyle: { color: C.text, width: 1.5 }, itemStyle: { color: C.text },
        markArea: { silent: true, label: { show: false }, data: areas } },
      { name: t('c.ma20'), type: 'line', showSymbol: false, data: pts(s.sma20), lineStyle: { color: C.accent, width: 1, type: 'dashed' }, itemStyle: { color: C.accent } },
      { name: t('c.ma200'), type: 'line', showSymbol: false, data: pts(s.sma200), lineStyle: { color: C.amber, width: 1, type: 'dotted' }, itemStyle: { color: C.amber } },
      { name: t('c.entry'), type: 'scatter', data: entries, symbol: 'triangle', symbolSize: 10, itemStyle: { color: C.up, borderColor: C.canvas, borderWidth: 1 } },
      { name: t('c.exit'), type: 'scatter', data: exits, symbol: 'rect', symbolSize: 8, itemStyle: { color: C.down, borderColor: C.canvas, borderWidth: 1 } },
    ],
  });
  return chart;
}

// 영향 분석 (P3-13.3): 종가 선 위에 자료 사건 점. 구간(학습·검증·평가)마다 모양과 색을 둘 다 다르게 한다(색만으로 구분하지 않음).
// d = { bars: [{date, close}], points: {train|valid|test: [{date, close, title, group, contribution}]} } — 어떤 사건을 찍을지는 화면이 정한다.
// 툴팁 문구는 자료 제목을 HTML 로 해석하지 않도록 바꿔서 넣는다.
// color 는 pal() 의 이름 — 테마마다 그릴 때 읽는다
const EV_STYLE = {
  train: { symbol: 'emptyCircle', color: 'muted', label: 'inf.c.train' },
  valid: { symbol: 'diamond', color: 'amber', label: 'inf.c.valid' },
  test: { symbol: 'triangle', color: 'accent', label: 'inf.c.test' },
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

export function influence(el, d, ariaLabel) {
  const C = pal();
  const ax = axisCommon(C);
  const chart = base(el, ariaLabel);
  const num = (v) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const evSeries = Object.entries(EV_STYLE).map(([k, s]) => ({
    name: t(s.label), type: 'scatter', symbol: s.symbol, symbolSize: 11,
    itemStyle: { color: C[s.color], borderColor: C.canvas, borderWidth: 1 },
    data: (d.points[k] || []).map((p) => ({ value: [p.date, p.close], ev: p })),
  }));
  chart.setOption({
    grid: { left: 64, right: 16, top: 56, bottom: 80 },
    legend: { type: 'scroll', top: 0, left: 8, right: 8, textStyle: { color: C.muted, fontSize: 11 }, pageTextStyle: { color: C.muted }, pageIconColor: C.muted, pageIconInactiveColor: C.lineStrong },
    tooltip: { ...tooltip(C), formatter: (params) => {
      // p.marker 는 ECharts 가 만드는 색 점 HTML 이라 그대로 두고, 자료에서 온 글자(제목·묶음)만 바꿔 넣는다
      const rows = params.map((p) => (p.seriesType === 'line'
        ? `${p.marker}${esc(p.seriesName)} ${num(p.value[1])}`
        : `${p.marker}${esc(p.seriesName)} · ${esc(p.data.ev.title)} (${esc(p.data.ev.group ?? '—')}${p.data.ev.contribution == null ? '' : `, ${Number(p.data.ev.contribution).toFixed(4)}`})`));
      return [esc(params[0]?.axisValueLabel ?? ''), ...rows].join('<br>');
    } },
    xAxis: { type: 'time', ...ax, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: t('c.price'), nameTextStyle: { color: C.muted, align: 'left' }, nameGap: 12, ...ax },
    dataZoom: [{ type: 'inside' }, zoomSlider(C)],
    series: [
      { name: t('c.close'), type: 'line', showSymbol: false, data: d.bars.map((b) => [b.date, b.close]), lineStyle: { color: C.text, width: 1.6 }, itemStyle: { color: C.text } },
      ...evSeries,
    ],
  });
  return chart;
}
