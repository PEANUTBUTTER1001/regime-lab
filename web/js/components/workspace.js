// 작업면 부품 (design.md §5.4·§6). 전략 만들기·실행 결과(3열)와 목표로 전략 찾기·공시·뉴스 검색(2열)이 같이 쓴다.
// 열 틀·탭 줄·경계선 조절바·가운데↔오른쪽 자리 바꾸기·좁은 화면 전환 버튼만 맡는다. 분석 계산은 하지 않는다.
// 열 너비는 .app 의 --col1/--col3 변수 하나로 정하고, 상단바 칸도 같은 변수를 써서 칸 경계 = 열 경계가 된다.
import { t } from '../i18n.js';
import { state } from '../state.js';
import { h } from './ui.js';

// 메뉴 208 고정. 조건 열 240–520, 오른쪽 열 280–720, 가운데는 400 아래로 줄이지 않는다 (design.md §5.4)
const RAIL = 208;
const LIM = { c1: [240, 520], c3: [280, 720] };
const MIN_CENTER = 400;
const clamp = (lo, v, hi) => Math.max(lo, Math.min(hi, v));
const defaults = () => ({ c1: clamp(264, innerWidth * 0.22, 320), c3: clamp(300, innerWidth * 0.26, 400) });

let kind = 'page'; // 현재 화면 종류: ws(3열) · focus(조건 서랍) · alignr(오른쪽 열이 col3) · two · page
export function setLayoutKind(k) { kind = k; applyWidths(); }

// 저장한 폭 → 지금 창에 맞춘 폭. 가운데 400 을 지키도록 오른쪽 → 조건 순서로 최소값까지 줄인다 (저장값은 그대로 둔다)
export function widths() {
  const lay = state.layout;
  const d = defaults();
  // 조건 열(또는 탐색 입력 열)과 오른쪽 열이 지금 화면에 보이는가
  const c1on = (kind === 'ws' && innerWidth >= 1280) || (kind === 'two' && innerWidth >= 1024);
  const c3on = ['ws', 'focus', 'alignr'].includes(kind) && innerWidth >= 1024;
  let c1 = clamp(LIM.c1[0], lay.c1 ?? d.c1, LIM.c1[1]);
  let c3 = clamp(LIM.c3[0], lay.c3 ?? d.c3, LIM.c3[1]);
  const avail = innerWidth - (innerWidth >= 1024 ? RAIL : 0);
  let over = (c1on ? c1 : 0) + (c3on ? c3 : 0) + MIN_CENTER - avail;
  if (over > 0 && c3on) { const d3 = Math.min(over, c3 - LIM.c3[0]); c3 -= d3; over -= d3; }
  if (over > 0 && c1on) c1 -= Math.min(over, c1 - LIM.c1[0]);
  return { c1: Math.round(c1), c3: Math.round(c3), avail, c1on, c3on };
}
function maxFor(k) {
  const w = widths();
  return k === 'c1' ? Math.min(LIM.c1[1], w.avail - MIN_CENTER - (w.c3on ? w.c3 : 0)) : Math.min(LIM.c3[1], w.avail - MIN_CENTER - (w.c1on ? w.c1 : 0));
}

export function applyWidths() {
  const app = document.querySelector('.app');
  if (!app) return;
  const w = widths();
  app.style.setProperty('--col1', `${w.c1}px`);
  app.style.setProperty('--col3', `${w.c3}px`);
  document.querySelectorAll('.split').forEach((sp) => {
    const k = sp.dataset.split;
    const v = w[k];
    sp.setAttribute('aria-valuenow', String(v));
    sp.setAttribute('aria-valuemin', String(LIM[k][0]));
    sp.setAttribute('aria-valuemax', String(Math.max(v, maxFor(k))));
    sp.setAttribute('aria-valuetext', t('ws.px', { n: v }));
    const tip = sp.querySelector('.stip');
    if (tip) tip.textContent = `${v}px${state.layout[k] == null ? ` · ${t('ws.default')}` : ''}`;
  });
}

function setWidth(k, v) {
  const lo = LIM[k][0];
  state.layout = { ...state.layout, [k]: Math.round(clamp(lo, v, Math.max(lo, maxFor(k)))) };
  applyWidths();
}
function resetWidth(k) { state.layout = { ...state.layout, [k]: null }; applyWidths(); }

// ---------------------------------------------------------------- 경계선 조절바 (끌기 · 두 번 누르기 · 키보드)
export function splitter(k, label) {
  return h('div', { class: `split ${k === 'c1' ? 's1' : 's2'}`, id: `split-${k}`, role: 'separator', 'aria-orientation': 'vertical', tabindex: '0',
    'data-split': k, 'aria-label': label, title: t('ws.splitHint') }, h('span', { class: 'stip', 'aria-hidden': 'true' }));
}
let drag = null;
document.addEventListener('pointerdown', (e) => {
  const sp = e.target.closest?.('.split');
  if (!sp || e.button !== 0) return;
  e.preventDefault();
  const k = sp.dataset.split;
  drag = { k, x: e.clientX, start: widths()[k], sp };
  sp.setPointerCapture(e.pointerId);
  sp.classList.add('drag');
  document.body.classList.add('resizing');
  sp.focus({ preventScroll: true });
});
document.addEventListener('pointermove', (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x;
  setWidth(drag.k, drag.k === 'c1' ? drag.start + dx : drag.start - dx);
});
function endDrag() {
  if (!drag) return;
  drag.sp.classList.remove('drag');
  document.body.classList.remove('resizing');
  drag = null;
}
document.addEventListener('pointerup', endDrag);
document.addEventListener('pointercancel', endDrag);
document.addEventListener('dblclick', (e) => { const sp = e.target.closest?.('.split'); if (sp) resetWidth(sp.dataset.split); });
document.addEventListener('keydown', (e) => {
  const sp = e.target.closest?.('.split');
  if (!sp) return;
  const k = sp.dataset.split;
  const cur = widths()[k];
  const step = e.shiftKey ? 64 : 16;
  // 오른쪽 열 조절바는 ← 가 넓히기 (경계가 왼쪽으로 가면 오른쪽 열이 넓어진다)
  const grow = k === 'c1' ? { ArrowRight: 1, ArrowLeft: -1 } : { ArrowLeft: 1, ArrowRight: -1 };
  if (grow[e.key]) setWidth(k, cur + grow[e.key] * step);
  else if (e.key === 'Home') setWidth(k, 0);
  else if (e.key === 'End') setWidth(k, 99999);
  else if (e.key === 'Enter') resetWidth(k);
  else return;
  e.preventDefault();
});
let rz;
window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(applyWidths, 80); });

// ---------------------------------------------------------------- 기록 이동 처리 (design.md §6.6)
// 화면이 같은 작업면 안의 기록 이동(예: 종목 상세 ↔ 목록)을 스스로 처리하면 true 를 돌려준다 → app.js 가 전체를 다시 그리지 않는다.
// 화면을 새로 그릴 때마다 app.js 가 비운다.
let routeGuard = null;
export function setRouteGuard(fn) { routeGuard = fn; }
export function takeRoute(hash) { return !!(routeGuard && routeGuard(hash)); }

// ---------------------------------------------------------------- 가운데 ↔ 오른쪽 자리 바꾸기 (1024px 이상에서만 뜻이 있다)
export const swapped = () => state.layout.swap && innerWidth >= 1024;
export function toggleSwap() { state.layout = { ...state.layout, swap: !state.layout.swap }; }

// ---------------------------------------------------------------- 열 머리 탭 줄 (←/→/Home/End, 선택 탭만 tabindex 0)
export function paneTabs(items, current, onPick, label) {
  const list = h('div', { class: 'tablist', role: 'tablist', 'aria-label': label });
  const buttons = items.map(([key, text]) => h('button', { type: 'button', class: 'ptab', role: 'tab', 'aria-selected': String(key === current),
    tabindex: key === current ? '0' : '-1', 'data-key': key, onclick: () => onPick(key) }, text));
  list.append(...buttons);
  list.addEventListener('keydown', (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (i < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const n = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    e.preventDefault();
    buttons[n].focus();
    onPick(buttons[n].dataset.key);
  });
  return list;
}

// ---------------------------------------------------------------- 열 틀
// cols: [{ label }] 열 이름(좁은 화면 전환 버튼·열 aria-label), cls: ws 수식어, splits: 붙일 조절바
// 반환: { root, panes, select(i), current } — 좁은 화면에서는 select 한 열 하나만 보인다
export function paneFrame({ cols, cls = '', splits = [], current = 0 }) {
  const panes = cols.map((c, i) => h('section', { class: `pane p${i + 1}`, id: `p${i + 1}`, 'aria-label': c.label }));
  const sw = h('div', { class: 'switch', role: 'tablist', 'aria-label': t('ws.switch') });
  const buttons = cols.map((c, i) => h('button', { type: 'button', role: 'tab', onclick: () => select(i) }, c.label));
  sw.append(...buttons);
  const ws = h('div', { class: `ws ${cls}`.trim() }, panes, splits.map(([k, label]) => splitter(k, label)));
  const frame = { root: h('div', { class: 'wsw' }, sw, ws), panes, current };
  function select(i) {
    frame.current = i;
    panes.forEach((p, j) => p.classList.toggle('on', j === i));
    buttons.forEach((b, j) => b.setAttribute('aria-selected', String(j === i)));
  }
  frame.select = select;
  frame.relabel = (labels) => labels.forEach((l, i) => { buttons[i].textContent = l; panes[i].setAttribute('aria-label', l); });
  select(current);
  return frame;
}

// 열 머리: 제목(또는 탭 줄) + 오른쪽 보조 버튼 자리
export function paneHead(main, aside = []) {
  return h('div', { class: 'pane-head' }, typeof main === 'string' ? h('div', { class: 'ptitle' }, main) : main,
    h('div', { class: 'aside' }, aside));
}
