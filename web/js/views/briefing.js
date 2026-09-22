// Morning Briefing (FR-G6, C-7): 야간·뉴스 입력이 없으므로 브리핑 대신 상태와 사유만 표시한다 (§8 T-2).
import { api } from '../api.js';
import { errorView, h, loading } from '../components/ui.js';
import { t } from '../i18n.js';

export async function renderBriefing(el) {
  el.append(h('div', { class: 'topline' }, h('div', {},
    h('div', { class: 'eyebrow' }, t('br.eyebrow')),
    h('h1', { text: t('br.title') }),
    h('p', { class: 'lead', text: t('br.lead') }))));
  const body = h('div', {}, loading(t('br.checking')));
  el.append(body);
  try {
    const b = await api.briefing();
    const unavailable = b.status === 'data_unavailable';
    body.replaceChildren(h('section', { class: `state ${unavailable ? 'warn' : ''}`, role: 'status' },
      h('h2', { text: unavailable ? t('br.unavailable') : t('br.failed') }),
      // 사유 코드가 알려진 경우 화면 언어 문구, 아니면 서버 문구
      h('p', { text: b.reason_code === 'external_inputs_missing' ? t('br.reason') : b.reason }),
      h('p', { class: 'muted', text: t('br.missing', { m: (b.missing_inputs || []).join(', ') || '—' }) }),
      h('p', { class: 'muted', text: t('br.none') }),
      h('div', { class: 'actions' }, h('a', { class: 'secondary', href: '#/builder' }, t('common.backStrategy')))));
  } catch (e) {
    body.replaceChildren(errorView(e, { onRetry: () => { el.replaceChildren(); renderBriefing(el); } }));
  }
}
