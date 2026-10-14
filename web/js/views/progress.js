// Progress (FR-U2, FR-X3, design.md §6.3). 서버가 제공한 단계·처리 수/전체 수·경과 시간만 표시한다.
// 전체 진행률 퍼센트나 예상 남은 시간은 만들지 않는다. 단계 안의 막대는 서버의 처리 수/전체 수 그대로다.
import { api } from '../api.js';
import { errorView, fmt, h, linkButton, toast } from '../components/ui.js';
import { has, t } from '../i18n.js';
import { state } from '../state.js';

const stageLabel = (s) => (has(`p.stage.${s}`) ? t(`p.stage.${s}`) : s);
const unitLabel = (s) => (has(`p.unit.${s}`) ? t(`p.unit.${s}`) : t('p.unit.items'));

// opts.embedded: 작업면 가운데 열 안 (제목 h2, 끝나면 주소 이동 없이 opts.onDone)
export function renderProgress(el, runId, opts = {}) {
  state.lastRun = runId;
  const title = h(opts.embedded ? 'h2' : 'h1', { id: 'progress-title', text: t('p.preparing') });
  const orb = h('div', { class: 'orb', 'aria-hidden': 'true' });
  const fill = h('div', { class: 'progress-fill' });
  const track = h('div', { class: 'progress-track hidden', role: 'progressbar', 'aria-label': t('p.bar'), 'aria-valuemin': '0' }, fill);
  const statusLine = h('span', {});
  const counts = h('span', {});
  const steps = h('ol', { class: 'steps', 'aria-label': t('p.stages') });
  const live = h('p', { class: 'sr-only', 'aria-live': 'polite' });
  const cancelBtn = h('button', { class: 'secondary', type: 'button' }, t('p.cancel'));
  const actions = h('div', { class: 'actions' }, cancelBtn);
  const note = h('p', { class: 'lead', style: { margin: 'auto' }, text: t('p.note') });
  const wrap = h('div', { class: 'progress-wrap' }, orb, h('div', { class: 'eyebrow' }, t('p.runId', { id: runId })), title, note,
    track, h('div', { class: 'progress-info' }, statusLine, counts), steps, live, actions);
  el.append(wrap);

  let timer = null;
  let failures = 0;
  let lastStage = null;
  let lastQueue = null;
  let stopped = false;

  function renderSteps(snap) {
    const idx = snap.stages.indexOf(snap.stage);
    const done = snap.status === 'completed';
    steps.replaceChildren(...snap.stages.map((s, i) => h('li', {
      class: `step${done || i < idx ? ' done' : ''}${!done && i === idx && snap.status === 'running' ? ' active' : ''}`,
      'aria-current': !done && i === idx ? 'step' : null,
    }, has(`p.step.${s}`) ? t(`p.step.${s}`) : s)));
  }

  function finish(kind, heading, message, buttons) {
    stopped = true;
    clearTimeout(timer);
    orb.classList.add('stopped');
    track.classList.add('hidden');
    title.textContent = heading;
    if (!opts.embedded) document.title = `${heading} — ${runId}`;
    statusLine.textContent = kind === 'completed' ? t('p.allDone') : t('p.stopped', { k: heading });
    counts.textContent = '';
    note.textContent = message;
    live.textContent = `${heading}. ${message}`;
    actions.replaceChildren(...buttons);
    wrap.setAttribute('data-state', kind);
  }

  async function poll() {
    if (stopped) return;
    let snap;
    try {
      snap = await api.status(runId);
      failures = 0;
    } catch (e) {
      if (e.code === 'run_not_found') { el.replaceChildren(errorView(e)); stopped = true; return; }
      failures += 1;
      statusLine.textContent = t('p.retrying', { m: e.message, n: failures });
      timer = setTimeout(poll, Math.min(10000, 1000 * 2 ** failures));
      return;
    }
    renderSteps(snap);
    wrap.setAttribute('data-state', snap.status);
    if (snap.status === 'completed') {
      finish('completed', t('p.done'), t('p.doneMsg', { id: runId, t: fmt.num(snap.elapsed_sec, 1) }),
        [linkButton(t('p.viewResults'), `#/runs/${runId}/results`, true), linkButton(t('common.backStrategy'), '#/builder')]);
      toast(t('p.toastDone'));
      if (opts.onDone) setTimeout(() => { if (el.isConnected) opts.onDone(runId); }, 900);
      else setTimeout(() => { if (location.hash.endsWith(`${runId}/progress`)) location.hash = `#/runs/${runId}/results`; }, 1200);
      return;
    }
    if (snap.status === 'cancelled') {
      finish('cancelled', t('p.cancelled'), t('p.cancelledMsg'), [linkButton(t('common.backStrategy'), '#/builder', true)]);
      return;
    }
    if (snap.status === 'failed') {
      const msg = snap.error?.code === 'server_restarted' ? t('p.restarted') : (snap.error?.message || t('errmsg.run_failed'));
      finish('failed', t('p.failed'), msg, [linkButton(t('common.backStrategy'), '#/builder', true)]);
      return;
    }
    const label = snap.stage ? stageLabel(snap.stage) : (snap.status === 'queued' ? t('p.waiting') : t('p.starting'));
    const queued = snap.status === 'queued';
    const queueLabel = snap.queue_position > 0 ? t('p.queuePosition', { n: snap.queue_position }) : t('p.starting');
    title.textContent = queued ? t('p.waiting') : `${label}…`;
    note.textContent = queued ? t('p.queueNote') : t('p.note');
    if (!opts.embedded) document.title = `${label}… — ${runId}`;
    statusLine.textContent = queued ? queueLabel : t('p.elapsed', { s: label, t: fmt.num(snap.elapsed_sec, 1) });
    if (snap.total > 0) {
      track.classList.remove('hidden');
      fill.style.width = `${Math.min(100, (100 * snap.processed) / snap.total)}%`;
      track.setAttribute('aria-valuemax', String(snap.total));
      track.setAttribute('aria-valuenow', String(snap.processed));
      counts.textContent = `${fmt.int(snap.processed)} / ${fmt.int(snap.total)} ${unitLabel(snap.stage)}`;
    } else {
      track.classList.add('hidden');
      counts.textContent = '';
    }
    if (snap.stage !== lastStage || snap.queue_position !== lastQueue) {
      live.textContent = queued ? queueLabel : label;
      lastStage = snap.stage; lastQueue = snap.queue_position;
    }
    timer = setTimeout(poll, 1000);
  }

  cancelBtn.addEventListener('click', async () => {
    cancelBtn.disabled = true;
    try {
      await api.cancel(runId);
      statusLine.textContent = wrap.getAttribute('data-state') === 'queued' ? t('p.queueCancelRequested') : t('p.cancelRequested');
    } catch (e) {
      toast(e.code === 'not_running' ? t('p.alreadyDone') : e.message);
      cancelBtn.disabled = false;
    }
  });

  poll();
  return () => { stopped = true; clearTimeout(timer); };
}
