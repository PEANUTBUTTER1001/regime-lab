// AI Report (FR-L1~L4, NFR-7, design.md §6.5): 요약 → 근거 수치 → 데이터 상태 순서.
// verified(숫자·표현 검증 통과)와 fallback(템플릿)을 구분해 표시한다. 본문은 텍스트로만 넣는다(HTML 해석 없음).
// 보고서 본문과 데이터 한계 문구는 한국어로 생성된다(FR-L1). 화면 언어 설정과 무관하다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

function renderText(text) {
  const out = [];
  let para = [];
  const flush = () => { if (para.length) { out.push(h('p', {}, para.join(' '))); para = []; } };
  for (const line of text.split('\n')) {
    const s = line.trim();
    if (!s) { flush(); continue; }
    const m = s.match(/^#{1,4}\s*(.+)$/);
    // AI 가 가끔 쓰는 마크다운 강조(굵게·백틱)와 목록 기호는 글자로 보이지 않게 지운다
    if (m) { flush(); out.push(h('h3', {}, m[1].replace(/\*\*|`/g, ''))); } else para.push(s.replace(/\*\*|`/g, '').replace(/^[-*•]\s+/, ''));
  }
  flush();
  return out;
}

export async function renderReport(el, runId, query) {
  state.lastRun = runId;
  el.append(h('h1', { text: t('rep.title') }), loading(t('rep.loading')));
  let res;
  let rep;
  try {
    res = await api.result(runId);
    const names = res.strategies.map((s) => s.strategy);
    const cur = names.includes(query.get('strategy')) ? query.get('strategy') : names[0];
    rep = await api.report(runId, cur);
  } catch (e) {
    el.replaceChildren(h('h1', { text: t('rep.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderReport(el, runId, query); },
      backHref: `#/runs/${runId}/results`, backText: t('common.backResults') }));
    return;
  }
  el.replaceChildren();
  const names = res.strategies.map((s) => s.strategy);
  const f = rep.facts;
  const verified = rep.status === 'verified';

  el.append(h('div', { class: 'topline' },
    h('div', {}, h('div', { class: 'eyebrow' }, t('rep.eyebrow', { id: runId, s: rep.strategy })), h('h1', { text: t('rep.title') }),
      h('p', { class: 'lead', text: t('rep.lead') })),
    verified ? pill(t('rep.verified')) : pill(t('rep.template'), 'warn')));

  if (names.length > 1) {
    el.append(h('div', { class: 'tabs', role: 'group', 'aria-label': t('common.strategyTabs') },
      names.map((n) => h('a', { class: 'tab', href: `#/runs/${runId}/report?strategy=${encodeURIComponent(n)}`, 'aria-pressed': String(n === rep.strategy) }, n))));
  }

  if (!verified) {
    el.append(h('div', { class: 'callout', role: 'note', style: { marginBottom: '18px' } },
      h('b', {}, t('rep.fallback')), has(`rr.${rep.reason}`) ? t(`rr.${rep.reason}`) : rep.reason,
      rep.warnings?.length ? h('span', {}, t('rep.details', { d: rep.warnings.join(' / ') })) : null));
  }

  el.append(h('article', { class: 'card report', lang: 'ko' },
    h('div', { class: 'eyebrow' }, verified ? `${rep.provider} · ${rep.model}${rep.cached ? t('rep.cached') : ''}` : t('rep.deterministic')),
    renderText(rep.text)));

  const m = f.metrics;
  const v = f.validation;
  const judgement = has(`j.${v.split_judgement}`) ? t(`j.${v.split_judgement}`) : v.split_judgement;
  el.append(h('div', { class: 'grid-two', style: { marginTop: '18px' } },
    h('article', { class: 'card' }, h('h2', {}, t('rep.evidence')),
      h('p', { class: 'card-sub', text: t('rep.evidenceSub') }),
      table({ columns: [{ label: t('common.item'), key: 'k' }, { label: t('common.value'), key: 'v', num: true }], rows: [
        { k: t('r.m.trades'), v: fmt.int(m.trades) }, { k: t('r.m.win'), v: `${m.win_rate_pct ?? '—'}%` },
        { k: t('r.m.avg'), v: `${m.mean_ret_pct ?? '—'}%` }, { k: t('r.m.excess'), v: `${m.mean_excess_pct ?? '—'}%` },
        { k: t('r.m.payoff'), v: m.payoff_ratio ?? '—' }, { k: t('r.m.mdd'), v: `${m.mdd_pct ?? '—'}%` },
        { k: t('rep.pvalue', { m: v.fdr_family_size }), v: v.p_value ?? '—' }, { k: t('r.m.random'), v: v.random_percentile ?? '—' },
        { k: t('r.split'), v: judgement }, { k: t('rep.target'), v: v.analysis_target ? t('common.yes') : t('common.no') },
      ] })),
    h('article', { class: 'card' }, h('h2', {}, t('rep.status')),
      h('p', { class: 'card-sub', text: t('rep.statusSub', { d: f.data_as_of, i: f.cells.insufficient_cells, t: f.cells.total_cells, n: fmt.int(f.cells.min_cell_trades) }) }),
      h('ul', { lang: 'ko', class: 'muted', style: { fontSize: '13px', lineHeight: '1.7', paddingLeft: '18px' } }, f.limitations.map((x) => h('li', {}, x))),
      h('div', { class: 'callout', style: { marginTop: '12px' } }, t('rep.aiLimits')),
      h('div', { class: 'actions', style: { justifyContent: 'flex-start' } },
        h('a', { class: 'secondary', href: `#/runs/${runId}/results${names.length > 1 ? `?strategy=${encodeURIComponent(rep.strategy)}` : ''}` }, t('common.backResults'))))));
}
