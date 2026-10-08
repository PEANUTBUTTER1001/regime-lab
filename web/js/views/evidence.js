// 근거 검색 (P3-11, SRS FR-N5). 그 시점(as_of)에 이용 가능했던 공시·뉴스 중 질의와 맞는 근거를 보여 준다.
// 순위·시점 판정은 모두 엔진(/api/evidence/search)이 하고, 화면은 입력을 넘기고 응답을 표시만 한다 (FR-X6).
// 검색 조건은 주소(#/evidence?q=…)에 남겨 새로고침·뒤로 가기에도 같은 결과를 다시 연다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, pill, stateView, table } from '../components/ui.js';
import { has, t } from '../i18n.js';

const MODES = ['observed', 'historical_assumed'];
const KS = [5, 10, 20];
const KST = '+09:00';

// datetime-local 입력값(YYYY-MM-DDTHH:MM, KST로 간주) ↔ 시간대가 붙은 ISO 시각
const nowKst = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16);
const toIso = (local) => (local ? `${local}:00${KST}` : '');
const toLocal = (iso) => (iso ? iso.slice(0, 16) : '');
// 표시는 모두 KST로 맞춘다 (게시 시각은 UTC로 오는 출처가 있다). 날짜만 있는 값은 그대로 둔다
const KST_FMT = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const when = (iso) => (!iso ? '—' : iso.length === 10 ? iso : KST_FMT.format(new Date(iso)));
const srcLabel = (s) => (has(`ev.src.${s}`) ? t(`ev.src.${s}`) : s);

export async function renderEvidence(el, query) {
  const f = {
    q: query.get('q') || '',
    asOf: toLocal(query.get('as_of')) || nowKst(),
    mode: MODES.includes(query.get('mode')) ? query.get('mode') : 'observed',
    tickers: query.get('tickers') || '',
    k: KS.includes(Number(query.get('k'))) ? Number(query.get('k')) : KS[0],
  };
  const errEls = {};
  const errorSlot = (k) => (errEls[k] = h('p', { class: 'field-error hidden', id: `everr-${k}` }));
  const input = (id, key, attrs = {}) => h('input', { id, value: f[key], 'aria-describedby': `everr-${key === 'asOf' ? 'as_of' : key}`,
    oninput: (e) => { f[key] = e.target.value; }, ...attrs });

  const qIn = input('ev-q', 'q', { placeholder: t('ev.qPh'), maxlength: '200', autocomplete: 'off' });
  const asOfIn = input('ev-asof', 'asOf', { type: 'datetime-local', step: '60' });
  const tickIn = input('ev-tickers', 'tickers', { placeholder: t('ev.tickersPh'), inputmode: 'text', autocomplete: 'off' });
  const modeSel = h('select', { id: 'ev-mode', 'aria-describedby': 'everr-mode', onchange: (e) => { f.mode = e.target.value; } },
    MODES.map((m) => h('option', { value: m, selected: m === f.mode }, t(`ev.mode.${m}`))));
  const kSel = h('select', { id: 'ev-k', 'aria-describedby': 'everr-k', onchange: (e) => { f.k = Number(e.target.value); } },
    KS.map((k) => h('option', { value: String(k), selected: k === f.k }, t('ev.kOpt', { k }))));
  const inputs = { q: qIn, as_of: asOfIn, mode: modeSel, tickers: tickIn, k: kSel };
  const runBtn = h('button', { class: 'primary', type: 'submit' }, `${t('ev.run')} `, h('span', { 'aria-hidden': 'true' }, '→'));

  const showErrors = (fields = {}) => {
    for (const [k, slot] of Object.entries(errEls)) {
      const msg = fields[k];
      slot.textContent = msg ? (has(`ev.v.${k}`) ? t(`ev.v.${k}`) : msg) : '';
      slot.classList.toggle('hidden', !msg);
      inputs[k]?.setAttribute('aria-invalid', msg ? 'true' : 'false');
    }
    const first = Object.keys(fields).find((k) => inputs[k]);
    if (first) inputs[first].focus();
  };

  const form = h('form', { class: 'card', novalidate: true },
    h('h2', {}, t('ev.formTitle')), h('p', { class: 'card-sub', text: t('ev.formSub') }),
    h('label', { for: 'ev-q' }, t('ev.q')), qIn, errorSlot('q'),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'ev-asof' }, t('ev.asOf')), asOfIn, h('p', { class: 'hint', text: t('ev.asOfHint') }), errorSlot('as_of')),
      h('div', {}, h('label', { for: 'ev-mode' }, t('ev.mode')), modeSel, h('p', { class: 'hint', text: t('ev.modeHint') }), errorSlot('mode'))),
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'ev-tickers' }, t('ev.tickers')), tickIn, h('p', { class: 'hint', text: t('ev.tickersHint') }), errorSlot('tickers')),
      h('div', {}, h('label', { for: 'ev-k' }, t('ev.k')), kSel, errorSlot('k'))),
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } }, runBtn));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.q.trim()) { showErrors({ q: t('ev.v.q') }); return; }
    const p = new URLSearchParams({ q: f.q.trim(), as_of: toIso(f.asOf), mode: f.mode, k: String(f.k) });
    if (f.tickers.trim()) p.set('tickers', f.tickers.replace(/\s/g, ''));
    const next = `#/evidence?${p}`;
    if (location.hash === next) { el.replaceChildren(); renderEvidence(el, p); } else location.hash = next;
  });

  const resultBox = h('div', { 'aria-live': 'polite' });
  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('ev.eyebrow')), h('h1', { text: t('ev.title') }), h('p', { class: 'lead', text: t('ev.lead') })),
      h('div', { class: 'notice' }, t('ev.notice'))),
    form, resultBox);

  if (!query.get('q')) {
    resultBox.append(stateView({ title: t('ev.emptyTitle'), message: t('ev.emptyMsg') }));
    return;
  }
  resultBox.append(loading(t('ev.searching')));
  let res;
  try {
    res = await api.evidence(Object.fromEntries(query));
  } catch (err) {
    resultBox.replaceChildren();
    if (err.code === 'validation_failed') { showErrors(err.detail?.fields || {}); return; }
    if (err.code === 'data_unavailable') {
      resultBox.append(stateView({ kind: 'error', title: t('err.data_unavailable'), message: t('ev.unavailable'),
        detail: err.detail?.error ? String(err.detail.error) : t('common.code', { c: err.code }) }));
      return;
    }
    resultBox.append(errorView(err, { onRetry: () => { el.replaceChildren(); renderEvidence(el, query); }, backHref: '#/evidence', backText: t('ev.reset') }));
    return;
  }
  resultBox.replaceChildren();
  const ix = res.index;
  const summary = h('p', { class: 'muted', style: { margin: '18px 0 10px' } },
    t('ev.summary', { v: fmt.int(ix.visible_docs), n: fmt.int(ix.total_docs), a: when(ix.as_of), m: t(`ev.mode.${ix.mode}`) }),
    ix.grouping != null ? ` · ${t('ev.grouped')}` : '');
  if (res.status === 'no_evidence') {
    resultBox.append(summary, stateView({ title: t('ev.noneTitle'), message: t('ev.noneMsg') }));
    return;
  }
  // 제목이 비슷한 기사는 대표 아래에 접어 둔다 (엔진이 그 시점 후보만으로 묶음, 같은 사건이라는 판정은 아님)
  const link = (r) => h('a', { href: r.url, target: '_blank', rel: 'noopener noreferrer' }, r.title);
  const titleCell = (r) => h('div', {}, link(r), r.similar_count ? h('details', { class: 'ev-similar' },
    h('summary', {}, t('ev.similar', { n: r.similar_count })),
    h('ul', {}, r.similar.map((s) => h('li', {}, link(s),
      h('small', { class: 'muted' }, ` · ${srcLabel(s.source)} · ${t('ev.col.available')} ${when(s.available_at)}`))))) : null);
  const columns = [
    { label: t('ev.col.rank'), num: true, render: (r) => String(res.items.indexOf(r) + 1) },
    { label: t('ev.col.title'), render: titleCell },
    { label: t('ev.col.source'), render: (r) => h('span', {}, srcLabel(r.source), r.corp_name ? h('small', { class: 'muted', style: { display: 'block' } }, r.corp_name) : null) },
    { label: t('ev.col.published'), render: (r) => h('span', {}, when(r.published_at), r.time_precision === 'date_only' ? h('span', {}, ' ', pill(t('ev.dateOnly'), 'neutral')) : null) },
    { label: t('ev.col.available'), render: (r) => when(r.available_at) },
    { label: t('ev.col.score'), num: true, render: (r) => fmt.num(r.score, 2) },
    { label: t('ev.col.matched'), render: (r) => h('small', { class: 'muted' }, r.matched_tokens.join(' · ')) },
  ];
  // 비권유 고지는 모든 화면 하단의 고정 고지(화면 언어)로 표시한다
  resultBox.append(summary,
    h('article', { class: 'card' }, table({ caption: t('ev.caption', { n: res.items.length }), columns, rows: res.items })));
}
