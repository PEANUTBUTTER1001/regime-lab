import { h } from '../components/ui.js';
import { api } from '../api.js';
import { state } from '../state.js';
import { t } from '../i18n.js';

export function renderPassword(el) {
  const min = state.authConfig?.password_min_length;
  const pw = h('input', {id:'new-password', type:'password', autocomplete:'new-password', required:true});
  const confirm = h('input', {id:'confirm-password', type:'password', autocomplete:'new-password', required:true});
  const message = h('p', {role:'alert', class:'field-error hidden'});
  const submit = h('button', {type:'submit', class:'primary'}, t('login.changePassword'));
  function show(text) { message.textContent = text; message.classList.remove('hidden'); }
  const form = h('form', {novalidate:true}, h('h1', {}, t('login.reset')),
    h('label', {for:'new-password'}, t('login.password')), pw,
    h('label', {for:'confirm-password'}, t('login.password2')), confirm, message, submit,
    h('a', {href:'#/login?recover=1'}, t('login.sendReset')));
  form.onsubmit = async event => {
    event.preventDefault();
    if (!pw.value || pw.value.length < min) { show(t('login.errPasswordLength', {min})); return; }
    if (pw.value !== confirm.value) { show(t('login.errMatch')); return; }
    submit.disabled = true;
    try { await api.password(pw.value); state.clearAuth(); location.hash = '#/login?verified=1'; }
    catch (error) { show(t(error.status === 401 ? 'login.linkExpired' : 'login.errAuth')); }
    finally { submit.disabled = false; pw.value = ''; confirm.value = ''; }
  };
  el.append(h('div', {class:'login'}, h('section', {class:'login-form'}, form)));
  pw.focus();
}
