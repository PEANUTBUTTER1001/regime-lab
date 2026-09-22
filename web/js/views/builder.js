// Strategy Builder (FR-U1, FR-X5, design.md §6.2). 기본값·허용 범위는 모두 /api/meta 에서 받는다.
// 입력 검증은 전송 전(여기)과 서버(422) 양쪽에서 한다. 서버 오류 필드는 같은 입력칸 아래에 표시한다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, toast } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const patLabel = (p) => (has(`pat.${p.name}`) ? t(`pat.${p.name}`) : p.label);
const patRule = (p) => (has(`rule.${p.name}`) ? t(`rule.${p.name}`) : p.rule);

function defaults(meta) {
  const ex = meta.exit_defaults;
  return {
    name: 'my_strategy', patterns: ['breakout_20d'], combine: 'or',
    useStop: ex.stop_loss_pct != null, stop: ex.stop_loss_pct ?? -8,
    useProfit: ex.take_profit_pct != null, profit: ex.take_profit_pct ?? 20, hold: ex.max_hold_days,
    markets: [...meta.markets], start: meta.backtest_start, end: meta.data_as_of,
    minValue: meta.min_avg_value_krw, caps: [...meta.cap_groups],
  };
}

function validate(f, meta) {
  const e = {};
  const lim = meta.exit_limits;
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(f.name)) e.name = t('v.name');
  if (!f.patterns.length) e.patterns = t('v.patterns');
  const num = (v) => (v === '' || v == null || Number.isNaN(Number(v)) ? null : Number(v));
  if (f.useStop) { const v = num(f.stop); if (v == null || v < lim.stop_loss_pct[0] || v > lim.stop_loss_pct[1]) e['exit.stop_loss_pct'] = t('v.range', { a: lim.stop_loss_pct[0], b: lim.stop_loss_pct[1] }); }
  if (f.useProfit) { const v = num(f.profit); if (v == null || v < lim.take_profit_pct[0] || v > lim.take_profit_pct[1]) e['exit.take_profit_pct'] = t('v.range', { a: lim.take_profit_pct[0], b: lim.take_profit_pct[1] }); }
  const hd = num(f.hold);
  if (hd == null || !Number.isInteger(hd) || hd < lim.max_hold_days[0] || hd > lim.max_hold_days[1]) e['exit.max_hold_days'] = t('v.hold', { a: lim.max_hold_days[0], b: lim.max_hold_days[1] });
  if (!f.markets.length) e.markets = t('v.markets');
  if (!DATE_RE.test(f.start)) e['period.start'] = t('v.date');
  else if (f.start < meta.backtest_start) e['period.start'] = t('v.after', { d: meta.backtest_start });
  if (!DATE_RE.test(f.end)) e['period.end'] = t('v.date');
  else if (f.end > meta.data_as_of) e['period.end'] = t('v.before', { d: meta.data_as_of });
  if (!e['period.start'] && !e['period.end'] && f.start > f.end) e.period = t('v.order');
  const mv = num(f.minValue);
  if (mv == null || !Number.isInteger(mv) || mv < meta.min_avg_value_krw) e.min_avg_value_krw = t('v.value', { v: fmt.int(meta.min_avg_value_krw) });
  if (!f.caps.length) e.cap_groups = t('v.caps');
  return e;
}

function toBody(f) {
  return {
    name: f.name, patterns: f.patterns, combine: f.combine,
    exit: { stop_loss_pct: f.useStop ? Number(f.stop) : null, take_profit_pct: f.useProfit ? Number(f.profit) : null,
      max_hold_days: Number(f.hold), trailing_stop_pct: null },
    markets: f.markets, period: { start: f.start, end: f.end }, min_avg_value_krw: Number(f.minValue), cap_groups: f.caps,
  };
}

export async function renderBuilder(el) {
  let meta = state.meta;
  if (!meta || meta.status !== 'ready') {
    el.append(h('h1', { text: t('b.title') }), loading(t('b.loadingData')));
    try { meta = await api.meta(); state.meta = meta; } catch (e) {
      el.replaceChildren(h('h1', { text: t('b.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderBuilder(el); } }));
      return;
    }
    if (meta.status !== 'ready') {
      const tm = setTimeout(() => { el.replaceChildren(); renderBuilder(el); }, 2000);
      return () => clearTimeout(tm);
    }
    el.replaceChildren();
  }

  const f = { ...defaults(meta), ...(state.draft || {}) };
  const errs = {};
  const errEls = {};
  const errorSlot = (key) => (errEls[key] = h('p', { class: 'field-error hidden', id: `err-${key.replace(/\W/g, '-')}` }));
  const described = (key) => `err-${key.replace(/\W/g, '-')}`;
  const cost = meta.execution.round_trip_cost_pct.toFixed(2);

  // ---------------------------------------------------------------- Buy conditions
  const seg = h('div', { class: 'segment', role: 'group', 'aria-label': t('b.logic') },
    ['and', 'or'].map((c) => h('button', { type: 'button', 'aria-pressed': String(f.combine === c), 'data-c': c,
      onclick: () => { f.combine = c; seg.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.c === c))); save(); } }, c.toUpperCase())));
  const patternChecks = meta.patterns.map((p) => {
    const box = h('input', { type: 'checkbox', checked: f.patterns.includes(p.name), 'aria-label': patLabel(p), 'aria-describedby': described('patterns') });
    const lab = h('label', { class: `check${f.patterns.includes(p.name) ? ' selected' : ''}` }, box, h('span', {}, patLabel(p), h('small', {}, patRule(p))));
    box.addEventListener('change', () => {
      f.patterns = meta.patterns.map((q) => q.name).filter((n) => (n === p.name ? box.checked : f.patterns.includes(n)));
      lab.classList.toggle('selected', box.checked); save();
    });
    return lab;
  });
  const buyCard = h('article', { class: 'card' }, h('h2', {}, t('b.buy')),
    h('p', { class: 'card-sub', text: t('b.buySub') }),
    h('span', { class: 'label' }, t('b.logic')), seg,
    h('div', { class: 'checks', role: 'group', 'aria-label': t('b.patterns') }, patternChecks), errorSlot('patterns'));

  // ---------------------------------------------------------------- Exit rules
  const numInput = (id, key, value, mode = 'decimal') => h('input', { id, inputmode: mode, value: String(value ?? ''), 'aria-describedby': described(key),
    oninput: (e) => { f[id] = e.target.value.trim(); save(); } });
  const stopIn = numInput('stop', 'exit.stop_loss_pct', f.stop);
  const profitIn = numInput('profit', 'exit.take_profit_pct', f.profit);
  const holdIn = numInput('hold', 'exit.max_hold_days', f.hold, 'numeric');
  const useToggle = (key, input, word) => {
    const box = h('input', { type: 'checkbox', checked: f[key], 'aria-label': t('b.useLabel', { x: word }) });
    input.disabled = !f[key];
    box.addEventListener('change', () => { f[key] = box.checked; input.disabled = !box.checked; save(); });
    return h('span', { class: 'opt' }, box, h('span', { class: 'muted', style: { fontSize: '11px' } }, t('b.use')));
  };
  const lim = meta.exit_limits;
  const exitCard = h('article', { class: 'card' }, h('h2', {}, t('b.exit')),
    h('p', { class: 'card-sub', text: t('b.exitSub') }),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'stop' }, t('b.stop')), stopIn, useToggle('useStop', stopIn, t('b.stopWord')),
        h('p', { class: 'hint', text: `${lim.stop_loss_pct[0]} ~ ${lim.stop_loss_pct[1]}` }), errorSlot('exit.stop_loss_pct')),
      h('div', {}, h('label', { for: 'profit' }, t('b.profit')), profitIn, useToggle('useProfit', profitIn, t('b.profitWord')),
        h('p', { class: 'hint', text: `+${lim.take_profit_pct[0]} ~ +${lim.take_profit_pct[1]}` }), errorSlot('exit.take_profit_pct'))),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'hold' }, t('b.hold')), holdIn,
        h('p', { class: 'hint', text: t('b.holdHint', { a: lim.max_hold_days[0], b: lim.max_hold_days[1] }) }), errorSlot('exit.max_hold_days')),
      h('div', {}, h('label', { for: 'trail' }, t('b.trail')), h('input', { id: 'trail', value: t('b.trailOff'), disabled: true, 'aria-describedby': 'trail-hint' }),
        h('p', { class: 'hint', id: 'trail-hint', text: t('b.trailHint') }))),
    h('div', { class: 'callout info', style: { marginTop: '16px' } }, t('b.exitCallout')));

  // ---------------------------------------------------------------- Universe & period
  const multi = (key, options, labelOf, groupLabel) => h('div', { class: 'inline-checks', role: 'group', 'aria-label': groupLabel },
    options.map((o) => {
      const box = h('input', { type: 'checkbox', checked: f[key].includes(o), 'aria-label': String(labelOf(o)), 'aria-describedby': described(key === 'caps' ? 'cap_groups' : key) });
      const lab = h('label', { class: `check${f[key].includes(o) ? ' selected' : ''}` }, box, labelOf(o));
      box.addEventListener('change', () => { f[key] = options.filter((x) => (x === o ? box.checked : f[key].includes(x))); lab.classList.toggle('selected', box.checked); save(true); });
      return lab;
    }));
  const dateIn = (id, key) => h('input', { id, type: 'date', value: f[key], min: meta.backtest_start, max: meta.data_as_of, 'aria-describedby': `${described(`period.${key}`)} ${described('period')}`,
    onchange: (e) => { f[key] = e.target.value; save(true); } });
  const valueIn = h('input', { id: 'value', inputmode: 'numeric', value: String(f.minValue), 'aria-describedby': described('min_avg_value_krw'),
    oninput: (e) => { f.minValue = e.target.value.replace(/[,\s]/g, ''); save(true); } });
  const previewBox = h('div', { class: 'callout', style: { marginTop: '18px' }, 'aria-live': 'polite' }, t('b.estimating'));
  const uniCard = h('article', { class: 'card' }, h('h2', {}, t('b.uni')),
    h('p', { class: 'card-sub', text: t('b.uniSub') }),
    h('span', { class: 'label' }, t('b.market')), multi('markets', meta.markets, (m) => m, t('b.market')), errorSlot('markets'),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'start' }, t('b.start')), dateIn('start', 'start'), errorSlot('period.start')),
      h('div', {}, h('label', { for: 'end' }, t('b.end')), dateIn('end', 'end'), errorSlot('period.end'))),
    errorSlot('period'),
    h('label', { for: 'value' }, t('b.value')), valueIn,
    h('p', { class: 'hint', text: t('b.valueHint', { v: fmt.int(meta.min_avg_value_krw) }) }), errorSlot('min_avg_value_krw'),
    h('span', { class: 'label' }, t('b.cap')), multi('caps', meta.cap_groups, (c) => t(`cap.${c}`), t('b.cap')), errorSlot('cap_groups'),
    previewBox);

  // ---------------------------------------------------------------- Run bar
  const nameIn = h('input', { id: 'sname', value: f.name, style: { maxWidth: '260px' }, 'aria-describedby': described('name'),
    oninput: (e) => { f.name = e.target.value.trim(); save(); } });
  const runBtn = h('button', { class: 'primary', type: 'button' }, `${t('b.run')} `, h('span', { 'aria-hidden': 'true' }, '→'));
  const runbar = h('div', { class: 'runbar' },
    h('div', {}, h('label', { for: 'sname', style: { margin: '0 0 4px' } }, t('b.name')), nameIn, errorSlot('name'),
      h('div', { class: 'run-facts' },
        h('span', {}, h('b', {}, t('b.execution')), t('b.nextOpen')),
        h('span', {}, h('b', {}, t('b.cost')), `${cost}%`),
        h('span', {}, h('b', {}, t('b.validation')), t('b.validationVal')))),
    runBtn);

  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('b.eyebrow')), h('h1', { text: t('b.title') }),
        h('p', { class: 'lead', text: t('b.lead') })),
      h('div', { class: 'notice' }, t('b.notice', { c: cost, d: meta.data_as_of }))),
    h('div', { class: 'grid-three' }, buyCard, exitCard, uniCard), runbar);

  // ---------------------------------------------------------------- behaviour
  let previewTimer;
  let previewSeq = 0;
  function showErrors(e) {
    for (const [k, slot] of Object.entries(errEls)) {
      const msg = e[k];
      slot.textContent = msg || '';
      slot.classList.toggle('hidden', !msg);
    }
    const map = { name: nameIn, 'exit.stop_loss_pct': stopIn, 'exit.take_profit_pct': profitIn, 'exit.max_hold_days': holdIn,
      'period.start': el.querySelector('#start'), 'period.end': el.querySelector('#end'), min_avg_value_krw: valueIn };
    for (const [k, input] of Object.entries(map)) input?.setAttribute('aria-invalid', e[k] || (k.startsWith('period') && e.period) ? 'true' : 'false');
  }
  async function refreshPreview() {
    const e = validate(f, meta);
    const blocking = ['markets', 'period.start', 'period.end', 'period', 'min_avg_value_krw', 'cap_groups'].filter((k) => e[k]);
    if (blocking.length) { previewBox.textContent = t('b.estimateFix'); return; }
    const seq = ++previewSeq;
    previewBox.textContent = t('b.estimating');
    try {
      const p = await api.preview({ markets: f.markets, period: { start: f.start, end: f.end }, min_avg_value_krw: Number(f.minValue), cap_groups: f.caps });
      if (seq !== previewSeq) return;
      previewBox.replaceChildren(h('b', {}, t('b.estimate', { n: fmt.int(p.eligible_tickers) })),
        h('br'), h('small', { class: 'muted' }, t('b.estimateSub', { a: fmt.int(Math.round(p.avg_tickers_per_day)), d: fmt.int(p.trading_days) })));
    } catch (err) {
      if (seq === previewSeq) previewBox.textContent = t('b.estimateErr', { m: err.message });
    }
  }
  function save(universeChanged = false) {
    state.draft = { ...f };
    if (Object.keys(errs).length) showErrors(validate(f, meta));
    if (universeChanged) { clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 350); }
  }

  runBtn.addEventListener('click', async () => {
    const e = validate(f, meta);
    Object.keys(errs).forEach((k) => delete errs[k]);
    Object.assign(errs, e);
    showErrors(e);
    if (Object.keys(e).length) {
      toast(t('b.checkFields', { n: Object.keys(e).length }));
      el.querySelector('.field-error:not(.hidden)')?.scrollIntoView({ block: 'center' });
      return;
    }
    runBtn.disabled = true;
    try {
      const r = await api.submit([toBody(f)]);
      state.lastRun = r.run_id;
      location.hash = `#/runs/${r.run_id}/progress`;
    } catch (err) {
      runBtn.disabled = false;
      if (err.code === 'validation_failed' && err.detail?.fields) {
        const server = {};
        for (const [k, v] of Object.entries(err.detail.fields)) server[k.replace(/^strategies\[0\]\.?/, '') || 'name'] = v;
        Object.assign(errs, server);
        showErrors(server);
        toast(t('b.serverRejected'));
      } else if (err.code === 'busy') {
        toast(t('b.busy'));
        location.hash = `#/runs/${err.detail.run_id}/progress`;
      } else {
        toast(`${err.message} (${err.code})`);
      }
    }
  });

  refreshPreview();
  return () => clearTimeout(previewTimer);
}
