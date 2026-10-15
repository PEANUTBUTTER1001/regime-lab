// 화면 요소 도우미와 숫자 형식. 화면은 계산하지 않고 API 값을 표시만 한다 (FR-X6).
import { has, t } from '../i18n.js';

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const nf = new Intl.NumberFormat('en-US');
export const fmt = {
  int: (v) => (v == null ? '—' : nf.format(v)),
  pct: (v, d = 2, sign = true) => (v == null ? '—' : `${sign && v > 0 ? '+' : ''}${(v * 100).toFixed(d)}%`),
  pctp: (v, d = 2) => (v == null ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(d)}%p`),
  num: (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d)),
  krw: (v) => (v == null ? '—' : `₩${nf.format(Math.round(v))}`),
  eok: (v) => (v == null ? '—' : `${nf.format(v / 1e8)} (100M KRW)`),
  // 한국식 손익 색 (E18 ④): 상승 = 빨강(up), 하락 = 파랑(down), 0 은 색 없음
  tone: (v) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : ''),
};

// 손익 값 표시: 색과 함께 ▲/▼ 를 붙인다. 부호(+/−)가 글자로 이미 있으므로 기호는 화면 읽기에서 뺀다.
export function updown(v, text) {
  const tone = fmt.tone(v);
  return h('span', { class: tone }, tone && h('span', { class: 'updown-mark', 'aria-hidden': 'true' }, tone === 'up' ? '▲' : '▼'), text);
}

export function pill(text, kind = '') { return h('span', { class: `pill ${kind}` }, text); }

export function passPill(ok, labelPass = t('common.passed'), labelFail = t('common.notPassed')) {
  return ok ? pill(`✓ ${labelPass}`) : pill(`✕ ${labelFail}`, 'bad');
}

// 상태 화면 (design.md §8.2): Loading · Empty · Sample insufficient · Validation failed · Network/server error · Cancelled
export function stateView({ kind = 'info', title, message, detail, actions = [] }) {
  return h('section', { class: `state ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    h('h2', { text: title }),
    message && h('p', { text: message }),
    detail && h('p', { class: 'muted', text: detail }),
    actions.length ? h('div', { class: 'actions' }, actions) : null);
}

// 한 실행의 세 화면(요약·종목별 거래·AI 보고서)을 오가는 탭 줄. 사이드바에는 '실행 결과' 하나만 둔다 (E18).
export function runTabs(runId, active) {
  const tabs = [['results', t('run.tab.summary')], ['stock', t('nav.stock')], ['report', t('nav.report')]];
  return h('nav', { class: 'run-tabs', 'aria-label': t('run.tabs') },
    tabs.map(([key, label]) => h('a', { class: `run-tab${key === active ? ' active' : ''}`, href: `#/runs/${runId}/${key}`,
      'aria-current': key === active ? 'page' : null }, label)));
}

// ? 도움말 토글팁 (E18). 마우스 올리기·키보드 초점·클릭으로 열고, Esc·바깥 클릭·초점 이동으로 닫는다.
// 설명은 aria-describedby 로 연결해 화면 읽기 프로그램이 초점에서 읽는다. 한 번에 하나만 연다.
let tipSeq = 0;
let closeOpenTip = null;
function onDocKey(e) { if (e.key === 'Escape' && closeOpenTip) closeOpenTip(); }
function onDocClick() { if (closeOpenTip) closeOpenTip(); }

// 화면 밖으로 나가지 않게 가로 위치만 당긴다 (좁은 화면에서 가로 스크롤 방지)
function keepInView(bubble) {
  bubble.style.left = '';
  const r = bubble.getBoundingClientRect();
  const edge = document.documentElement.clientWidth - 8;
  if (r.right > edge) bubble.style.left = `${-(r.right - edge)}px`;
  else if (r.left < 8) bubble.style.left = `${8 - r.left}px`;
}

// up: 화면 아래쪽에 붙은 영역(실행 막대)처럼 아래로 열면 가려지는 곳에서 위로 연다
export function helpTip(text, term, { up = false } = {}) {
  if (!tipSeq) { document.addEventListener('keydown', onDocKey); document.addEventListener('click', onDocClick); }
  const id = `help-tip-${++tipSeq}`;
  const bubble = h('span', { class: 'tip-bubble', id, role: 'tooltip', hidden: true }, text);
  const btn = h('button', { type: 'button', class: 'tip-btn', 'aria-label': t('help.aria', { x: term }), 'aria-expanded': 'false', 'aria-describedby': id }, '?');
  let pinned = false;
  function close() {
    bubble.hidden = true; pinned = false; btn.setAttribute('aria-expanded', 'false');
    if (closeOpenTip === close) closeOpenTip = null;
  }
  function open() {
    if (closeOpenTip && closeOpenTip !== close) closeOpenTip();
    bubble.hidden = false; btn.setAttribute('aria-expanded', 'true'); closeOpenTip = close;
    keepInView(bubble);
  }
  btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); if (pinned) close(); else { open(); pinned = true; } });
  btn.addEventListener('focus', open);
  btn.addEventListener('blur', close);
  const wrap = h('span', { class: up ? 'tip up' : 'tip' }, btn, bubble);
  wrap.addEventListener('mouseenter', open);
  wrap.addEventListener('mouseleave', () => { if (!pinned && document.activeElement !== btn) close(); });
  bubble.addEventListener('click', (e) => e.stopPropagation());
  return wrap;
}

// 용어 설명 (X15, design.md §7.9): "?" 단추 대신 용어 글자에 옅은 점선을 긋고, 올리기·초점·누르기로 설명 카드 하나를 연다.
// info = 문자열(쉬운 한 줄) 또는 { say, rule, now: { label, value, text, tone }, viz(SVG 문자열) }.
// 카드는 화면에 하나만 두고 위치는 고정(fixed)으로 계산해, 스크롤 열 안에서도 잘리지 않는다. 600px 미만은 아래에서 올라온다.
let pop = null;
let popOwner = null;
let popPinned = false;
let popTimer = 0;
function popEl() {
  if (pop) return pop;
  pop = h('div', { class: 'term-pop', id: 'term-pop', role: 'tooltip', hidden: true });
  pop.addEventListener('mouseenter', () => clearTimeout(popTimer));
  pop.addEventListener('mouseleave', () => { if (!popPinned) popTimer = setTimeout(closeTerm, 160); });
  pop.addEventListener('click', (e) => e.stopPropagation());
  document.body.append(pop);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && popOwner) { const o = popOwner; closeTerm(); o.focus(); } });
  document.addEventListener('click', () => { if (popOwner) closeTerm(); });
  addEventListener('scroll', () => { if (popOwner && !popPinned) closeTerm(); else if (popOwner) placeTerm(popOwner); }, true);
  window.addEventListener('hashchange', () => closeTerm());
  return pop;
}
function closeTerm() {
  clearTimeout(popTimer);
  if (!pop) return;
  pop.classList.remove('on');
  pop.hidden = true;
  popPinned = false;
  popOwner?.setAttribute('aria-expanded', 'false');
  popOwner = null;
}
// 설명하는 숫자를 가리지 않게 그 칸(지표 칸·표 행·판정 줄) 아래에 연다. 아래가 모자라면 위로
function placeTerm(el) {
  if (!el.isConnected) { closeTerm(); return; }
  const narrow = innerWidth < 600;
  pop.classList.toggle('sheet', narrow);
  if (narrow) { pop.style.left = ''; pop.style.top = ''; return; }
  const r = el.getBoundingClientRect();
  const box = (el.closest('.metric, tr, .verdict-row, .label-row, .goal-row > div') || el).getBoundingClientRect();
  const pw = pop.offsetWidth;
  const ph = pop.offsetHeight;
  const x = Math.min(Math.max(16, r.left - 12), innerWidth - pw - 16);
  let y = box.bottom + 8;
  if (y + ph > innerHeight - 16) y = box.top - ph - 8;
  pop.style.left = `${x}px`;
  pop.style.top = `${Math.max(16, y)}px`;
}
function openTerm(el, label, info) {
  const card = popEl();
  clearTimeout(popTimer);
  if (popOwner && popOwner !== el) popOwner.setAttribute('aria-expanded', 'false');
  popOwner = el;
  const i = typeof info === 'string' ? { say: info } : info;
  const viz = i.viz ? h('div', { class: 'term-viz' }) : null;
  if (viz) viz.innerHTML = i.viz;
  // replaceChildren 은 null 을 글자 "null" 로 넣으므로 없는 칸은 걸러 낸다
  card.replaceChildren(...[h('span', { class: 'term-name' }, label), h('p', { class: 'term-say' }, i.say), viz,
    i.now && h('div', { class: 'term-now' }, h('span', {}, i.now.label || t('tm.now')), h('b', { class: i.now.tone || '' }, i.now.value), i.now.text && h('em', {}, i.now.text)),
    i.rule && h('p', { class: 'term-rule' }, i.rule)].filter(Boolean));
  card.hidden = false;
  placeTerm(el);
  el.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => card.classList.add('on'));
}
export function term(label, info, { tag = 'span' } = {}) {
  const el = h(tag, { class: 'term', tabindex: '0', role: 'button', 'aria-expanded': 'false', 'aria-describedby': 'term-pop' }, label);
  let showTimer = 0;
  el.addEventListener('mouseenter', () => { clearTimeout(showTimer); showTimer = setTimeout(() => openTerm(el, label, info), 120); });
  el.addEventListener('mouseleave', () => { clearTimeout(showTimer); if (!popPinned) popTimer = setTimeout(closeTerm, 160); });
  el.addEventListener('focus', () => openTerm(el, label, info));
  el.addEventListener('blur', () => { if (!popPinned) popTimer = setTimeout(closeTerm, 160); });
  // 이름표(label) 안에 있어도 입력칸으로 초점이 넘어가지 않게 기본 동작을 막는다
  el.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    if (popOwner === el && popPinned) closeTerm(); else { openTerm(el, label, info); popPinned = true; }
  });
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); } });
  return el;
}

// 문장 속 값만 굵게: 문구 사전 문장을 그대로 쓰고 {값} 자리만 <strong> 으로 바꾼다
export function strongT(key, vars = {}) {
  const marks = Object.fromEntries(Object.keys(vars).map((k, i) => [k, `\u0001${i}\u0001`]));
  const vals = Object.values(vars);
  return t(key, marks).split('\u0001').map((part, i) => (i % 2 ? h('strong', {}, vals[Number(part)]) : part));
}

export function linkButton(text, href, primary = false) {
  return h('a', { class: primary ? 'primary' : 'secondary', href }, text);
}

// 오류 코드별 제목·문구는 화면 언어로 표시하고, 알 수 없는 코드만 서버 문구를 그대로 보여 준다.
export function errorView(err, { onRetry, backHref = '#/builder', backText = t('common.backStrategy') } = {}) {
  const actions = [];
  if (err.retryable && onRetry) actions.push(h('button', { class: 'secondary', onclick: onRetry }, t('common.retry')));
  actions.push(linkButton(backText, backHref));
  return stateView({
    kind: err.code === 'run_cancelled' || err.code === 'warming_up' ? 'warn' : 'error',
    title: has(`err.${err.code}`) ? t(`err.${err.code}`) : t('err.default'),
    message: has(`errmsg.${err.code}`) ? t(`errmsg.${err.code}`) : err.message,
    detail: t('common.code', { c: err.code }), actions,
  });
}

export function loading(text = t('common.loading')) {
  return h('div', { class: 'state', role: 'status', 'aria-live': 'polite' }, h('div', { class: 'orb', 'aria-hidden': 'true' }), h('p', { text }));
}

export function table({ caption, columns, rows, onRow, rowClass }) {
  return h('div', { class: 'table-wrap' },
    h('table', {},
      caption && h('caption', { text: caption }),
      h('thead', {}, h('tr', {}, columns.map((c) => h('th', { scope: 'col', class: c.num ? 'num' : '' }, c.label)))),
      h('tbody', {}, rows.map((r) => {
        const tr = h('tr', { class: [onRow ? 'clickable' : '', rowClass ? rowClass(r) : ''].join(' ') },
          columns.map((c) => h('td', { class: c.num ? 'num' : '' }, c.render ? c.render(r) : r[c.key])));
        if (onRow) {
          tr.tabIndex = 0;
          tr.addEventListener('click', () => onRow(r));
          tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onRow(r); });
        }
        return tr;
      }))));
}

let toastTimer;
export function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

// open: 처음부터 펼침, onToggle(open): 사용자가 펼침 상태를 바꿀 때 (기억은 부르는 쪽이 한다)
export function metricDefs(defs, { open = false, onToggle = null } = {}) {
  const rows = Object.entries(defs || {}).map(([k, d]) => {
    if (!has(`md.${k}`)) return d;
    const [name, unit, ...rest] = t(`md.${k}`).split('|');
    return { name, unit, formula: rest.join('|') };
  });
  const box = h('details', { open: open || null }, h('summary', {}, t('common.metricDefs')),
    h('dl', { class: 'defs' }, rows.flatMap((d) => [h('dt', { text: `${d.name} (${d.unit})` }), h('dd', { text: d.formula })])));
  if (onToggle) box.addEventListener('toggle', (e) => { e.stopPropagation(); onToggle(box.open); });
  return box;
}
