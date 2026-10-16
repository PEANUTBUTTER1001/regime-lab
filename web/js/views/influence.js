// 영향 분석 (P3-13.3, SRS FR-N6·FR-N7, 결정 P3-12a). 기준 시각(as_of)까지 학습이 끝난 영향력 모델의 정보·기준선 대비·
// 가중치 표와, 한 종목의 가격 위 자료 사건을 보인다. 모델 고르기·시점 거르기·연결 봉은 모두 엔진
// (/api/influence/*)이 하고, 화면은 조건을 넘기고 응답을 표시만 한다 (FR-X6).
// 결과 없음은 네 가지를 서로 다르게 보인다: 학습 결과 없음 · 사건 없음 · 수집 공백 · 데이터 없음 (FR-N4).
import { api } from '../api.js';
import { influence } from '../charts.js';
import { errorView, fmt, h, helpTip, loading, pill, stateView, table } from '../components/ui.js';
import { paneFrame, paneHead, paneTabs } from '../components/workspace.js';
import { has, t } from '../i18n.js';

const MODES = ['', 'observed', 'historical_assumed'];  // '' = 모델의 학습 기준을 따름
const KST = '+09:00';
const nowKst = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16);
const toIso = (local) => (local ? `${local}:00${KST}` : '');
// 주소의 as_of 는 어느 시간대로 와도 같은 순간의 KST 로 바꿔 입력칸에 넣는다 (문자열을 자르면 +00:00 이 9시간 어긋남, PR #66 리뷰)
const KST_LOCAL = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const HAS_TZ = /([zZ]|[+-]\d{2}:?\d{2})$/;
const toLocal = (iso) => {
  if (!iso) return '';
  const d = new Date(HAS_TZ.test(iso) ? iso : `${iso}${KST}`);
  return Number.isNaN(d.getTime()) ? '' : KST_LOCAL.format(d).replace(' ', 'T');
};
const KST_FMT = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const when = (iso) => (!iso ? '—' : KST_FMT.format(new Date(iso)));
const day = (iso) => (iso ? when(iso).slice(0, 10) : '—');
const PERIOD_KIND = { train: 'neutral', valid: 'neutral', test: '' };
const period = (p) => (p ? pill(has(`inf.period.${p}`) ? t(`inf.period.${p}`) : p, PERIOD_KIND[p] ?? 'neutral') : '—');
const DIR = { '+': () => t('inf.dir.pos'), '-': () => t('inf.dir.neg'), uncertain: () => t('inf.dir.uncertain'), hidden: () => '—' };
const docHref = (id, asOf, mode) => `#/archive/doc?${new URLSearchParams({ id, as_of: asOf, mode })}`;

function unavailable(err) {
  return stateView({ kind: 'error', title: t('err.data_unavailable'), message: t('inf.unavailable'),
    detail: err.detail?.error ? String(err.detail.error) : t('common.code', { c: err.code }) });
}

function modelCard(m) {
  const p = m.periods || {};
  const span = (k) => (p[k] ? `${p[k].start} ~ ${p[k].end}` : '—');
  return h('article', { class: 'card' },
    h('h2', {}, t('inf.model.title')),
    h('dl', { class: 'info' },
      h('dt', {}, t('inf.model.version')), h('dd', {}, m.model_version),
      h('dt', {}, t('inf.model.target')), h('dd', {}, has(`inf.target.${m.target}`) ? t(`inf.target.${m.target}`) : String(m.target ?? '—')),
      h('dt', {}, t('inf.model.mode')), h('dd', {}, t(`ev.mode.${m.as_of_mode}`),
        m.as_of_mode === 'historical_assumed' ? h('small', { class: 'muted' }, ` · ${t('inf.model.assumed')}`) : null),
      h('dt', {}, t('inf.period.train')), h('dd', {}, span('train')),
      h('dt', {}, t('inf.period.valid')), h('dd', {}, span('valid')),
      h('dt', {}, t('inf.period.test')), h('dd', {}, span('test')),
      h('dt', {}, t('inf.model.minEval')), h('dd', {}, fmt.int(m.min_eval_samples))),
    m.eval_beyond_as_of ? h('p', { class: 'hint', text: t('inf.model.evalBeyond') }) : null);
}

function baselineLine(b) {
  if (!b) return null;
  const vals = t('inf.base.values', { n: b.name, m: b.metric, b: fmt.num(b.baseline, 3), v: fmt.num(b.model, 3) });
  if (b.improved === false) return h('div', { class: 'callout', role: 'note' }, h('strong', {}, t('inf.base.none')), ` ${vals}`);
  return h('p', { class: 'muted' }, b.improved ? t('inf.base.better') : t('inf.base.unknown'), ` ${vals}`);
}

function weightsTable(res) {
  const rows = res.rows;
  const digits = res.model?.format_version >= 2 ? 6 : 3;
  if (!rows.length) return h('p', { class: 'muted', text: t('inf.w.empty') });
  return table({
    caption: t('inf.w.caption', { n: fmt.int(rows.length), s: fmt.int(rows.filter((r) => r.sample_ok).length) }),
    rowClass: (r) => (r.sample_ok ? '' : 'dim'),
    rows,
    columns: [
      { label: t('inf.w.group'), render: (r) => h('span', {}, h('small', { class: 'muted' }, `${has(`inf.gt.${r.group_type}`) ? t(`inf.gt.${r.group_type}`) : r.group_type} · `),
        has(`inf.group.${r.group}`) ? t(`inf.group.${r.group}`) : r.group,
        r.window ? h('small', { class: 'muted' }, ` · ${t('inf.window', { n: r.window })}`) : null) },
      { label: t('inf.w.weight'), num: true, render: (r) => fmt.num(r.weight, digits) },
      { label: t('inf.w.dir'), render: (r) => DIR[r.direction_shown]?.() ?? '—' },
      { label: t('inf.w.ci'), num: true, render: (r) => (r.ci_low == null ? '—' : `${fmt.num(r.ci_low, digits)} ~ ${fmt.num(r.ci_high, digits)}`) },
      { label: t('inf.w.nEvents'), num: true, render: (r) => fmt.int(r.n_events) },
      { label: t('inf.w.nEval'), num: true, render: (r) => fmt.int(r.n_eval) },
      { label: t('inf.w.metric'), num: true, render: (r) => fmt.num(r.eval_metric, 3) },
      { label: t('inf.w.sample'), render: (r) => (r.sample_ok ? pill(t('inf.w.enough')) : pill(t('inf.w.short'), 'warn')) },
    ],
  });
}

function eventsSection(res, f) {
  const out = [];
  const chartEl = res.bars.length ? h('div', { class: 'chart zoom' }) : null;
  if (chartEl) out.push(chartEl);
  if (res.status === 'coverage_gap') {
    const days = Object.values(res.coverage_gaps).flatMap((s) => [...s.gap_days, ...s.partial_days]);
    const q = new URLSearchParams({ as_of: res.as_of, mode: res.mode });
    if (res.from) q.set('start', res.from);
    if (res.to) q.set('end', res.to);
    out.push(stateView({ kind: 'warn', title: t('inf.e.gapTitle'), message: t('inf.e.gapMsg'),
      detail: days.slice(0, 20).join(', '), actions: [h('a', { class: 'secondary', href: `#/archive?${q}` }, t('inf.e.toArchive'))] }));
  } else if (res.status === 'no_events') {
    out.push(stateView({ kind: 'info', title: t('inf.e.noneTitle'), message: t('inf.e.noneMsg') }));
  } else {
    if (Object.keys(res.coverage_gaps).length) out.push(h('div', { class: 'callout', role: 'note' }, t('inf.e.someGaps')));
    out.push(table({
      caption: t('inf.e.caption', { n: fmt.int(res.events.length) }),
      columns: [
        { label: t('inf.e.bar'), render: (e) => (e.linked_bar_start ? day(e.linked_bar_start) : h('small', { class: 'muted' }, t('inf.e.pending'))) },
        { label: t('inf.e.doc'), render: (e) => h('a', { href: docHref(e.doc_id, res.as_of, res.mode) }, e.title || e.doc_id) },
        { label: t('inf.e.group'), render: (e) => e.group ?? '—' },
        { label: t('inf.e.period'), render: (e) => period(e.period) },
        { label: t('inf.e.contrib'), num: true, render: (e) => fmt.num(e.contribution, 4) },
        { label: t('inf.e.available'), render: (e) => when(e.available_at) },
      ],
      rows: res.events,
    }));
  }
  // 차트에는 연결 봉과 그날 종가가 있고 학습·검증·평가 구간인 사건만 점으로 찍는다. 연결 봉 없음·기간 밖(outside)은 표에만 남긴다.
  // 화면 읽기용 설명의 구간별 건수는 실제로 찍은 점 수와 같다.
  const draw = () => {
    if (!chartEl) return;
    const points = { train: [], valid: [], test: [] };
    for (const e of res.events) {
      if (e.linked_bar_start && e.close != null && points[e.period]) {
        points[e.period].push({ date: e.linked_bar_start.slice(0, 10), close: e.close, title: e.title || e.doc_id, group: e.group, contribution: e.contribution });
      }
    }
    const n = (k) => fmt.int(points[k].length);
    influence(chartEl, { bars: res.bars, points }, t('inf.e.chartAria', { t: res.ticker, s: res.bars[0].date, e: res.bars.at(-1).date,
      n: fmt.int(points.train.length + points.valid.length + points.test.length), a: n('train'), b: n('valid'), c: n('test') }));
  };
  return { nodes: out, draw };
}

// 2열 (design.md §8.5, 10-13 hongsungmin0315 승인): 왼쪽 조건·모델 정보(--col1), 오른쪽 [가중치 | 사건·차트] 탭. 조회 로직은 그대로다.
export async function renderInfluence(root, query) {
  const frame = paneFrame({ cols: [{ label: t('ws.col.cond') }, { label: t('ws.col.analysis') }], cls: 'ws2l', splits: [['c1', t('ws.split.c1')]], current: 0 });
  root.append(frame.root);
  const [left, right] = frame.panes;
  const el = h('div', { class: 'pane-body' });
  const rhead = h('div', { class: 'pane-head' });
  const rbody = h('div', { class: 'pane-body' });
  left.append(paneHead(t('inf.title')), el);
  right.append(rhead, rbody);
  const f = {
    asOf: toLocal(query.get('as_of')) || nowKst(),
    ticker: query.get('ticker') || '',
    start: query.get('from') || '', end: query.get('to') || '',
    mode: MODES.includes(query.get('mode') || '') ? (query.get('mode') || '') : '',
    horizon: query.get('h') || '1',
  };
  const errEls = {};
  const errorSlot = (k) => (errEls[k] = h('p', { class: 'field-error hidden', id: `inferr-${k}` }));
  const inp = (id, key, err, attrs = {}) => h('input', { id, value: f[key], 'aria-describedby': `inferr-${err}`,
    oninput: (e) => { f[key] = e.target.value; }, ...attrs });
  const inputs = {
    h: inp('inf-horizon', 'horizon', 'h', { type: 'number', min: '1', max: '20', step: '1' }),
    as_of: inp('inf-asof', 'asOf', 'as_of', { type: 'datetime-local', step: '60' }),
    ticker: inp('inf-ticker', 'ticker', 'ticker', { placeholder: t('inf.tickerPh'), autocomplete: 'off', maxlength: '6' }),
    from: inp('inf-from', 'start', 'from', { type: 'date' }),
    to: inp('inf-to', 'end', 'to', { type: 'date' }),
    mode: h('select', { id: 'inf-mode', 'aria-describedby': 'inferr-mode', onchange: (e) => { f.mode = e.target.value; } },
      MODES.map((m) => h('option', { value: m, selected: m === f.mode }, m ? t(`ev.mode.${m}`) : t('inf.modeModel')))),
  };
  const showErrors = (fields = {}) => {
    for (const [k, slot] of Object.entries(errEls)) {
      const msg = fields[k];
      slot.textContent = msg ? (has(`inf.v.${k}`) ? t(`inf.v.${k}`) : msg) : '';
      slot.classList.toggle('hidden', !msg);
      inputs[k]?.setAttribute('aria-invalid', msg ? 'true' : 'false');
    }
    const first = Object.keys(fields).find((k) => inputs[k]);
    if (first) inputs[first].focus();
  };
  const go = () => {
    const p = new URLSearchParams({ as_of: toIso(f.asOf) });
    const add = (k, v) => { if (v) p.set(k, v); };
    add('ticker', f.ticker.trim()); add('from', f.start); add('to', f.end); add('mode', f.mode);
    add('h', f.horizon);
    const next = `#/influence?${p}`;
    if (location.hash === next) { root.replaceChildren(); renderInfluence(root, p); } else location.hash = next;
  };
  const field = (id, key, label, hint, tip) => h('div', {},
    tip ? h('div', { class: 'label-row' }, h('label', { for: id }, label), helpTip(tip, label)) : h('label', { for: id }, label),
    inputs[key], hint ? h('p', { class: 'hint', text: hint }) : null, errorSlot(key));
  const form = h('form', { class: 'card', novalidate: true },
    h('h2', {}, t('inf.formTitle')), h('p', { class: 'card-sub', text: t('inf.formSub') }),
    h('div', { class: 'field-two' }, field('inf-asof', 'as_of', t('ev.asOf'), t('inf.asOfHint')), field('inf-mode', 'mode', t('ev.mode'), null, t('inf.modeHint'))),
    h('div', { class: 'field-two' }, field('inf-ticker', 'ticker', t('inf.ticker'), t('inf.tickerHint')), field('inf-horizon', 'h', t('inf.horizon'))),
    h('div', { class: 'field-two' }, field('inf-from', 'from', t('inf.from')), field('inf-to', 'to', t('inf.to'), t('inf.periodHint'))),
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } },
      h('button', { class: 'primary', type: 'submit' }, `${t('inf.run')} `, h('span', { 'aria-hidden': 'true' }, '→'))));
  form.addEventListener('submit', (e) => { e.preventDefault(); go(); });

  // 왼쪽 아래: 모델 정보. 오른쪽: 결과 탭 (모델이 없거나 오류면 탭 없이 상태만)
  const box = h('div', { 'aria-live': 'polite' });
  const single = (title, node) => { rhead.replaceChildren(h('div', { class: 'ptitle' }, title)); rbody.replaceChildren(node); };
  single(t('ws.col.analysis'), loading(t('inf.loading')));
  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('inf.eyebrow')), h('h1', { text: t('inf.title') }), h('p', { class: 'lead', text: t('inf.lead') })),
      h('div', { class: 'notice' }, t('inf.notice'))),
    form, box);

  const asOf = toIso(f.asOf);
  const fail = (err, retry) => {
    if (err.code === 'validation_failed') { showErrors(err.detail?.fields || {}); return null; }
    if (err.code === 'ticker_not_found') { showErrors({ ticker: t('inf.v.unknownTicker') }); return null; }
    if (err.code === 'data_unavailable') return unavailable(err);
    return errorView(err, { onRetry: retry, backHref: '#/influence', backText: t('inf.reset') });
  };
  const retry = () => { root.replaceChildren(); renderInfluence(root, query); };

  let w;
  try {
    w = await api.influenceWeights({ as_of: asOf, h: f.horizon });
  } catch (err) {
    const node = fail(err, retry);
    single(t('ws.col.analysis'), node || h('p', { class: 'muted', style: { padding: '14px 16px' }, text: t('inf.fixFields') }));
    return;
  }
  if (w.status === 'no_model') {
    single(t('ws.col.analysis'), stateView({ kind: 'info', title: t('inf.noModelTitle'), message: t('inf.noModelMsg') }));
    return;
  }
  box.append(modelCard(w.model));
  const wBox = h('div', {}, h('article', { class: 'card' }, h('h2', {}, t('inf.w.title')),
    w.model.format_version >= 2 ? h('p', { class: 'hint', text: t('inf.auxiliary') }) : null,
    h('p', { class: 'notice', text: t('inf.fixedNotice') }), baselineLine(w.baseline), weightsTable(w)));
  const evBox = h('article', { class: 'card' }, h('h2', {}, t('inf.e.title')));
  const eBox = h('div', {}, evBox);
  let tab = f.ticker.trim() ? 'e' : 'w';
  let drawn = null; // 사건 차트는 탭이 보일 때 그린다 (숨은 칸에서는 크기가 0)
  const showTab = (k) => {
    tab = k;
    rhead.replaceChildren(paneTabs([['w', t('inf.tab.weights')], ['e', t('inf.tab.events')]], tab, (x) => { showTab(x); rhead.querySelector('[aria-selected="true"]')?.focus(); }, t('ws.col.analysis')));
    rbody.replaceChildren(tab === 'w' ? wBox : eBox);
    if (tab === 'e' && drawn) { drawn(); drawn = null; }
  };
  showTab(tab);
  if (!f.ticker.trim()) { evBox.append(h('p', { class: 'muted', text: t('inf.e.needTicker') })); return; }
  evBox.append(loading(t('inf.e.loading')));
  const params = { as_of: asOf, ticker: f.ticker.trim() };
  if (f.start) params.from = f.start;
  if (f.end) params.to = f.end;
  if (f.mode) params.mode = f.mode;
  let res;
  try {
    res = await api.influenceEvents(params);
  } catch (err) {
    evBox.replaceChildren(h('h2', {}, t('inf.e.title')), ...[fail(err, retry)].filter(Boolean));
    return;
  }
  const { nodes, draw } = eventsSection(res, f);
  evBox.replaceChildren(h('h2', {}, t('inf.e.title')),
    h('p', { class: 'card-sub', text: t('inf.e.sub', { t: res.ticker, m: t(`ev.mode.${res.mode}`), b: res.last_bar || '—' }) }), ...nodes);
  if (tab === 'e') draw(); else drawn = draw;
}
