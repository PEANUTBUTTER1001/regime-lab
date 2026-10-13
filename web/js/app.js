// 공통 프레임과 해시 라우터 (FR-X1). 화면 데이터는 모두 API JSON 에서만 읽는다 (FR-X2).
// 틀 (design.md §5, UI 개혁 B안 2026-10-13): 상단바(칸 = 아래 열 경계) · 고정 메뉴(E18 4묶음) · 본문 · 비권유 고지 줄.
import { api } from './api.js';
import { disposeAll } from './charts.js';
import { h } from './components/ui.js';
import { applyWidths, setLayoutKind, setRouteGuard, takeRoute } from './components/workspace.js';
import { BRAND } from './config.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { renderBriefing } from './views/briefing.js';
import { renderArchive, renderArchivePage } from './views/archive.js';
import { renderEvidence } from './views/evidence.js';
import { renderInfluence } from './views/influence.js';
import { renderLogin } from './views/login.js';
import { renderPassword } from './views/password.js';
import { renderRunWorkspace } from './views/results.js';
import { renderRuns } from './views/runs.js';
import { renderPresets } from './views/presets.js';
import { renderSearchPage } from './views/search.js';
import { renderSettings } from './views/settings.js';

// 메뉴는 사용자 작업 순서대로 묶는다 (2026-10-12 UI 개편 결정 E18). 새 화면은 알맞은 group 으로 항목을 추가한다.
// soon: 화면 자리만 있고 내용이 아직 없는 메뉴 ("준비 중" 표시). 화면을 붙일 때 지운다.
const NAV_GROUPS = ['test', 'results', 'docs', 'market'];
const NAV = [
  { id: 'builder', group: 'test', icon: '⌘', label: 'nav.builder', href: () => '#/builder' },
  { id: 'search', group: 'test', icon: '⟲', label: 'nav.search', href: () => '#/search' },
  { id: 'presets', group: 'test', icon: '☆', label: 'nav.presets', href: () => '#/presets' },
  { id: 'runs', group: 'test', icon: '☰', label: 'nav.runs', href: () => '#/runs' },
  // 실행 결과는 작업면의 넓은 보기다 (조건 열은 서랍). 메뉴는 최근 실행으로 간다
  { id: 'results', group: 'results', icon: '▦', label: 'nav.results', href: () => (state.lastRun ? `#/runs/${state.lastRun}/results` : '#/results') },
  { id: 'evidence', group: 'docs', icon: '⌕', label: 'nav.evidence', href: () => '#/evidence' },
  { id: 'archive', group: 'docs', icon: '▤', label: 'nav.archive', href: () => '#/archive' },
  { id: 'influence', group: 'docs', icon: '◇', label: 'nav.influence', href: () => '#/influence' },
  // 봉 예측 화면은 P2-10(Seonghwanaa) 범위 — 화면을 붙이면 soon 을 지우고 view 를 바꾼다
  { id: 'forecast', group: 'market', icon: '◫', label: 'nav.forecast', href: () => '#/forecast', soon: true },
  { id: 'briefing', group: 'market', icon: '◌', label: 'nav.briefing', href: () => '#/briefing', soon: true },
];
const NAV_BY_ID = Object.fromEntries(NAV.map((n) => [n.id, n]));

// kind: ws(조건·결과·근거 3열) · focus(결과·근거, 조건은 서랍) · two(2열) · alignr(2열, 오른쪽 = col3) · page(1열) · bare(틀 없음)
const ROUTES = [
  [/^#\/password$/, () => ({ kind: 'bare', view: renderPassword })],
  [/^#\/login$/, () => ({ kind: 'bare', view: renderLogin })],
  [/^#\/builder$/, () => ({ nav: 'builder', kind: 'ws', view: (el, q) => renderRunWorkspace(el, { mode: 'ws', query: q }) })],
  [/^#\/runs$/, () => ({ nav: 'runs', kind: 'page', view: renderRuns })],
  [/^#\/runs\/([^/]+)\/progress$/, (m) => ({ nav: 'builder', kind: 'ws', view: (el, q) => renderRunWorkspace(el, { mode: 'ws', runId: m[1], progress: true, query: q }) })],
  [/^#\/runs\/([^/]+)\/results$/, (m) => ({ nav: 'results', kind: 'focus', view: (el, q) => renderRunWorkspace(el, { mode: 'focus', runId: m[1], ins: 'stocks', query: q }) })],
  [/^#\/runs\/([^/]+)\/stock(?:\/([^/]+))?$/, (m) => ({ nav: 'results', kind: 'focus', view: (el, q) => renderRunWorkspace(el, { mode: 'focus', runId: m[1], ins: 'stocks', ticker: m[2], query: q }) })],
  [/^#\/runs\/([^/]+)\/report$/, (m) => ({ nav: 'results', kind: 'focus', view: (el, q) => renderRunWorkspace(el, { mode: 'focus', runId: m[1], ins: 'report', query: q }) })],
  [/^#\/(results|stock|report)$/, (m) => ({ nav: 'results', kind: 'focus', view: (el, q) => renderRunWorkspace(el, { mode: 'focus', ins: m[1] === 'report' ? 'report' : 'stocks', query: q }) })],
  [/^#\/search$/, () => ({ nav: 'search', kind: 'two', view: (el) => renderSearchPage(el, {}) })],
  [/^#\/searches\/([^/]+)\/progress$/, (m) => ({ nav: 'search', kind: 'two', view: (el) => renderSearchPage(el, { id: m[1], step: 'progress' }) })],
  [/^#\/searches\/([^/]+)\/results$/, (m) => ({ nav: 'search', kind: 'two', view: (el) => renderSearchPage(el, { id: m[1], step: 'results' }) })],
  [/^#\/presets$/, () => ({ nav: 'presets', kind: 'page', view: renderPresets })],
  [/^#\/evidence$/, () => ({ nav: 'evidence', kind: 'alignr', view: renderEvidence })],
  [/^#\/archive$/, () => ({ nav: 'archive', kind: 'alignr', view: renderArchive })],
  [/^#\/archive\/doc$/, () => ({ nav: 'archive', kind: 'alignr', view: renderArchivePage })],
  [/^#\/influence$/, () => ({ nav: 'influence', kind: 'two', view: renderInfluence })],
  [/^#\/forecast$/, () => ({ nav: 'forecast', kind: 'page', view: renderSoon })],
  [/^#\/briefing$/, () => ({ nav: 'briefing', kind: 'page', view: renderBriefing })],
  [/^#\/settings$/, () => ({ nav: 'settings', kind: 'page', view: renderSettings })],
];

// 내용이 아직 없는 메뉴 자리 (봉 예측 — P2-10)
function renderSoon(el) {
  el.append(h('div', { class: 'topline' }, h('div', {}, h('h1', { tabindex: '-1', text: t('nav.forecast') }))),
    h('section', { class: 'state', role: 'status' }, h('h2', { text: t('soon.title') }), h('p', { text: t('soon.forecast') })));
}

// ---------------------------------------------------------------- 테마 (design.md §6.3)
// 첫 테마는 index.html 머리 스크립트가 정한다. 고르면 저장하고, 저장값이 없으면 기기 설정을 따른다.
const theme = () => document.documentElement.dataset.theme || 'dark';
function setTheme(v, save) {
  document.documentElement.dataset.theme = v;
  if (save) state.theme = v;
  route(); // 차트 색은 그릴 때 토큰에서 읽으므로 화면을 다시 그린다
}
try {
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => { if (!state.theme) setTheme(e.matches ? 'light' : 'dark', false); });
} catch { /* 오래된 브라우저 */ }

// ---------------------------------------------------------------- 서랍 (메뉴·조건): 열면 서랍 안으로 초점, 닫으면 연 버튼으로 (design.md §10.1)
let drawerOpener = null;
function closeDrawers() {
  const was = document.body.classList.contains('drawer-open') || document.body.classList.contains('nav-open');
  document.body.classList.remove('nav-open', 'drawer-open');
  if (was && drawerOpener?.isConnected) drawerOpener.focus();
  drawerOpener = null;
}
function toggleDrawer(cls, opener, focusSel) {
  if (document.body.classList.contains(cls)) { closeDrawers(); return; }
  document.body.classList.remove('nav-open', 'drawer-open');
  document.body.classList.add(cls);
  drawerOpener = opener;
  setTimeout(() => document.querySelector(focusSel)?.focus(), 0); // visibility 는 클래스와 함께 바로 바뀐다
}
const COND_FOCUS = '#p1 .pane-body input, #p1 .pane-body button';

// ---------------------------------------------------------------- 상단바
function navLink(n, navId) {
  const label = t(n.label);
  const soon = n.soon ? t('nav.soon') : null;
  return h('a', { class: `nav${n.id === navId ? ' active' : ''}`, href: n.href(), 'aria-label': soon ? `${label} (${soon})` : label, 'aria-current': n.id === navId ? 'page' : null },
    h('span', { 'aria-hidden': 'true' }, n.icon), h('span', {}, label),
    soon && h('span', { class: 'nav-soon', 'aria-hidden': 'true' }, soon));
}

function navGroups(navId) {
  return NAV_GROUPS.map((g) => h('div', { class: 'nav-group', role: 'group', 'aria-label': t(`nav.g.${g}`) },
    h('div', { class: 'nav-title', 'aria-hidden': 'true' }, t(`nav.g.${g}`)),
    NAV.filter((n) => n.group === g).map((n) => navLink(n, navId))));
}

function statusCell() {
  const light = theme() === 'light';
  const toLabel = t(light ? 'theme.toDark' : 'theme.toLight');
  const asOf = state.meta?.data_as_of;
  return h('div', { class: 'cell' },
    h('button', { class: 'ibtn', type: 'button', 'aria-label': toLabel, title: toLabel, onclick: () => setTheme(light ? 'dark' : 'light', true) },
      h('span', { 'aria-hidden': 'true' }, light ? '☾' : '☀︎')),
    h('span', { class: 'chip hide-narrow', title: t('header.asOfTitle') }, h('span', { class: 'hide-mid' }, t('header.data')), h('b', {}, asOf || '…')),
    state.meta?.scope === 'sample30' ? h('span', { class: 'chip warn hide-narrow', title: 'REGIME_SAMPLE=1' }, t('header.sample')) : null,
    state.guest ? h('span', { class: 'chip warn hide-narrow' }, t('header.guest')) : null,
    h('a', { class: 'ibtn', href: '#/settings', 'aria-label': t('nav.settings'), title: t('nav.settings') }, h('span', { 'aria-hidden': 'true' }, '⚙')));
}

function headerBar(navId, kind) {
  const brand = h('div', { class: 'cell' },
    h('button', { class: 'ibtn only-narrow', type: 'button', 'aria-label': t('nav.open'), 'aria-controls': 'nav-panel',
      onclick: (e) => toggleDrawer('nav-open', e.currentTarget, '#nav-panel a.nav') }, h('span', { 'aria-hidden': 'true' }, '☰')),
    h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), BRAND));
  const n = NAV_BY_ID[navId];
  const pageTitle = h('div', { class: 'page-title' }, h('b', {}, t(n ? n.label : 'nav.settings')), n && h('span', {}, t(`nav.g.${n.group}`)));
  // 작업면: 가운데 칸(#bar-ctx)은 실행 맥락을 작업면이 채운다
  const ctx = h('div', { class: 'cell ctx' });
  if (kind === 'ws') {
    ctx.append(h('button', { class: 'ibtn only-mid', type: 'button', 'aria-label': t('ws.openConditions'), title: t('ws.openConditions'),
      onclick: (e) => toggleDrawer('drawer-open', e.currentTarget, COND_FOCUS) }, h('span', { 'aria-hidden': 'true' }, '☰')), h('div', { id: 'bar-ctx', class: 'run-ctx' }));
    return h('header', { class: 'bar' }, brand, h('div', { class: 'cell c1title' }, pageTitle), ctx, statusCell());
  }
  if (kind === 'focus') {
    ctx.append(h('button', { class: 'secondary small hide-narrow', type: 'button', onclick: (e) => toggleDrawer('drawer-open', e.currentTarget, COND_FOCUS) }, t('ws.editConditions')),
      h('div', { id: 'bar-ctx', class: 'run-ctx' }));
    return h('header', { class: 'bar' }, brand, ctx, statusCell());
  }
  ctx.append(pageTitle);
  return h('header', { class: 'bar' }, brand, ctx, statusCell());
}

let cleanup = null;

function shell(navId, kind) {
  const root = document.getElementById('root');
  root.replaceChildren();
  const ws = ['ws', 'focus', 'two', 'alignr'].includes(kind);
  const main = h('main', { id: 'main', tabindex: '-1', class: ws ? 'ws-main' : '' });
  const account = h('div', { class: 'nav-account' },
    h('span', {}, state.guest ? t('header.guest') : (state.user?.email || '')),
    h('button', { class: 'link-btn', type: 'button', onclick: async (event) => {
      const button = event.currentTarget; button.disabled = true;
      try { if (state.authConfig?.enabled) await api.logout(); state.clearAuth(); location.hash = '#/login'; }
      catch { button.title = t('login.unavailable'); }
      finally { button.disabled = false; }
    } }, state.guest ? t('header.signIn') : t('header.signOut')));
  root.append(h('div', { class: `app k-${kind}` },
    headerBar(navId, kind),
    h('aside', { class: 'nav-panel', id: 'nav-panel', 'aria-label': t('nav.main') }, navGroups(navId), h('div', { class: 'nav-bottom' }, account)),
    main),
  h('div', { class: 'scrim', onclick: closeDrawers }));
  setLayoutKind(kind);
  return main;
}

async function route(fromHistory = false) {
  // 같은 작업면 안의 기록 이동(뒤로·앞으로)은 그 화면이 처리한다
  if (fromHistory === true && takeRoute(location.hash)) return;
  setRouteGuard(null);
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  disposeAll();
  document.body.classList.remove('nav-open', 'drawer-open');
  const [path, qs] = (location.hash || '#/builder').split('?');
  const query = new URLSearchParams(qs || '');
  if (!state.signedIn && !['#/login', '#/password'].includes(path)) { location.replace('#/login'); return; }
  let match = null;
  for (const [re, make] of ROUTES) { const m = path.match(re); if (m) { match = make(m); break; } }
  if (!match) { location.replace('#/builder'); return; }
  // 실행 없이 결과 메뉴로 들어오면 최근 실행으로 보낸다
  const bare = path.match(/^#\/(results|stock|report)$/);
  if (bare && state.lastRun) { location.replace(`#/runs/${state.lastRun}/${bare[1]}`); return; }
  document.getElementById('disclaimer').textContent = t('disclaimer');
  document.body.classList.toggle('bare', match.kind === 'bare'); // 메뉴 없는 화면(로그인)
  document.querySelector('.skip').textContent = t('skip');
  const root = document.getElementById('root');
  let el;
  if (match.kind === 'bare') { root.replaceChildren(); el = h('div', { id: 'main' }); root.append(el); }
  else { el = shell(match.nav, match.kind); }
  const wrap = h('div', { class: 'view' });
  if (['ws', 'focus', 'two', 'alignr'].includes(match.kind)) wrap.style.display = 'contents';
  el.append(wrap);
  cleanup = (await match.view(wrap, query)) || null;
  applyWidths();
  const title = wrap.querySelector('h1') || document.querySelector('.page-title b');
  if (title && match.kind !== 'ws' && match.kind !== 'focus') { title.setAttribute('tabindex', '-1'); title.focus({ preventScroll: true }); }
  const n = NAV_BY_ID[match.nav];
  document.title = `${(wrap.querySelector('h1') || {}).textContent || (n ? t(n.label) : BRAND)} — ${BRAND}`;
}

async function loadMeta() {
  try { state.meta = await api.meta(); } catch { state.meta = null; }
}

window.addEventListener('hashchange', () => route(true));
window.addEventListener('rl:lang', route); // 언어 변경 시 현재 화면을 새 언어로 다시 그린다
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawers(); });
// 서랍이 열린 채 화면이 넓어지면 닫는다 (메뉴 서랍은 1024 이상에서 뜻이 없다)
window.addEventListener('resize', () => { if (innerWidth >= 1024) document.body.classList.remove('nav-open'); });
// 작은 화면·언어 변경으로 고지가 여러 줄이 되어도 본문 높이를 맞춘다
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty('--disclaimer-height', `${Math.ceil(entry.target.getBoundingClientRect().height)}px`);
}).observe(document.getElementById('disclaimer'));
async function start() {
  try {
    state.authConfig = await api.authConfig();
    if (state.authConfig.enabled) {
      state.guest = false;
      if (!(location.hash || '').startsWith('#/password')) {
        try { const data = await api.me(); state.setUser(data.user); }
        catch { state.clearAuth(); }
      }
    }
  } catch { state.authConfig = {enabled:true, available:false}; state.clearAuth(); }
  if (state.signedIn) await loadMeta();
  await route();
}
window.addEventListener('rl:unauthorized', () => { state.clearAuth(); location.hash = '#/login'; });
start();
