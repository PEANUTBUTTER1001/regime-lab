// 설정 (2026-09-22 사용자 요청): 화면 언어(한국어 기본·영어)와 읽기 전용 데이터 정보.
import { api } from '../api.js';
import { h, toast } from '../components/ui.js';
import { getLang, has, LANGS, setLang, t } from '../i18n.js';
import { state } from '../state.js';

export async function renderSettings(el) {
  const current = getLang();
  const options = Object.entries(LANGS).map(([code, label]) => {
    const input = h('input', { type: 'radio', name: 'lang', value: code, checked: code === current });
    input.addEventListener('change', () => {
      if (!input.checked) return;
      setLang(code); // 화면 전체를 새 언어로 다시 그린다 (app.js 의 rl:lang 처리)
      toast(t('set.saved', { l: label }));
    });
    return h('label', { class: `check${code === current ? ' selected' : ''}`, lang: code }, input, label);
  });

  const infoBox = h('dl', { class: 'info' });
  el.append(
    h('div', { class: 'topline' }, h('div', {},
      h('div', { class: 'eyebrow' }, t('set.eyebrow')), h('h1', { text: t('set.title') }),
      h('p', { class: 'lead', text: t('set.lead') }))),
    h('div', { class: 'grid-two' },
      h('article', { class: 'card' }, h('h2', {}, t('set.lang')),
        h('p', { class: 'card-sub', text: t('set.langSub') }),
        h('fieldset', { class: 'lang-options' }, h('legend', {}, t('set.lang')), options)),
      h('article', { class: 'card' }, h('h2', {}, t('set.data')),
        h('p', { class: 'card-sub', text: t('set.dataSub') }), infoBox)));

  let meta = state.meta;
  try { meta = await api.meta(); state.meta = meta; } catch { /* 표시 가능한 값만 보여 준다 */ }
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
  infoBox.replaceChildren(
    ...row(t('set.asOf'), meta?.data_as_of),
    ...row(t('set.start'), meta?.backtest_start),
    ...row(t('set.scope'), meta ? t(meta.scope === 'sample30' ? 'set.scopeSample' : 'set.scopeAll') : null),
    ...row(t('set.status'), meta?.status && has(`set.st.${meta.status}`) ? t(`set.st.${meta.status}`) : meta?.status));
}
