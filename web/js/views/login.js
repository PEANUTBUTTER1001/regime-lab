// 소셜 로그인과 테스트용 게스트 진입. 인증 상태와 세션은 서버에서 확인한다.
import { h } from '../components/ui.js';
import { t } from '../i18n.js';
import { state } from '../state.js';
import { api } from '../api.js';
import { mountHero } from '../components/hero.js';

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
  // Back/forward cache restores the disabled DOM after leaving for OAuth.
  const onPageShow = event => { if (event.persisted) setBusy(false); };
  window.addEventListener('pageshow', onPageShow);
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
  // 왼쪽 소개 영역은 히어로(차트 → 로켓 영상 → 우주 장면, X1)가 그린다. 로그인 카드는 그 위에 뜬다
  const intro = h('section', {class:'login-intro'});
  const stopHero = mountHero(intro);
  el.append(h('div', {class:'login login-hero'}, intro, h('section', {class:'login-form'}, form)));
  return () => { window.removeEventListener('pageshow', onPageShow); stopHero(); };
}
