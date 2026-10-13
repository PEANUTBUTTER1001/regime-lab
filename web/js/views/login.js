// 소셜 로그인과 테스트용 게스트 진입. 인증 상태와 세션은 서버에서 확인한다.
import { h } from '../components/ui.js';
import { BRAND } from '../config.js';
import { t } from '../i18n.js';
import { state } from '../state.js';
import { api } from '../api.js';

function decoChart() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 600 200');
  svg.setAttribute('class', 'deco');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const [d, color, dash] of [
    ['M0 150 L60 138 L120 146 L180 110 L240 122 L300 88 L360 96 L420 64 L480 78 L540 46 L600 52', '#88E6BC', ''],
    ['M0 160 L60 152 L120 158 L180 136 L240 144 L300 120 L360 128 L420 108 L480 116 L540 96 L600 102', '#97A9A3', '6 6'],
  ]) {
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', d); p.setAttribute('fill', 'none'); p.setAttribute('stroke', color); p.setAttribute('stroke-width', '3');
    if (dash) p.setAttribute('stroke-dasharray', dash);
    svg.append(p);
  }
  return svg;
}

export function renderLogin(el, query = new URLSearchParams()) {
  const config = state.authConfig || { enabled: true, available: false };
  const message = h('p', {class:'field-error hidden', role:'alert', id:'login-error'});
  function show(text) { message.textContent = text; message.classList.remove('hidden'); }
  const providers = ['kakao', 'google'];
  const socials = providers.map(provider => {
    const button = h('button', {class:'social-login ' + provider, type:'button'}, t(provider === 'kakao' ? 'login.kakao' : 'login.google'));
    button.onclick = async () => {
      setBusy(true); message.classList.add('hidden');
      try { const result = await api.startOAuth(provider); location.assign(result.url); }
      catch (error) {
        show(t(error.code === 'auth_rate_limited' ? 'login.limited' : error.code === 'auth_oauth_unavailable' ? 'login.socialPending' : 'login.socialFailed'));
        setBusy(false);
      }
    };
    return button;
  });
  const guest = h('button', {class:'secondary', type:'button'}, t('login.guest'));
  function setBusy(busy) {
    socials.forEach((item, index) => { item.disabled = busy || config.available === false || !config.social_providers?.[providers[index]]; });
    guest.disabled = busy || config.available === false || !config.guest_allowed;
  }
  guest.onclick = async () => {
    setBusy(true); message.classList.add('hidden');
    try {
      if (config.enabled) {
        const result = await api.guest(); state.setUser(result.user);
      } else { state.clearAuth(); state.guest = true; state.signedIn = true; }
      try { state.meta = await api.meta(); } catch { state.meta = null; }
      location.hash = '#/builder';
    } catch (error) {
      show(t(error.code === 'auth_rate_limited' ? 'login.limited' : 'login.unavailable'));
      setBusy(false);
    }
  };
  const guestWrap = h('div', {class:'login-guest'}, guest, h('p', {class:'card-sub'}, t('login.guestHint')));
  guestWrap.classList.toggle('hidden', !config.guest_allowed);
  const form = h('form', {'aria-labelledby':'form-title'}, h('h2', {id:'form-title'}, t('login.signIn')),
    h('p', {class:'card-sub'}, t(config.enabled ? 'login.authHint' : 'login.demo')),
    h('div', {class:'login-social'}, ...socials), message, guestWrap);
  form.onsubmit = event => event.preventDefault();
  setBusy(false);
  if (query.has('auth_error')) show(t(query.get('auth_error') === 'auth_oauth_failed' ? 'login.socialFailed' : 'login.linkExpired'));
  if (config.available === false) {
    show(t('login.unavailable'));
    form.append(h('button', {type:'button', class:'secondary', onclick:()=>location.reload()}, t('common.retry')));
  } else if (config.enabled && !Object.values(config.social_providers || {}).some(Boolean)) show(t('login.socialPending'));
  el.append(h('div', {class:'login'}, h('section', {class:'login-intro'},
    h('div', {class:'brand'}, h('span', {class:'brand-mark', 'aria-hidden':'true'}), BRAND),
    h('h1', {}, t('login.title')), h('p', {class:'lead'}, t('login.lead')), h('p', {class:'notice'}, t('login.notice')), decoChart()),
    h('section', {class:'login-form'}, form)));
}
