// 자료 보관함·자료 상세 (P3-13, SRS FR-N7). 기준 시각(as_of)에 이용 가능했던 공시·뉴스를 출처 유형·기간·종목·상태로
// 거른 목록과, 한 자료의 버전 이력·정정 후보·표시 범위를 보여 준다. 시점 판정·필터·개수는 모두 엔진
// (/api/evidence/documents)이 하고, 화면은 조건을 넘기고 응답을 표시만 한다 (FR-X6).
// 조건은 주소(#/archive?…, #/archive/doc?id=…)에 남겨 새로고침·뒤로 가기에도 같은 화면을 다시 연다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, pill, stateView, table } from '../components/ui.js';
import { has, t } from '../i18n.js';

const MODES = ['observed', 'historical_assumed'];
const TYPES = ['', 'disclosure', 'news'];
const STATUSES = ['all', 'amendment', 'revised', 'unlinked'];
const KST = '+09:00';
const nowKst = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16);
const toIso = (local) => (local ? `${local}:00${KST}` : '');
const toLocal = (iso) => (iso ? iso.slice(0, 16) : '');
const KST_FMT = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const when = (iso) => (!iso ? '—' : iso.length === 10 ? iso : KST_FMT.format(new Date(iso)));
const srcLabel = (s) => (has(`ev.src.${s}`) ? t(`ev.src.${s}`) : s);
const STATUS_KIND = { amendment: 'warn', revised: 'neutral', unlinked: 'neutral' };
const statusPills = (list) => (list || []).map((s) => pill(t(`ar.status.${s}`), STATUS_KIND[s] || ''));
const docHref = (id, asOf, mode) => `#/archive/doc?${new URLSearchParams({ id, as_of: asOf, mode })}`;

// 기간 안 수집 상태 (FR-N4): 공백(gap)은 '자료 0건'과 다르다. 목록과 따로 불러오며, 실패해도 목록은 그대로 둔다.
function coveragePanel(params) {
  const box = h('div', { class: 'ar-coverage', 'aria-live': 'polite' }, h('p', { class: 'muted', text: t('ar.cov.loading') }));
  const q = Object.fromEntries(Object.entries(params).filter(([, v]) => v));
  api.coverage(q).then((res) => {
    if (res.status === 'no_coverage') { box.replaceChildren(h('p', { class: 'muted', text: t('ar.cov.none') })); return; }
    box.replaceChildren(h('h3', {}, t('ar.cov.title')), h('p', { class: 'hint', text: t('ar.cov.sub') }),
      ...Object.entries(res.sources).map(([src, s]) => {
        const c = s.counts;
        const days = (list, total, key) => (list.length ? h('details', { class: 'ev-similar' },
          h('summary', {}, t(key, { n: fmt.int(total) })), h('p', { class: 'muted' }, list.join(', '),
            total > list.length ? ` … ${t('ar.cov.more', { n: fmt.int(total - list.length) })}` : '')) : null);
        return h('div', { class: 'ar-cov-row' },
          h('strong', {}, srcLabel(src)), ' ',
          h('small', { class: 'muted' }, `${s.first_day} ~ ${s.last_day} · ${s.markets.join('·')}`), ' ',
          pill(`${t('ar.cov.collected')} ${fmt.int(c.collected)}`), ' ',
          c.forward ? pill(`${t('ar.cov.forward')} ${fmt.int(c.forward)}`, 'neutral') : null, ' ',
          c.partial ? pill(`${t('ar.cov.partial')} ${fmt.int(c.partial)}`, 'warn') : null, ' ',
          c.gap ? pill(`${t('ar.cov.gap')} ${fmt.int(c.gap)}`, 'bad') : null,
          days(s.gap_days, c.gap, 'ar.cov.gapDays'), days(s.partial_days, c.partial, 'ar.cov.partialDays'));
      }));
  }).catch((err) => {
    box.replaceChildren(h('p', { class: 'muted', text: err.code === 'data_unavailable' ? t('ar.cov.none') : t('ar.cov.failed') }));
  });
  return box;
}

function unavailable(err) {
  return stateView({ kind: 'error', title: t('err.data_unavailable'), message: t('ev.unavailable'),
    detail: err.detail?.error ? String(err.detail.error) : t('common.code', { c: err.code }) });
}

export async function renderArchive(el, query) {
  const f = {
    asOf: toLocal(query.get('as_of')) || nowKst(),
    mode: MODES.includes(query.get('mode')) ? query.get('mode') : 'observed',
    sourceType: TYPES.includes(query.get('source_type') || '') ? (query.get('source_type') || '') : '',
    start: query.get('start') || '', end: query.get('end') || '',
    tickers: query.get('tickers') || '',
    status: STATUSES.includes(query.get('status')) ? query.get('status') : 'all',
  };
  const errEls = {};
  const errorSlot = (k) => (errEls[k] = h('p', { class: 'field-error hidden', id: `arerr-${k}` }));
  const inp = (id, key, err, attrs = {}) => h('input', { id, value: f[key], 'aria-describedby': `arerr-${err}`,
    oninput: (e) => { f[key] = e.target.value; }, ...attrs });
  const sel = (id, key, err, values, label) => h('select', { id, 'aria-describedby': `arerr-${err}`, onchange: (e) => { f[key] = e.target.value; } },
    values.map((v) => h('option', { value: v, selected: v === f[key] }, label(v))));

  const inputs = {
    as_of: inp('ar-asof', 'asOf', 'as_of', { type: 'datetime-local', step: '60' }),
    mode: sel('ar-mode', 'mode', 'mode', MODES, (m) => t(`ev.mode.${m}`)),
    source_type: sel('ar-type', 'sourceType', 'source_type', TYPES, (v) => t(`ar.type.${v || 'all'}`)),
    start: inp('ar-start', 'start', 'start', { type: 'date' }),
    end: inp('ar-end', 'end', 'end', { type: 'date' }),
    tickers: inp('ar-tickers', 'tickers', 'tickers', { placeholder: t('ev.tickersPh'), autocomplete: 'off' }),
    status: sel('ar-status', 'status', 'status', STATUSES, (s) => t(`ar.status.${s}`)),
  };
  const showErrors = (fields = {}) => {
    for (const [k, slot] of Object.entries(errEls)) {
      const msg = fields[k];
      slot.textContent = msg ? (has(`ar.v.${k}`) ? t(`ar.v.${k}`) : msg) : '';
      slot.classList.toggle('hidden', !msg);
      inputs[k]?.setAttribute('aria-invalid', msg ? 'true' : 'false');
    }
    const first = Object.keys(fields).find((k) => inputs[k]);
    if (first) inputs[first].focus();
  };
  const go = (overrides = {}) => {
    const p = new URLSearchParams({ as_of: toIso(f.asOf), mode: f.mode });
    const add = (k, v) => { if (v) p.set(k, v); };
    add('source_type', f.sourceType); add('start', f.start); add('end', f.end);
    add('tickers', f.tickers.replace(/\s/g, '')); if (f.status !== 'all') p.set('status', f.status);
    for (const [k, v] of Object.entries(overrides)) { if (v == null) p.delete(k); else p.set(k, String(v)); }
    const next = `#/archive?${p}`;
    if (location.hash === next) { el.replaceChildren(); renderArchive(el, p); } else location.hash = next;
  };
  const field = (id, key, label, hint) => h('div', {}, h('label', { for: id }, label), inputs[key], hint ? h('p', { class: 'hint', text: hint }) : null, errorSlot(key));
  const form = h('form', { class: 'card', novalidate: true },
    h('h2', {}, t('ar.formTitle')), h('p', { class: 'card-sub', text: t('ar.formSub') }),
    h('div', { class: 'field-two' }, field('ar-asof', 'as_of', t('ev.asOf'), t('ev.asOfHint')), field('ar-mode', 'mode', t('ev.mode'), t('ev.modeHint'))),
    h('div', { class: 'field-two' }, field('ar-type', 'source_type', t('ar.type')), field('ar-status', 'status', t('ar.status'), t('ar.statusHint'))),
    h('div', { class: 'field-two' }, field('ar-start', 'start', t('ar.start')), field('ar-end', 'end', t('ar.end'), t('ar.periodHint'))),
    h('div', { class: 'field-two' }, field('ar-tickers', 'tickers', t('ev.tickers'), t('ev.tickersHint')), h('div', {})),
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } },
      h('button', { class: 'primary', type: 'submit' }, `${t('ar.run')} `, h('span', { 'aria-hidden': 'true' }, '→'))));
  form.addEventListener('submit', (e) => { e.preventDefault(); go({ page: null }); });

  const box = h('div', { 'aria-live': 'polite' });
  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('ar.eyebrow')), h('h1', { text: t('ar.title') }), h('p', { class: 'lead', text: t('ar.lead') })),
      h('div', { class: 'notice' }, t('ar.notice'))),
    form, box);

  box.append(loading(t('ar.loading')));
  const params = Object.fromEntries(query);
  if (!params.as_of) params.as_of = toIso(f.asOf);
  let res;
  try {
    res = await api.documents(params);
  } catch (err) {
    box.replaceChildren();
    if (err.code === 'validation_failed') { showErrors(err.detail?.fields || {}); return; }
    if (err.code === 'data_unavailable') { box.append(unavailable(err)); return; }
    box.append(errorView(err, { onRetry: () => { el.replaceChildren(); renderArchive(el, query); }, backHref: '#/archive', backText: t('ar.reset') }));
    return;
  }
  box.replaceChildren();
  const ix = res.index;
  // 상태 개수 (유형·상태 필터 전, 시점·기간·종목만 적용) — 누르면 그 상태로 거른다
  const chips = h('div', { class: 'ar-chips', role: 'group', 'aria-label': t('ar.status') },
    STATUSES.map((s) => {
      const n = s === 'all' ? Object.values(res.facets.source_type).reduce((a, b) => a + b, 0) : res.facets.status[s] || 0;
      return h('button', { type: 'button', class: `chip${s === f.status ? ' active' : ''}`, 'aria-pressed': s === f.status ? 'true' : 'false',
        onclick: () => { f.status = s; go({ page: null, status: s === 'all' ? null : s }); } }, `${t(`ar.status.${s}`)} ${fmt.int(n)}`);
    }));
  const summary = h('p', { class: 'muted', style: { margin: '18px 0 10px' } },
    t('ar.summary', { v: fmt.int(res.total), n: fmt.int(ix.total_docs), a: when(ix.as_of), m: t(`ev.mode.${ix.mode}`) }));
  const cov = coveragePanel({ as_of: ix.as_of, mode: ix.mode, start: f.start, end: f.end });
  if (res.status === 'no_documents') {
    box.append(chips, summary, cov, stateView({ title: t('ar.noneTitle'), message: t('ar.noneMsg') }));
    return;
  }
  const columns = [
    { label: t('ev.col.published'), render: (r) => h('span', {}, when(r.published_at), r.time_precision === 'date_only' ? h('span', {}, ' ', pill(t('ev.dateOnly'), 'neutral')) : null) },
    { label: t('ar.col.title'), render: (r) => h('div', {}, h('a', { href: docHref(r.doc_id, ix.as_of, ix.mode) }, r.title),
      r.url ? h('a', { class: 'ar-ext', href: r.url, target: '_blank', rel: 'noopener noreferrer', 'aria-label': t('ar.openSource') }, ' ↗') : null) },
    { label: t('ev.col.source'), render: (r) => h('span', {}, srcLabel(r.source), r.corp_name ? h('small', { class: 'muted', style: { display: 'block' } }, r.corp_name) : null) },
    { label: t('ar.col.tickers'), render: (r) => (r.tickers.length ? r.tickers.join(', ') : '—') },
    { label: t('ar.col.status'), render: (r) => h('span', { class: 'ar-pills' }, statusPills(r.statuses)) },
    { label: t('ar.col.versions'), num: true, render: (r) => fmt.int(r.versions_visible) },
    { label: t('ev.col.available'), render: (r) => when(r.available_at) },
  ];
  const pager = h('nav', { class: 'ar-pager', 'aria-label': t('ar.pager') },
    h('button', { type: 'button', class: 'secondary', disabled: res.page <= 1, onclick: () => go({ page: res.page - 1 }) }, `← ${t('ar.prev')}`),
    h('span', { class: 'muted' }, t('ar.pageOf', { p: res.page, n: res.pages })),
    h('button', { type: 'button', class: 'secondary', disabled: res.page >= res.pages, onclick: () => go({ page: res.page + 1 }) }, `${t('ar.next')} →`));
  box.append(chips, summary, cov,
    h('article', { class: 'card' }, table({ caption: t('ar.caption', { n: res.items.length, total: fmt.int(res.total) }), columns, rows: res.items }), pager));
}

export async function renderArchiveDoc(el, query) {
  const id = query.get('id') || '';
  const asOf = query.get('as_of') || toIso(nowKst());
  const mode = MODES.includes(query.get('mode')) ? query.get('mode') : 'observed';
  const back = `#/archive?${new URLSearchParams({ as_of: asOf, mode })}`;
  el.append(h('div', { class: 'topline' },
    h('div', {}, h('div', { class: 'eyebrow' }, t('ar.eyebrow')), h('h1', { tabindex: '-1', text: t('ar.docTitle') }),
      h('p', { class: 'lead', text: t('ar.docLead', { a: when(asOf), m: t(`ev.mode.${mode}`) }) })),
    h('a', { class: 'secondary', href: back }, `← ${t('ar.backList')}`)));
  const box = h('div', { class: 'ar-doc', 'aria-live': 'polite' }, loading(t('ar.loading')));
  el.append(box);
  let res;
  try {
    res = await api.document(id, { as_of: asOf, mode });
  } catch (err) {
    box.replaceChildren();
    if (err.code === 'data_unavailable') { box.append(unavailable(err)); return; }
    box.append(errorView(err, { onRetry: () => { el.replaceChildren(); renderArchiveDoc(el, query); }, backHref: back, backText: t('ar.backList') }));
    return;
  }
  box.replaceChildren();
  const d = res.doc;
  const pol = res.content_policy;
  const row = (k, v) => h('tr', {}, h('th', { scope: 'row' }, k), h('td', {}, v));
  const meta = h('table', { class: 'ar-meta' }, h('tbody', {},
    row(t('ar.col.title'), d.url ? h('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer' }, `${d.title} ↗`) : d.title),
    row(t('ev.col.source'), `${srcLabel(d.source)}${d.corp_name ? ` · ${d.corp_name}` : ''}`),
    row(t('ar.col.tickers'), d.tickers.length ? d.tickers.map((x) => x.code).join(', ') : t('ar.noTicker')),
    row(t('ar.col.status'), h('span', { class: 'ar-pills' }, statusPills(d.statuses).length ? statusPills(d.statuses) : t('ar.status.none'))),
    row(t('ev.col.published'), h('span', {}, when(d.published_at), d.time_precision === 'date_only' ? h('span', {}, ' ', pill(t('ev.dateOnly'), 'neutral')) : null)),
    row(t('ar.firstSeen'), when(d.first_seen_at)),
    row(t('ev.col.available'), when(d.available_at)),
    row(t('ar.scope'), has(`ar.scope.${pol.license_scope}`) ? t(`ar.scope.${pol.license_scope}`) : pol.license_scope)));
  const policy = h('p', { class: 'notice', style: { marginTop: '12px' } }, pol.summary_shown ? t('ar.policy.summary') : t('ar.policy.linkOnly'));
  const summary = pol.summary_shown && d.summary ? h('blockquote', { class: 'ar-summary' }, d.summary) : null;
  const versions = table({
    caption: t('ar.versionsCaption', { n: res.versions.length }), rows: res.versions,
    columns: [
      { label: t('ar.col.version'), num: true, render: (v) => `v${v.version}` },
      { label: t('ar.col.title'), render: (v) => v.title },
      { label: t('ar.firstSeen'), render: (v) => when(v.first_seen_at) },
      { label: t('ev.col.available'), render: (v) => when(v.available_at) },
      { label: t('ar.col.backfilled'), render: (v) => (v.backfilled ? t('ar.backfilled') : t('ar.observed')) },
    ],
  });
  const linkTo = (x) => h('a', { href: docHref(x.doc_id, res.as_of, res.mode) }, x.title || x.doc_id);
  const cand = res.amends_candidate;
  const rel = h('article', { class: 'card ar-rel' }, h('h2', {}, t('ar.relTitle')), h('p', { class: 'card-sub', text: t('ar.relSub') }),
    h('h3', {}, t('ar.amends')),
    !cand ? h('p', { class: 'muted', text: t('ar.amendsNone') })
      : cand.visible ? h('p', {}, pill(t('ar.candidate'), 'warn'), ' ', linkTo(cand), h('small', { class: 'muted' }, ` · ${when(cand.published_at)}`))
        : h('p', { class: 'muted', text: t('ar.amendsHidden', { id: cand.doc_id }) }),
    h('h3', {}, t('ar.amendedBy')),
    res.amended_by_candidates.length
      ? h('ul', {}, res.amended_by_candidates.map((x) => h('li', {}, pill(t('ar.candidate'), 'warn'), ' ', linkTo(x), h('small', { class: 'muted' }, ` · ${when(x.published_at)}`))))
      : h('p', { class: 'muted', text: t('ar.amendedByNone') }));
  box.append(h('article', { class: 'card' }, h('h2', {}, d.title), meta, policy, summary),
    h('article', { class: 'card' }, h('h2', {}, t('ar.versionsTitle')), h('p', { class: 'card-sub', text: t('ar.versionsSub') }), versions),
    rel);
}
