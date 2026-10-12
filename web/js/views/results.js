// Results (FR-U3, FR-A1~A5, NFR-9, design.md §6.4). 모든 수치는 같은 run_id 의 /result 응답에서만 가져온다.
import { api } from '../api.js';
import { histogram, lines } from '../charts.js';
import { errorView, fmt, h, helpTip, loading, metricDefs, passPill, pill, table, updown } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const JUDGE_KIND = { maintained: '', weakened: 'warn', reversed: 'bad', negative_both: 'bad', sample_insufficient: 'warn', not_applicable: 'neutral' };
const REGIMES = ['bull', 'sideways', 'bear', 'unavailable'];
const CAPS = ['large', 'mid', 'small'];
const judge = (j) => [has(`j.${j}`) ? t(`j.${j}`) : j, has(`jd.${j}`) ? t(`jd.${j}`) : '', JUDGE_KIND[j] ?? 'neutral'];

// help: 어려운 지표 이름 옆 ? 도움말 문구 (E18)
function metric(label, value, sub, tone = '', help = null) {
  return h('div', { class: 'metric' }, h('span', { class: 'label' }, label, help && helpTip(help, label)), h('strong', { class: tone }, value), h('small', {}, sub));
}

function heatCell(c) {
  if (!c) return h('td', { class: 'num heat-cell' }, h('span', { class: 'muted' }, '—'), h('br'), h('small', {}, t('r.zeroTrades')));
  // 한국식 색: 시장보다 더 벌면 빨강 ▲, 덜 벌면 파랑 ▼ (E18 ④). 표본 부족 칸은 빗금만
  const cls = c.sample_insufficient ? 'insufficient' : c.mean_excess > 0 ? 'pos' : c.mean_excess < 0 ? 'neg' : '';
  const mark = cls === 'pos' ? '▲' : cls === 'neg' ? '▼' : '';
  return h('td', { class: `num heat-cell ${cls}` }, h('b', {}, mark && h('span', { class: 'updown-mark', 'aria-hidden': 'true' }, mark), fmt.pctp(c.mean_excess)), h('br'),
    h('small', {}, `${t('common.trades', { n: fmt.int(c.trades) })}${c.sample_insufficient ? t('r.insufficientTag') : ''}`));
}

function heatmap(cells, market, minN) {
  const by = new Map(cells.filter((c) => c.market === market).map((c) => [`${c.regime}|${c.cap_group}`, c]));
  const rows = REGIMES.filter((r) => CAPS.some((c) => by.has(`${r}|${c}`)));
  if (!rows.length) return h('p', { class: 'muted', text: t('r.noTradesIn', { mk: market }) });
  const head = h('tr', {}, h('th', { scope: 'col' }, t('r.regime')), CAPS.map((c) => h('th', { scope: 'col', class: 'num' }, t(`cap.${c}`))));
  const body = rows.map((r) => h('tr', {}, h('th', { scope: 'row' }, t(`regime.${r}`)), CAPS.map((cap) => heatCell(by.get(`${r}|${cap}`)))));
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'heat' }, h('caption', {}, t('r.heatCaption', { mk: market, n: fmt.int(minN) })), h('thead', {}, head), h('tbody', {}, body)));
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

// 청산 사유별 건수 (API summary.exit_by_reason). 0건 사유는 숨기고 많은 순으로 — 선택 청산 규칙이 실제로 얼마나
// 작동했는지 보이기 위함. 기준일 보유 중(end_of_data)은 집계 제외 거래라 따로 표시하지 않는다.
function exitReasons(by) {
  const rows = Object.entries(by || {}).filter(([k, n]) => n > 0 && k !== 'end_of_data').sort((a, b) => b[1] - a[1]);
  if (!rows.length) return null;
  const label = (k) => (has(`reason.${k}`) ? t(`reason.${k}`) : k);
  return h('div', { class: 'exit-reasons', role: 'group', 'aria-label': t('r.exitReasons') },
    h('span', { class: 'label' }, t('r.exitReasons')), ...rows.map(([k, n]) => pill(`${label(k)} ${fmt.int(n)}`, 'neutral')));
}

export async function renderResults(el, runId, query) {
  state.lastRun = runId;
  el.append(h('h1', { text: t('r.title') }), loading(t('r.loading')));
  let res;
  try { res = await api.result(runId); } catch (e) {
    el.replaceChildren(h('h1', { text: t('r.title') }), errorView(e, {
      onRetry: () => { el.replaceChildren(); renderResults(el, runId, query); },
      ...(e.code === 'run_not_ready' ? { backHref: `#/runs/${runId}/progress`, backText: t('common.viewProgress') } : {}),
    }));
    return;
  }
  el.replaceChildren();
  const names = res.strategies.map((s) => s.strategy);
  const cur = names.includes(query.get('strategy')) ? query.get('strategy') : names[0];
  const s = res.strategies.find((x) => x.strategy === cur);
  const m = s.summary;
  const v = s.validation;
  const inp = s.strategy_input;
  const minN = res.min_cell_trades;
  const q = (name) => (names.length > 1 ? `?strategy=${encodeURIComponent(name)}` : '');
  const pct = fmt.num((v.random_percentile ?? 0) * 100, 1);

  el.append(h('div', { class: 'topline' },
    h('div', {}, h('div', { class: 'eyebrow' }, t('p.runId', { id: runId })), h('h1', { text: t('r.title') }),
      h('p', { class: 'lead', text: t('r.lead', {
        name: cur, pats: inp.patterns.map((p) => (has(`pat.${p}`) ? t(`pat.${p}`) : p)).join(` ${inp.combine.toUpperCase()} `),
        mk: inp.markets.join(' + '), s: inp.period.start, e: inp.period.end,
        sl: inp.exit.stop_loss_pct ?? t('r.off'), tp: inp.exit.take_profit_pct ?? t('r.off'), h: inp.exit.max_hold_days })
        + extraExits(inp.exit) }))));

  if (names.length > 1) {
    el.append(h('div', { class: 'tabs', role: 'group', 'aria-label': t('common.strategyTabs') },
      names.map((n) => h('a', { class: 'tab', href: `#/runs/${runId}/results${q(n)}`, 'aria-pressed': String(n === cur) }, n))));
  }

  // 판정 블록 (E18 2차): 결론(서버 판정 analysis_target)을 먼저, 그 근거인 검증 3종을 질문으로 보여 준다 (FR-A3~A5).
  // 상세 수치(p·q·m·백분위·분할일)는 그대로 둔다 — 쉬운 말로 바꿔도 3중 검증 표시는 약화하지 않는다.
  const [jl, jd, jk] = judge(v.split_judgement);
  const check = (question, name, help, result, detail) => h('li', { class: 'verdict-row' },
    h('div', { class: 'verdict-q' }, h('span', { class: 'with-tip' }, h('b', {}, question), helpTip(help, name)), h('small', { class: 'muted' }, name)),
    h('div', { class: 'verdict-res' }, result),
    h('p', { class: 'verdict-detail' }, detail));
  const target = !!v.analysis_target;
  el.append(h('section', { class: `verdict ${target ? 'ok' : 'no'}`, 'aria-label': t('r.gates') },
    h('div', { class: 'verdict-head' }, h('span', { class: 'verdict-mark', 'aria-hidden': 'true' }, target ? '✓' : '!'),
      h('div', {}, h('h2', {}, t(target ? 'r.vTarget' : 'r.vNotTarget')), h('p', {}, t(target ? 'r.vTargetMsg' : 'r.vNotTargetMsg')))),
    h('ol', { class: 'verdict-list' },
      check(t('r.qSplit'), t('r.split'), t('help.split'), v.split_pass ? passPill(true) : pill(jl, jk || 'bad'),
        t('r.splitText', { j: jd, d: s.split.split_date, n1: fmt.int(v.h1_trades), e1: fmt.pctp(v.h1_mean_excess), n2: fmt.int(v.h2_trades), e2: fmt.pctp(v.h2_mean_excess) })),
      check(t('r.qFdr'), t('r.fdr'), t('help.fdr'), passPill(v.fdr_pass),
        t('r.fdrText', { p: fmt.num(v.p_value, 4), q: fmt.num(state.meta?.validation?.fdr_q ?? 0.1, 2), m: res.fdr_family_size })),
      check(t('r.qRandom'), t('r.random'), t('help.random'), passPill(v.random_pass),
        t('r.randomText', { p: pct, n: fmt.int(state.meta?.validation?.random_iterations ?? 1000) })))));

  // 핵심 지표 4개 (거래 수 병기, NFR-9)
  el.append(h('div', { class: 'metrics key' },
    metric(t('r.m.trades'), fmt.int(m.trades), `${m.period_start ?? '—'} ~ ${m.period_end ?? '—'}`),
    metric(t('r.m.win'), fmt.pct(m.win_rate, 1, false), t('r.m.winSub', { n: fmt.int(m.trades) })),
    metric(t('r.m.avg'), updown(m.mean_ret, fmt.pct(m.mean_ret)), t('r.m.avgSub')),
    metric(t('r.m.excess'), updown(m.mean_excess, fmt.pctp(m.mean_excess)), t('r.m.excessSub'), '', t('help.excess'))));

  // 자세한 지표: 나머지 6개는 표로 (항목 · 값 · 설명). 도움말이 잘리지 않게 가로 스크롤 감싸개 없이 둔다
  const detail = [
    [t('r.m.random'), h('span', { class: v.random_pass ? 'positive' : 'warning' }, t('r.m.randomVal', { p: pct })),
      t('r.m.randomSub', { p: pct, k: fmt.int(state.meta?.validation?.random_iterations ?? 1000), n: fmt.int(v.random_n_trades) }), t('help.randomPct')],
    [t('r.m.median'), updown(m.median_ret, fmt.pct(m.median_ret)), t('r.m.perTrade')],
    [t('r.m.payoff'), fmt.num(m.payoff_ratio), t('r.m.payoffSub'), t('help.payoff')],
    [t('r.m.sharpe'), fmt.num(m.sharpe, 3), t('r.m.sharpeSub'), t('help.sharpe')],
    [t('r.m.mdd'), updown(m.mdd, fmt.pct(m.mdd, 1)), t('r.m.mddSub'), t('help.mdd')],
    [t('r.m.excl'), `${fmt.int(m.excluded_trades)} / ${fmt.int(m.skipped_entries)}`, t('r.m.exclSub')],
  ];
  el.append(h('section', { class: 'detail-metrics', 'aria-labelledby': 'detail-metrics-title' },
    h('h2', { id: 'detail-metrics-title' }, t('r.detail')),
    h('table', { class: 'detail-table' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('common.item')), h('th', { scope: 'col', class: 'num' }, t('common.value')), h('th', { scope: 'col' }, t('r.detailDesc')))),
      h('tbody', {}, detail.map(([name, value, desc, help]) => h('tr', {},
        h('th', { scope: 'row' }, h('span', { class: 'with-tip' }, name, help && helpTip(help, name))),
        h('td', { class: 'num' }, value), h('td', { class: 'muted' }, desc)))))));
  const reasons = exitReasons(m.exit_by_reason);
  if (reasons) el.append(reasons);

  // 자산곡선 + 히트맵
  const eqEl = h('div', { class: 'chart zoom' });
  let basis = 'cells_market';
  let market = inp.markets[0];
  const heatBox = h('div', {});
  const drawHeat = () => heatBox.replaceChildren(heatmap(s[basis], market, minN));
  const toggles = h('div', { class: 'tabs' },
    toggleGroup(t('r.basis'), [['cells_market', t('r.basisMarket')], ['cells_stock', t('r.basisStock')]], basis, (k) => { basis = k; drawHeat(); }),
    helpTip(t('help.regime'), t('r.basis')),
    toggleGroup(t('b.market'), inp.markets.map((mk) => [mk, mk]), market, (k) => { market = k; drawHeat(); }));
  el.append(h('div', { class: 'result-grid' },
    h('article', { class: 'card' }, h('h2', {}, t('r.equity')), h('p', { class: 'how', text: t('r.equityHow') }), h('p', { class: 'card-sub', text: t('r.equitySub', { n: fmt.int(m.trades) }) }), eqEl),
    h('article', { class: 'card' }, h('h2', {}, t('r.heat')), h('p', { class: 'how', text: t('r.heatHow') }), h('p', { class: 'card-sub', text: t('r.heatSub') }), toggles, heatBox)));
  drawHeat();

  // 손익 분포 + 기간 분할 (차트마다 '보는 법' 한 줄, E18 2차)
  const histEl = h('div', { class: 'chart' });
  const half = (x) => [fmt.int(x.trades), fmt.pct(x.win_rate, 1, false), fmt.pct(x.mean_ret), fmt.pctp(x.mean_excess)];
  const h1 = half(s.split.first_half);
  const h2 = half(s.split.second_half);
  el.append(h('div', { class: 'grid-two', style: { marginBottom: '18px' } },
    h('article', { class: 'card' }, h('h2', {}, t('r.dist')), h('p', { class: 'how', text: t('r.distHow') }), h('p', { class: 'card-sub', text: t('r.distSub', { n: fmt.int(m.trades) }) }), histEl),
    h('article', { class: 'card' }, h('h2', {}, t('r.splitCard')), h('p', { class: 'how', text: t('r.splitHow') }), h('p', { class: 'card-sub', text: t('r.splitSub', { d: s.split.split_date }) }),
      table({ columns: [{ label: t('r.metric'), key: 'k' }, { label: t('r.first'), key: 'a', num: true }, { label: t('r.second'), key: 'b', num: true }],
        rows: [t('r.m.trades'), t('r.m.win'), t('r.m.avg'), t('r.m.excess')].map((k, i) => ({ k, a: h1[i], b: h2[i] })) }),
      h('div', { class: 'callout', style: { marginTop: '14px' } }, t('r.judgement', { a: jl, b: jd })))));

  // 종목별 표본 표
  const all = s.tickers;
  let shown = 30;
  const tBox = h('div', {});
  const drawTickers = () => {
    tBox.replaceChildren(table({
      caption: t('r.tickersCaption', { n: Math.min(shown, all.length), all: fmt.int(all.length) }),
      columns: [
        { label: t('r.security'), render: (r) => h('span', {}, h('b', {}, r.ticker), ' ', r.name || '') },
        { label: t('b.market'), key: 'market' },
        { label: t('r.m.trades'), num: true, render: (r) => fmt.int(r.trades) },
        { label: t('r.m.win'), num: true, render: (r) => fmt.pct(r.win_rate, 1, false) },
        { label: t('r.m.avg'), num: true, render: (r) => fmt.pct(r.mean_ret) },
        { label: t('r.avgExcessShort'), num: true, render: (r) => fmt.pctp(r.mean_excess) },
        { label: t('r.sample'), num: true, render: (r) => (r.sample_insufficient ? pill(t('common.sampleInsufficient'), 'warn') : pill(t('common.sufficient'))) },
      ],
      rows: all.slice(0, shown),
      onRow: (r) => { location.hash = `#/runs/${runId}/stock/${r.ticker}${q(cur)}`; },
    }), ...(all.length > shown ? [h('div', { class: 'actions' }, h('button', { class: 'secondary', type: 'button', onclick: () => { shown += 50; drawTickers(); } }, t('r.showMore')))] : []));
  };
  drawTickers();
  el.append(h('article', { class: 'card' },
    h('div', { class: 'stock-head' }, h('div', {}, h('h2', {}, t('r.tickers')), h('p', { class: 'card-sub', text: t('r.tickersSub') })),
      h('a', { class: 'secondary', href: `#/runs/${runId}/report${q(cur)}` }, t('r.openReport'))),
    tBox, metricDefs(res.metric_definitions)));

  // 차트 (DOM 부착 후)
  if (s.equity?.length) {
    lines(eqEl, [{ name: t('r.equityName'), data: s.equity.map((p) => [p.date, p.equity]) }],
      { ariaLabel: t('r.equityAria', { s: s.equity[0].date, e: s.equity.at(-1).date, v: fmt.num(s.equity.at(-1).equity, 3), m: fmt.pct(m.mdd, 1) }), yName: t('c.return'), percentBase: 1 });
  } else eqEl.replaceWith(h('p', { class: 'muted', text: t('r.noEquity') }));
  histogram(histEl, s.pnl_histogram, t('r.distAria', { n: fmt.int(m.trades) }));
}
