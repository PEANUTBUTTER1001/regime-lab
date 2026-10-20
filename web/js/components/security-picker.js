// 공시·뉴스 두 화면의 종목 선택. 표시만 이름으로, 기존 요청 계약은 쉼표로 구분한 코드.
import { api } from '../api.js';
import { h, pill } from './ui.js';
import { t } from '../i18n.js';

let catalogRequest;
function catalog() {
  if (!catalogRequest) catalogRequest = api.securities().then(res => res.items).catch(error => { catalogRequest = null; throw error; });
  return catalogRequest;
}
const norm = value => value.replace(/\s/g, '').toLocaleLowerCase();

export function tickerMatchBadge(match) {
  if (!match) return null;
  const names = [...new Set(match.name_mentions.map(item => item.name))];
  return h('span', { class: 'ar-pills' },
    match.codes.length ? pill(t('security.codeLinked', { codes: match.codes.join(', ') })) : null,
    names.length ? pill(t('security.nameMention', { names: names.join(', ') }), 'neutral') : null);
}

export function securityPicker({ id, value = '', describedBy, onChange }) {
  let codes = [...new Set(value.split(',').map(v => v.trim()).filter(Boolean))];
  let items = [];
  let options = [];
  let active = -1;
  let composing = false;
  let open = false;
  let status = 'loading';
  let invalid = false;
  const listId = `${id}-options`;
  const chips = h('div', { class: 'security-chips' });
  const list = h('div', { id: listId, class: 'security-options', role: 'listbox', hidden: true, 'aria-label': t('security.candidates') });
  const feedback = h('p', { id: `${id}-status`, class: 'hint', role: 'status', 'aria-live': 'polite' });
  const input = h('input', { id, role: 'combobox', autocomplete: 'off', 'aria-autocomplete': 'list',
    'aria-expanded': 'false', 'aria-controls': listId, 'aria-describedby': [describedBy, feedback.id].filter(Boolean).join(' '),
    placeholder: t('security.placeholder') });
  const retry = h('button', { type: 'button', class: 'link-btn', hidden: true, onclick: load }, t('common.retry'));
  const clear = h('button', { type: 'button', class: 'link-btn', onclick: () => {
    codes = []; input.value = ''; invalid = false; input.setAttribute('aria-invalid', 'false'); changed(); input.focus();
  } }, t('security.clear'));
  const root = h('div', { class: 'security-picker' }, chips, input, list, feedback,
    h('div', { class: 'security-tools' }, clear, retry));
  function renderChips() {
    chips.replaceChildren(...codes.map(code => {
      const name = items.find(item => item.ticker === code)?.name;
      const label = name ? `${name} · ${code}` : code;
      return h('span', { class: 'security-chip' }, h('span', {}, label), h('button', { class: 'ibtn', type: 'button',
        'aria-label': t('security.remove', { name: label }), onclick: () => {
          codes = codes.filter(v => v !== code); changed(); input.focus();
        } }, h('span', { 'aria-hidden': 'true' }, '×')));
    }));
    clear.hidden = !codes.length && !input.value;
  }
  function changed() { renderChips(); onChange(codes.join(',')); renderOptions(); }
  function choose(item) {
    if (!codes.includes(item.ticker)) codes.push(item.ticker);
    input.value = ''; invalid = false; input.setAttribute('aria-invalid', 'false'); open = false; active = -1; changed();
  }
  function renderOptions() {
    const q = norm(input.value.trim());
    options = q ? items.filter(item => !codes.includes(item.ticker) &&
      (norm(item.name).includes(q) || item.ticker.toLowerCase().includes(q))).slice(0, 10) : [];
    if (active >= options.length) active = -1;
    const expanded = open && !!options.length;
    input.setAttribute('aria-expanded', String(expanded));
    if (expanded && active >= 0) input.setAttribute('aria-activedescendant', `${listId}-${active}`);
    else input.removeAttribute('aria-activedescendant');
    list.hidden = !expanded;
    list.replaceChildren(...options.map((item, i) => h('div', { id: `${listId}-${i}`, role: 'option',
      class: `security-option${i === active ? ' active' : ''}`, 'aria-selected': String(i === active),
      onpointerdown: event => { event.preventDefault(); choose(item); } }, `${item.name} · ${item.ticker}`)));
    feedback.textContent = invalid ? t('security.unresolved') : status === 'loading' ? t('security.loading') : status === 'failed' ? t('security.failed') :
      q && !options.length ? t('security.noMatch') : options.length && open ? t('security.matches', { n: options.length }) : t('security.hint');
    retry.hidden = status !== 'failed';
    clear.hidden = !codes.length && !input.value;
    if (expanded && active >= 0) list.children[active]?.scrollIntoView({ block: 'nearest' });
  }
  // 이름은 명시적 선택 필요. 여섯 자리 코드는 목록 장애·미등록 상태에서도 기존 방식으로 전달.
  function commit() {
    const pending = input.value.trim();
    if (!pending) return true;
    const entered = pending.split(',').map(v => v.trim().toUpperCase());
    if (entered.every(v => /^[A-Z0-9]{6}$/.test(v))) {
      codes = [...new Set([...codes, ...entered])]; input.value = ''; invalid = false;
      input.setAttribute('aria-invalid', 'false'); changed(); return true;
    }
    invalid = true; input.setAttribute('aria-invalid', 'true'); renderOptions(); input.focus(); return false;
  }
  input.addEventListener('input', () => { invalid = false; input.setAttribute('aria-invalid', 'false'); open = true; active = -1; renderOptions(); });
  input.addEventListener('focus', () => { open = true; renderOptions(); });
  input.addEventListener('blur', () => { open = false; renderOptions(); });
  input.addEventListener('compositionstart', () => { composing = true; });
  input.addEventListener('compositionend', () => { composing = false; open = true; active = -1; renderOptions(); });
  input.addEventListener('keydown', event => {
    if (composing || event.isComposing || event.keyCode === 229) return;
    if (event.key === 'Escape') { event.preventDefault(); open = false; active = -1; renderOptions(); }
    else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); open = true;
      active = !options.length ? -1 : active < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1) :
        (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      renderOptions();
    } else if (event.key === 'Enter' && input.value.trim()) {
      event.preventDefault();
      if (open && active >= 0 && options[active]) choose(options[active]); else commit();
    }
  });
  async function load() {
    status = 'loading'; renderOptions();
    try { items = [...await catalog()].sort((a, b) => a.name.localeCompare(b.name, 'ko') || a.ticker.localeCompare(b.ticker)); status = 'ready'; }
    catch { status = 'failed'; }
    renderChips(); renderOptions();
  }
  renderChips(); load();
  return { root, input, commit };
}
