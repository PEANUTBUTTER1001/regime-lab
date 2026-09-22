// Login (FR-X1, design.md §6.1): UI 흐름만 제공한다. 실제 인증·계정 저장은 하지 않는다.
import { h } from '../components/ui.js';
import { BRAND } from '../config.js';
import { t } from '../i18n.js';
import { state } from '../state.js';

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

export function renderLogin(el) {
  let mode = 'signin';
  const err = h('p', { class: 'field-error hidden', id: 'login-error', role: 'alert' });
  const email = h('input', { id: 'email', type: 'email', autocomplete: 'username', required: true, 'aria-describedby': 'login-error' });
  const pw = h('input', { id: 'password', type: 'password', autocomplete: 'current-password', required: true, minlength: '4' });
  const pw2wrap = h('div', { class: 'hidden' }, h('label', { for: 'password2' }, t('login.password2')),
    h('input', { id: 'password2', type: 'password', autocomplete: 'new-password' }));
  const title = h('h2', { id: 'form-title', text: t('login.signIn') });
  const submit = h('button', { class: 'primary', type: 'submit' }, t('login.signIn'));
  const toggle = h('button', { class: 'link-btn', type: 'button' }, t('login.signUp'));
  // 계정 가입 없이 바로 진행 (2026-09-22 사용자 요청)
  const guestBtn = h('button', { class: 'secondary', type: 'button', 'aria-describedby': 'guest-hint' }, t('login.guest'));
  guestBtn.addEventListener('click', () => {
    state.guest = true;
    state.signedIn = true;
    location.hash = '#/builder';
  });

  toggle.addEventListener('click', () => {
    mode = mode === 'signin' ? 'signup' : 'signin';
    title.textContent = mode === 'signin' ? t('login.signIn') : t('login.signUp');
    submit.textContent = mode === 'signin' ? t('login.signIn') : t('login.create');
    toggle.textContent = mode === 'signin' ? t('login.signUp') : t('login.back');
    pw2wrap.classList.toggle('hidden', mode === 'signin');
    err.classList.add('hidden');
  });

  const form = h('form', { 'aria-labelledby': 'form-title', novalidate: true },
    title,
    h('p', { class: 'card-sub', text: t('login.demo') }),
    h('label', { for: 'email' }, t('login.email')), email,
    h('label', { for: 'password' }, t('login.password')), pw,
    pw2wrap, err, submit,
    h('div', { class: 'login-alt' }, toggle, guestBtn),
    h('p', { class: 'hint', id: 'guest-hint', style: { textAlign: 'center' }, text: t('login.guestHint') }));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const problems = [];
    const badEmail = !email.value || !email.checkValidity();
    if (badEmail) problems.push(t('login.errEmail'));
    if (pw.value.length < 4) problems.push(t('login.errPw'));
    if (mode === 'signup' && document.getElementById('password2').value !== pw.value) problems.push(t('login.errMatch'));
    email.setAttribute('aria-invalid', badEmail ? 'true' : 'false');
    if (problems.length) { err.textContent = problems.join(' '); err.classList.remove('hidden'); return; }
    state.guest = false;
    state.signedIn = true;
    location.hash = '#/builder';
  });

  el.append(h('div', { class: 'login' },
    h('section', { class: 'login-intro' },
      h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), BRAND),
      h('h1', { text: t('login.title') }),
      h('p', { class: 'lead', text: t('login.lead') }),
      h('p', { class: 'notice', text: t('login.notice') }),
      decoChart()),
    h('section', { class: 'login-form' }, form)));
  email.focus();
}
