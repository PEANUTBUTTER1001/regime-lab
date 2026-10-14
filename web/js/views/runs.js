// 실행 기록 목록 (X2, SRS FR-S5). /api/runs 의 상태 스냅샷만 표시한다. 새로고침 뒤에도 과거 실행을 다시 연다 (NFR-13).
import { api } from '../api.js';
import { errorView, fmt, h, linkButton, loading, pill, table, toast } from '../components/ui.js';
import { state } from '../state.js';
import { has, t } from '../i18n.js';

const KIND = { completed: '', cancelled: 'warn', failed: 'bad', running: 'neutral', queued: 'neutral' };
const FILTERS = ['all', 'completed', 'running', 'cancelled', 'failed'];

function open(r) {
  if (r.status === 'completed') return linkButton(t('rh.viewResults'), `#/runs/${r.run_id}/results`);
  if (r.status === 'running' || r.status === 'queued') return linkButton(t('rh.viewProgress'), `#/runs/${r.run_id}/progress`);
  return h('span', { class: 'muted' }, '—');
}

// 휴지통 (색 없는 선 아이콘, design.md §7.3 — 이모지를 쓰지 않는다)
const SVG = 'http://www.w3.org/2000/svg';
function trashIcon() {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  for (const d of ['M2.5 4h11', 'M6 4V2.5h4V4', 'M4 4l.7 9.5h6.6L12 4', 'M6.8 6.5v5', 'M9.2 6.5v5']) {
    const p = document.createElementNS(SVG, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}

// 실행 삭제 (10-13 결정 E20): 같은 칸에서 한 번 더 묻는다. 서버는 파일을 runs/.deleted/ 로 옮겨 되살릴 수 있게 둔다
function deleteCell(r, onDeleted) {
  const cell = h('span', { class: 'del-cell' });
  const active = r.status === 'running' || r.status === 'queued';
  const idle = () => {
    const b = h('button', { type: 'button', class: 'ibtn danger', disabled: active || null,
      'aria-label': t('rh.delete', { id: r.run_id }), title: active ? t('rh.deleteActive') : t('rh.delete', { id: r.run_id }) }, trashIcon());
    b.addEventListener('click', (e) => { e.stopPropagation(); ask(); });
    cell.replaceChildren(b);
    return b;
  };
  const ask = () => {
    const no = h('button', { type: 'button', class: 'secondary small' }, t('common.cancel'));
    const yes = h('button', { type: 'button', class: 'secondary small danger' }, t('rh.deleteYes'));
    no.addEventListener('click', (e) => { e.stopPropagation(); idle().focus(); });
    yes.addEventListener('click', async (e) => {
      e.stopPropagation();
      yes.disabled = true; no.disabled = true;
      try {
        await api.deleteRun(r.run_id);
        if (state.lastRun === r.run_id) state.lastRun = null;
        toast(t('rh.deleted', { id: r.run_id }));
        onDeleted();
      } catch (err) {
        toast(`${has(`err.${err.code}`) ? t(`err.${err.code}`) : err.message} (${err.code})`);
        idle().focus();
      }
    });
    cell.replaceChildren(h('span', { class: 'confirm' }, h('span', { class: 'warning' }, t('rh.deleteAsk')), yes, no));
    no.focus();
  };
  idle();
  return cell;
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
    FILTERS.map((f) => h('a', { class: 'tab', href: f === 'all' ? '#/runs' : `#/runs?status=${f}`, 'aria-current': f === cur ? 'true' : null }, `${t(`rh.f.${f}`)} (${f === 'all' ? runs.length : runs.filter((r) => r.status === f).length})`)));

  const columns = [
    { label: t('rh.col.started'), render: (r) => (r.created_at ? r.created_at.replace('T', ' ') : h('span', { class: 'muted' }, t('rh.cli'))) },
    { label: t('rh.col.strategies'), render: (r) => (r.strategies?.length ? r.strategies.join(', ') : h('span', { class: 'muted' }, '—')) },
    { label: t('rh.col.status'), render: (r) => h('span', {},
      pill(has(`sh.st.${r.status}`) ? t(`sh.st.${r.status}`) : r.status, KIND[r.status] ?? 'neutral'),
      r.error?.code && r.status !== 'cancelled' ? h('small', { class: 'muted', style: { display: 'block' } }, r.error.code) : null) },
    { label: t('rh.col.elapsed'), num: true, render: (r) => (r.elapsed_sec ? t('sx.seconds', { s: fmt.num(r.elapsed_sec, 1) }) : '—') },
    { label: t('rh.col.id'), render: (r) => h('small', { class: 'muted' }, r.run_id) },
    { label: t('rh.col.open'), render: open },
    { label: t('rh.col.delete'), render: (r) => deleteCell(r, () => { el.replaceChildren(); renderRuns(el, query); }) },
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
