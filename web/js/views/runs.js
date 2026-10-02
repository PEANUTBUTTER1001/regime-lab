// 실행 기록 목록 (X2, SRS FR-S5). /api/runs 의 상태 스냅샷만 표시한다. 새로고침 뒤에도 과거 실행을 다시 연다 (NFR-13).
import { api } from '../api.js';
import { errorView, fmt, h, linkButton, loading, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';

const KIND = { completed: '', cancelled: 'warn', failed: 'bad', running: 'neutral', queued: 'neutral' };
const FILTERS = ['all', 'completed', 'running', 'cancelled', 'failed'];

function open(r) {
  if (r.status === 'completed') return linkButton(t('rh.viewResults'), `#/runs/${r.run_id}/results`);
  if (r.status === 'running' || r.status === 'queued') return linkButton(t('rh.viewProgress'), `#/runs/${r.run_id}/progress`);
  return h('span', { class: 'muted' }, '—');
}

export async function renderRuns(el, query) {
  el.append(h('h1', { text: t('rh.title') }), loading());
  let runs;
  try { runs = (await api.runs()).runs; } catch (e) {
    el.replaceChildren(h('h1', { text: t('rh.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderRuns(el, query); } }));
    return;
  }
  el.replaceChildren();
  const cur = FILTERS.includes(query.get('status')) ? query.get('status') : 'all';
  const rows = cur === 'all' ? runs : runs.filter((r) => r.status === cur);

  const filterBar = h('div', { class: 'tabs', role: 'group', 'aria-label': t('rh.filter'), style: { margin: '0 0 14px' } },
    FILTERS.map((f) => h('a', { class: 'tab', href: f === 'all' ? '#/runs' : `#/runs?status=${f}`, 'aria-current': f === cur ? 'true' : null,
      style: f === cur ? { textDecoration: 'none', color: 'var(--mint-ink)', background: 'var(--mint)', borderColor: 'var(--mint)', fontWeight: '750' }
        : { textDecoration: 'none' } }, `${t(`rh.f.${f}`)} (${f === 'all' ? runs.length : runs.filter((r) => r.status === f).length})`)));

  const columns = [
    { label: t('rh.col.started'), render: (r) => (r.created_at ? r.created_at.replace('T', ' ') : h('span', { class: 'muted' }, t('rh.cli'))) },
    { label: t('rh.col.strategies'), render: (r) => (r.strategies?.length ? r.strategies.join(', ') : h('span', { class: 'muted' }, '—')) },
    { label: t('rh.col.status'), render: (r) => h('span', {},
      pill(has(`sh.st.${r.status}`) ? t(`sh.st.${r.status}`) : r.status, KIND[r.status] ?? 'neutral'),
      r.error?.code && r.status !== 'cancelled' ? h('small', { class: 'muted', style: { display: 'block' } }, r.error.code) : null) },
    { label: t('rh.col.elapsed'), num: true, render: (r) => (r.elapsed_sec ? t('sx.seconds', { s: fmt.num(r.elapsed_sec, 1) }) : '—') },
    { label: t('rh.col.id'), render: (r) => h('small', { class: 'muted' }, r.run_id) },
    { label: t('rh.col.open'), render: open },
  ];

  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('rh.eyebrow')), h('h1', { text: t('rh.title') }), h('p', { class: 'lead', text: t('rh.lead') })),
      h('div', { class: 'notice' }, t('rh.notice'))),
    runs.length
      ? h('article', { class: 'card' }, filterBar,
        rows.length ? table({ caption: t('rh.caption', { n: rows.length }), columns, rows }) : h('p', { class: 'muted', text: t('rh.noneInFilter') }))
      : h('section', { class: 'state', role: 'status' }, h('h2', { text: t('rh.emptyTitle') }), h('p', { text: t('norun.msg') }),
        h('div', { class: 'actions' }, linkButton(t('norun.cta'), '#/builder', true))));
}
