// 공통 프레임과 해시 라우터 (FR-X1). 화면 데이터는 모두 API JSON 에서만 읽는다 (FR-X2).
import { api } from './api.js';
import { disposeAll } from './charts.js';
import { h } from './components/ui.js';
import { BRAND } from './config.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { renderBriefing } from './views/briefing.js';
import { renderBuilder } from './views/builder.js';
import { renderLogin } from './views/login.js';
import { renderProgress } from './views/progress.js';
import { renderReport } from './views/report.js';
import { renderResults } from './views/results.js';
import { renderSettings } from './views/settings.js';
import { renderStock } from './views/stock.js';

const NAV = [
  { id: 'builder', icon: '⌘', label: 'nav.builder', href: () => '#/builder' },
  { id: 'results', icon: '▦', label: 'nav.results', href: () => (state.lastRun ? `#/runs/${state.lastRun}/results` : '#/results') },
  { id: 'stock', icon: '⌁', label: 'nav.stock', href: () => (state.lastRun ? `#/runs/${state.lastRun}/stock` : '#/stock') },
  { id: 'report', icon: '✦', label: 'nav.report', href: () => (state.lastRun ? `#/runs/${state.lastRun}/report` : '#/report') },
  { id: 'briefing', icon: '◌', label: 'nav.briefing', href: () => '#/briefing' },
];

const ROUTES = [
  [/^#\/login$/, () => ({ nav: null, view: renderLogin, bare: true })],
  [/^#\/builder$/, () => ({ nav: 'builder', view: renderBuilder })],
  [/^#\/runs\/([^/]+)\/progress$/, (m) => ({ nav: 'builder', view: (el) => renderProgress(el, m[1]) })],
  [/^#\/runs\/([^/]+)\/results$/, (m) => ({ nav: 'results', view: (el, q) => renderResults(el, m[1], q) })],
  [/^#\/runs\/([^/]+)\/stock(?:\/([^/]+))?$/, (m) => ({ nav: 'stock', view: (el, q) => renderStock(el, m[1], m[2], q) })],
  [/^#\/runs\/([^/]+)\/report$/, (m) => ({ nav: 'report', view: (el, q) => renderReport(el, m[1], q) })],
  [/^#\/(results|stock|report)$/, (m) => ({ nav: m[1], view: (el) => renderNoRun(el, m[1]) })],
  [/^#\/briefing$/, () => ({ nav: 'briefing', view: renderBriefing })],
  [/^#\/settings$/, () => ({ nav: 'settings', view: renderSettings })],
];

function renderNoRun(el, which) {
  el.append(h('div', { class: 'topline' }, h('div', {}, h('h1', { tabindex: '-1', text: t(`nav.${which}`) }))),
    h('section', { class: 'state', role: 'status' }, h('h2', { text: t('norun.title') }),
      h('p', { text: t('norun.msg') }),
      h('div', { class: 'actions' }, h('a', { class: 'primary', href: '#/builder' }, t('norun.cta')))));
}

function navLink(n, navId) {
  const label = t(n.label);
  return h('a', { class: `nav${n.id === navId ? ' active' : ''}`, href: n.href(), 'aria-label': label, 'aria-current': n.id === navId ? 'page' : null },
    h('span', { 'aria-hidden': 'true' }, n.icon), h('span', {}, label));
}

let cleanup = null;

function shell(navId) {
  const root = document.getElementById('root');
  root.replaceChildren();
  const asOf = state.meta ? t('header.asOf', { d: state.meta.data_as_of }) : t('header.loading');
  const scope = state.meta?.scope === 'sample30' ? h('span', { class: 'badge warn', title: 'REGIME_SAMPLE=1' }, t('header.sample')) : null;
  const main = h('main', { id: 'main', tabindex: '-1' });
  root.append(h('div', { class: 'app' },
    h('header', {},
      h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), BRAND),
      h('div', { class: 'header-meta' }, h('span', { class: 'hide-sm' }, t('header.workspace')), scope,
        state.guest ? h('span', { class: 'badge warn' }, t('header.guest')) : null,
        h('span', { class: 'badge' }, asOf),
        h('button', { class: 'link-btn', onclick: () => { state.signedIn = false; state.guest = false; location.hash = '#/login'; } },
          state.guest ? t('header.signIn') : t('header.signOut')))),
    h('aside', { class: 'nav-panel', 'aria-label': t('nav.main') },
      h('div', { class: 'nav-title' }, t('nav.workspace')),
      NAV.map((n) => navLink(n, navId)),
      // 사이드바 하단: 설정 탭, 그 아래 구분선과 고지 문구 (2026-09-22 사용자 요청)
      h('div', { class: 'nav-bottom' }, navLink({ id: 'settings', icon: '⚙', label: 'nav.settings', href: () => '#/settings' }, navId)),
      h('div', { class: 'sidebar-foot' }, t('sidebar.foot', { b: BRAND }))),
    main));
  return main;
}

async function route() {
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  disposeAll();
  const [path, qs] = (location.hash || '#/builder').split('?');
  const query = new URLSearchParams(qs || '');
  if (!state.signedIn && path !== '#/login') { location.replace('#/login'); return; }
  let match = null;
  for (const [re, make] of ROUTES) { const m = path.match(re); if (m) { match = make(m); break; } }
  if (!match) { location.replace('#/builder'); return; }
  document.getElementById('disclaimer').textContent = t('disclaimer');
  document.body.classList.toggle('bare', !!match.bare); // 사이드바 없는 화면(로그인)은 하단 고지를 왼쪽 끝부터
  document.querySelector('.skip').textContent = t('skip');
  const root = document.getElementById('root');
  let el;
  if (match.bare) { root.replaceChildren(); el = h('div', { id: 'main' }); root.append(el); }
  else { el = shell(match.nav); }
  const wrap = h('div', { class: 'view' });
  el.append(wrap);
  cleanup = (await match.view(wrap, query)) || null;
  const title = wrap.querySelector('h1');
  if (title) { title.setAttribute('tabindex', '-1'); title.focus({ preventScroll: true }); document.title = `${title.textContent} — ${BRAND}`; }
  window.scrollTo({ top: 0 });
}

async function loadMeta() {
  try { state.meta = await api.meta(); } catch { state.meta = null; }
}

window.addEventListener('hashchange', route);
window.addEventListener('rl:lang', route); // 언어 변경 시 현재 화면을 새 언어로 다시 그린다
loadMeta().then(route);
