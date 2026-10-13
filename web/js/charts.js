// ECharts 래퍼 (design.md §7). 축·단위·범례·툴팁을 차트 가까이에 두고, 색 외에 선 종류·마커 모양으로도 구분한다.
import { t } from './i18n.js';

// up/down: 한국식 손익 색 (E18 ④, css --up/--down 과 같은 값). 상승·이익·매수 = 빨강, 하락·손실·매도 = 파랑
const C = { mint: '#88E6BC', amber: '#EAC07A', up: '#EF8F8F', down: '#8AC4E8', text: '#E8F0ED', muted: '#97A9A3', line: '#2A383B', gray: '#697b79' };
const live = new Set();

window.addEventListener('resize', () => live.forEach((c) => c.resize()));

export function disposeAll() { live.forEach((c) => c.dispose()); live.clear(); }

function base(el, ariaLabel) {
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', ariaLabel);
  const chart = window.echarts.init(el, null, { renderer: 'canvas' });
  live.add(chart);
  return chart;
}

const axisCommon = {
  axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted, fontSize: 10 },
  splitLine: { lineStyle: { color: C.line } },
};
// 기간 조절 막대(x축 아래): 끌기 쉬운 높이 (2026-09-22 사용자 요청)
const ZOOM_SLIDER = { type: 'slider', height: 32, bottom: 8, borderColor: C.line, textStyle: { color: C.muted }, handleSize: '110%' };
const tooltip = { trigger: 'axis', backgroundColor: '#1B2429', borderColor: '#314145', textStyle: { color: C.text, fontSize: 12 } };

export function histogram(el, bins, ariaLabel) {
  const pc = (x) => (x * 100).toFixed(0);
  const label = (b) => (b.lo == null ? `< ${pc(b.hi)}%` : b.hi == null ? `≥ ${pc(b.lo)}%` : `${pc(b.lo)}%`);
  const chart = base(el, ariaLabel);
  chart.setOption({
    grid: { left: 48, right: 12, top: 18, bottom: 48 },
    tooltip: { ...tooltip, formatter: (p) => {
      const b = bins[p[0].dataIndex];
      const rng = b.lo == null ? t('c.binBelow', { a: pc(b.hi) }) : b.hi == null ? t('c.binAbove', { a: pc(b.lo) }) : t('c.binRange', { a: pc(b.lo), b: pc(b.hi) });
      return t('c.binTip', { r: rng, n: b.count.toLocaleString('en-US') });
    } },
    xAxis: { type: 'category', data: bins.map(label), name: t('c.netReturn'), nameLocation: 'middle', nameGap: 32, nameTextStyle: { color: C.muted }, ...axisCommon, axisLabel: { ...axisCommon.axisLabel, interval: 4 } },
    yAxis: { type: 'value', name: t('c.trades'), nameTextStyle: { color: C.muted }, ...axisCommon },
    series: [{ type: 'bar', data: bins.map((b) => ({ value: b.count, itemStyle: { color: (b.hi != null && b.hi <= 0) ? C.down : C.up } })), barCategoryGap: '8%' }],
  });
  return chart;
}

export function lines(el, series, { ariaLabel, yName, percentBase = null } = {}) {
  const chart = base(el, ariaLabel);
  const styles = [{ color: C.mint, type: 'solid', width: 2.5 }, { color: C.muted, type: 'dashed', width: 2 }, { color: C.amber, type: 'dotted', width: 2 }];
  chart.setOption({
    grid: { left: 58, right: 16, top: 40, bottom: 76 },
    legend: { top: 0, right: 8, textStyle: { color: C.muted, fontSize: 11 } },
    tooltip: { ...tooltip, valueFormatter: (v) => (v == null ? '—' : percentBase != null ? `${((v / percentBase - 1) * 100).toFixed(2)}%` : v.toFixed(3)) },
    xAxis: { type: 'time', ...axisCommon, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: yName, nameTextStyle: { color: C.muted }, ...axisCommon,
      axisLabel: { ...axisCommon.axisLabel, formatter: (v) => (percentBase != null ? `${((v / percentBase - 1) * 100).toFixed(0)}%` : v) } },
    dataZoom: [{ type: 'inside' }, ZOOM_SLIDER],
    series: series.map((s, i) => ({ name: s.name, type: 'line', showSymbol: false, data: s.data, lineStyle: styles[i % styles.length], itemStyle: { color: styles[i % styles.length].color } })),
  });
  return chart;
}

const REGIME_BG = { bull: 'rgba(239,143,143,0.09)', bear: 'rgba(138,196,232,0.09)' };

export function price(el, d, ariaLabel) {
  const s = d.series;
  const pts = (arr) => s.date.map((t, i) => [t, arr[i]]);
  const areas = d.stock_regime_segments.filter((g) => REGIME_BG[g.regime])
    .map((g) => [{ xAxis: g.start, itemStyle: { color: REGIME_BG[g.regime] }, name: g.regime }, { xAxis: g.end }]);
  const entries = d.markers.filter((m) => m.type === 'entry').map((m) => [m.date, m.price]);
  const exits = d.markers.filter((m) => m.type === 'exit').map((m) => [m.date, m.price, m.reason]);
  const chart = base(el, ariaLabel);
  chart.setOption({
    grid: { left: 64, right: 16, top: 42, bottom: 80 },
    legend: { top: 0, right: 8, textStyle: { color: C.muted, fontSize: 11 } },
    tooltip: { ...tooltip, valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 })) },
    xAxis: { type: 'time', ...axisCommon, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: t('c.price'), nameTextStyle: { color: C.muted }, ...axisCommon },
    dataZoom: [{ type: 'inside' }, ZOOM_SLIDER],
    series: [
      { name: t('c.close'), type: 'line', showSymbol: false, data: pts(s.close), lineStyle: { color: C.text, width: 1.6 }, itemStyle: { color: C.text },
        markArea: { silent: true, label: { show: false }, data: areas } },
      { name: t('c.ma20'), type: 'line', showSymbol: false, data: pts(s.sma20), lineStyle: { color: C.mint, width: 1.2, type: 'dashed' }, itemStyle: { color: C.mint } },
      { name: t('c.ma200'), type: 'line', showSymbol: false, data: pts(s.sma200), lineStyle: { color: C.amber, width: 1.2, type: 'dotted' }, itemStyle: { color: C.amber } },
      { name: t('c.entry'), type: 'scatter', data: entries, symbol: 'triangle', symbolSize: 11, itemStyle: { color: C.up, borderColor: '#0C1114', borderWidth: 1 } },
      { name: t('c.exit'), type: 'scatter', data: exits, symbol: 'rect', symbolSize: 9, itemStyle: { color: C.down, borderColor: '#0C1114', borderWidth: 1 } },
    ],
  });
  return chart;
}

// 영향 분석 (P3-13.3): 종가 선 위에 자료 사건 점. 구간(학습·검증·평가)마다 모양과 색을 둘 다 다르게 한다(색만으로 구분하지 않음).
// d = { bars: [{date, close}], points: {train|valid|test: [{date, close, title, group, contribution}]} } — 어떤 사건을 찍을지는 화면이 정한다.
// 툴팁 문구는 자료 제목을 HTML 로 해석하지 않도록 바꿔서 넣는다.
const EV_STYLE = {
  train: { symbol: 'emptyCircle', color: C.gray, label: 'inf.c.train' },
  valid: { symbol: 'diamond', color: C.amber, label: 'inf.c.valid' },
  test: { symbol: 'triangle', color: C.mint, label: 'inf.c.test' },
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

export function influence(el, d, ariaLabel) {
  const chart = base(el, ariaLabel);
  const num = (v) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const evSeries = Object.entries(EV_STYLE).map(([k, s]) => ({
    name: t(s.label), type: 'scatter', symbol: s.symbol, symbolSize: 11,
    itemStyle: { color: s.color, borderColor: '#0C1114', borderWidth: 1 },
    data: (d.points[k] || []).map((p) => ({ value: [p.date, p.close], ev: p })),
  }));
  chart.setOption({
    grid: { left: 64, right: 16, top: 42, bottom: 80 },
    legend: { top: 0, right: 8, textStyle: { color: C.muted, fontSize: 11 } },
    tooltip: { ...tooltip, formatter: (params) => {
      // p.marker 는 ECharts 가 만드는 색 점 HTML 이라 그대로 두고, 자료에서 온 글자(제목·묶음)만 바꿔 넣는다
      const rows = params.map((p) => (p.seriesType === 'line'
        ? `${p.marker}${esc(p.seriesName)} ${num(p.value[1])}`
        : `${p.marker}${esc(p.seriesName)} · ${esc(p.data.ev.title)} (${esc(p.data.ev.group ?? '—')}${p.data.ev.contribution == null ? '' : `, ${Number(p.data.ev.contribution).toFixed(4)}`})`));
      return [esc(params[0]?.axisValueLabel ?? ''), ...rows].join('<br>');
    } },
    xAxis: { type: 'time', ...axisCommon, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, name: t('c.price'), nameTextStyle: { color: C.muted }, ...axisCommon },
    dataZoom: [{ type: 'inside' }, ZOOM_SLIDER],
    series: [
      { name: t('c.close'), type: 'line', showSymbol: false, data: d.bars.map((b) => [b.date, b.close]), lineStyle: { color: C.text, width: 1.6 }, itemStyle: { color: C.text } },
      ...evSeries,
    ],
  });
  return chart;
}
