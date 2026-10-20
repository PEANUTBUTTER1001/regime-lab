// 홈은 기존 API의 기록·상태만 표시한다. 분석 계산과 기록 저장은 기존 기능의 책임.
import { api } from '../api.js';
import { h, linkButton, loading, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

// 기존 작업 시각은 offset 없는 서버 로컬 시각도 포함한다. 공식 서버 운영 시간대 KST로 해석.
const instant = (value) => {
  if (!value) return null;
  const d = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}+09:00`);
  return Number.isNaN(d.getTime()) ? null : d;
};
const dateFormat = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const when = (value) => { const d = instant(value); return d ? dateFormat.format(d) : '—'; };
const statusLabel = (value) => has(`sh.st.${value}`) ? t(`sh.st.${value}`) : t('home.unknown');
const kindLabel = (r) => t(r.kind === 'search' ? 'home.search' : 'home.backtest');
const href = (r) => {
  if (!['completed', 'running', 'queued'].includes(r.status)) return null;
  return `#/${r.kind === 'search' ? 'searches' : 'runs'}/${encodeURIComponent(r.id)}/${r.status === 'completed' ? 'results' : 'progress'}`;
};
export function recentAnalyses(runs, searches) {
  return [
    ...runs.map(r => ({ ...r, kind: 'run', id: r.run_id, name: (r.strategies || []).join(' · ') || r.run_id })),
    ...searches.map(r => ({ ...r, kind: 'search', id: r.search_id, name: r.name || r.search_id })),
  ].sort((a, b) => (instant(b.created_at)?.getTime() || 0) - (instant(a.created_at)?.getTime() || 0) || `${a.kind}:${a.id}`.localeCompare(`${b.kind}:${b.id}`));
}

export function renderHome(el) {
  let disposed = false;
  let request = 0;
  const greeting = h('h1', {}, t('home.welcome'));
  const sub = h('p', { class: 'lead' }, t('home.lead'));
  const recent = h('div', { 'aria-live': 'polite' });
  const data = h('div', { class: 'sec' });
  const shortcut = (icon, title, copy, target) => h('a', { class: 'card home-shortcut', href: target },
    h('span', { class: 'home-shortcut-top', 'aria-hidden': 'true' }, h('span', {}, icon), h('span', {}, '→')),
    h('h2', {}, t(title)), h('p', {}, t(copy)));
  el.append(
    h('div', { class: 'topline home-top' }, h('div', {}, h('div', { class: 'eyebrow' }, t('home.workspace')), greeting, sub)),
    h('section', { class: 'card home-start', 'aria-labelledby': 'home-start-title' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('nav.g.test')), h('h2', { id: 'home-start-title' }, t('home.hero')),
        h('p', {}, t('home.heroCopy')), h('div', { class: 'actions' }, linkButton(t('nav.builder'), '#/builder', true), linkButton(t('home.load'), '#/presets'))),
      h('div', { class: 'home-flow' }, h('span', {}, t('home.flowConditions')), h('span', { 'aria-hidden': 'true' }, '→'),
        h('span', {}, t('home.flowCompare')), h('span', { 'aria-hidden': 'true' }, '→'), h('span', {}, t('home.flowVerify')))),
    h('section', { class: 'grid-three home-shortcuts', 'aria-label': t('home.shortcuts') },
      shortcut('⟲', 'nav.search', 'home.searchCopy', '#/search'), shortcut('◫', 'home.forecast', 'home.forecastCopy', '#/forecast'), shortcut('☆', 'home.load', 'home.loadCopy', '#/presets')),
    h('div', { class: 'home-lower' },
      h('section', { class: 'card home-panel', 'aria-labelledby': 'home-recent-title' },
        h('div', { class: 'pane-head' }, h('h2', { class: 'ptitle', id: 'home-recent-title' }, t('home.recent')),
          h('div', { class: 'aside' }, linkButton(t('home.records'), '#/runs'), linkButton(t('home.searchRecords'), '#/search'))), recent),
      h('aside', { class: 'card home-panel home-data', 'aria-label': t('home.data') },
        h('div', { class: 'pane-head' }, h('h2', { class: 'ptitle' }, t('home.data'))), data,
        h('div', { class: 'home-reading' }, h('h3', {}, t('home.reading')), h('ol', {},
          h('li', {}, t('home.read1')), h('li', {}, t('home.read2')), h('li', {}, t('home.read3')))))));

  function showData(meta) {
    data.replaceChildren(h('div', { class: 'dim' }, t('home.asOf')),
      h('div', { class: 'home-data-date' }, meta?.data_as_of || '—'),
      h('dl', { class: 'info' }, h('dt', {}, t('home.markets')), h('dd', {}, meta?.markets?.join(' · ') || '—'),
        h('dt', {}, t('home.timeframe')), h('dd', {}, t('home.daily')),
        h('dt', {}, t('home.state')), h('dd', {}, t(meta?.status === 'ready' ? 'home.ready' : meta?.status === 'warming_up' ? 'home.warming' : 'home.unavailable'))),
      h('p', { class: 'home-data-note' }, t('home.dataNote')));
  }
  async function load() {
    const ticket = ++request;
    recent.replaceChildren(loading());
    const [runResult, searchResult, metaResult] = await Promise.allSettled([api.runs(), api.searches(), api.meta()]);
    if (disposed || ticket !== request) return;
    showData(metaResult.status === 'fulfilled' ? metaResult.value : null);
    recent.replaceChildren();
    const failed = runResult.status === 'rejected' || searchResult.status === 'rejected';
    if (failed || metaResult.status === 'rejected') recent.append(h('div', { class: 'notice home-retry', role: 'status' },
      h('span', {}, t('home.loadFailed')), h('button', { class: 'secondary small', type: 'button', onclick: load }, t('common.retry'))));
    const rows = recentAnalyses(runResult.status === 'fulfilled' ? runResult.value.runs : [], searchResult.status === 'fulfilled' ? searchResult.value.searches : []);
    if (!rows.length) {
      if (!failed) {
        greeting.textContent = t('home.firstWelcome'); sub.textContent = t('home.firstLead');
        recent.append(h('div', { class: 'home-empty' }, h('h3', {}, t('home.emptyTitle')), h('p', {}, t('home.emptyCopy')), linkButton(t('nav.builder'), '#/builder')));
      }
      return;
    }
    greeting.textContent = t('home.welcome'); sub.textContent = t('home.lead');
    const last = rows.find(r => r.kind === 'run' && r.status === 'completed') || rows.find(r => r.status === 'completed');
    if (last) recent.append(h('div', { class: 'home-continue' }, h('div', { class: 'home-continue-text' },
      h('span', { class: 'dim' }, t('home.lastCompleted')), h('b', {}, last.name), h('small', {}, `${kindLabel(last)} · ${when(last.created_at)}`)), linkButton(t('home.continue'), href(last))));
    recent.append(table({ caption: t('home.recent'), columns: [
      { label: t('home.name'), render: r => href(r) ? h('a', { class: 'home-record', href: href(r) }, r.name) : h('span', {}, r.name) },
      { label: t('home.kind'), render: kindLabel }, { label: t('home.started'), render: r => when(r.created_at) },
      { label: t('home.state'), render: r => pill(statusLabel(r.status), r.status === 'failed' ? 'bad' : r.status === 'cancelled' ? 'warn' : 'neutral') },
    ], rows: rows.slice(0, 5) }));
  }
  showData(state.meta); load();
  return () => { disposed = true; };
}
