// 종목 상세 (FR-U4, design.md §8.2): 작업면 오른쪽 열 안에서 한 종목의 거래 근거를 보인다.
// 같은 run_id 의 거래에서만 파생한다. 유사 패턴 차트는 E7 보류로 제외.
import { api } from '../api.js';
import { lines, price } from '../charts.js';
import { errorView, fmt, h, loading, pill, table, updown } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';
import { favStar } from './results.js';

const reason = (r) => (has(`reason.${r}`) ? t(`reason.${r}`) : r);
const regime = (r) => (r ? t(`regime.${r}`) : t('regime.unavailable'));

// opts.onBack(): 종목 목록으로, opts.onDocs(trade, name): 그 거래 시점 이전 공시·뉴스 (오른쪽 공시·뉴스 탭)
export async function renderStockDetail(el, runId, ticker, strategy, opts = {}) {
  const back = h('button', { class: 'link-btn stock-back', type: 'button', onclick: () => opts.onBack?.() }, `← ${t('ws.backStocks')}`);
  el.append(h('div', { class: 'filterbar' }, back), loading(t('s.loading', { t: ticker })));
  let d;
  try { d = await api.stock(runId, ticker, strategy); } catch (e) {
    el.replaceChildren(h('div', { class: 'filterbar' }, back), errorView(e, { onRetry: () => { el.replaceChildren(); renderStockDetail(el, runId, ticker, strategy, opts); },
      backHref: `#/runs/${runId}/results`, backText: t('common.backResults') }));
    return;
  }
  const st = d.stats || {};
  const lastClose = d.series.close.at(-1);
  const priceEl = h('div', { class: 'chart tall' });
  const eqEl = h('div', { class: 'chart zoom' });
  el.replaceChildren(h('div', { class: 'filterbar' }, back, h('span', { class: 'push num' }, t('s.eyebrow', { id: runId, s: d.strategy }))));

  el.append(h('section', { class: 'card', 'aria-labelledby': 'stock-title' },
    h('div', { class: 'stock-head' },
      h('div', {}, h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } }, favStar(d.ticker, d.name),
          h('h2', { id: 'stock-title', style: { fontSize: 'var(--fs-head)' } }, d.name || d.ticker, ' ', h('span', { class: 'dim num', style: { fontSize: 'var(--fs-body)' } }, d.ticker))),
        h('p', { class: 'card-sub', style: { margin: '2px 0 0' }, text: t('s.sub', { n: fmt.int(d.sample.trades), d: d.data_as_of }) })),
      h('div', { style: { textAlign: 'right' } }, h('b', { class: 'num', style: { fontSize: 'var(--fs-head)' } }, fmt.krw(lastClose)), h('br'), h('small', { class: 'dim' }, t('s.lastClose')))),
    d.sample.sample_insufficient ? h('div', { class: 'callout', role: 'note', style: { marginBottom: '10px' } },
      t('s.insufficient', { n: fmt.int(d.sample.trades), m: fmt.int(state.meta?.validation?.min_cell_trades ?? 300) })) : null,
    priceEl,
    h('div', { class: 'legend' },
      h('span', {}, h('i', { class: 'dot up' }), t('s.legendEntry')), h('span', {}, h('i', { class: 'dot down' }), t('s.legendExit')),
      h('span', {}, t('s.legendLines')),
      h('span', {}, h('i', { class: 'dot shade-up' }), t('s.legendBull')),
      h('span', {}, h('i', { class: 'dot shade-down' }), t('s.legendBear')))));

  el.append(h('section', { class: 'card', 'aria-label': t('s.summaryAria') },
    h('h2', {}, t('s.summary')), h('p', { class: 'card-sub', text: t('s.summarySub') }),
    table({ columns: [{ label: t('common.item'), key: 'k' }, { label: t('common.value'), key: 'v', num: true }], rows: [
      { k: t('s.included'), v: fmt.int(d.sample.trades) },
      { k: t('s.openAtEnd'), v: fmt.int(d.sample.open_at_data_date) },
      { k: t('r.m.win'), v: fmt.pct(st.win_rate, 1, false) },
      { k: t('r.m.avg'), v: updown(st.mean_ret, fmt.pct(st.mean_ret)) },
      { k: t('r.m.excess'), v: updown(st.mean_excess, fmt.pctp(st.mean_excess)) },
      { k: t('r.m.payoff'), v: fmt.num(st.payoff_ratio) },
      { k: t('r.sample'), v: d.sample.sample_insufficient ? pill(t('common.sampleInsufficient'), 'warn') : pill(t('common.sufficient'), 'neutral') },
    ] }),
    h('p', { class: 'hint', style: { marginTop: '8px' } }, t('s.callout'))));

  el.append(h('section', { class: 'card' }, h('h2', {}, t('s.vsHold')), h('p', { class: 'card-sub', text: t('s.vsHoldSub') }), eqEl));

  // 거래 표: 칸 줄바꿈 없이, 표 영역 안에서 가로·세로로 넘긴다 (첫 열·머리 고정) — 거래가 많아도 가로 스크롤바가 바로 보인다
  const trades = table({ caption: t('s.tradesCaption'),
      columns: [
        { label: t('s.col.entry'), render: (x) => h('span', { class: 'num' }, x.entry_date) },
        { label: t('s.col.exit'), render: (x) => h('span', { class: 'num' }, x.exit_date || '—') },
        { label: t('s.col.net'), num: true, render: (x) => updown(x.net_ret, fmt.pct(x.net_ret)) },
        { label: t('s.col.reason'), render: (x) => reason(x.exit_reason) },
        { label: t('s.col.days'), num: true, render: (x) => fmt.int(x.hold_days) },
        { label: t('ws.docsCol'), render: (x) => h('button', { class: 'link-btn', type: 'button', 'aria-label': t('ws.docsAria', { d: x.entry_date }),
          onclick: () => opts.onDocs?.(x, d.name || d.ticker) }, t('ws.docsBtn')) },
        { label: t('s.col.signal'), render: (x) => h('span', { class: 'num' }, x.signal_date) },
        { label: t('s.col.entryPx'), num: true, render: (x) => fmt.int(Math.round(x.entry_price)) },
        { label: t('s.col.exitPx'), num: true, render: (x) => (x.exit_price == null ? '—' : fmt.int(Math.round(x.exit_price))) },
        { label: t('s.col.excess'), num: true, render: (x) => fmt.pctp(x.excess_ret) },
        { label: t('s.col.mreg'), render: (x) => regime(x.market_regime) },
        { label: t('s.col.sreg'), render: (x) => regime(x.stock_regime) },
      ],
      rows: d.trades, rowClass: (x) => (x.excluded ? 'dim' : '') });
  trades.classList.add('trades');
  trades.tabIndex = 0;
  trades.setAttribute('role', 'region');
  trades.setAttribute('aria-label', t('s.trades'));
  el.append(h('section', { class: 'card', style: { padding: '14px 0 0' } }, h('h2', { style: { padding: '0 16px 8px' } }, t('s.trades')), trades));

  price(priceEl, d, t('s.priceAria', { t: d.ticker, s: d.series.date[0], e: d.series.date.at(-1), n: fmt.int(d.trades.length) }));
  if (d.equity.length) {
    lines(eqEl, [{ name: t('s.stratTrades'), data: d.equity.map((p) => [p.date, p.value]) },
      { name: t('s.buyHold'), data: d.buy_and_hold.map((p) => [p.date, p.value]) }],
    { ariaLabel: t('s.vsAria', { a: fmt.num(d.equity.at(-1).value, 3), b: fmt.num(d.buy_and_hold.at(-1)?.value, 3) }), yName: t('c.return'), percentBase: 1 });
  } else eqEl.replaceWith(h('p', { class: 'muted', text: t('s.noIncluded') }));
}
