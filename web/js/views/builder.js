// Strategy Builder (FR-U1, FR-X5, design.md §6.2). 기본값·허용 범위는 모두 /api/meta 에서 받는다.
// 입력 검증은 전송 전(여기)과 서버(422) 양쪽에서 한다. 서버 오류 필드는 같은 입력칸 아래에 표시한다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, toast } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const patLabel = (p) => (has(`pat.${p.name}`) ? t(`pat.${p.name}`) : p.label);
const patRule = (p) => (has(`rule.${p.name}`) ? t(`rule.${p.name}`) : p.rule);

// 저장한 조합(기본값을 채운 전략) → 빌더 입력값 (P1-10)
export function fromStrategy(st, meta) {
  const ex = st.exit || {};
  const base = defaults(meta);
  return {
    ...base, name: st.name, patterns: [...st.patterns], combine: st.combine,
    useStop: ex.stop_loss_pct != null, stop: ex.stop_loss_pct ?? base.stop,
    useProfit: ex.take_profit_pct != null, profit: ex.take_profit_pct ?? base.profit, hold: ex.max_hold_days,
    markets: [...st.markets], start: st.period.start, end: st.period.end, minValue: st.min_avg_value_krw, caps: [...st.cap_groups],
    pp: JSON.parse(JSON.stringify(st.pattern_params || {})),
  };
}

// 빌더 입력이 불러온 조합과 다른가 (저장하지 않은 변경 경고용)
export function isDirty() {
  const lp = state.loadedPreset;
  if (!state.draft) return false;
  return !lp || JSON.stringify(toBody(state.draft)) !== lp.body;
}

function defaults(meta) {
  const ex = meta.exit_defaults;
  return {
    name: 'my_strategy', patterns: ['breakout_20d'], combine: 'or',
    useStop: ex.stop_loss_pct != null, stop: ex.stop_loss_pct ?? -8,
    useProfit: ex.take_profit_pct != null, profit: ex.take_profit_pct ?? 20, hold: ex.max_hold_days,
    markets: [...meta.markets], start: meta.backtest_start, end: meta.data_as_of,
    minValue: meta.min_avg_value_krw, caps: [...meta.cap_groups], pp: {},
  };
}

// 고른 패턴의 바꾼 수치만 (기본값과 같으면 싣지 않는다 → 손대지 않은 조합은 기존 요청과 같다) (P1-4)
function changedParams(f, meta) {
  const out = {};
  for (const p of f.patterns) {
    for (const [k, v] of Object.entries(f.pp?.[p] || {})) {
      const lim = meta.pattern_params?.[p]?.[k];
      if (!lim || v === '' || v == null || Number(v) === lim.default) continue;
      (out[p] ||= {})[k] = Number(v);
    }
  }
  return out;
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
  for (const p of f.patterns) {
    for (const [k, v] of Object.entries(f.pp?.[p] || {})) {
      const lim = meta.pattern_params?.[p]?.[k];
      if (lim && v !== '' && (Number.isNaN(Number(v)) || Number(v) < lim.min || Number(v) > lim.max)) {
        e[`pattern_params.${p}.${k}`] = t('pp.range', { a: lim.min, b: lim.max });
      }
    }
  }
  return e;
}

export function toBody(f, meta = state.meta) {
  const pp = meta ? changedParams(f, meta) : {};
  return {
    ...(Object.keys(pp).length ? { pattern_params: pp } : {}),
    name: f.name, patterns: f.patterns, combine: f.combine,
    exit: { stop_loss_pct: f.useStop ? Number(f.stop) : null, take_profit_pct: f.useProfit ? Number(f.profit) : null,
      max_hold_days: Number(f.hold), trailing_stop_pct: null },
    markets: f.markets, period: { start: f.start, end: f.end }, min_avg_value_krw: Number(f.minValue), cap_groups: f.caps,
  };
}

export async function renderBuilder(el, query = new URLSearchParams()) {
  let meta = state.meta;
  if (!meta || meta.status !== 'ready') {
    el.append(h('h1', { text: t('b.title') }), loading(t('b.loadingData')));
    try { meta = await api.meta(); state.meta = meta; } catch (e) {
      el.replaceChildren(h('h1', { text: t('b.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderBuilder(el, query); } }));
      return;
    }
    if (meta.status !== 'ready') {
      const tm = setTimeout(() => { el.replaceChildren(); renderBuilder(el, query); }, 2000);
      return () => clearTimeout(tm);
    }
    el.replaceChildren();
  }

  // 저장 목록에서 불러오기: #/builder?preset=<id> → 입력값을 채우고 주소에서 매개변수를 지운다 (P1-10)
  const presetId = query.get('preset');
  if (presetId) {
    try {
      const p = await api.preset(presetId);
      const loaded = fromStrategy(p.strategy, meta);
      state.draft = loaded;
      state.loadedPreset = { id: p.id, name: p.name, revision: p.revision, body: JSON.stringify(toBody(loaded)) };
      toast(t('pb.loaded', { n: p.name }));
    } catch (e) {
      toast(`${has(`err.${e.code}`) ? t(`err.${e.code}`) : e.message} (${e.code})`);
    }
    history.replaceState(null, '', '#/builder');
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
  // 세부 조건 (P1-4): 고른 패턴 중 조합별로 바꿀 수 있는 수치. 범위·기본값은 /api/meta 의 pattern_params
  f.pp = f.pp || {};
  const ppBox = h('div', {});
  function renderPP() {
    const items = f.patterns.flatMap((p) => Object.entries(meta.pattern_params?.[p] || {}).map(([k, lim]) => [p, k, lim]));
    if (!items.length) { ppBox.replaceChildren(); return; }
    ppBox.replaceChildren(h('details', { open: Object.keys(changedParams(f, meta)).length ? true : null },
      h('summary', {}, t('pp.details')),
      items.map(([p, k, lim]) => {
        const id = `pp-${p}-${k}`;
        const key = `pattern_params.${p}.${k}`;
        const input = h('input', { id, inputmode: 'decimal', value: String(f.pp[p]?.[k] ?? lim.default), 'aria-describedby': described(key),
          oninput: (e) => { (f.pp[p] ||= {})[k] = e.target.value.trim(); save(); } });
        return h('div', {}, h('label', { for: id }, `${has(`pat.${p}`) ? t(`pat.${p}`) : p} · ${t(`pp.${p}.${k}`)}`), input,
          h('p', { class: 'hint', text: t('pp.hint', { d: lim.default, a: lim.min, b: lim.max }) }), errorSlot(key));
      })));
  }
  renderPP();
  const patternChecks = meta.patterns.map((p) => {
    const box = h('input', { type: 'checkbox', checked: f.patterns.includes(p.name), 'aria-label': patLabel(p), 'aria-describedby': described('patterns') });
    const lab = h('label', { class: `check${f.patterns.includes(p.name) ? ' selected' : ''}` }, box, h('span', {}, patLabel(p), h('small', {}, patRule(p))));
    box.addEventListener('change', () => {
      f.patterns = meta.patterns.map((q) => q.name).filter((n) => (n === p.name ? box.checked : f.patterns.includes(n)));
      lab.classList.toggle('selected', box.checked); renderPP(); save();
    });
    return lab;
  });
  const buyCard = h('article', { class: 'card' }, h('h2', {}, t('b.buy')),
    h('p', { class: 'card-sub', text: t('b.buySub') }),
    h('span', { class: 'label' }, t('b.logic')), seg,
    h('div', { class: 'checks', role: 'group', 'aria-label': t('b.patterns') }, patternChecks), errorSlot('patterns'), ppBox);

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

  // ---------------------------------------------------------------- 저장한 조합 (P1-10)
  const presetStatus = h('p', { class: 'card-sub', 'aria-live': 'polite' });
  const presetName = h('input', { id: 'pname', maxlength: '60', value: state.loadedPreset?.name || '', 'aria-describedby': 'pname-err' });
  const presetErr = h('p', { class: 'field-error hidden', id: 'pname-err' });
  const saveNewBtn = h('button', { class: 'secondary', type: 'button' }, t('pb.saveNew'));
  const overwriteBtn = h('button', { class: 'secondary', type: 'button' }, t('pb.overwrite'));
  function renderPresetStatus() {
    const lp = state.loadedPreset;
    overwriteBtn.disabled = !lp;
    presetStatus.textContent = lp ? t(isDirty() ? 'pb.statusDirty' : 'pb.statusClean', { n: lp.name }) : t('pb.statusNone');
  }
  function presetFail(e) {
    presetErr.textContent = has(`errmsg.${e.code}`) ? t(`errmsg.${e.code}`)
      : e.code === 'validation_failed' ? Object.entries(e.detail?.fields || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') : e.message;
    presetErr.classList.remove('hidden');
    presetName.setAttribute('aria-invalid', 'true');
  }
  async function savePreset(overwrite) {
    presetErr.classList.add('hidden');
    presetName.setAttribute('aria-invalid', 'false');
    const e = validate(f, meta);
    if (Object.keys(e).length) { Object.assign(errs, e); showErrors(e); toast(t('b.checkFields', { n: Object.keys(e).length })); return; }
    const name = presetName.value.trim();
    if (!name) { presetFail({ code: 'name_required', message: t('pb.nameRequired') }); return; }
    const body = toBody(f);
    saveNewBtn.disabled = true; overwriteBtn.disabled = true;
    try {
      const lp = state.loadedPreset;
      const p = overwrite
        ? await api.updatePreset(lp.id, { revision: lp.revision, name, strategy: body })
        : await api.savePreset({ name, strategy: body });
      // 서버가 전략 이름을 조합 id 로 바꾸므로 빌더 입력도 맞춘다
      f.name = p.strategy.name; nameIn.value = f.name; state.draft = { ...f };
      state.loadedPreset = { id: p.id, name: p.name, revision: p.revision, body: JSON.stringify(toBody(f)) };
      toast(t(overwrite ? 'pb.overwritten' : 'pb.saved', { n: p.name }));
    } catch (err) { presetFail(err); } finally { saveNewBtn.disabled = false; renderPresetStatus(); }
  }
  saveNewBtn.addEventListener('click', () => savePreset(false));
  overwriteBtn.addEventListener('click', () => savePreset(true));
  const presetCard = h('article', { class: 'card', style: { marginTop: '18px' } }, h('h2', {}, t('pb.title')), presetStatus,
    h('label', { for: 'pname' }, t('pb.name')), presetName, presetErr,
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } }, saveNewBtn, overwriteBtn,
      h('a', { class: 'secondary', href: '#/presets' }, t('pb.list'))));

  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('b.eyebrow')), h('h1', { text: t('b.title') }),
        h('p', { class: 'lead', text: t('b.lead') })),
      h('div', { class: 'notice' }, t('b.notice', { c: cost, d: meta.data_as_of }))),
    h('div', { class: 'grid-three' }, buyCard, exitCard, uniCard), presetCard, runbar);

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
    renderPresetStatus();
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
        // 실행·탐색이 작업 슬롯을 공유한다 (P1-7). 진행 중인 쪽의 진행 화면을 연다
        const search = err.detail?.kind === 'search';
        toast(search ? t('b.busySearch') : t('b.busy'));
        location.hash = search ? `#/searches/${err.detail.id}/progress` : `#/runs/${err.detail.run_id}/progress`;
      } else {
        toast(`${err.message} (${err.code})`);
      }
    }
  });

  renderPresetStatus();
  refreshPreview();
  return () => clearTimeout(previewTimer);
}
