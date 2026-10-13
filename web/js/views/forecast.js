// 봉 예측 (P2-10, SRS FR-F1~F7, E17). 직전 종가와 다음 h 봉의 회색 예상 범위(10~90%), 통계적 방향만 보여 준다.
// 예상 캔들·가운데 선은 그리지 않고 고정 문구를 항상 함께 둔다. 계산은 모두 엔진(/api/forecasts/*)이 하고 화면은 표시만 한다.
// 조건은 주소에 남긴다: #/forecast/{run_id}/{code}?as_of=… (로드맵 S4). 예전 #/forecast?run=…&code=… 도 읽는다.
import { api } from '../api.js';
import { forecastBand } from '../charts.js';
import { errorView, fmt, h, helpTip, loading, pill, stateView, table, updown } from '../components/ui.js';
import { t } from '../i18n.js';

const KST = '+09:00';
const toIso = (local) => (local ? `${local}:00${KST}` : '');
// 시각 문자열을 자르지 않고 파싱해 KST 로 바꾼다 (AGENTS §7 함정). 시간대 없는 값은 KST 로 본다.
const toLocal = (iso) => {
  if (!iso) return '';
  const s = iso.trim().replace(' ', 'T');
  const d = new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}${KST}`);
  return Number.isNaN(d.getTime()) ? '' : new Date(d.getTime() + 9 * 3600e3).toISOString().slice(0, 16);
};
const won = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }));
// 방향 표시는 한국식 색(E18 ④): 상승 쪽 빨강 ▲, 하락 쪽 파랑 ▼, 불확실은 색 없는 표시
const DIR_SIGN = { up: 1, down: -1 };
const dirCell = (dir) => (dir in DIR_SIGN ? updown(DIR_SIGN[dir], t(`fc.dir.${dir}`)) : pill(t('fc.dir.uncertain'), 'neutral'));
const withTip = (label, tip) => h('span', { class: 'with-tip' }, label, helpTip(tip, label));

// 평가 기간(test) 방향 적중률 — NXT 구간별로 나뉜 채점 행을 방향을 낸 표본 수로 가중해 합친다
function testHit(scores, h) {
  const rows = (scores?.[String(h)]?.model || []).filter((r) => r.split === 'test' && r.dir_hit != null);
  const w = rows.reduce((a, r) => a + r.n * r.dir_share, 0);
  return w ? rows.reduce((a, r) => a + r.dir_hit * r.n * r.dir_share, 0) / w : null;
}

// 상태별 안내 (P2-9a): 실행 없음은 목록에서, 나머지는 종목 응답에서
const STATE_VIEW = {
  no_stock: () => ({ title: t('fc.noStock'), message: t('fc.noStockMsg') }),
  no_as_of: (d) => ({ title: t('fc.noAsOf'), message: d.test_as_of_range
    ? t('fc.noAsOfMsg', { a: toLocal(d.test_as_of_range[0]).replace('T', ' '), b: toLocal(d.test_as_of_range[1]).replace('T', ' ') }) : t('fc.noPredMsg') }),
  insufficient_history: () => ({ title: t('fc.thin'), message: t('fc.thinMsg') }),
};

export async function renderForecast(el, query, runId, code) {
  el.append(h('h1', { text: t('fc.title') }), loading());
  let runs;
  try { runs = await api.forecastRuns(); } catch (e) { el.replaceChildren(h('h1', { text: t('fc.title') }), errorView(e)); return; }
  const head = h('div', { class: 'topline' }, h('div', {}, h('h1', { tabindex: '-1', text: t('fc.title') }), h('p', { class: 'lead', text: t('fc.lead') })));
  const notice = h('p', { class: 'notice', role: 'note', text: t('fc.notice') });
  if (runs.status === 'no_run') {
    el.replaceChildren(head, notice, stateView({ title: t('fc.noRun'), message: t('fc.noRunMsg') }));
    return;
  }
  const f = {
    run: [runId, query.get('run')].find((x) => runs.runs.some((r) => r.run_id === x)) || runs.runs[0].run_id,
    code: (code || query.get('code') || '005930').trim(),
    asOf: toLocal(query.get('as_of')),
  };
  const runSel = h('select', { id: 'fc-run', onchange: (e) => { f.run = e.target.value; } },
    runs.runs.map((r) => h('option', { value: r.run_id, selected: r.run_id === f.run },
      t('fc.runOpt', { tf: r.timeframe, at: (r.created_at || '').slice(0, 16).replace('T', ' '), n: fmt.int(r.codes) })
      + (r.test_as_of_range ? t('fc.runRange', { a: r.test_as_of_range[0].slice(0, 10), b: r.test_as_of_range[1].slice(0, 10) }) : ''))));
  const codeIn = h('input', { id: 'fc-code', value: f.code, placeholder: t('fc.codePh'), maxlength: '6', autocomplete: 'off',
    oninput: (e) => { f.code = e.target.value.trim(); } });
  const asOfIn = h('input', { id: 'fc-asof', type: 'datetime-local', step: '60', value: f.asOf, oninput: (e) => { f.asOf = e.target.value; } });
  const form = h('form', { class: 'card', novalidate: true },
    h('h2', {}, t('fc.formTitle')),
    h('label', { for: 'fc-run' }, t('fc.run')), runSel,
    h('div', { class: 'field-two' },
      h('div', {}, h('label', { for: 'fc-code' }, t('fc.code')), codeIn),
      h('div', {}, h('label', { for: 'fc-asof' }, t('fc.asOf')), asOfIn)),
    h('div', { class: 'actions', style: { justifyContent: 'flex-start' } }, h('button', { class: 'primary', type: 'submit' }, t('fc.show'))));
  const out = h('section', { 'aria-live': 'polite' });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = new URLSearchParams(f.asOf ? { as_of: toIso(f.asOf) } : {});
    location.hash = `#/forecast/${encodeURIComponent(f.run)}/${encodeURIComponent(f.code)}${f.asOf ? `?${q}` : ''}`;
  });
  el.replaceChildren(head, notice, form, out);
  await show(out, f);
}

async function show(out, f) {
  out.replaceChildren(loading(t('fc.loading')));
  let d, sc;
  try {
    [d, sc] = await Promise.all([api.forecastStock(f.run, f.code, f.asOf ? { as_of: toIso(f.asOf) } : {}), api.forecastScores(f.run)]);
  } catch (e) {
    out.replaceChildren(errorView(e, { onRetry: () => show(out, f), backHref: '#/forecast', backText: t('fc.back') }));
    return;
  }
  if (d.status !== 'ok') {
    out.replaceChildren(stateView((STATE_VIEW[d.status] || (() => ({ title: t('fc.noPred'), message: t('fc.noPredMsg') })))(d)));
    return;
  }
  const chartEl = h('div', { class: 'chart' });
  const at = d.as_of.slice(0, 16).replace('T', ' ');
  out.replaceChildren(
    h('article', { class: 'card' },
      h('h2', {}, `${d.code} · ${d.timeframe}`), h('p', { class: 'card-sub', text: t('fc.asOfAt', { at, p: won(d.base_close) }) }),
      chartEl, h('p', { class: 'notice', role: 'note', text: d.notice || t('fc.notice') })),  // E17 문구는 응답 값 그대로
    table({
      caption: t('fc.tableCap'),
      columns: [
        { label: t('fc.col.h'), render: (s) => (s.cross_day
          ? h('span', {}, t('fc.step', { h: s.h }), ' ', pill(t('fc.col.nextDay'), 'neutral')) : t('fc.step', { h: s.h })) },
        { label: withTip(t('fc.col.range'), t('help.fcRange')), num: true, render: (s) => `${won(s.low)} ~ ${won(s.high)}` },
        { label: t('fc.col.dir'), render: (s) => dirCell(s.direction) },
        { label: t('fc.col.pUp'), num: true, render: (s) => fmt.pct(s.p_up, 0, false) },
        { label: withTip(t('fc.col.testHit'), t('help.fcHit')), num: true, render: (s) => fmt.pct(testHit(sc.scores, s.h), 1, false) },
        { label: t('fc.col.actual'), num: true, render: (s) => won(s.actual) },
      ],
      rows: d.steps,
    }),
    h('p', { class: 'hint', text: t('fc.dirNote') }));
  forecastBand(chartEl, d, t('fc.chart', { c: d.code, n: d.steps.length }));
}
