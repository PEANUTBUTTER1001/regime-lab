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
  tone: (v) => (v == null ? '' : v > 0 ? 'positive' : v < 0 ? 'negative' : ''),
};

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

export function metricDefs(defs) {
  const rows = Object.entries(defs || {}).map(([k, d]) => {
    if (!has(`md.${k}`)) return d;
    const [name, unit, ...rest] = t(`md.${k}`).split('|');
    return { name, unit, formula: rest.join('|') };
  });
  return h('details', {}, h('summary', {}, t('common.metricDefs')),
    h('dl', { class: 'defs' }, rows.flatMap((d) => [h('dt', { text: `${d.name} (${d.unit})` }), h('dd', { text: d.formula })])));
}
