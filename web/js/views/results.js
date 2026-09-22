// Results (FR-U3, FR-A1~A5, NFR-9, design.md §6.4). 모든 수치는 같은 run_id 의 /result 응답에서만 가져온다.
import { api } from '../api.js';
import { histogram, lines } from '../charts.js';
import { errorView, fmt, h, loading, metricDefs, passPill, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const JUDGE_KIND = { maintained: '', weakened: 'warn', reversed: 'bad', negative_both: 'bad', sample_insufficient: 'warn', not_applicable: 'neutral' };
const REGIMES = ['bull', 'sideways', 'bear', 'unavailable'];
const CAPS = ['large', 'mid', 'small'];
const judge = (j) => [has(`j.${j}`) ? t(`j.${j}`) : j, has(`jd.${j}`) ? t(`jd.${j}`) : '', JUDGE_KIND[j] ?? 'neutral'];

function metric(label, value, sub, tone = '') {
  return h('div', { class: 'metric' }, h('span', { class: 'label' }, label), h('strong', { class: tone }, value), h('small', {}, sub));
}

function heatCell(c) {
  if (!c) return h('td', { class: 'num heat-cell' }, h('span', { class: 'muted' }, '—'), h('br'), h('small', {}, t('r.zeroTrades')));
  const cls = c.sample_insufficient ? 'insufficient' : c.mean_excess > 0 ? 'pos' : 'neg';
  return h('td', { class: `num heat-cell ${cls}` }, h('b', {}, fmt.pctp(c.mean_excess)), h('br'),
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
        sl: inp.exit.stop_loss_pct ?? t('r.off'), tp: inp.exit.take_profit_pct ?? t('r.off'), h: inp.exit.max_hold_days }) })),
    h('div', {}, v.analysis_target ? pill(t('r.target')) : pill(t('r.notTarget'), 'warn'))));

  if (names.length > 1) {
    el.append(h('div', { class: 'tabs', role: 'group', 'aria-label': t('common.strategyTabs') },
      names.map((n) => h('a', { class: 'tab', href: `#/runs/${runId}/results${q(n)}`, 'aria-pressed': String(n === cur) }, n))));
  }

  // 3중 검증 (FR-A3~A5)
  const [jl, jd, jk] = judge(v.split_judgement);
  el.append(h('section', { class: 'gates', 'aria-label': t('r.gates') },
    h('div', { class: 'gate' }, h('h3', {}, t('r.split'), v.split_pass ? passPill(true) : pill(jl, jk || 'bad')),
      h('p', {}, t('r.splitText', { j: jd, d: s.split.split_date, n1: fmt.int(v.h1_trades), e1: fmt.pctp(v.h1_mean_excess), n2: fmt.int(v.h2_trades), e2: fmt.pctp(v.h2_mean_excess) }))),
    h('div', { class: 'gate' }, h('h3', {}, t('r.fdr'), passPill(v.fdr_pass)),
      h('p', {}, t('r.fdrText', { p: fmt.num(v.p_value, 4), q: fmt.num(state.meta?.validation?.fdr_q ?? 0.1, 2), m: res.fdr_family_size }))),
    h('div', { class: 'gate' }, h('h3', {}, t('r.random'), passPill(v.random_pass)),
      h('p', {}, t('r.randomText', { p: pct, n: fmt.int(state.meta?.validation?.random_iterations ?? 1000) })))));

  // 요약 카드 (거래 수 병기, NFR-9)
  el.append(h('div', { class: 'metrics' },
    metric(t('r.m.trades'), fmt.int(m.trades), `${m.period_start ?? '—'} ~ ${m.period_end ?? '—'}`),
    metric(t('r.m.win'), fmt.pct(m.win_rate, 1, false), t('r.m.winSub', { n: fmt.int(m.trades) })),
    metric(t('r.m.avg'), fmt.pct(m.mean_ret), t('r.m.avgSub'), fmt.tone(m.mean_ret)),
    metric(t('r.m.excess'), fmt.pctp(m.mean_excess), t('r.m.excessSub'), fmt.tone(m.mean_excess)),
    metric(t('r.m.random'), t('r.m.randomVal', { p: pct }), t('r.m.randomSub', { p: pct, k: fmt.int(state.meta?.validation?.random_iterations ?? 1000), n: fmt.int(v.random_n_trades) }), v.random_pass ? 'positive' : 'warning')),
  h('div', { class: 'metrics' },
    metric(t('r.m.median'), fmt.pct(m.median_ret), t('r.m.perTrade'), fmt.tone(m.median_ret)),
    metric(t('r.m.payoff'), fmt.num(m.payoff_ratio), t('r.m.payoffSub')),
    metric(t('r.m.sharpe'), fmt.num(m.sharpe, 3), t('r.m.sharpeSub')),
    metric(t('r.m.mdd'), fmt.pct(m.mdd, 1), t('r.m.mddSub'), 'negative'),
    metric(t('r.m.excl'), `${fmt.int(m.excluded_trades)} / ${fmt.int(m.skipped_entries)}`, t('r.m.exclSub'))));

  // 자산곡선 + 히트맵
  const eqEl = h('div', { class: 'chart zoom' });
  let basis = 'cells_market';
  let market = inp.markets[0];
  const heatBox = h('div', {});
  const drawHeat = () => heatBox.replaceChildren(heatmap(s[basis], market, minN));
  const toggles = h('div', { class: 'tabs' },
    toggleGroup(t('r.basis'), [['cells_market', t('r.basisMarket')], ['cells_stock', t('r.basisStock')]], basis, (k) => { basis = k; drawHeat(); }),
    toggleGroup(t('b.market'), inp.markets.map((mk) => [mk, mk]), market, (k) => { market = k; drawHeat(); }));
  el.append(h('div', { class: 'result-grid' },
    h('article', { class: 'card' }, h('h2', {}, t('r.equity')), h('p', { class: 'card-sub', text: t('r.equitySub', { n: fmt.int(m.trades) }) }), eqEl),
    h('article', { class: 'card' }, h('h2', {}, t('r.heat')), h('p', { class: 'card-sub', text: t('r.heatSub') }), toggles, heatBox)));
  drawHeat();

  // 손익 분포 + 기간 분할
  const histEl = h('div', { class: 'chart' });
  const half = (x) => [fmt.int(x.trades), fmt.pct(x.win_rate, 1, false), fmt.pct(x.mean_ret), fmt.pctp(x.mean_excess)];
  const h1 = half(s.split.first_half);
  const h2 = half(s.split.second_half);
  el.append(h('div', { class: 'grid-two', style: { marginBottom: '18px' } },
    h('article', { class: 'card' }, h('h2', {}, t('r.dist')), h('p', { class: 'card-sub', text: t('r.distSub', { n: fmt.int(m.trades) }) }), histEl),
    h('article', { class: 'card' }, h('h2', {}, t('r.splitCard')), h('p', { class: 'card-sub', text: t('r.splitSub', { d: s.split.split_date }) }),
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
    }), all.length > shown ? h('div', { class: 'actions' }, h('button', { class: 'secondary', type: 'button', onclick: () => { shown += 50; drawTickers(); } }, t('r.showMore'))) : null);
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
