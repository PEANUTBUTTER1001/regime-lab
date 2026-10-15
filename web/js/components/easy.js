// 처음 쓰는 사람을 위한 화면 부품 (X15): 패턴 카드(언제 사는지 세 묶음·모양 그림·쉬운 이름), 용어 설명 카드의 작은 그림,
// 승률 동그라미, 찾는 기간·확인 기간 그림, 입력값을 옮겨 적는 "이렇게 사요/팔아요" 문장.
// 계산·판정은 하지 않고 API 값과 입력값을 그대로 보여 준다 (FR-X6). 색은 app.css 클래스로만 정한다 (E19).
import { h } from './ui.js';
import { has, t } from '../i18n.js';

// 패턴 묶음은 화면 표시용이다. 새 패턴이 묶음에 없으면 '그 밖의 순간'에 들어간다
const GROUPS = [
  ['breakout', ['breakout_20d', 'high_52w', 'breakout_vol', 'bb_squeeze_break']],
  ['rebound', ['rsi_rebound', 'bb_lower_recover', 'three_down_up', 'engulfing', 'disparity_rebound', 'stochastic_rebound']],
  ['trend', ['ma_cross_5_20', 'macd_cross', 'granville_buy1', 'pullback_ma20']],
];

// 모양 그림 (viewBox 64x32): g-p 가격, g-a 평균선·기준선(점선), g-d 신호가 난 날. 촛대는 [x, 시가, 종가] (위가 작은 y)
const pl = (pts, cls) => `<polyline points="${pts}" class="${cls}"/>`;
const dot = (x, y) => `<circle cx="${x}" cy="${y}" r="3" class="g-d"/>`;
const candle = ([x, o, c], w = 6) => `<line x1="${x}" y1="${Math.min(o, c) - 3}" x2="${x}" y2="${Math.max(o, c) + 3}" class="g-w"/>`
  + `<rect x="${x - w / 2}" y="${Math.min(o, c)}" width="${w}" height="${Math.abs(c - o)}" rx="1" class="${c < o ? 'g-up' : 'g-dn'}"/>`;
const bars = (xs, hs, base, cls) => xs.map((x, i) => `<rect x="${x - 2}" y="${hs[i] < 0 ? base : base - hs[i]}" width="4" height="${Math.abs(hs[i])}" class="${typeof cls === 'function' ? cls(hs[i]) : cls}"/>`).join('');
const GLYPH = {
  breakout_20d: pl('4,10 60,10', 'g-a') + pl('4,22 12,14 20,18 28,11 36,17 44,13 52,15 60,4', 'g-p') + dot(60, 4),
  high_52w: pl('22,7 60,7', 'g-a') + pl('4,27 10,20 16,23 22,7 30,15 38,20 46,14 53,11 60,3', 'g-p') + '<circle cx="22" cy="7" r="2.2" class="g-m"/>' + dot(60, 3),
  breakout_vol: pl('4,8 60,8', 'g-a') + pl('4,16 14,11 24,14 34,10 44,13 52,12 60,3', 'g-p')
    + bars([8, 16, 24, 32, 40, 48], [3, 4, 3, 5, 3, 4], 27, 'g-v') + '<rect x="56" y="17" width="5" height="10" class="g-vh"/>' + dot(60, 3),
  bb_squeeze_break: pl('4,5 20,10 36,13 50,13', 'g-a') + pl('4,27 20,22 36,19 50,19', 'g-a') + pl('4,16 12,19 20,14 28,18 36,15 44,17 50,16 60,4', 'g-p') + dot(60, 4),
  rsi_rebound: pl('4,24 60,24', 'g-a') + pl('4,5 14,11 24,17 34,25 42,28 50,22 60,15', 'g-p') + dot(49, 23),
  bb_lower_recover: pl('4,19 60,22', 'g-a') + pl('4,10 14,12 24,15 32,24 40,27 48,18 60,13', 'g-p') + dot(48, 18),
  three_down_up: [[10, 6, 12], [22, 11, 17], [34, 16, 22], [48, 23, 11]].map((c) => candle(c)).join(''),
  engulfing: [[10, 5, 11], [22, 10, 16], [34, 15, 19], [48, 21, 9]].map((c, i) => candle(c, i === 3 ? 10 : 6)).join(''),
  disparity_rebound: pl('4,9 60,12', 'g-a') + pl('4,9 14,14 24,22 32,27 40,26 50,20 60,15', 'g-p') + dot(50, 20),
  stochastic_rebound: pl('4,6 14,12 24,19 32,24 40,26 48,24 60,19', 'g-a') + pl('4,8 14,16 24,24 32,28 40,26 48,20 60,13', 'g-p') + dot(45, 22.5),
  ma_cross_5_20: pl('4,12 60,16', 'g-a') + pl('4,26 18,24 32,19 42,14.5 60,5', 'g-p') + dot(42, 14.5),
  macd_cross: pl('2,18 62,18', 'g-a') + bars([6, 12, 18, 24, 30, 36, 42, 48, 54, 60], [-4, -7, -8, -6, -4, -2, 2, 5, 8, 10], 18, (v) => (v < 0 ? 'g-v' : 'g-vh')) + dot(39, 18),
  granville_buy1: pl('4,9 20,17 36,20 50,17 60,12', 'g-a') + pl('4,20 14,24 24,26 34,23 44,18 52,11 60,6', 'g-p') + dot(46, 17.6),
  pullback_ma20: pl('4,28 60,8', 'g-a') + pl('4,22 14,13 22,9 30,13 38,15.5 46,9 54,5 60,3', 'g-p') + dot(38, 15.5),
};

export const easyName = (name) => (has(`pe.${name}`) ? t(`pe.${name}`) : has(`pat.${name}`) ? t(`pat.${name}`) : name);
export const glyph = (name, cls = 'pat-glyph') => {
  const el = h('span', { class: cls, 'aria-hidden': 'true' });
  el.innerHTML = `<svg viewBox="0 0 64 32" focusable="false">${GLYPH[name] || ''}</svg>`;
  return el;
};

// 패턴 카드: 진짜 체크박스를 카드 모양으로 감싼다 (키보드·화면 읽기는 체크박스 그대로).
// items = [{ name, label(원래 이름), rule? }], onChange(name, checked) 는 부르는 쪽이 입력값을 바꾼다
export function patternCards({ items, selected, onChange, describedBy, groupLabel }) {
  const known = new Set(GROUPS.flatMap(([, names]) => names));
  const groups = [...GROUPS, ['other', items.map((x) => x.name).filter((n) => !known.has(n))]];
  const byName = new Map(items.map((x) => [x.name, x]));
  const root = h('div', { class: 'pat-cards', role: 'group', 'aria-label': groupLabel });
  for (const [key, names] of groups) {
    const list = names.filter((n) => byName.has(n));
    if (!list.length) continue;
    root.append(h('div', { class: 'pat-group' },
      h('div', { class: 'pat-gh' }, h('b', {}, t(`pg.${key}.t`)), has(`pg.${key}.s`) && h('span', {}, t(`pg.${key}.s`))),
      list.map((n) => {
        const it = byName.get(n);
        const box = h('input', { type: 'checkbox', class: 'pat-box', checked: selected.includes(n), 'aria-describedby': describedBy || null });
        const card = h('label', { class: `pat-card${selected.includes(n) ? ' selected' : ''}` }, box, glyph(n),
          h('span', { class: 'pat-t' }, h('b', {}, easyName(n)), h('small', {}, it.label), it.rule && h('code', { class: 'rule-text' }, it.rule)),
          h('i', { class: 'pat-check', 'aria-hidden': 'true' }));
        box.addEventListener('change', () => { card.classList.toggle('selected', box.checked); onChange(n, box.checked); });
        return card;
      })));
  }
  return root;
}

// 입력값 → "이렇게 사요" 문장. 이름이 여럿이면 OR 은 '또는', AND 는 '같은 날 모두'
export function buySentence(names, combine) {
  if (!names.length) return t('say.buyNone');
  const q = names.map((n) => t('say.quote', { x: easyName(n) }));
  if (names.length > 1 && combine === 'and') return t('say.buyAnd', { p: q.join(t('say.and')) });
  return t('say.buyOr', { p: q.join(t('say.or')) });
}

// 입력값 → "이렇게 팔아요" 문장. 추가 매도 조건은 개수만 덧붙인다
export function sellSentence({ stop = null, profit = null, hold, extra = 0 }) {
  const w = hold >= 5 ? t('say.weeks', { w: Math.round(hold / 5) }) : '';
  const vars = { s: Math.abs(stop), p: profit, d: hold, w };
  const key = stop != null && profit != null ? 'say.sellBoth' : stop != null ? 'say.sellStop' : profit != null ? 'say.sellProfit' : 'say.sellHold';
  return t(key, vars) + (extra ? ` ${t('say.sellExtra', { n: extra })}` : '');
}

export function sayBox(titleKey) {
  const body = h('span', {});
  return { el: h('p', { class: 'say', 'aria-live': 'polite' }, h('span', { class: 'say-h' }, t(titleKey)), body), body };
}

// 승률 동그라미 10개 (예: 0.545 → 5개 채움)
export function dots(rate) {
  const n = Math.round(Math.max(0, Math.min(1, rate ?? 0)) * 10);
  return h('span', { class: 'dots', 'aria-hidden': 'true' }, Array.from({ length: 10 }, (_, i) => h('i', { class: i < n ? 'on' : '' })));
}

// 찾는 기간 | 확인 기간 (날짜는 API 값 그대로, 길이는 날짜 수 비율)
export function timeline({ start, split, evalStart, end, note = true }) {
  const days = (a, b) => Math.max(1, (Date.parse(b) - Date.parse(a)) / 864e5) || 1;
  return h('div', { class: `tl${note ? '' : ' compact'}` },
    h('div', { class: 'tl-bar' },
      h('span', { class: 'a', style: { flex: String(days(start, split)) } }, t('tl.explore')),
      h('span', { class: 'b', style: { flex: String(days(evalStart, end)) } }, t('tl.evaluate'))),
    h('div', { class: 'tl-dates num' }, h('span', {}, start), h('span', {}, split), h('span', {}, end)),
    note && h('p', {}, t('tl.note')));
}

// ---------------------------------------------------------------- 용어 설명 카드의 작은 그림 (SVG 문자열, 글자는 문구 사전 값)
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const sgn = (x, d = 2) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x * 100).toFixed(d)}`;
const W = 268;
export const viz = {
  dots: (rate) => {
    const n = Math.round(Math.max(0, Math.min(1, rate)) * 10);
    return `<svg viewBox="0 0 ${W} 30">${Array.from({ length: 10 }, (_, i) => `<circle cx="${13 + i * 26.5}" cy="15" r="8.5" class="${i < n ? 'v-on' : 'v-off'}"/>`).join('')}</svg>`;
  },
  // 전략의 거래당 평균 수익과, 거기서 초과수익을 뺀 시장 몫 (API 값의 차이를 그림으로만 옮김)
  excess: (meanRet, meanExcess) => {
    const k = meanRet - meanExcess;
    const mx = Math.max(Math.abs(meanRet), Math.abs(k), 1e-9);
    const bar = (y, v, cls, name) => `<text x="0" y="${y + 9}" class="v-mut">${esc(name)}</text>`
      + `<rect x="44" y="${y}" width="${Math.max(3, 166 * Math.abs(v) / mx)}" height="12" rx="3" class="${cls}"/>`
      + `<text x="${52 + 166 * Math.abs(v) / mx}" y="${y + 10}" class="v-txt">${sgn(v)}%</text>`;
    return `<svg viewBox="0 0 ${W} 40">${bar(2, meanRet, 'v-on', t('viz.strategy'))}${bar(24, k, 'v-mid', t('viz.market'))}</svg>`;
  },
  // 무작위 진입 결과 분포 안에서 이 전략의 자리 (모양은 설명용 종 모양, 자리는 API 백분위)
  random: (pct) => {
    const p = Math.min(0.998, Math.max(0.002, pct));
    const z = Math.log(p / (1 - p)) / 1.702;
    const X = (v) => 8 + ((v + 3) / 6) * (W - 16);
    const Y = (v) => 40 - 32 * Math.exp(-(v * v) / 2);
    const pts = (a, b) => Array.from({ length: 41 }, (_, i) => { const v = a + ((b - a) * i) / 40; return `${X(v).toFixed(1)},${Y(v).toFixed(1)}`; }).join(' ');
    const right = X(z) > W / 2;
    return `<svg viewBox="0 0 ${W} 46"><polygon points="${X(-3)},40 ${pts(-3, z)} ${X(z)},40" class="v-soft"/>`
      + `<polyline points="${pts(-3, 3)}" class="v-line"/><line x1="${X(z)}" y1="4" x2="${X(z)}" y2="40" class="v-mark"/>`
      + `<circle cx="${X(z)}" cy="4" r="3.5" class="v-on"/><line x1="8" y1="40.5" x2="${W - 8}" y2="40.5" class="v-base"/>`
      + `<text x="${X(z) + (right ? -6 : 6)}" y="12" text-anchor="${right ? 'end' : 'start'}" class="v-txt">${esc(t('viz.this'))}</text></svg>`;
  },
  mdd: (mdd) => {
    const pts = [[8, 34], [40, 28], [70, 22], [96, 12], [118, 8], [142, 20], [166, 34], [184, 38], [210, 30], [236, 26], [260, 22]];
    return `<svg viewBox="0 0 ${W} 46"><polyline points="${pts.map((q) => q.join(',')).join(' ')}" class="v-path"/>`
      + '<line x1="118" y1="8" x2="184" y2="8" class="v-dash"/><line x1="184" y1="8" x2="184" y2="38" class="v-down"/>'
      + '<circle cx="118" cy="8" r="3.5" class="v-dot"/><circle cx="184" cy="38" r="3.5" class="v-downf"/>'
      + `<text x="192" y="22" class="v-downt">${(mdd * 100).toFixed(1)}%</text></svg>`;
  },
  split: (n1, e1, n2, e2) => {
    const c = (x) => (x > 0 ? 'v-upt' : x < 0 ? 'v-downt' : 'v-txt');
    return `<svg viewBox="0 0 ${W} 40"><rect x="0" y="4" width="${W / 2 - 3}" height="14" rx="4" class="v-soft"/>`
      + `<rect x="${W / 2 + 3}" y="4" width="${W / 2 - 3}" height="14" rx="4" class="v-soft"/>`
      + `<text x="${W / 4}" y="14.5" text-anchor="middle" class="v-mut">${esc(t('viz.first', { n: n1 }))}</text>`
      + `<text x="${(W * 3) / 4}" y="14.5" text-anchor="middle" class="v-mut">${esc(t('viz.second', { n: n2 }))}</text>`
      + `<text x="${W / 4}" y="34" text-anchor="middle" class="${c(e1)}">${sgn(e1)}%p</text>`
      + `<text x="${(W * 3) / 4}" y="34" text-anchor="middle" class="${c(e2)}">${sgn(e2)}%p</text></svg>`;
  },
};
