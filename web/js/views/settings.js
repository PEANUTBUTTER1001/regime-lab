// 설정 (2026-09-22 사용자 요청): 화면 언어(한국어 기본·영어), AI 보고서 모델·키(E10 잠정), 읽기 전용 데이터 정보.
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
        h('p', { class: 'card-sub', text: t('set.dataSub') }), infoBox)),
    aiCard());

  let meta = state.meta;
  try { meta = await api.meta(); state.meta = meta; } catch { /* 표시 가능한 값만 보여 준다 */ }
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
  infoBox.replaceChildren(
    ...row(t('set.asOf'), meta?.data_as_of),
    ...row(t('set.start'), meta?.backtest_start),
    ...row(t('set.scope'), meta ? t(meta.scope === 'sample30' ? 'set.scopeSample' : 'set.scopeAll') : null),
    ...row(t('set.status'), meta?.status && has(`set.st.${meta.status}`) ? t(`set.st.${meta.status}`) : meta?.status));
}

// AI 보고서 공급사 설정 (결정 E10 잠정). 키는 서버 메모리에만 두며 서버를 끄면 사라진다.
// 키 원문은 화면·브라우저 저장소에 남기지 않는다: 적용 즉시 입력 칸을 비우고, 서버는 마지막 4자리만 돌려준다.
function aiCard() {
  const status = h('dl', { class: 'info', 'aria-live': 'polite' });
  const err = {};
  const errSlot = (k) => (err[k] = h('p', { class: 'field-error hidden', id: `err-ai-${k}` }));
  const modelIn = h('input', { id: 'ai-model', autocomplete: 'off', spellcheck: 'false', maxlength: '100',
    placeholder: t('set.ai.modelPh'), 'aria-describedby': 'err-ai-model' });
  const keyIn = h('input', { id: 'ai-key', type: 'password', autocomplete: 'off', spellcheck: 'false', maxlength: '300',
    placeholder: t('set.ai.keyPh'), 'aria-describedby': 'ai-key-hint err-ai-api_key' });
  const applyBtn = h('button', { class: 'primary', type: 'submit' }, t('set.ai.apply'));
  const clearBtn = h('button', { class: 'secondary', type: 'button' }, t('set.ai.clear'));

  const showErrors = (fields = {}) => {
    for (const k of ['model', 'api_key']) {
      const msg = fields[k];
      err[k].textContent = msg ? t(k === 'model' ? 'set.ai.errModel' : 'set.ai.errKey') : '';
      err[k].classList.toggle('hidden', !msg);
      (k === 'model' ? modelIn : keyIn).setAttribute('aria-invalid', msg ? 'true' : 'false');
    }
  };
  const render = (c) => {
    const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
    status.replaceChildren(
      ...row(t('set.ai.provider'), c.provider),
      ...row(t('set.ai.model'), c.model),
      ...row(t('set.ai.key'), c.key_set ? `${t('set.ai.keySet')} · ${c.key_hint}` : t('set.ai.keyUnset')),
      ...row(t('set.ai.source'), t(`set.ai.src.${c.source}`)));
    if (c.model && !modelIn.value) modelIn.value = c.model;
    clearBtn.disabled = c.source !== 'ui';
  };
  const run = async (fn, okKey) => {
    applyBtn.disabled = clearBtn.disabled = true;
    try {
      const c = await fn();
      keyIn.value = '';
      showErrors();
      render(c);
      toast(t(okKey));
    } catch (e) {
      if (e.code === 'validation_failed' && e.detail?.fields) showErrors(e.detail.fields);
      else toast(e.code === 'local_only' ? t('set.ai.errLocal') : e.message);
      try { render(await api.llmConfig()); } catch { /* 상태 표시는 유지 */ }
    } finally {
      applyBtn.disabled = false;
    }
  };

  const form = h('form', { class: 'ai-form', novalidate: true },
    h('label', { for: 'ai-model' }, t('set.ai.model')), modelIn, errSlot('model'),
    h('label', { for: 'ai-key' }, t('set.ai.key')), keyIn,
    h('p', { class: 'card-sub', id: 'ai-key-hint', text: t('set.ai.keyHint') }), errSlot('api_key'),
    h('div', { class: 'ai-actions' }, applyBtn, clearBtn));
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const body = { model: modelIn.value };
    if (keyIn.value.trim()) body.api_key = keyIn.value;
    run(() => api.setLlmConfig(body), 'set.ai.applied');
  });
  clearBtn.addEventListener('click', () => {
    modelIn.value = '';
    run(() => api.clearLlmConfig(), 'set.ai.cleared');
  });

  api.llmConfig().then(render).catch(() => status.replaceChildren(h('dd', {}, t('set.ai.loadFail'))));
  return h('article', { class: 'card ai-card' }, h('h2', {}, t('set.ai.title')),
    h('p', { class: 'card-sub', text: t('set.ai.sub') }),
    h('div', { class: 'grid-two' }, form, h('div', {}, h('h3', { class: 'label' }, t('set.ai.current')), status,
      h('p', { class: 'card-sub ai-note', text: t('set.ai.volatile') }))));
}
