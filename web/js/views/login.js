// 이메일 인증·로그인·복구 화면. 인증 상태와 세션은 서버에서 확인한다.
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
  let mode = query.get('recover') === '1' ? 'recover' : 'signin';
  const min = config.password_min_length;
  const email = h('input', { id: 'email', type: 'email', autocomplete: 'username', required: true });
  const pw = h('input', { id: 'password', type: 'password', autocomplete: 'current-password', required: true });
  const pw2 = h('input', { id: 'password2', type: 'password', autocomplete: 'new-password' });
  const pwWrap = h('div', {}, h('label', {for:'password'}, t('login.password')), pw);
  const pw2Wrap = h('div', {}, h('label', {for:'password2'}, t('login.password2')), pw2);
  const title = h('h2', {id:'form-title'});
  const submit = h('button', {class:'primary', type:'submit'});
  const toggle = h('button', {class:'link-btn', type:'button'});
  const recover = h('button', {class:'link-btn', type:'button'}, t('login.forgot'));
  const message = h('p', {class:'field-error hidden', role:'alert', id:'login-error'});
  const socials = ['kakao', 'google'].map(provider => {
    const button = h('button', {class:'social-login ' + provider, type:'button', disabled:!config.social_providers?.[provider] || config.available === false}, t(provider === 'kakao' ? 'login.kakao' : 'login.google'));
    button.onclick = async () => {
      socials.forEach(item => { item.disabled = true; });
      pw.value = ''; pw2.value = ''; message.classList.add('hidden');
      try { const result = await api.startOAuth(provider); location.assign(result.url); }
      catch (error) {
        show(t(error.code === 'auth_rate_limited' ? 'login.limited' : error.code === 'auth_oauth_unavailable' ? 'login.socialPending' : 'login.socialFailed'));
        socials.forEach((item, index) => { item.disabled = !config.social_providers?.[['kakao', 'google'][index]]; });
      }
    };
    return button;
  });
  const socialWrap = h('div', {class:'login-social'}, ...socials,
    h('p', {class:'card-sub'}, t(Object.values(config.social_providers || {}).some(Boolean) ? 'login.emailAlternative' : 'login.socialPending')));
  email.setAttribute('aria-describedby', 'login-error'); pw.setAttribute('aria-describedby', 'login-error');
  function show(text) { message.textContent = text; message.classList.remove('hidden'); }
  function update() {
    title.textContent = t(mode === 'signup' ? 'login.signUp' : mode === 'recover' ? 'login.reset' : 'login.signIn');
    submit.textContent = t(mode === 'signup' ? 'login.create' : mode === 'recover' ? 'login.sendReset' : 'login.signIn');
    toggle.textContent = t(mode === 'signin' ? 'login.signUp' : 'login.back');
    pwWrap.classList.toggle('hidden', mode === 'recover'); pw2Wrap.classList.toggle('hidden', mode !== 'signup');
    socialWrap.classList.toggle('hidden', mode === 'recover' || !config.enabled);
    recover.classList.toggle('hidden', mode !== 'signin' || !config.enabled);
    pw.autocomplete = mode === 'signup' ? 'new-password' : 'current-password';
  }
  toggle.onclick = () => { mode = mode === 'signin' ? 'signup' : 'signin'; message.classList.add('hidden'); pw.value = ''; pw2.value = ''; update(); };
  recover.onclick = () => { mode = 'recover'; message.classList.add('hidden'); pw.value = ''; update(); };
  const guest = h('button', {class:'secondary', type:'button'}, t('login.guest'));
  guest.onclick = () => { state.guest = true; state.signedIn = true; location.hash = '#/builder'; };
  guest.classList.toggle('hidden', !config.guest_allowed);
  const form = h('form', {'aria-labelledby':'form-title', novalidate:true}, title,
    h('p', {class:'card-sub'}, t(config.enabled ? 'login.authHint' : 'login.demo')), socialWrap,
    h('label', {for:'email'}, t('login.email')), email, pwWrap, pw2Wrap, message, submit,
    h('div', {class:'login-alt'}, toggle, guest), recover);
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!email.value || !email.checkValidity()) { show(t('login.errEmail')); return; }
    if (mode !== 'recover' && (!pw.value || ((mode === 'signup' || !config.enabled) && pw.value.length < min))) { show(t('login.errPasswordLength', {min})); return; }
    if (mode === 'signup' && pw.value !== pw2.value) { show(t('login.errMatch')); return; }
    if (!config.enabled) { state.guest = false; state.signedIn = true; location.hash = '#/builder'; return; }
    submit.disabled = true;
    try {
      if (mode === 'recover') { await api.recover(email.value); show(t('login.checkEmail')); }
      else if (mode === 'signup') {
        await api.signup(email.value, pw.value); mode = 'signin'; update(); show(t('login.checkEmail'));
      } else {
        const result = await api.login(email.value, pw.value);
        state.setUser(result.user); state.guest = false;
        try { state.meta = await api.meta(); } catch { state.meta = null; }
        location.hash = '#/builder';
      }
    } catch (error) { const key = error.code === 'auth_unavailable' && mode !== 'signin' ? 'login.mailUnavailable' : {auth_rate_limited:'login.limited',auth_unconfirmed:'login.unconfirmed',auth_password:'login.errPasswordLength',auth_email:'login.errEmail',auth_unavailable:'login.unavailable',network_error:'login.unavailable'}[error.code] || 'login.errAuth'; show(t(key, {min})); }
    finally { submit.disabled = false; pw.value = ''; pw2.value = ''; }
  };
  update();
  if (query.has('verified')) show(t('login.verified'));
  if (query.has('auth_error')) show(t(query.get('auth_error') === 'auth_oauth_failed' ? 'login.socialFailed' : 'login.linkExpired'));
  if (config.available === false) {
    submit.disabled = true; toggle.disabled = true; recover.disabled = true;
    show(t('login.unavailable'));
    form.append(h('button', {type:'button', class:'secondary', onclick:()=>location.reload()}, t('common.retry')));
  }
  el.append(h('div', {class:'login'}, h('section', {class:'login-intro'},
    h('div', {class:'brand'}, h('span', {class:'brand-mark', 'aria-hidden':'true'}), BRAND),
    h('h1', {}, t('login.title')), h('p', {class:'lead'}, t('login.lead')), h('p', {class:'notice'}, t('login.notice')), decoChart()),
    h('section', {class:'login-form'}, form)));
  email.focus();
}
