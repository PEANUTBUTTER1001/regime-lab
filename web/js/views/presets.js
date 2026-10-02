// 저장한 조합 목록 (P1-10, 설계 §5, plan/01 작업 ③). 불러오기·이름 바꾸기·삭제. 값은 /api/presets 에서만 읽는다.
import { api } from '../api.js';
import { errorView, h, linkButton, loading, pill, table, toast } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';
import { isDirty } from './builder.js';

const patLabel = (n) => (has(`pat.${n}`) ? t(`pat.${n}`) : n);
const errText = (e) => (has(`errmsg.${e.code}`) ? t(`errmsg.${e.code}`)
  : e.code === 'validation_failed' ? Object.values(e.detail?.fields || {}).join(' · ') : e.message);

function summary(st) {
  const ex = st.exit || {};
  return h('span', {}, st.patterns.map(patLabel).join(st.patterns.length > 1 ? ` ${st.combine.toUpperCase()} ` : ''), h('br'),
    h('small', { class: 'muted' }, t('sr.exit', {
      s: ex.stop_loss_pct == null ? t('sr.noStop') : `${ex.stop_loss_pct}%`,
      p: ex.take_profit_pct == null ? t('sr.noProfit') : `+${ex.take_profit_pct}%`, h: ex.max_hold_days,
    }), ` · ${st.markets.join('·')} · ${st.period.start}~${st.period.end}`));
}

function source(p) {
  if (p.source?.kind !== 'search') return pill(t('pl.manual'), 'neutral');
  return h('span', {}, pill(t('pl.fromSearch')), ' ',
    h('a', { href: `#/searches/${p.source.search_id}/results` }, `${p.source.candidate_id}`),
    h('br'), h('small', { class: 'muted' }, has(`sr.st.${p.source.status}`) ? t(`sr.st.${p.source.status}`) : ''));
}

export async function renderPresets(el) {
  el.append(h('h1', { text: t('pl.title') }), loading());
  let list;
  try { list = (await api.presets()).presets; } catch (e) {
    el.replaceChildren(h('h1', { text: t('pl.title') }), errorView(e, { onRetry: () => { el.replaceChildren(); renderPresets(el); } }));
    return;
  }
  el.replaceChildren();
  const reload = () => { el.replaceChildren(); renderPresets(el); };

  function load(p) {
    if (isDirty() && !window.confirm(t('pl.discardConfirm'))) return;
    location.hash = `#/builder?preset=${encodeURIComponent(p.id)}`;
  }

  // 이름 바꾸기: 표 칸을 입력칸으로 바꾼다
  function renameCell(p) {
    const cell = h('span', {}, h('b', {}, p.name));
    const btn = h('button', { type: 'button', class: 'link-btn', 'aria-label': t('pl.renameOf', { n: p.name }) }, t('pl.rename'));
    btn.addEventListener('click', () => {
      const input = h('input', { value: p.name, maxlength: '60', 'aria-label': t('pl.newName'), style: { minWidth: '180px' } });
      const err = h('p', { class: 'field-error hidden' });
      const ok = h('button', { type: 'button', class: 'secondary' }, t('pl.ok'));
      const cancel = h('button', { type: 'button', class: 'link-btn' }, t('pl.cancel'));
      const submit = async () => {
        ok.disabled = true;
        try {
          const u = await api.updatePreset(p.id, { revision: p.revision, name: input.value });
          if (state.loadedPreset?.id === p.id) state.loadedPreset = { ...state.loadedPreset, name: u.name, revision: u.revision };
          toast(t('pl.renamed', { n: u.name }));
          reload();
        } catch (e) {
          err.textContent = errText(e); err.classList.remove('hidden'); input.setAttribute('aria-invalid', 'true'); ok.disabled = false;
        }
      };
      ok.addEventListener('click', submit);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') reload(); });
      cancel.addEventListener('click', reload);
      cell.replaceChildren(input, ok, cancel, err);
      input.focus();
    });
    return h('span', {}, cell, ' ', btn);
  }

  async function remove(p) {
    if (!window.confirm(t('pl.deleteConfirm', { n: p.name }))) return;
    try {
      await api.deletePreset(p.id);
      if (state.loadedPreset?.id === p.id) state.loadedPreset = null;
      toast(t('pl.deleted', { n: p.name }));
      reload();
    } catch (e) { toast(errText(e)); }
  }

  const columns = [
    { label: t('pl.col.name'), render: renameCell },
    { label: t('pl.col.combo'), render: (p) => summary(p.strategy) },
    { label: t('pl.col.source'), render: source },
    { label: t('pl.col.updated'), render: (p) => h('span', {}, p.updated_at.replace('T', ' '), h('br'), h('small', { class: 'muted' }, `rev ${p.revision}`)) },
    { label: t('pl.col.actions'), render: (p) => h('span', { class: 'opt' },
      h('button', { type: 'button', class: 'secondary', 'aria-label': t('pl.loadOf', { n: p.name }), onclick: () => load(p) }, t('pl.load')),
      h('button', { type: 'button', class: 'link-btn', 'aria-label': t('pl.deleteOf', { n: p.name }), onclick: () => remove(p) }, t('pl.delete'))) },
  ];

  el.append(
    h('div', { class: 'topline' },
      h('div', {}, h('div', { class: 'eyebrow' }, t('pl.eyebrow')), h('h1', { text: t('pl.title') }), h('p', { class: 'lead', text: t('pl.lead') })),
      h('div', { class: 'notice' }, t('pl.notice'))),
    list.length
      ? h('article', { class: 'card' }, table({ caption: t('pl.caption', { n: list.length }), columns, rows: list }))
      : h('section', { class: 'state', role: 'status' }, h('h2', { text: t('pl.emptyTitle') }), h('p', { text: t('pl.emptyMsg') }),
        h('div', { class: 'actions' }, linkButton(t('pl.toSearch'), '#/search', true), linkButton(t('pl.toBuilder'), '#/builder'))));
}
