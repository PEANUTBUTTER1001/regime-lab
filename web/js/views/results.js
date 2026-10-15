// 실행 작업면 (FR-U2~U4, FR-A1~A5, NFR-9, design.md §5.4·§8.1·§8.2, UI 개혁 B안 2026-10-13).
// 조건(전략 만들기) · 가운데(진행 → 실행 결과) · 오른쪽(종목별 거래 · 공시·뉴스 · AI 보고서) 세 열을 한 화면에 둔다.
// 모든 수치는 같은 run_id 의 /result·/stocks·/report 응답에서만 가져온다. 화면은 계산하지 않는다.
import { api } from '../api.js';
import { disposeIn, histogram, lines } from '../charts.js';
import { easyName, glyph, viz } from '../components/easy.js';
import { errorView, fmt, h, loading, metricDefs, passPill, pill, stateView, strongT, table, term, updown } from '../components/ui.js';
import { paneFrame, paneTabs, setRouteGuard, swapped, toggleSwap } from '../components/workspace.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';
import { exampleDraft, renderBuilder } from './builder.js';
import { srcLabel, when } from './evidence.js';
import { renderProgress } from './progress.js';
import { renderReportInto } from './report.js';
import { renderStockDetail } from './stock.js';

const JUDGE_KIND = { maintained: '', weakened: 'warn', reversed: 'bad', negative_both: 'bad', sample_insufficient: 'warn', not_applicable: 'neutral' };
const REGIMES = ['bull', 'sideways', 'bear', 'unavailable'];
const CAPS = ['large', 'mid', 'small'];
const judge = (j) => [has(`j.${j}`) ? t(`j.${j}`) : j, has(`jd.${j}`) ? t(`jd.${j}`) : '', JUDGE_KIND[j] ?? 'neutral'];
const patText = (inp) => inp.patterns.map((p) => (has(`pat.${p}`) ? t(`pat.${p}`) : p)).join(` ${inp.combine.toUpperCase()} `);

// info: 지표 이름을 점선 용어로 만들고 누르면 설명 카드 (X15 ③, design.md §7.9)
function metric(label, value, sub, tone = '', info = null) {
  return h('div', { class: 'metric' }, h('span', { class: 'label' }, info ? term(label, info) : label), h('strong', { class: tone }, value), h('small', {}, sub));
}

// 용어 설명 카드 문구 (쉬운 한 줄 + 보는 기준). 값은 이 실행의 API 값을 옮겨 적기만 한다
const tip = (id, vars = {}) => ({ say: t(`tm.${id}.say`, vars), rule: t(`tm.${id}.rule`, vars) });
const finite = (...xs) => xs.every((x) => typeof x === 'number' && Number.isFinite(x));
const year = (d) => (d ? new Date(`${d}T00:00:00Z`).getUTCFullYear() : '—');

// 결과 맨 위 한 줄 요약과 신호등 (X15 ①). 초록 = 서버 판정 analysis_target, 노랑 = 검사 3개 중 2개 이상 통과, 빨강 = 그 밖.
// 새 판정이 아니라 서버의 판정·검사 결과를 쉬운 문장으로 옮긴다. 아래 판정 블록과 수치는 그대로 둔다
function summaryCard(s, onMore) {
  const m = s.summary;
  const v = s.validation;
  const passes = [v.split_pass, v.fdr_pass, v.random_pass].filter(Boolean).length;
  const light = v.analysis_target ? 'green' : passes >= 2 ? 'yellow' : 'red';
  const k = fmt.int(state.meta?.validation?.random_iterations ?? 1000);
  const top = fmt.num((1 - (v.random_percentile ?? 0)) * 100, 1);
  const split = v.split_pass ? ['ok', t('sum.split.ok')] : v.split_judgement === 'sample_insufficient' ? ['warn', t('sum.split.wait')] : ['no', t('sum.split.no')];
  const checks = [split, v.fdr_pass ? ['ok', t('sum.fdr.ok')] : ['no', t('sum.fdr.no')],
    v.random_pass ? ['ok', t('sum.random.ok', { k, top })] : ['no', t('sum.random.no')]];
  const mark = { ok: '✓', warn: '!', no: '✕' };
  const more = h('a', { href: '#', class: 'sum-more' }, t('sum.more'));
  more.addEventListener('click', (e) => { e.preventDefault(); onMore(); });
  return h('section', { class: `sum ${light}`, 'aria-label': t('sum.aria') },
    h('div', { class: 'sum-head' }, h('span', { class: 'sum-light', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('b', {}, t(`sum.${light}`))),
    h('p', { class: 'sum-main' },
      strongT('sum.s1', { a: year(m.period_start), b: year(m.period_end), n: fmt.int(m.trades), w: fmt.pct(m.win_rate, 1, false) }),
      finite(m.mean_excess) && [' ', strongT(m.mean_excess >= 0 ? 'sum.s2up' : 'sum.s2down', { x: `${Math.abs(m.mean_excess * 100).toFixed(2)}%p` })]),
    h('ul', { class: 'sum-checks' }, checks.map(([kind, text]) => h('li', { class: kind }, h('span', { 'aria-hidden': 'true' }, mark[kind]), text))),
    h('div', { class: 'sum-foot' }, h('span', {}, t('sum.foot')), more));
}

// 예시로 시작하기 (X15 ②): 실행 기록이 없는 사람에게 결과 칸에서 바로 시험할 예시 3개. 값은 허용 범위 안의 기본 청산
const EXAMPLES = [
  { key: 'high_52w', patterns: ['high_52w'], stop: -8, profit: 20, hold: 10 },
  { key: 'rsi_rebound', patterns: ['rsi_rebound'], stop: -8, profit: 15, hold: 10 },
  { key: 'ma_cross', patterns: ['ma_cross_5_20'], stop: -8, profit: 20, hold: 20 },
];
function examplesBlock(meta, onPick) {
  const known = new Set(meta.patterns.map((p) => p.name));
  const years = Math.round((Date.parse(meta.data_as_of) - Date.parse(meta.backtest_start)) / (365.25 * 864e5));
  return h('section', { class: 'examples', 'aria-labelledby': 'ex-title' },
    h('h2', { id: 'ex-title' }, t('ex.title')), h('p', { class: 'card-sub' }, t('ex.sub', { y: years })),
    h('div', { class: 'ex-grid' }, EXAMPLES.filter((ex) => ex.patterns.every((n) => known.has(n))).map((ex) => {
      const go = h('span', { class: 'ex-go' }, t('ex.go'));
      const btn = h('button', { type: 'button', class: 'ex-card' }, glyph(ex.patterns[0], 'ex-glyph'), h('b', {}, t(`ex.${ex.key}`)),
        h('span', { class: 'ex-d' }, easyName(ex.patterns[0])), h('span', { class: 'ex-e' }, t('ex.exit', { d: ex.hold, p: `${ex.profit}`, s: ex.stop })), go);
      btn.addEventListener('click', () => {
        btn.disabled = true; go.textContent = t('ex.busy');
        onPick(ex);
        // 실행이 시작되지 않았으면(입력 오류·대기열 가득) 다시 누를 수 있게
        setTimeout(() => { if (btn.isConnected) { btn.disabled = false; go.textContent = t('ex.go'); } }, 4000);
      });
      return btn;
    })),
    h('p', { class: 'hint' }, t('ex.or'), ' ', h('a', { href: '#/presets' }, t('nav.presets')), ' · ', h('a', { href: '#/runs' }, t('nav.runs'))));
}

function heatCell(c) {
  if (!c) return h('td', { class: 'num heat-cell' }, h('span', { class: 'muted' }, '—'), h('br'), h('small', {}, t('r.zeroTrades')));
  // 한국식 색: 시장보다 더 벌면 빨강 ▲, 덜 벌면 파랑 ▼ (E18 ④). 표본 부족 칸은 빗금, 값은 흐리게
  const cls = c.sample_insufficient ? 'insufficient' : c.mean_excess > 0 ? 'pos' : c.mean_excess < 0 ? 'neg' : '';
  const sign = c.mean_excess > 0 ? 'pos' : c.mean_excess < 0 ? 'neg' : '';
  const mark = sign === 'pos' ? '▲' : sign === 'neg' ? '▼' : '';
  return h('td', { class: `num heat-cell ${cls} ${c.sample_insufficient ? sign : ''}`.trim() },
    h('b', {}, mark && h('span', { class: 'updown-mark', 'aria-hidden': 'true' }, mark), fmt.pctp(c.mean_excess)), h('br'),
    h('small', {}, `${t('common.trades', { n: fmt.int(c.trades) })}${c.sample_insufficient ? t('r.insufficientTag') : ''}`));
}

function heatmap(cells, market, minN) {
  const by = new Map(cells.filter((c) => c.market === market).map((c) => [`${c.regime}|${c.cap_group}`, c]));
  const rows = REGIMES.filter((r) => CAPS.some((c) => by.has(`${r}|${c}`)));
  if (!rows.length) return h('p', { class: 'muted', text: t('r.noTradesIn', { mk: market }) });
  const head = h('tr', {}, h('th', { scope: 'col' }, t('r.regime')), CAPS.map((c) => h('th', { scope: 'col', class: 'num' }, t(`cap.${c}`))));
  const body = rows.map((r) => h('tr', {}, h('th', { scope: 'row' }, t(`regime.${r}`)), CAPS.map((cap) => heatCell(by.get(`${r}|${cap}`)))));
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'heat' }, h('caption', {}, t('r.heatCaption', { mk: market, n: fmt.int(minN) })), h('thead', {}, head), h('tbody', {}, body)),
    h('div', { class: 'legend' }, h('span', {}, h('i', { class: 'hatch', 'aria-hidden': 'true' }), t('ws.hatchLegend', { n: fmt.int(minN) }))));
}

function toggleGroup(label, items, current, onPick) {
  const g = h('div', { role: 'group', 'aria-label': label, class: 'tabs', style: { margin: 0 } });
  for (const [key, text] of items) {
    g.append(h('button', { type: 'button', class: 'tab', 'aria-pressed': String(key === current),
      onclick: (e) => { g.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === e.currentTarget))); onPick(key); } }, text));
  }
  return g;
}

// 선택 청산 규칙(2026-10-07)을 켠 실행만 요약에 덧붙인다
function extraExits(ex) {
  const parts = [];
  if (ex.trailing_stop_pct != null) parts.push(t('r.exTrail', { v: ex.trailing_stop_pct }));
  if (ex.breakeven_trigger_pct != null) parts.push(t('r.exBe', { v: ex.breakeven_trigger_pct }));
  if (ex.ma_exit_window != null) parts.push(t('r.exMa', { v: ex.ma_exit_window }));
  return parts.length ? ` · ${parts.join(' · ')}` : '';
}

// 청산 사유별 건수 (API summary.exit_by_reason). 0건 사유는 숨기고 많은 순으로. 기준일 보유 중(end_of_data)은 따로 표시하지 않는다.
function exitReasons(by) {
  const rows = Object.entries(by || {}).filter(([k, n]) => n > 0 && k !== 'end_of_data').sort((a, b) => b[1] - a[1]);
  if (!rows.length) return null;
  const label = (k) => (has(`reason.${k}`) ? t(`reason.${k}`) : k);
  return h('div', { class: 'exit-reasons', role: 'group', 'aria-label': t('r.exitReasons') },
    h('span', { class: 'label' }, t('r.exitReasons')), ...rows.map(([k, n]) => pill(`${label(k)} ${fmt.int(n)}`, 'neutral')));
}

// ================================================================ 가운데: 요약 · 곡선·분포 · 국면·기간
function summaryTab(box, res, s) {
  const m = s.summary;
  const v = s.validation;
  const inp = s.strategy_input;
  const minN = res.min_cell_trades;
  const pct = fmt.num((v.random_percentile ?? 0) * 100, 1);
  const [jl, jd, jk] = judge(v.split_judgement);
  const k = fmt.int(state.meta?.validation?.random_iterations ?? 1000);
  const top = fmt.num((1 - (v.random_percentile ?? 0)) * 100, 1);
  const ok = (pass, yes, no) => (pass ? { value: t('common.passed'), tone: 'good', text: yes } : { value: t('common.notPassed'), tone: 'bad', text: no });
  const info = {
    trades: { ...tip('trades'), now: { value: fmt.int(m.trades), text: t('tm.trades.now', { a: year(m.period_start), b: year(m.period_end) }) } },
    win: { ...tip('win'), viz: finite(m.win_rate) ? viz.dots(m.win_rate) : null,
      now: { value: fmt.pct(m.win_rate, 1, false), text: finite(m.win_rate) ? t('tm.win.now', { n: Math.round(m.win_rate * 10) }) : '' } },
    avg: { ...tip('avg'), now: { value: fmt.pct(m.mean_ret), tone: fmt.tone(m.mean_ret), text: m.mean_ret > 0 ? t('tm.avg.up') : m.mean_ret < 0 ? t('tm.avg.down') : '' } },
    excess: { ...tip('excess'), viz: finite(m.mean_ret, m.mean_excess) ? viz.excess(m.mean_ret, m.mean_excess) : null,
      now: { value: fmt.pctp(m.mean_excess), tone: fmt.tone(m.mean_excess), text: m.mean_excess > 0 ? t('tm.excess.up') : m.mean_excess < 0 ? t('tm.excess.down') : '' } },
    split: { ...tip('split'), viz: finite(v.h1_mean_excess, v.h2_mean_excess) ? viz.split(fmt.int(v.h1_trades), v.h1_mean_excess, fmt.int(v.h2_trades), v.h2_mean_excess) : null,
      now: v.split_pass ? { value: t('common.passed'), tone: 'good', text: t('tm.split.pass') }
        : v.split_judgement === 'sample_insufficient' ? { value: jl, tone: 'warn', text: t('tm.split.wait') } : { value: jl, tone: 'bad', text: t('tm.split.fail') } },
    fdr: { ...tip('fdr'), now: ok(v.fdr_pass, t('tm.fdr.pass'), t('tm.fdr.fail')) },
    random: { ...tip('random'), viz: finite(v.random_percentile) ? viz.random(v.random_percentile) : null, now: ok(v.random_pass, t('tm.random.pass', { k, top }), t('tm.random.fail')) },
    randomPct: { ...tip('randomPct', { k }), viz: finite(v.random_percentile) ? viz.random(v.random_percentile) : null,
      now: { value: t('r.m.randomVal', { p: pct }), tone: v.random_pass ? 'good' : 'bad', text: t('tm.randomPct.now', { k, top }) } },
    payoff: { ...tip('payoff'), now: { value: t('tm.payoff.now', { x: fmt.num(m.payoff_ratio) }), tone: m.payoff_ratio > 1 ? 'good' : 'bad', text: finite(m.payoff_ratio) ? t(m.payoff_ratio > 1 ? 'tm.payoff.up' : 'tm.payoff.down') : '' } },
    sharpe: { ...tip('sharpe'), now: { value: fmt.num(m.sharpe, 2), text: '' } },
    mdd: { ...tip('mdd'), viz: finite(m.mdd) ? viz.mdd(m.mdd) : null,
      now: { value: fmt.pct(m.mdd, 1), tone: 'down', text: finite(m.mdd) ? t('tm.mdd.now', { v: Math.round(100 * (1 + m.mdd)) }) : '' } },
  };
  box.append(h('div', { class: 'filterbar' }, h('span', {}, t('r.lead', {
    name: s.strategy, pats: patText(inp), mk: inp.markets.join(' + '), s: inp.period.start, e: inp.period.end,
    sl: inp.exit.stop_loss_pct ?? t('r.off'), tp: inp.exit.take_profit_pct ?? t('r.off'), h: inp.exit.max_hold_days }) + extraExits(inp.exit))));

  // 판정 블록 (E18 2차): 결론(서버 판정 analysis_target)을 먼저, 그 근거인 검증 3종을 질문으로. 상세 수치(p·q·m·백분위·분할일)는 그대로 둔다
  const check = (question, name, about, result, detail) => h('li', { class: 'verdict-row' },
    h('div', { class: 'verdict-q' }, h('b', {}, question), h('small', { class: 'muted' }, term(name, about))),
    h('div', { class: 'verdict-res' }, result),
    h('p', { class: 'verdict-detail' }, detail));
  const target = !!v.analysis_target;
  const verdictEl = h('section', { class: `verdict${target ? '' : ' no'}`, 'aria-label': t('r.gates') });
  if (m.trades > 0) box.append(summaryCard(s, () => verdictEl.scrollIntoView({ behavior: 'smooth', block: 'start' })));
  box.append(verdictEl);
  verdictEl.append(
    h('div', { class: 'verdict-head' }, h('span', { class: 'verdict-mark', 'aria-hidden': 'true' }, target ? '●' : '✕'),
      h('div', {}, h('h2', {}, t(target ? 'r.vTarget' : 'r.vNotTarget')), h('p', {}, t(target ? 'r.vTargetMsg' : 'r.vNotTargetMsg')))),
    h('ol', { class: 'verdict-list' },
      check(t('r.qSplit'), t('r.split'), info.split, v.split_pass ? passPill(true) : pill(jl, jk || 'bad'),
        t('r.splitText', { j: jd, d: s.split.split_date, n1: fmt.int(v.h1_trades), e1: fmt.pctp(v.h1_mean_excess), n2: fmt.int(v.h2_trades), e2: fmt.pctp(v.h2_mean_excess) })),
      check(t('r.qFdr'), t('r.fdr'), info.fdr, passPill(v.fdr_pass),
        t('r.fdrText', { p: fmt.num(v.p_value, 4), q: fmt.num(state.meta?.validation?.fdr_q ?? 0.1, 2), m: res.fdr_family_size })),
      check(t('r.qRandom'), t('r.random'), info.random, passPill(v.random_pass),
        t('r.randomText', { p: pct, n: fmt.int(state.meta?.validation?.random_iterations ?? 1000) }))));

  // 핵심 지표 4개 (거래 수 병기, NFR-9)
  box.append(h('div', { class: 'metrics key' },
    metric(t('r.m.trades'), fmt.int(m.trades), `${m.period_start ?? '—'} ~ ${m.period_end ?? '—'}`, '', info.trades),
    metric(t('r.m.win'), fmt.pct(m.win_rate, 1, false), t('r.m.winSub', { n: fmt.int(m.trades) }), '', info.win),
    metric(t('r.m.avg'), updown(m.mean_ret, fmt.pct(m.mean_ret)), t('r.m.avgSub'), '', info.avg),
    metric(t('r.m.excess'), updown(m.mean_excess, fmt.pctp(m.mean_excess)), t('r.m.excessSub'), '', info.excess)));

  // 국면 × 시가총액 (시장·국면 기준 선택)
  let basis = 'cells_market';
  let market = inp.markets[0];
  const heatBox = h('div', {});
  const drawHeat = () => heatBox.replaceChildren(heatmap(s[basis], market, minN));
  box.append(h('section', { class: 'card', 'aria-labelledby': 'heat-title' },
    h('div', { class: 'sec-h' }, h('h2', { id: 'heat-title' }, t('r.heat'))),
    h('p', { class: 'how', text: t('r.heatHow') }), h('p', { class: 'card-sub', text: t('r.heatSub') }),
    h('div', { class: 'tabs' },
      h('span', { class: 'label tabs-label' }, term(t('r.basis'), tip('regime'))),
      toggleGroup(t('r.basis'), [['cells_market', t('r.basisMarket')], ['cells_stock', t('r.basisStock')]], basis, (b) => { basis = b; drawHeat(); }),
      toggleGroup(t('b.market'), inp.markets.map((mk) => [mk, mk]), market, (mk) => { market = mk; drawHeat(); })),
    heatBox));
  drawHeat();
  const reasons = exitReasons(m.exit_by_reason);
  if (reasons) box.append(reasons);

  // 자세한 지표: 나머지 6개는 표로 (항목 · 값 · 설명). 기본 접힘
  const detail = [
    [t('r.m.random'), h('span', { class: v.random_pass ? 'positive' : 'warning' }, t('r.m.randomVal', { p: pct })),
      t('r.m.randomSub', { p: pct, k: fmt.int(state.meta?.validation?.random_iterations ?? 1000), n: fmt.int(v.random_n_trades) }), info.randomPct],
    [t('r.m.median'), updown(m.median_ret, fmt.pct(m.median_ret)), t('r.m.perTrade')],
    [t('r.m.payoff'), fmt.num(m.payoff_ratio), t('r.m.payoffSub'), info.payoff],
    [t('r.m.sharpe'), fmt.num(m.sharpe, 3), t('r.m.sharpeSub'), info.sharpe],
    [t('r.m.mdd'), updown(m.mdd, fmt.pct(m.mdd, 1)), t('r.m.mddSub'), info.mdd],
    [t('r.m.excl'), `${fmt.int(m.excluded_trades)} / ${fmt.int(m.skipped_entries)}`, t('r.m.exclSub')],
  ];
  const prefs = state.prefs;
  const more = h('details', { class: 'more detail-metrics', style: { margin: 0, padding: '0 16px' }, open: prefs.detailOpen || null },
    h('summary', {}, t('r.detail')),
    h('div', { class: 'table-wrap' }, h('table', { class: 'detail-table' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('common.item')), h('th', { scope: 'col', class: 'num' }, t('common.value')), h('th', { scope: 'col' }, t('r.detailDesc')))),
      h('tbody', {}, detail.map(([name, value, desc, about]) => h('tr', {},
        h('th', { scope: 'row' }, about ? term(name, about) : name),
        h('td', { class: 'num' }, value), h('td', { class: 'muted' }, desc)))))),
    metricDefs(res.metric_definitions, { open: prefs.defsOpen, onToggle: (o) => { state.prefs = { ...state.prefs, defsOpen: o }; } }));
  more.addEventListener('toggle', (e) => { if (e.target === more) state.prefs = { ...state.prefs, detailOpen: more.open }; });
  box.append(more);
}

function curveTab(box, s) {
  const m = s.summary;
  const eqEl = h('div', { class: 'chart zoom' });
  const histEl = h('div', { class: 'chart' });
  box.append(
    h('section', { class: 'card' }, h('h2', {}, t('r.equity')), h('p', { class: 'how', text: t('r.equityHow') }), h('p', { class: 'card-sub', text: t('r.equitySub', { n: fmt.int(m.trades) }) }), eqEl),
    h('section', { class: 'card' }, h('h2', {}, t('r.dist')), h('p', { class: 'how', text: t('r.distHow') }), h('p', { class: 'card-sub', text: t('r.distSub', { n: fmt.int(m.trades) }) }), histEl));
  if (s.equity?.length) {
    lines(eqEl, [{ name: t('r.equityName'), data: s.equity.map((p) => [p.date, p.equity]) }],
      { ariaLabel: t('r.equityAria', { s: s.equity[0].date, e: s.equity.at(-1).date, v: fmt.num(s.equity.at(-1).equity, 3), m: fmt.pct(m.mdd, 1) }), yName: t('c.return'), percentBase: 1 });
  } else eqEl.replaceWith(h('p', { class: 'muted', text: t('r.noEquity') }));
  histogram(histEl, s.pnl_histogram, t('r.distAria', { n: fmt.int(m.trades) }));
}

function periodTab(box, s) {
  const [jl, jd] = judge(s.validation.split_judgement);
  const half = (x) => [fmt.int(x.trades), fmt.pct(x.win_rate, 1, false), fmt.pct(x.mean_ret), fmt.pctp(x.mean_excess)];
  const h1 = half(s.split.first_half);
  const h2 = half(s.split.second_half);
  box.append(h('section', { class: 'card' }, h('h2', {}, t('r.splitCard')), h('p', { class: 'how', text: t('r.splitHow') }), h('p', { class: 'card-sub', text: t('r.splitSub', { d: s.split.split_date }) }),
    table({ columns: [{ label: t('r.metric'), key: 'k' }, { label: t('r.first'), key: 'a', num: true }, { label: t('r.second'), key: 'b', num: true }],
      rows: [t('r.m.trades'), t('r.m.win'), t('r.m.avg'), t('r.m.excess')].map((k, i) => ({ k, a: h1[i], b: h2[i] })) }),
    h('div', { class: 'callout', style: { marginTop: '12px' } }, t('r.judgement', { a: jl, b: jd }))),
  h('section', { class: 'card' }, h('h2', {}, t('r.basis')), h('p', { class: 'how', text: t('help.regime') })));
}

// ================================================================ 오른쪽: 종목별 거래 표
// 정렬은 사용자가 고르는 보기 순서다 (추천 순위가 아님). 기본은 거래 수 많은 순. 같은 값이면 거래 수 → 코드 순
const SORTS = {
  trades_desc: (a, b) => b.trades - a.trades,
  trades_asc: (a, b) => a.trades - b.trades,
  win_desc: (a, b) => (b.win_rate ?? -1) - (a.win_rate ?? -1),
  win_asc: (a, b) => (a.win_rate ?? 2) - (b.win_rate ?? 2),
  ret_desc: (a, b) => (b.mean_ret ?? -Infinity) - (a.mean_ret ?? -Infinity),
  ret_asc: (a, b) => (a.mean_ret ?? Infinity) - (b.mean_ret ?? Infinity),
  name_asc: (a, b) => (a.name || a.ticker).localeCompare(b.name || b.ticker, 'ko'),
};
const sortRows = (rows, key) => [...rows].sort((a, b) => SORTS[key](a, b) || b.trades - a.trades || a.ticker.localeCompare(b.ticker));

// 즐겨찾기 별: 꺼짐 ☆ · 켜짐 ★ (색 --accent). 누르면 행은 열리지 않는다
export function favStar(ticker, name, onChange = null) {
  const btn = h('button', { type: 'button', class: 'star' });
  const paint = () => {
    const on = state.favs.includes(ticker);
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', t(on ? 'ws.favOff' : 'ws.favOn', { n: name || ticker }));
    btn.title = btn.getAttribute('aria-label');
    btn.textContent = on ? '★' : '☆';
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const favs = state.favs;
    state.favs = favs.includes(ticker) ? favs.filter((x) => x !== ticker) : [...favs, ticker];
    paint();
    onChange?.();
  });
  btn.addEventListener('keydown', (e) => e.stopPropagation()); // 행의 Enter 열기와 겹치지 않게
  paint();
  return btn;
}

// 최근 조회 종목: 종목 상세를 연 순서 (최대 8, 같은 종목은 맨 앞으로)
export function rememberStock(code, name) {
  state.recentStocks = [{ code, name: name || '' }, ...state.recentStocks.filter((x) => x.code !== code)].slice(0, 8);
}

function tickersTable(box, s, { onPick, focusTicker = null }) {
  const all = s.tickers;
  const known = new Map(all.map((r) => [r.ticker, r]));
  let sort = SORTS[state.prefs.stockSort] ? state.prefs.stockSort : 'trades_desc';
  let q = '';
  let favOnly = false;
  let shown = 30;
  const listBox = h('div', {});
  const recentBox = h('div', { class: 'recent-row', role: 'group', 'aria-label': t('ws.recent') });
  const qIn = h('input', { type: 'search', id: 'stock-q', placeholder: t('ws.findPh'), 'aria-label': t('ws.find'), autocomplete: 'off',
    oninput: (e) => { q = e.target.value.trim().toLowerCase(); shown = 30; drawList(); } });
  const favBtn = h('button', { type: 'button', class: 'tab', 'aria-pressed': 'false',
    onclick: () => { favOnly = !favOnly; favBtn.setAttribute('aria-pressed', String(favOnly)); shown = 30; drawList(); } }, `★ ${t('ws.favOnly')}`);
  const sortSel = h('select', { id: 'stock-sort', onchange: (e) => { sort = e.target.value; state.prefs = { ...state.prefs, stockSort: sort }; shown = 30; drawList(); } },
    Object.keys(SORTS).map((k) => h('option', { value: k, selected: k === sort }, t(`ws.sort.${k}`))));
  function drawRecent() {
    const list = state.recentStocks;
    if (!list.length) { recentBox.replaceChildren(); recentBox.hidden = true; return; }
    recentBox.hidden = false;
    recentBox.replaceChildren(h('span', { class: 'label' }, t('ws.recent')),
      ...list.map((x) => {
        const here = known.has(x.code);
        return h('button', { type: 'button', class: 'chip-btn', disabled: !here, title: here ? x.code : t('ws.recentAbsent'),
          onclick: () => onPick(x.code) }, x.name || x.code);
      }),
      h('button', { type: 'button', class: 'link-btn', onclick: () => { state.recentStocks = []; drawRecent(); } }, t('ws.recentClear')));
  }
  function drawList() {
    const favs = new Set(state.favs);
    let rows = all;
    if (favOnly) rows = rows.filter((r) => favs.has(r.ticker));
    if (q) rows = rows.filter((r) => r.ticker.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q));
    rows = sortRows(rows, sort);
    if (!rows.length) {
      listBox.replaceChildren(stateView({ title: t('ws.findNone'), message: t('ws.findNoneMsg'),
        actions: [h('button', { class: 'secondary', type: 'button', onclick: () => { q = ''; qIn.value = ''; favOnly = false; favBtn.setAttribute('aria-pressed', 'false'); drawList(); } }, t('ws.findClear'))] }));
      return;
    }
    listBox.replaceChildren(table({
      caption: t('ws.tickersCaption', { s: t(`ws.sort.${sort}`), n: fmt.int(Math.min(shown, rows.length)), all: fmt.int(rows.length) }),
      columns: [
        { label: h('span', { class: 'sr-only' }, t('ws.fav')), render: (r) => favStar(r.ticker, r.name, () => { if (favOnly) drawList(); }) },
        { label: t('r.security'), render: (r) => h('span', {}, h('b', {}, r.name || r.ticker), h('br'), h('small', { class: 'dim num' }, `${r.ticker} · ${r.market}`)) },
        { label: t('r.m.trades'), num: true, render: (r) => fmt.int(r.trades) },
        { label: t('r.m.win'), num: true, render: (r) => fmt.pct(r.win_rate, 1, false) },
        { label: t('r.m.avg'), num: true, render: (r) => updown(r.mean_ret, fmt.pct(r.mean_ret)) },
        { label: t('r.sample'), render: (r) => (r.sample_insufficient ? pill(t('common.sampleInsufficient'), 'warn') : pill(t('common.sufficient'), 'neutral')) },
      ],
      rows: rows.slice(0, shown),
      onRow: (r) => onPick(r.ticker),
    }), ...(rows.length > shown ? [h('div', { class: 'actions', style: { padding: '0 14px 14px' } }, h('button', { class: 'secondary', type: 'button', onclick: () => { shown += 50; drawList(); } }, t('r.showMore')))] : []));
    if (focusTicker) {
      const i = rows.slice(0, shown).findIndex((r) => r.ticker === focusTicker);
      if (i >= 0) listBox.querySelectorAll('tbody tr')[i]?.focus();
      focusTicker = null;
    }
  }
  box.replaceChildren(
    h('div', { class: 'filterbar' }, h('span', {}, t('r.tickersSub'))),
    h('div', { class: 'stock-tools' }, qIn, favBtn,
      h('label', { for: 'stock-sort', class: 'sr-only' }, t('ws.sortLabel')), sortSel),
    recentBox, listBox);
  drawRecent();
  drawList();
}

// 오른쪽: 거래 시점 이전 공시·뉴스 (as_of = 진입일 장 시작 전 08:59 KST, /api/evidence/documents 그대로)
async function tradeDocs(box, d) {
  const asOf = `${d.entry}T08:59:00+09:00`;
  const start = new Date(`${d.entry}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - 30);
  const params = { as_of: asOf, mode: 'observed', tickers: d.ticker, start: start.toISOString().slice(0, 10), end: d.entry, page_size: '50' };
  box.replaceChildren(h('div', { class: 'filterbar' }, h('span', {}, t('ws.docsFor', { n: d.name || d.ticker, d: d.entry, a: when(asOf) }))), loading(t('ar.loading')));
  let res;
  try { res = await api.documents(params); } catch (e) {
    box.replaceChildren(box.firstChild, e.code === 'data_unavailable'
      ? stateView({ kind: 'error', title: t('err.data_unavailable'), message: t('ev.unavailable') })
      : errorView(e, { backHref: '#/evidence', backText: t('nav.evidence') }));
    return;
  }
  const head = box.firstChild;
  if (res.status !== 'ok' || !res.items?.length) {
    box.replaceChildren(head, stateView({ title: t('ws.docsNone'), message: t('ws.docsNoneMsg', { d: params.start, e: d.entry }) }));
    return;
  }
  box.replaceChildren(head, ...res.items.map((x) => h('a', { class: 'doc', href: `#/archive/doc?${new URLSearchParams({ id: x.doc_id, as_of: asOf, mode: 'observed' })}` },
    h('div', { class: 't' }, x.title),
    h('div', { class: 'm' }, h('span', {}, srcLabel(x.source)), x.corp_name && h('span', {}, x.corp_name), h('span', { class: 'num' }, when(x.published_at))))),
  h('p', { class: 'hint', style: { padding: '8px 14px' } }, t('ws.docsNote')));
}

// ================================================================ 작업면
export async function renderRunWorkspace(el, { mode, runId = null, progress = false, ins = 'stocks', ticker = null, query = new URLSearchParams() }) {
  const ctx = { runId: runId || (mode === 'ws' ? state.lastRun : null), progress, ins, ticker: ticker || query.get('stock') || null, docs: null, tab: 'summary',
    strategy: query.get('strategy'), res: null, resP: null, pushed: false, lastTicker: null };
  const cleanups = [];
  const frame = paneFrame({
    cols: [{ label: t('ws.col.cond') }, { label: t('ws.col.result') }, { label: t('ws.col.evidence') }],
    splits: [['c1', t('ws.split.c1')], ['c3', t('ws.split.c3')]],
    current: mode === 'ws' && !ctx.runId ? 0 : 1,
  });
  el.append(h('h1', { class: 'sr-only' }, t(mode === 'focus' ? 'r.title' : 'nav.builder')), frame.root);
  const [p1, p2, p3] = frame.panes;

  // 열 자리: 가운데(결과)와 오른쪽(근거). 자리 바꾸기는 DOM 을 옮길 뿐이라 입력·차트 상태가 남는다
  const slot = () => { const main = h('div', { style: { display: 'flex', flex: '1', minWidth: '0' } }); const aside = h('div', { class: 'aside' });
    return { main, aside, head: h('div', { class: 'pane-head' }, main, aside), body: h('div', { class: 'pane-body' }) }; };
  const center = slot();
  const right = slot();
  const swapBtn = h('button', { class: 'ibtn swap-btn', type: 'button', onclick: () => { toggleSwap(); mount(); swapBtn.focus(); } }, h('span', { 'aria-hidden': 'true' }, '⇄'));
  const swapBox = h('div', { class: 'aside', style: { marginLeft: '0', paddingLeft: '0' } }, swapBtn);
  let mountedSwap = null;
  function mount() {
    const sw = swapped();
    const [a, b] = sw ? [right, center] : [center, right];
    p2.replaceChildren(a.head, a.body);
    p3.replaceChildren(b.head, b.body);
    b.head.append(swapBox);
    const rightName = t(sw ? 'ws.col.result' : 'ws.col.evidence');
    swapBtn.setAttribute('aria-pressed', String(sw));
    swapBtn.setAttribute('aria-label', t('ws.swap', { c: rightName }));
    swapBtn.title = t('ws.swap', { c: rightName });
    frame.relabel([t('ws.col.cond'), t(sw ? 'ws.col.evidence' : 'ws.col.result'), rightName]);
    mountedSwap = sw;
  }
  mount();
  const onResize = () => { if (swapped() !== mountedSwap) mount(); };
  window.addEventListener('resize', onResize);
  cleanups.push(() => window.removeEventListener('resize', onResize));

  // 주소: 실행 결과 화면은 지금 보는 탭을 주소에 남긴다 (새로고침·공유에 같은 상태). 전략 만들기는 #/builder(?stock=)
  function urlFor() {
    if (mode !== 'focus') return ctx.ticker && ctx.ins !== 'report' ? `#/builder?stock=${encodeURIComponent(ctx.ticker)}` : '#/builder';
    const q = ctx.strategy && ctx.res?.strategies.length > 1 ? `?strategy=${encodeURIComponent(ctx.strategy)}` : '';
    const tail = ctx.ins === 'report' ? 'report' : ctx.ticker ? `stock/${encodeURIComponent(ctx.ticker)}` : 'results';
    return `#/runs/${ctx.runId}/${tail}${q}`;
  }
  function syncUrl() {
    if (!ctx.runId || ctx.progress) return;
    if (location.hash !== urlFor()) history.replaceState(null, '', urlFor());
  }
  // 종목 상세를 열면 기록을 하나 남긴다 → 브라우저 뒤로가기(마우스 뒤로 버튼·Alt+←)가 종목 목록으로 돌아온다
  function openStock(tk) {
    ctx.ticker = tk; ctx.ins = 'stocks';
    if (location.hash !== urlFor()) { history.pushState(null, '', urlFor()); ctx.pushed = true; }
    if (innerWidth < 1024) frame.select(2);
    renderRight().then(() => right.body.querySelector('.stock-back')?.focus());
  }
  function closeStock() {
    if (ctx.pushed) { history.back(); return; } // 기록 이동 → 아래 guard 가 목록으로 되돌린다
    ctx.lastTicker = ctx.ticker; ctx.ticker = null; syncUrl(); renderRight();
  }
  // 같은 작업면 안의 기록 이동(종목 열기·닫기)은 화면 전체를 다시 그리지 않고 오른쪽 열만 바꾼다
  setRouteGuard((hash) => {
    const [path, qs] = hash.split('?');
    const qp = new URLSearchParams(qs || '');
    let tk;
    if (mode === 'focus') {
      const m = path.match(/^#\/runs\/([^/]+)\/(results|report|stock)(?:\/([^/]+))?$/);
      if (!m || m[1] !== ctx.runId || (qp.get('strategy') || ctx.strategy) !== ctx.strategy || m[2] === 'report' || ctx.ins === 'report') return false;
      tk = m[3] ? decodeURIComponent(m[3]) : null;
    } else {
      if (path !== '#/builder' || qp.get('preset')) return false;
      tk = qp.get('stock');
    }
    if ((tk || null) === ctx.ticker) return true;
    if (!tk) { ctx.lastTicker = ctx.ticker; ctx.pushed = false; }
    ctx.ticker = tk || null; ctx.ins = 'stocks';
    renderRight();
    return true;
  });

  // 상단바 가운데 칸: 실행 맥락 (좁아지면 기간 → 거래 수 → 검증 순으로 숨김)
  function barCtx(kind, s) {
    const box = document.getElementById('bar-ctx');
    if (!box) return;
    const sep = (p) => h('span', { class: 'sep', 'data-pri': p });
    if (kind === 'none') { box.replaceChildren(h('span', { class: 'sdot' }), h('span', { class: 'name muted' }, t('ws.noRun'))); return; }
    if (kind === 'running') { box.replaceChildren(h('span', { class: 'sdot run' }), h('span', { class: 'name', title: ctx.runId }, ctx.runId), sep(1), h('span', { 'data-pri': 1 }, t('ws.running'))); return; }
    if (kind === 'error') { box.replaceChildren(h('span', { class: 'sdot bad' }), h('span', { class: 'name', title: ctx.runId }, ctx.runId), sep(1), h('span', { class: 'warning', 'data-pri': 1 }, t('ws.failed'))); return; }
    const v = s.validation;
    const n = [v.split_pass, v.fdr_pass, v.random_pass].filter(Boolean).length;
    const inp = s.strategy_input;
    box.replaceChildren(h('span', { class: `sdot ${v.analysis_target ? 'ok' : 'bad'}` }),
      h('span', { class: 'name', title: `${s.strategy} · ${ctx.runId}` }, s.strategy),
      sep(3), h('span', { class: 'num muted', 'data-pri': 3 }, `${inp.period.start} – ${inp.period.end}`),
      sep(2), h('span', { class: 'num', 'data-pri': 2 }, t('common.trades', { n: fmt.int(s.summary.trades) })),
      sep(1), h('span', { 'data-pri': 1, class: v.analysis_target ? '' : 'warning' }, t(v.analysis_target ? 'ws.passN' : 'ws.passNot', { n })));
  }

  const loadResult = () => (ctx.resP ||= api.result(ctx.runId).then((r) => { ctx.res = r; return r; }));
  const curStrategy = () => {
    const names = ctx.res.strategies.map((s) => s.strategy);
    if (!names.includes(ctx.strategy)) ctx.strategy = names[0];
    return ctx.res.strategies.find((s) => s.strategy === ctx.strategy);
  };

  // ---------------------------------------------------------------- 가운데
  let stopProgress = null;
  async function renderCenter() {
    if (stopProgress) { stopProgress(); stopProgress = null; }
    disposeIn(center.body);
    center.aside.replaceChildren();
    if (!ctx.runId) {
      center.main.replaceChildren(h('div', { class: 'ptitle' }, t('r.title')));
      center.body.replaceChildren(mode === 'ws' && state.meta?.status === 'ready' ? examplesBlock(state.meta, runExample)
        : stateView({ title: t('norun.title'), message: t('norun.msg'),
          actions: [h('a', { class: 'secondary', href: '#/presets' }, t('nav.presets')), h('a', { class: 'secondary', href: '#/runs' }, t('nav.runs'))] }));
      barCtx('none');
      return;
    }
    if (ctx.progress) {
      center.main.replaceChildren(h('div', { class: 'ptitle' }, t('ws.progressTitle')));
      center.body.replaceChildren();
      barCtx('running');
      stopProgress = renderProgress(center.body, ctx.runId, { embedded: true, onDone: () => {
        ctx.progress = false; ctx.resP = null; ctx.res = null; ctx.ticker = null; ctx.docs = null;
        if (mode === 'ws') history.replaceState(null, '', '#/builder'); else syncUrl();
        renderCenter(); renderRight();
      } });
      return;
    }
    center.main.replaceChildren(h('div', { class: 'ptitle' }, t('r.title')));
    center.body.replaceChildren(loading(t('r.loading')));
    let res;
    try { res = await loadResult(); } catch (e) {
      ctx.resP = null;
      if (e.code === 'run_not_ready') { ctx.progress = true; renderCenter(); renderRight(); return; }
      center.body.replaceChildren(errorView(e, { onRetry: () => renderCenter() }));
      barCtx('error');
      return;
    }
    const s = curStrategy();
    barCtx('ok', s);
    center.main.replaceChildren(paneTabs([['summary', t('run.tab.summary')], ['curve', t('ws.tab.curve')], ['period', t('ws.tab.period')]], ctx.tab,
      (k) => { ctx.tab = k; renderCenter().then(() => center.main.querySelector('[aria-selected="true"]')?.focus()); }, t('r.title')));
    const box = h('div', {});
    if (res.strategies.length > 1) {
      box.append(h('div', { class: 'strat-row', role: 'group', 'aria-label': t('common.strategyTabs') },
        res.strategies.map((x) => h('button', { type: 'button', class: 'tab', 'aria-pressed': String(x.strategy === s.strategy),
          onclick: () => { ctx.strategy = x.strategy; ctx.ticker = null; ctx.docs = null; syncUrl(); renderCenter(); renderRight(); } }, x.strategy))));
    }
    center.body.replaceChildren(box);
    if (ctx.tab === 'curve') curveTab(box, s);
    else if (ctx.tab === 'period') periodTab(box, s);
    else summaryTab(box, res, s);
  }

  // ---------------------------------------------------------------- 오른쪽
  let rightSeq = 0;
  async function renderRight() {
    const my = ++rightSeq;
    disposeIn(right.body);
    right.aside.replaceChildren();
    right.main.replaceChildren(paneTabs([['stocks', t('nav.stock')], ['docs', t('ws.tab.docs')], ['report', t('nav.report')]], ctx.ins,
      (k) => { ctx.ins = k; syncUrl(); renderRight().then(() => right.main.querySelector('[aria-selected="true"]')?.focus()); }, t('ws.col.evidence')));
    if (!ctx.runId || ctx.progress) {
      right.body.replaceChildren(stateView({ title: t(ctx.progress ? 'ws.rightWaitTitle' : 'norun.title'), message: t(ctx.progress ? 'ws.rightWait' : 'ws.rightNone') }));
      return;
    }
    right.body.replaceChildren(loading());
    let res;
    try { res = await loadResult(); } catch (e) { if (my === rightSeq) right.body.replaceChildren(errorView(e)); return; }
    if (my !== rightSeq) return;
    const s = curStrategy();
    const strategy = res.strategies.length > 1 ? s.strategy : undefined;
    if (ctx.ins === 'report') { right.body.replaceChildren(); await renderReportInto(right.body, ctx.runId, s.strategy); return; }
    if (ctx.ins === 'docs') {
      if (!ctx.docs) { right.body.replaceChildren(stateView({ title: t('ws.docsPickTitle'), message: t('ws.docsPick') })); return; }
      await tradeDocs(right.body, ctx.docs);
      return;
    }
    if (ctx.ticker) {
      right.body.replaceChildren();
      rememberStock(ctx.ticker, s.tickers.find((x) => x.ticker === ctx.ticker)?.name);
      await renderStockDetail(right.body, ctx.runId, ctx.ticker, strategy, {
        onBack: closeStock,
        onDocs: (tr, name) => { ctx.docs = { ticker: ctx.ticker, name, entry: tr.entry_date }; ctx.ins = 'docs'; syncUrl(); renderRight(); },
      });
      return;
    }
    right.body.replaceChildren();
    const focusTicker = ctx.lastTicker;
    ctx.lastTicker = null;
    tickersTable(right.body, s, { onPick: openStock, focusTicker });
  }

  // ---------------------------------------------------------------- 조건 (전략 만들기)
  let builderCtl = null;
  const builderOpts = {
    title: t('ws.col.cond'),
    onSubmitted: (id) => {
      Object.assign(ctx, { runId: id, progress: true, resP: null, res: null, ticker: null, docs: null, tab: 'summary', strategy: null });
      state.lastRun = id;
      history.replaceState(null, '', `#/runs/${id}/progress`);
      frame.select(1);
      renderCenter(); renderRight();
    },
    onReady: (ctl) => { builderCtl = ctl; },
  };
  let disposeBuilder = await renderBuilder(p1, query, builderOpts);
  cleanups.push(() => { if (typeof disposeBuilder === 'function') disposeBuilder(); });
  // 예시 카드: 조건 열을 예시 값으로 다시 그린 뒤 원래 실행 버튼과 같은 경로로 실행한다 (검증·대기열 그대로)
  async function runExample(ex) {
    state.draft = exampleDraft(state.meta, ex);
    state.loadedPreset = null;
    if (typeof disposeBuilder === 'function') disposeBuilder();
    disposeBuilder = await renderBuilder(p1, new URLSearchParams(), builderOpts);
    builderCtl?.run();
  }
  if (ctx.runId) state.lastRun = ctx.runId;
  renderCenter();
  renderRight();
  return () => { cleanups.forEach((f) => f()); if (stopProgress) stopProgress(); };
}
