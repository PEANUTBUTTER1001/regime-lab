// Stock Detail (FR-U4, design.md §6.4). 같은 run_id 의 거래에서만 파생한다. 유사 패턴 차트는 E7 보류로 제외.
import { api } from '../api.js';
import { lines, price } from '../charts.js';
import { errorView, fmt, h, loading, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const reason = (r) => (has(`reason.${r}`) ? t(`reason.${r}`) : r);
const regime = (r) => (r ? t(`regime.${r}`) : t('regime.unavailable'));

async function picker(el, runId, query) {
  el.append(h('h1', { text: t('s.title') }), loading(t('s.loadingList')));
  let res;
  try { res = await api.result(runId); } catch (e) { el.replaceChildren(h('h1', { text: t('s.title') }), errorView(e)); return; }
  const names = res.strategies.map((s) => s.strategy);
  const cur = names.includes(query.get('strategy')) ? query.get('strategy') : names[0];
  const s = res.strategies.find((x) => x.strategy === cur);
  const sel = h('select', { id: 'ticker' }, s.tickers.map((x) => h('option', { value: x.ticker }, `${x.ticker} ${x.name || ''} · ${t('common.trades', { n: fmt.int(x.trades) })}`)));
  const go = h('button', { class: 'primary', type: 'button', onclick: () => { location.hash = `#/runs/${runId}/stock/${sel.value}${names.length > 1 ? `?strategy=${cur}` : ''}`; } }, t('s.open'));
  el.replaceChildren(h('div', { class: 'topline' }, h('div', {}, h('div', { class: 'eyebrow' }, t('p.runId', { id: runId })), h('h1', { text: t('s.title') }),
    h('p', { class: 'lead', text: t('s.pickLead') }))),
  s.tickers.length
    ? h('article', { class: 'card', style: { maxWidth: '560px' } }, h('label', { for: 'ticker' }, t('s.pickLabel', { s: cur })), sel, h('div', { class: 'actions', style: { justifyContent: 'flex-start' } }, go))
    : h('section', { class: 'state' }, h('h2', { text: t('s.noTrades') }), h('p', { text: t('s.noTradesMsg') })));
}

export async function renderStock(el, runId, ticker, query) {
  state.lastRun = runId;
  if (!ticker) return picker(el, runId, query);
  const strategy = query.get('strategy') || undefined;
  el.append(h('h1', { text: t('s.title') }), loading(t('s.loading', { t: ticker })));
  let d;
  try { d = await api.stock(runId, ticker, strategy); } catch (e) {
    el.replaceChildren(h('h1', { text: t('s.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderStock(el, runId, ticker, query); },
      backHref: `#/runs/${runId}/results`, backText: t('common.backResults') }));
    return;
  }
  el.replaceChildren();
  const q = strategy ? `?strategy=${encodeURIComponent(strategy)}` : '';
  const st = d.stats || {};
  const lastClose = d.series.close.at(-1);

  el.append(h('div', { class: 'topline' },
    h('div', {}, h('div', { class: 'eyebrow' }, t('s.eyebrow', { id: runId, s: d.strategy })), h('h1', { text: t('s.title') }),
      h('p', { class: 'lead', text: t('s.lead') })),
    h('a', { class: 'secondary', href: `#/runs/${runId}/results${q}` }, t('common.backResults'))));

  if (d.sample.sample_insufficient) {
    el.append(h('div', { class: 'callout', role: 'note', style: { marginBottom: '18px' } },
      t('s.insufficient', { n: fmt.int(d.sample.trades), m: fmt.int(state.meta?.validation?.min_cell_trades ?? 300) })));
  }

  const priceEl = h('div', { class: 'chart tall' });
  const eqEl = h('div', { class: 'chart zoom' });
  el.append(h('div', { class: 'detail-layout' },
    h('article', { class: 'card' },
      h('div', { class: 'stock-head' },
        h('div', {}, h('h2', {}, d.name || d.ticker, ' ', h('span', { class: 'muted', style: { fontSize: '13px' } }, `${d.ticker}`)),
          h('p', { class: 'card-sub', text: t('s.sub', { n: fmt.int(d.sample.trades), d: d.data_as_of }) })),
        h('div', { style: { textAlign: 'right' } }, h('div', { style: { fontSize: '24px', fontWeight: 800 } }, fmt.krw(lastClose)), h('small', { class: 'muted' }, t('s.lastClose')))),
      priceEl,
      h('div', { class: 'legend' },
        h('span', {}, h('i', { class: 'dot' }), t('s.legendEntry')), h('span', {}, h('i', { class: 'dot red' }), t('s.legendExit')),
        h('span', {}, t('s.legendLines')),
        h('span', {}, h('i', { class: 'dot', style: { background: 'rgba(136,230,188,.35)' } }), t('s.legendBull')),
        h('span', {}, h('i', { class: 'dot', style: { background: 'rgba(239,143,143,.4)' } }), t('s.legendBear')))),
    h('aside', { class: 'card', 'aria-label': t('s.summaryAria') },
      h('h2', {}, t('s.summary')),
      h('p', { class: 'card-sub', text: t('s.summarySub') }),
      table({ columns: [{ label: t('common.item'), key: 'k' }, { label: t('common.value'), key: 'v', num: true }], rows: [
        { k: t('s.included'), v: fmt.int(d.sample.trades) },
        { k: t('s.openAtEnd'), v: fmt.int(d.sample.open_at_data_date) },
        { k: t('r.m.win'), v: fmt.pct(st.win_rate, 1, false) },
        { k: t('r.m.avg'), v: fmt.pct(st.mean_ret) },
        { k: t('r.m.excess'), v: fmt.pctp(st.mean_excess) },
        { k: t('r.m.payoff'), v: fmt.num(st.payoff_ratio) },
      ] }),
      h('div', { class: 'callout', style: { marginTop: '16px' } }, t('s.callout')),
      h('span', { class: 'label' }, t('r.sample')), d.sample.sample_insufficient ? pill(t('common.sampleInsufficient'), 'warn') : pill(t('common.sufficient')))));

  el.append(h('article', { class: 'card', style: { marginTop: '18px' } }, h('h2', {}, t('s.vsHold')),
    h('p', { class: 'card-sub', text: t('s.vsHoldSub') }), eqEl));

  el.append(h('article', { class: 'card', style: { marginTop: '18px' } }, h('h2', {}, t('s.trades')),
    table({ caption: t('s.tradesCaption'),
      columns: [
        { label: t('s.col.signal'), key: 'signal_date' }, { label: t('s.col.entry'), key: 'entry_date' },
        { label: t('s.col.entryPx'), num: true, render: (x) => fmt.int(Math.round(x.entry_price)) },
        { label: t('s.col.exit'), key: 'exit_date' }, { label: t('s.col.exitPx'), num: true, render: (x) => fmt.int(Math.round(x.exit_price)) },
        { label: t('s.col.reason'), render: (x) => reason(x.exit_reason) },
        { label: t('s.col.days'), num: true, render: (x) => fmt.int(x.hold_days) },
        { label: t('s.col.net'), num: true, render: (x) => h('span', { class: fmt.tone(x.net_ret) }, fmt.pct(x.net_ret)) },
        { label: t('s.col.excess'), num: true, render: (x) => fmt.pctp(x.excess_ret) },
        { label: t('s.col.mreg'), render: (x) => regime(x.market_regime) },
        { label: t('s.col.sreg'), render: (x) => regime(x.stock_regime) },
      ],
      rows: d.trades, rowClass: (x) => (x.excluded ? 'dim' : '') })));

  price(priceEl, d, t('s.priceAria', { t: d.ticker, s: d.series.date[0], e: d.series.date.at(-1), n: fmt.int(d.trades.length) }));
  if (d.equity.length) {
    lines(eqEl, [{ name: t('s.stratTrades'), data: d.equity.map((p) => [p.date, p.value]) },
      { name: t('s.buyHold'), data: d.buy_and_hold.map((p) => [p.date, p.value]) }],
    { ariaLabel: t('s.vsAria', { a: fmt.num(d.equity.at(-1).value, 3), b: fmt.num(d.buy_and_hold.at(-1)?.value, 3) }), yName: t('c.return'), percentBase: 1 });
  } else eqEl.replaceWith(h('p', { class: 'muted', text: t('s.noIncluded') }));
}
