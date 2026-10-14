// AI Report (FR-L1~L4, NFR-7, design.md §6.5): 요약 → 근거 수치 → 데이터 상태 순서.
// verified(숫자·표현 검증 통과)와 fallback(템플릿)을 구분해 표시한다. 본문은 텍스트로만 넣는다(HTML 해석 없음).
// 보고서 본문과 데이터 한계 문구는 한국어로 생성된다(FR-L1). 화면 언어 설정과 무관하다.
import { api } from '../api.js';
import { errorView, fmt, h, loading, pill, table } from '../components/ui.js';
import { has, t } from '../i18n.js';

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

// 작업면 오른쪽 열 안의 AI 보고서 (design.md §8.2). strategy 는 결과 응답의 전략 이름
export async function renderReportInto(el, runId, strategy) {
  el.append(loading(t('rep.loading')));
  let rep;
  try { rep = await api.report(runId, strategy); } catch (e) {
    el.replaceChildren(errorView(e, { onRetry: () => { el.replaceChildren(); renderReportInto(el, runId, strategy); },
      backHref: `#/runs/${runId}/results`, backText: t('common.backResults') }));
    return;
  }
  el.replaceChildren();
  const f = rep.facts;
  const verified = rep.status === 'verified';
  el.append(h('div', { class: 'filterbar' }, verified ? pill(t('rep.verified')) : pill(t('rep.template'), 'warn'),
    h('span', {}, verified ? `${rep.provider} · ${rep.model}${rep.cached ? t('rep.cached') : ''}` : t('rep.deterministic'))));
  if (!verified) {
    el.append(h('div', { style: { padding: '12px 16px 0' } }, h('div', { class: 'callout', role: 'note' },
      h('b', {}, t('rep.fallback')), has(`rr.${rep.reason}`) ? t(`rr.${rep.reason}`) : rep.reason,
      rep.warnings?.length ? h('span', {}, t('rep.details', { d: rep.warnings.join(' / ') })) : null)));
  }
  el.append(h('article', { class: 'card report', lang: 'ko' }, h('p', { class: 'hint', text: t('rep.lead') }), renderText(rep.text)));

  const m = f.metrics;
  const v = f.validation;
  const judgement = has(`j.${v.split_judgement}`) ? t(`j.${v.split_judgement}`) : v.split_judgement;
  el.append(
    h('section', { class: 'card' }, h('h2', {}, t('rep.evidence')),
      h('p', { class: 'card-sub', text: t('rep.evidenceSub') }),
      table({ columns: [{ label: t('common.item'), key: 'k' }, { label: t('common.value'), key: 'v', num: true }], rows: [
        { k: t('r.m.trades'), v: fmt.int(m.trades) }, { k: t('r.m.win'), v: `${m.win_rate_pct ?? '—'}%` },
        { k: t('r.m.avg'), v: `${m.mean_ret_pct ?? '—'}%` }, { k: t('r.m.excess'), v: `${m.mean_excess_pct ?? '—'}%` },
        { k: t('r.m.payoff'), v: m.payoff_ratio ?? '—' }, { k: t('r.m.mdd'), v: `${m.mdd_pct ?? '—'}%` },
        { k: t('rep.pvalue', { m: v.fdr_family_size }), v: v.p_value ?? '—' }, { k: t('r.m.random'), v: v.random_percentile ?? '—' },
        { k: t('r.split'), v: judgement }, { k: t('rep.target'), v: v.analysis_target ? t('common.yes') : t('common.no') },
      ] })),
    h('section', { class: 'card' }, h('h2', {}, t('rep.status')),
      h('p', { class: 'card-sub', text: t('rep.statusSub', { d: f.data_as_of, i: f.cells.insufficient_cells, t: f.cells.total_cells, n: fmt.int(f.cells.min_cell_trades) }) }),
      h('ul', { lang: 'ko', class: 'muted', style: { lineHeight: '1.7', paddingLeft: '18px', margin: 0 } }, f.limitations.map((x) => h('li', {}, x))),
      h('div', { class: 'callout', style: { marginTop: '10px' } }, t('rep.aiLimits'))));
}
