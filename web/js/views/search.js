// 역방향 백테스트 (P1-8, 설계 §3·§4, plan/01 작업 ②). 입력 → 진행 → 결과 세 화면.
// 허용 값·기본값은 /api/searches/options, 후보 수·예상 시간은 /api/searches/preview 에서 받는다. 화면은 계산하지 않는다.
import { api } from '../api.js';
import { errorView, fmt, h, linkButton, loading, metricDefs, pill, table, toast } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const DRAFT = 'rl.searchDraft';
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const patLabel = (n) => (has(`pat.${n}`) ? t(`pat.${n}`) : n);
const STATUS_KIND = { both: '', explore_only: 'warn', not_evaluated: 'neutral', not_met: 'bad', insufficient_trades: 'neutral' };

function loadDraft() { try { return JSON.parse(sessionStorage.getItem(DRAFT) || 'null'); } catch { return null; } }
function saveDraft(f) { try { sessionStorage.setItem(DRAFT, JSON.stringify(f)); } catch { /* 저장 불가 */ } }
const dayAfter = (d) => { const x = new Date(`${d}T00:00:00Z`); x.setUTCDate(x.getUTCDate() + 1); return x.toISOString().slice(0, 10); };
const duration = (sec) => (sec >= 90 ? t('sx.minutes', { m: Math.round(sec / 60) }) : t('sx.seconds', { s: Math.round(sec) }));
const pctLabel = (v) => (v == null ? t('sx.none') : `${v > 0 ? '+' : ''}${v}%`);

function defaults(meta, opt) {
  return {
    name: 'my_search', target: Math.round(opt.target_win_rate_default * 1000) / 10, minTrades: opt.min_trades.default,
    patterns: [...(opt.defaults.patterns || opt.patterns)], combine: [...opt.defaults.combine],
    stop: [...opt.defaults.stop_loss_pct], profit: [...opt.defaults.take_profit_pct], hold: [...opt.defaults.max_hold_days],
    markets: [...meta.markets], start: meta.backtest_start, end: meta.data_as_of,
    minValue: meta.min_avg_value_krw, caps: [...meta.cap_groups], pax: {},
  };
}

// 패턴 수치 축 (P1-4): 고른 패턴의 고른 값만 싣는다. 아무것도 안 고르면 기본값 하나만 쓴다
function paramAxes(f) {
  const out = {};
  for (const p of f.patterns) {
    for (const [k, v] of Object.entries(f.pax?.[p] || {})) if (v.length) (out[p] ||= {})[k] = v;
  }
  return out;
}

function validate(f, meta, opt) {
  const e = {};
  const w = Number(f.target);
  if (f.target === '' || Number.isNaN(w) || w < 0 || w > 100) e.target_win_rate = t('sx.v.target');
  const mt = Number(f.minTrades);
  if (!Number.isInteger(mt) || mt < opt.min_trades.min || mt > opt.min_trades.max) {
    e.min_trades = t('sx.minTradesHint', { a: opt.min_trades.min, b: opt.min_trades.max, d: opt.min_trades.default });
  }
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(f.name)) e.name = t('v.name');
  for (const k of ['patterns', 'combine', 'stop', 'profit', 'hold']) if (!f[k].length) e[`axes.${k}`] = t('sx.v.axis');
  if (!f.markets.length) e['filters.markets'] = t('v.markets');
  if (!f.caps.length) e['filters.cap_groups'] = t('v.caps');
  const mv = Number(f.minValue);
  if (!Number.isInteger(mv) || mv < meta.min_avg_value_krw) e['filters.min_avg_value_krw'] = t('v.value', { v: fmt.int(meta.min_avg_value_krw) });
  if (!DATE_RE.test(f.start) || !DATE_RE.test(f.end)) e['filters.period'] = t('v.date');
  else if (f.start < meta.backtest_start || f.end > meta.data_as_of || !(f.start <= opt.split_date && opt.split_date < f.end)) {
    e['filters.period'] = t('sx.v.period', { d: opt.split_date });
  }
  return e;
}

function toBody(f) {
  return {
    name: f.name, target_win_rate: Number(f.target) / 100, min_trades: Number(f.minTrades),
    axes: { patterns: f.patterns, combine: f.combine, stop_loss_pct: f.stop, take_profit_pct: f.profit, max_hold_days: f.hold,
      ...(Object.keys(paramAxes(f)).length ? { pattern_params: paramAxes(f) } : {}) },
    filters: { markets: f.markets, cap_groups: f.caps, min_avg_value_krw: Number(f.minValue), period: { start: f.start, end: f.end } },
  };
}

// 서버 오류 필드 경로(axes.stop_loss_pct 등) → 화면 칸 키
const SERVER_KEY = { 'axes.stop_loss_pct': 'axes.stop', 'axes.take_profit_pct': 'axes.profit', 'axes.max_hold_days': 'axes.hold' };

// ================================================================ 입력 화면 (#/search)
export async function renderSearch(el) {
  el.append(h('h1', { text: t('sx.title') }), loading(t('b.loadingData')));
  let meta = state.meta;
  let opt;
  try {
    if (!meta || meta.status !== 'ready') { meta = await api.meta(); state.meta = meta; }
    opt = await api.searchOptions();
  } catch (e) {
    el.replaceChildren(h('h1', { text: t('sx.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderSearch(el); } }));
    return;
  }
  if (meta.status !== 'ready') {
    const tm = setTimeout(() => { el.replaceChildren(); renderSearch(el); }, 2000);
    return () => clearTimeout(tm);
  }
  el.replaceChildren();

  const f = { ...defaults(meta, opt), ...(loadDraft() || {}) };
  const errEls = {};
  const slotId = (k) => `serr-${k.replace(/\W/g, '-')}`;
  const errorSlot = (k) => (errEls[k] = h('p', { class: 'field-error hidden', id: slotId(k) }));
  let shown = false;

  // 여러 값 고르기 (체크 칩). 값 순서는 options 순서 그대로
  const multi = (key, values, labelOf, groupLabel, errKey) => h('div', { class: 'inline-checks', role: 'group', 'aria-label': groupLabel },
    values.map((v) => {
      const box = h('input', { type: 'checkbox', checked: f[key].includes(v), 'aria-label': String(labelOf(v)), 'aria-describedby': slotId(errKey) });
      const lab = h('label', { class: `check${f[key].includes(v) ? ' selected' : ''}` }, box, labelOf(v));
      box.addEventListener('change', () => {
        f[key] = values.filter((x) => (x === v ? box.checked : f[key].includes(x)));
        lab.classList.toggle('selected', box.checked);
        changed();
      });
      return lab;
    }));
  const numIn = (id, key, mode = 'decimal') => h('input', { id, inputmode: mode, value: String(f[key] ?? ''),
    oninput: (e) => { f[key] = e.target.value.trim(); changed(); } });

  // ---------------------------------------------------------------- 목표
  const targetIn = numIn('s-target', 'target');
  const minIn = numIn('s-min', 'minTrades', 'numeric');
  targetIn.setAttribute('aria-describedby', slotId('target_win_rate'));
  minIn.setAttribute('aria-describedby', slotId('min_trades'));
  const goalCard = h('article', { class: 'card' }, h('h2', {}, t('sx.goal')),
    h('p', { class: 'card-sub', text: t('sx.goalSub') }),
    h('label', { for: 's-target' }, t('sx.target')), targetIn, errorSlot('target_win_rate'),
    h('label', { for: 's-min' }, t('sx.minTrades')), minIn,
    h('p', { class: 'hint', text: t('sx.minTradesHint', { a: opt.min_trades.min, b: opt.min_trades.max, d: opt.min_trades.default }) }),
    errorSlot('min_trades'));

  // 패턴 수치 축 (P1-4): 고른 패턴 중 탐색할 수 있는 수치마다 값 칩
  f.pax = f.pax || {};
  const paxBox = h('div', {});
  function renderPax() {
    const items = f.patterns.flatMap((p) => Object.entries(opt.pattern_axes?.[p] || {}).map(([k, vals]) => [p, k, vals]));
    paxBox.replaceChildren(...items.map(([p, k, vals]) => {
      const cur = () => f.pax[p]?.[k] || [];
      const key = `axes.pattern_params.${p}.${k}`;
      const def = meta.pattern_params?.[p]?.[k]?.default;
      return h('div', {}, h('span', { class: 'label' }, `${patLabel(p)} · ${t(`pp.${p}.${k}`)}`),
        h('div', { class: 'inline-checks', role: 'group', 'aria-label': `${patLabel(p)} ${t(`pp.${p}.${k}`)}` }, vals.map((v) => {
          const box = h('input', { type: 'checkbox', checked: cur().includes(v), 'aria-label': String(v), 'aria-describedby': slotId(key) });
          const lab = h('label', { class: `check${cur().includes(v) ? ' selected' : ''}` }, box, String(v));
          box.addEventListener('change', () => {
            (f.pax[p] ||= {})[k] = vals.filter((x) => (x === v ? box.checked : cur().includes(x)));
            lab.classList.toggle('selected', box.checked);
            changed();
          });
          return lab;
        })),
        h('p', { class: 'hint', text: t('pp.searchHint', { d: def ?? '—' }) }), errorSlot(key));
    }));
  }
  renderPax();

  // ---------------------------------------------------------------- 탐색 범위
  const rangeCard = h('article', { class: 'card' }, h('h2', {}, t('sx.range')),
    h('p', { class: 'card-sub', text: t('sx.rangeSub') }),
    h('span', { class: 'label' }, t('b.patterns')),
    h('div', { class: 'checks', role: 'group', 'aria-label': t('b.patterns') }, opt.patterns.map((p) => {
      const box = h('input', { type: 'checkbox', checked: f.patterns.includes(p), 'aria-label': patLabel(p), 'aria-describedby': slotId('axes.patterns') });
      const lab = h('label', { class: `check${f.patterns.includes(p) ? ' selected' : ''}` }, box, patLabel(p));
      box.addEventListener('change', () => {
        f.patterns = opt.patterns.filter((x) => (x === p ? box.checked : f.patterns.includes(x)));
        lab.classList.toggle('selected', box.checked);
        renderPax();
        changed();
      });
      return lab;
    })), errorSlot('axes.patterns'), paxBox,
    h('span', { class: 'label' }, t('sx.combine')), multi('combine', opt.axes.combine, (c) => c.toUpperCase(), t('sx.combine'), 'axes.combine'), errorSlot('axes.combine'),
    h('span', { class: 'label' }, t('sx.stop')), multi('stop', opt.axes.stop_loss_pct, pctLabel, t('sx.stop'), 'axes.stop'), errorSlot('axes.stop'),
    h('span', { class: 'label' }, t('sx.profit')), multi('profit', opt.axes.take_profit_pct, pctLabel, t('sx.profit'), 'axes.profit'), errorSlot('axes.profit'),
    h('span', { class: 'label' }, t('sx.hold')), multi('hold', opt.axes.max_hold_days, (d) => String(d), t('sx.hold'), 'axes.hold'), errorSlot('axes.hold'));

  // ---------------------------------------------------------------- 대상·기간
  const dateIn = (id, key) => h('input', { id, type: 'date', value: f[key], min: meta.backtest_start, max: meta.data_as_of,
    'aria-describedby': slotId('filters.period'), onchange: (e) => { f[key] = e.target.value; changed(); } });
  const valueIn = h('input', { id: 's-value', inputmode: 'numeric', value: String(f.minValue), 'aria-describedby': slotId('filters.min_avg_value_krw'),
    oninput: (e) => { f.minValue = e.target.value.replace(/[,\s]/g, ''); changed(); } });
  const scopeCard = h('article', { class: 'card' }, h('h2', {}, t('sx.scope')),
    h('p', { class: 'card-sub', text: t('sx.scopeSub', { d: opt.split_date }) }),
    h('span', { class: 'label' }, t('b.market')), multi('markets', meta.markets, (m) => m, t('b.market'), 'filters.markets'), errorSlot('filters.markets'),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 's-start' }, t('b.start')), dateIn('s-start', 'start')),
      h('div', {}, h('label', { for: 's-end' }, t('b.end')), dateIn('s-end', 'end'))),
    errorSlot('filters.period'),
    h('label', { for: 's-value' }, t('b.value')), valueIn,
    h('p', { class: 'hint', text: t('b.valueHint', { v: fmt.int(meta.min_avg_value_krw) }) }), errorSlot('filters.min_avg_value_krw'),
    h('span', { class: 'label' }, t('b.cap')), multi('caps', meta.cap_groups, (c) => t(`cap.${c}`), t('b.cap'), 'filters.cap_groups'), errorSlot('filters.cap_groups'));

  // ---------------------------------------------------------------- 실행 막대
  const nameIn = h('input', { id: 's-name', value: f.name, style: { maxWidth: '260px' }, 'aria-describedby': slotId('name'),
    oninput: (e) => { f.name = e.target.value.trim(); changed(); } });
  const countBox = h('div', { 'aria-live': 'polite', class: 'run-facts', style: { display: 'block' } }, t('sx.counting'));
  const serverBox = h('div', { class: 'callout hidden', role: 'alert' });
  const runBtn = h('button', { class: 'primary', type: 'button' }, `${t('sx.run')} `, h('span', { 'aria-hidden': 'true' }, '→'));
  const runbar = h('div', { class: 'runbar' },
    h('div', {}, h('label', { for: 's-name', style: { margin: '0 0 4px' } }, t('sx.name')), nameIn, errorSlot('name'), countBox),
    runBtn);

  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('sx.eyebrow')), h('h1', { text: t('sx.title') }), h('p', { class: 'lead', text: t('sx.lead') })),
      h('div', { class: 'notice' }, t('sx.notice', { s: opt.split_date, e: dayAfter(opt.split_date), n: opt.max_candidates }))),
    h('div', { class: 'grid-three' }, goalCard, rangeCard, scopeCard), serverBox, runbar);

  // ---------------------------------------------------------------- 동작
  let timer;
  let seq = 0;
  let withinLimit = false;
  function showErrors(e) {
    for (const [k, slot] of Object.entries(errEls)) {
      slot.textContent = e[k] || '';
      slot.classList.toggle('hidden', !e[k]);
    }
    const inputs = { target_win_rate: targetIn, min_trades: minIn, name: nameIn, 'filters.min_avg_value_krw': valueIn,
      'filters.period': [el.querySelector('#s-start'), el.querySelector('#s-end')] };
    for (const [k, inp] of Object.entries(inputs)) [inp].flat().forEach((x) => x?.setAttribute('aria-invalid', e[k] ? 'true' : 'false'));
  }
  async function refreshCount() {
    const e = validate(f, meta, opt);
    if (Object.keys(e).length) { countBox.textContent = t('sx.fixFirst'); withinLimit = false; return; }
    const my = ++seq;
    countBox.textContent = t('sx.counting');
    try {
      const p = await api.searchPreview(toBody(f));
      if (my !== seq) return;
      withinLimit = p.within_limit;
      countBox.replaceChildren(p.within_limit
        ? h('span', {}, h('b', {}, t('sx.count', { n: fmt.int(p.candidates), t: duration(p.estimated_sec) })), h('br'), h('small', { class: 'muted' }, t('sx.countSub')))
        : h('b', { class: 'warning' }, t('sx.over', { n: fmt.int(p.candidates), m: p.max_candidates })));
    } catch (err) {
      if (my === seq) { withinLimit = false; countBox.textContent = t('sx.countErr', { m: err.message }); }
    }
  }
  function changed() {
    saveDraft(f);
    serverBox.classList.add('hidden');
    if (shown) showErrors(validate(f, meta, opt));
    clearTimeout(timer);
    timer = setTimeout(refreshCount, 300);
  }

  runBtn.addEventListener('click', async () => {
    const e = validate(f, meta, opt);
    shown = true;
    showErrors(e);
    if (Object.keys(e).length) {
      toast(t('b.checkFields', { n: Object.keys(e).length }));
      el.querySelector('.field-error:not(.hidden)')?.scrollIntoView({ block: 'center' });
      return;
    }
    if (!withinLimit) { toast(countBox.textContent); return; }
    runBtn.disabled = true;
    try {
      const r = await api.submitSearch(toBody(f));
      location.hash = `#/searches/${r.search_id}/progress`;
    } catch (err) {
      runBtn.disabled = false;
      if (err.code === 'validation_failed' && err.detail?.fields) {
        const mapped = {};
        for (const [k, v] of Object.entries(err.detail.fields)) mapped[SERVER_KEY[k] || k.replace(/\[\d+\]$/, '')] = v;
        showErrors(mapped);
        const unknown = Object.keys(mapped).filter((k) => !errEls[k]);
        serverBox.replaceChildren(h('b', {}, t('sx.serverFields')), h('ul', {}, Object.entries(err.detail.fields).map(([k, v]) => h('li', {}, `${k}: ${v}`))));
        serverBox.classList.toggle('hidden', !unknown.length);
        toast(t('b.serverRejected'));
      } else if (err.code === 'busy') {
        toast(err.detail?.kind === 'search' ? t('b.busySearch') : t('b.busy'));
        location.hash = err.detail?.kind === 'search' ? `#/searches/${err.detail.id}/progress` : `#/runs/${err.detail.id}/progress`;
      } else {
        toast(`${has(`err.${err.code}`) ? t(`err.${err.code}`) : err.message} (${err.code})`);
      }
    }
  });

  refreshCount();
  renderHistory(el);
  return () => clearTimeout(timer);
}

// 최근 탐색 기록 (plan/01 작업 ③-3): 과거 탐색을 다시 연다. 목록 값은 /api/searches 상태 그대로
const RUN_KIND = { completed: '', cancelled: 'warn', failed: 'bad', running: 'neutral', queued: 'neutral' };
async function renderHistory(el) {
  const box = h('section', { style: { marginTop: '26px' } }, h('h2', {}, t('sh.title')));
  el.append(box);
  let list;
  try { list = (await api.searches()).searches; } catch (e) { box.append(h('p', { class: 'muted', text: e.message })); return; }
  if (!list.length) { box.append(h('p', { class: 'muted', text: t('sh.empty') })); return; }
  const href = (s) => (s.status === 'running' || s.status === 'queued' ? `#/searches/${s.search_id}/progress` : `#/searches/${s.search_id}/results`);
  box.append(h('article', { class: 'card', style: { marginTop: '12px' } }, table({
    caption: t('sh.caption'),
    columns: [
      { label: t('sh.col.name'), render: (s) => h('a', { href: href(s) }, s.name) },
      { label: t('sh.col.status'), render: (s) => pill(has(`sh.st.${s.status}`) ? t(`sh.st.${s.status}`) : s.status, RUN_KIND[s.status] ?? 'neutral') },
      { label: t('sh.col.created'), render: (s) => s.created_at.replace('T', ' ') },
      { label: t('sh.col.id'), render: (s) => h('small', { class: 'muted' }, s.search_id) },
    ],
    rows: list.filter((s) => s.status !== 'failed' || s.error),
  })));
}

// ================================================================ 진행 화면 (#/searches/:id/progress)
export function renderSearchProgress(el, id) {
  const title = h('h1', { text: t('sp.preparing') });
  const orb = h('div', { class: 'orb', 'aria-hidden': 'true' });
  const fill = h('div', { class: 'progress-fill' });
  const track = h('div', { class: 'progress-track hidden', role: 'progressbar', 'aria-label': t('p.bar'), 'aria-valuemin': '0' }, fill);
  const statusLine = h('span', {});
  const counts = h('span', {});
  const steps = h('ol', { class: 'steps', style: { gridTemplateColumns: 'repeat(4,1fr)' }, 'aria-label': t('p.stages') });
  const live = h('p', { class: 'sr-only', 'aria-live': 'polite' });
  const cancelBtn = h('button', { class: 'secondary', type: 'button' }, t('sp.cancel'));
  const actions = h('div', { class: 'actions' }, cancelBtn);
  const note = h('p', { class: 'lead', style: { margin: 'auto' }, text: t('sp.note') });
  const wrap = h('div', { class: 'progress-wrap' }, orb, h('div', { class: 'eyebrow' }, t('sp.id', { id })), title, note,
    track, h('div', { class: 'progress-info' }, statusLine, counts), steps, live, actions);
  el.append(wrap);

  let timer = null;
  let failures = 0;
  let lastStage = null;
  let stopped = false;
  const stageText = (s) => (has(`sp.stage.${s}`) ? t(`sp.stage.${s}`) : s);

  function renderSteps(snap) {
    const idx = snap.stages.indexOf(snap.stage);
    const done = snap.status === 'completed';
    steps.replaceChildren(...snap.stages.map((s, i) => h('li', {
      class: `step${done || i < idx ? ' done' : ''}${!done && i === idx && snap.status === 'running' ? ' active' : ''}`,
      'aria-current': !done && i === idx ? 'step' : null,
    }, has(`sp.step.${s}`) ? t(`sp.step.${s}`) : s)));
  }
  function finish(kind, heading, message, buttons) {
    stopped = true;
    clearTimeout(timer);
    orb.classList.add('stopped');
    track.classList.add('hidden');
    title.textContent = heading;
    document.title = `${heading} — ${id}`;
    statusLine.textContent = '';
    counts.textContent = '';
    note.textContent = message;
    live.textContent = `${heading}. ${message}`;
    actions.replaceChildren(...buttons);
    wrap.setAttribute('data-state', kind);
  }

  async function poll() {
    if (stopped) return;
    let snap;
    try { snap = await api.searchStatus(id); failures = 0; } catch (e) {
      if (e.code === 'search_not_found') { el.replaceChildren(errorView(e, { backHref: '#/search', backText: t('sp.back') })); stopped = true; return; }
      failures += 1;
      statusLine.textContent = t('p.retrying', { m: e.message, n: failures });
      timer = setTimeout(poll, Math.min(10000, 1000 * 2 ** failures));
      return;
    }
    renderSteps(snap);
    const resultHref = `#/searches/${id}/results`;
    if (snap.status === 'completed') {
      finish('completed', t('sp.done'), t('sp.doneMsg', { t: fmt.num(snap.elapsed_sec, 1) }),
        [linkButton(t('sp.view'), resultHref, true), linkButton(t('sp.back'), '#/search')]);
      toast(t('sp.done'));
      setTimeout(() => { if (location.hash === `#/searches/${id}/progress`) location.hash = resultHref; }, 1200);
      return;
    }
    if (snap.status === 'cancelled') {
      finish('cancelled', t('sp.cancelled'), t('sp.cancelledMsg'),
        [linkButton(t('sp.viewPartial'), resultHref, true), linkButton(t('sp.back'), '#/search')]);
      return;
    }
    if (snap.status === 'failed') {
      const msg = snap.error?.code === 'server_restarted' ? t('p.restarted') : (snap.error?.message || t('errmsg.search_failed'));
      finish('failed', t('sp.failed'), msg, [linkButton(t('sp.back'), '#/search', true)]);
      return;
    }
    const label = snap.stage ? stageText(snap.stage) : (snap.status === 'queued' ? t('p.waiting') : t('p.starting'));
    title.textContent = `${label}…`;
    statusLine.textContent = `${t('p.elapsed', { s: label, t: fmt.num(snap.elapsed_sec, 1) })}${snap.met_so_far != null ? ` · ${t('sp.met', { n: fmt.int(snap.met_so_far) })}` : ''}`;
    if (snap.total > 0) {
      track.classList.remove('hidden');
      fill.style.width = `${Math.min(100, (100 * snap.processed) / snap.total)}%`;
      track.setAttribute('aria-valuemax', String(snap.total));
      track.setAttribute('aria-valuenow', String(snap.processed));
      counts.textContent = `${fmt.int(snap.processed)} / ${fmt.int(snap.total)} ${t('sp.unit')}`;
    } else {
      track.classList.add('hidden');
      counts.textContent = '';
    }
    if (snap.stage !== lastStage) { live.textContent = label; lastStage = snap.stage; }
    timer = setTimeout(poll, 1000);
  }

  cancelBtn.addEventListener('click', async () => {
    cancelBtn.disabled = true;
    try { await api.cancelSearch(id); statusLine.textContent = t('p.cancelRequested'); } catch (e) {
      toast(e.code === 'not_running' ? t('p.alreadyDone') : e.message);
      cancelBtn.disabled = false;
    }
  });

  poll();
  return () => { stopped = true; clearTimeout(timer); };
}

// ================================================================ 결과 화면 (#/searches/:id/results)
export function patternsText(st) {
  const one = (p) => {
    const pp = st.pattern_params?.[p];
    return pp ? `${patLabel(p)} (${Object.entries(pp).map(([k, v]) => `${t(`pp.${p}.${k}`)} ${v}`).join(', ')})` : patLabel(p);
  };
  return st.patterns.map(one).join(st.patterns.length > 1 ? ` ${st.combine.toUpperCase()} ` : '');
}

function comboText(st) {
  const pats = patternsText(st);
  const ex = st.exit || {};
  return h('span', {}, pats, h('br'), h('small', { class: 'muted' }, t('sr.exit', {
    s: ex.stop_loss_pct == null ? t('sr.noStop') : `${ex.stop_loss_pct}%`,
    p: ex.take_profit_pct == null ? t('sr.noProfit') : `+${ex.take_profit_pct}%`, h: ex.max_hold_days,
  })));
}

function metric(label, value, sub) {
  return h('div', { class: 'metric' }, h('span', { class: 'label' }, label), h('strong', {}, value), h('small', {}, sub || ''));
}

export async function renderSearchResult(el, id) {
  el.append(h('h1', { text: t('sr.title') }), loading(t('sr.loading')));
  let res;
  try { res = await api.searchResult(id); } catch (e) {
    el.replaceChildren(h('h1', { text: t('sr.title') }), errorView(e, {
      onRetry: () => { el.replaceChildren(); renderSearchResult(el, id); },
      ...(e.code === 'search_not_ready' ? { backHref: `#/searches/${id}/progress`, backText: t('common.viewProgress') }
        : { backHref: '#/search', backText: t('sp.back') }),
    }));
    return;
  }
  el.replaceChildren();
  const req = res.request;
  const c = res.counts;
  const sp = res.split;
  const winPct = (v) => fmt.pct(v, 1, false);
  const fdrPill = (v) => (v == null ? h('span', { class: 'muted' }, '—') : v ? pill(`✓ ${t('common.passed')}`) : pill(`✕ ${t('common.notPassed')}`, 'bad'));

  // 저장 폼: 표의 '선택' 버튼이 후보를 채운다
  const candSel = h('select', { id: 'sr-cand' }, res.rows.map((r) => h('option', { value: r.id }, `${r.id} · ${t(`sr.st.${r.status}`)}`)));
  const nameIn = h('input', { id: 'sr-name', maxlength: '60', placeholder: `${req.name} ${res.rows[0]?.id || ''}`.trim() });
  const saveErr = h('p', { class: 'field-error hidden', id: 'sr-name-err' });
  nameIn.setAttribute('aria-describedby', 'sr-name-err');
  const saveBtn = h('button', { class: 'primary', type: 'button' }, t('sr.saveBtn'));
  saveBtn.addEventListener('click', async () => {
    const name = nameIn.value.trim() || nameIn.placeholder;
    saveBtn.disabled = true;
    saveErr.classList.add('hidden');
    try {
      const p = await api.savePreset({ name, from_search: { search_id: res.search_id, candidate_id: candSel.value } });
      toast(t('sr.saved', { n: p.name }));
      nameIn.value = '';
    } catch (e) {
      saveErr.textContent = has(`errmsg.${e.code}`) ? t(`errmsg.${e.code}`) : (e.detail?.fields?.name || e.message);
      saveErr.classList.remove('hidden');
      nameIn.setAttribute('aria-invalid', 'true');
    } finally { saveBtn.disabled = false; }
  });
  const saveCard = h('article', { class: 'card', style: { marginTop: '18px' } }, h('h2', {}, t('sr.saveTitle')),
    h('p', { class: 'card-sub', text: t('sr.saveSub') }),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'sr-cand' }, t('sr.saveCandidate')), candSel),
      h('div', {}, h('label', { for: 'sr-name' }, t('sr.saveName')), nameIn, saveErr)),
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } }, saveBtn));

  const columns = [
    { label: t('sr.col.order'), num: true, render: (r) => r.order },
    { label: t('sr.col.status'), render: (r) => pill(t(`sr.st.${r.status}`), STATUS_KIND[r.status] ?? 'neutral') },
    { label: t('sr.col.combo'), render: (r) => comboText(r.strategy) },
    { label: t('sr.col.exTrades'), num: true, render: (r) => fmt.int(r.explore?.trades) },
    { label: t('sr.col.exWin'), num: true, render: (r) => winPct(r.explore?.win_rate) },
    { label: t('sr.col.exFdr'), render: (r) => fdrPill(r.explore?.fdr_pass) },
    { label: t('sr.col.evTrades'), num: true, render: (r) => fmt.int(r.evaluate?.trades) },
    { label: t('sr.col.evWin'), num: true, render: (r) => winPct(r.evaluate?.win_rate) },
    { label: t('sr.col.evFdr'), render: (r) => fdrPill(r.evaluate?.fdr_pass) },
    { label: t('sr.col.random'), num: true, render: (r) => (r.evaluate ? t('sr.pctile', { p: fmt.num(100 * r.evaluate.random_percentile, 0) }) : '—') },
    { label: t('sr.col.save'), render: (r) => h('button', { type: 'button', class: 'secondary', style: { whiteSpace: 'nowrap' }, 'aria-label': `${t('sr.pick')} ${r.id}`,
      onclick: () => { candSel.value = r.id; nameIn.placeholder = `${req.name} ${r.id}`; nameIn.focus(); saveCard.scrollIntoView({ block: 'center' }); } }, t('sr.pick')) },
  ];

  // 조건부 요소(취소 배너·가장 가까운 후보·저장 카드)는 없으면 null — DOM append 는 null 을 글자 "null" 로 넣으므로 걸러 낸다
  el.append(...[
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('sp.id', { id: res.search_id })), h('h1', { text: t('sr.title') }),
        h('p', { class: 'lead', text: t('sr.tried', { n: fmt.int(c.candidates), w: winPct(req.target_win_rate), m: fmt.int(req.min_trades) }) })),
      h('div', { class: 'notice' }, t('sr.split', { a: sp.explore.start, b: sp.explore.end, c: sp.evaluate.start, d: sp.evaluate.end }))),
    res.status === 'cancelled'
      ? h('div', { class: 'callout', role: 'status', style: { marginBottom: '18px' } }, h('b', { class: 'warning' }, t('sr.cancelledBanner', { n: fmt.int(c.candidates), p: fmt.int(c.processed) })))
      : null,
    h('div', { class: 'callout info', style: { marginBottom: '18px' } }, `${res.note} ${res.disclaimer}`),
    h('div', { class: 'metrics' },
      metric(t('sr.m.candidates'), fmt.int(c.candidates), res.status === 'cancelled' ? `${fmt.int(c.processed)} / ${fmt.int(c.candidates)}` : ''),
      metric(t('sr.m.both'), fmt.int(c.both)),
      metric(t('sr.m.exploreOnly'), fmt.int(c.explore_only + (c.not_evaluated || 0))),
      metric(t('sr.m.notMet'), fmt.int(c.not_met + c.insufficient_trades)),
      metric(t('sr.m.fdr'), `${fmt.int(c.explore_fdr_pass)} · ${fmt.int(c.evaluate_fdr_pass)}`, t('sr.m.fdrSub', { m: fmt.int(res.fdr_family_size) }))),
    res.closest
      ? h('div', { class: 'callout', role: 'status', style: { marginBottom: '18px' } },
        t('sr.closest', { id: res.closest.id, w: fmt.pctp(res.closest.win_rate_short, 1).replace('+', ''), n: fmt.int(res.closest.trades_short) }))
      : null,
    h('p', { class: 'muted', style: { fontSize: '12px' }, text: t('sr.sortRule') }),
    res.rows.length
      ? h('article', { class: 'card' }, table({ caption: t('sr.caption'), columns, rows: res.rows }))
      : h('p', { class: 'muted', text: t('sr.empty') }),
    res.rows.length ? saveCard : null,
    h('div', { style: { marginTop: '18px' } }, metricDefs(res.metric_definitions)),
    h('div', { class: 'actions' }, linkButton(t('sr.newSearch'), '#/search', true))].filter(Boolean));
}
