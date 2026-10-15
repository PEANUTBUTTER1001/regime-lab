// 로그인 히어로 (X1): 차트 → 로켓 영상 → 우주 장면(지구 대기·별자리) → 제목.
// 영상은 소리 없이 한 번 재생하고, 끝나기 직전에 canvas 우주 장면으로 넘어간다.
// 움직임 줄이기 설정이거나 768px 미만 화면이면 영상을 받지 않고 마지막 장면만 그린다.
// 색은 app.css 의 .login-hero --hero-* 토큰에서 읽는다 (E19, JS 에 색 상수를 두지 않음).
import { h } from './ui.js';
import { BRAND } from '../config.js';
import { t } from '../i18n.js';

const VIDEO = 'assets/hero/rocket.mp4';
const POSTER = 'assets/hero/rocket-first.jpg';
const FADE = 1.6;   // 영상이 끝나기 몇 초 전부터 우주 장면으로 넘길지
const STARS = 240;
// 별자리: 상승 그래프 모양 (화면 비율 좌표, 오른쪽 로그인 카드와 겹치지 않게 왼쪽 절반)
const CONSTELLATION = [[.05, .80], [.11, .74], [.17, .78], [.24, .66], [.29, .70], [.36, .58], [.42, .62], [.50, .48]];

const ease = (x) => (x < 0 ? 0 : x > 1 ? 1 : x * x * (3 - 2 * x));

export function mountHero(section) {
  const motion = !matchMedia('(prefers-reduced-motion: reduce)').matches;
  const useVideo = motion && !matchMedia('(max-width: 767px)').matches;
  const canvas = h('canvas', { class: 'hero-canvas', 'aria-hidden': 'true' });
  const video = useVideo ? h('video', { class: 'hero-video', src: VIDEO, poster: POSTER, muted: true, playsinline: true,
    preload: 'auto', 'aria-hidden': 'true' }) : null;
  const text = h('div', { class: 'hero-text' },
    h('h1', {}, h('span', {}, t('hero.line1')), ' ', h('br'), h('span', { class: 'hero-grad' }, t('hero.line2'))),
    h('p', { class: 'hero-sub' }, t('hero.sub')));
  const replay = useVideo ? h('button', { type: 'button', class: 'hero-replay' }, '↻ ' + t('hero.replay')) : null;
  section.classList.add('hero');
  section.append(canvas, ...(video ? [video] : []), h('div', { class: 'hero-shade', 'aria-hidden': 'true' }),
    h('div', { class: 'hero-brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), BRAND), text,
    h('div', { class: 'hero-foot' }, ...(replay ? [replay] : []), h('p', { class: 'hero-notice' }, t('login.notice'))));
  if (video) { video.muted = true; video.playsInline = true; }

  // 토큰은 화면에 붙은 뒤에야 읽히므로 첫 그리기 때 읽는다
  let color = null;
  const readColors = () => {
    const css = getComputedStyle(section), tok = (name) => css.getPropertyValue(name).trim();
    color = { sky: tok('--hero-sky'), star: tok('--hero-star'), glow: tok('--hero-glow'), earth: tok('--hero-earth'), mint: tok('--hero-mint') };
  };
  const g = canvas.getContext('2d');
  let W = 0, H = 0, dpr = 1;
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2); W = section.clientWidth; H = section.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  };
  // 움직임이 없으면 크기가 바뀔 때만 다시 그린다
  const ro = new ResizeObserver(() => { resize(); if (!motion) space(performance.now()); });
  ro.observe(section);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const stars = Array.from({ length: STARS }, () => ({ x: rnd(), y: rnd(), r: rnd() * 1.3 + .3, tw: rnd() * 6.28 }));

  let spaceStart = null;   // 우주 장면 시작 시각 (performance.now)
  let raf = 0;
  function space(now) {
    if (!W || !section.isConnected) return;
    if (!color) readColors();
    const s = spaceStart == null ? 0 : (now - spaceStart) / 1000;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = color.sky; g.fillRect(0, 0, W, H);
    for (const st of stars) {
      const a = .35 + .65 * Math.sin((motion ? now / 900 : 0) + st.tw) ** 2;
      g.fillStyle = `rgba(${color.star},${a * .85})`; g.beginPath(); g.arc(st.x * W, st.y * H, st.r, 0, 6.28); g.fill();
    }
    // 로켓이 사라진 자리의 별
    const rk = ease(s / 1.2) * (1 - ease((s - 4) / 2) * .4);
    if (rk > 0) {
      g.fillStyle = `rgba(${color.mint},${rk})`; g.shadowColor = `rgb(${color.mint})`; g.shadowBlur = 18;
      g.beginPath(); g.arc(W * .42, H * .26, 2.6 + (motion ? Math.sin(now / 150) * .8 : 0), 0, 6.28); g.fill(); g.shadowBlur = 0;
    }
    // 지구 대기 빛
    const e = ease((s - .2) / 1.8);
    if (e > 0) {
      const R = W * 1.15, cy = H * (1.15 - .33 * e) + R;
      const eg = g.createRadialGradient(W * .5, cy, R * .96, W * .5, cy, R * 1.04);
      eg.addColorStop(0, `rgba(${color.glow},0)`); eg.addColorStop(.5, `rgba(${color.glow},${.5 * e})`); eg.addColorStop(1, `rgba(${color.glow},0)`);
      g.fillStyle = eg; g.beginPath(); g.arc(W * .5, cy, R * 1.04, 0, 6.28); g.fill();
      g.fillStyle = `rgba(${color.earth},${e})`; g.beginPath(); g.arc(W * .5, cy, R * .985, 0, 6.28); g.fill();
    }
    // 별자리 (상승 그래프 모양)
    const c = ease((s - 1.0) / 1.8);
    if (c > 0) {
      const n = CONSTELLATION.length - 1, f = c * n;
      g.strokeStyle = `rgba(${color.mint},.55)`; g.lineWidth = 1.5; g.setLineDash([2, 6]); g.beginPath();
      for (let i = 0; i <= Math.min(n, Math.floor(f)); i++) { const [x, y] = CONSTELLATION[i]; if (i) g.lineTo(x * W, y * H); else g.moveTo(x * W, y * H); }
      if (f < n) {
        const i = Math.floor(f), k = f - i, [x1, y1] = CONSTELLATION[i], [x2, y2] = CONSTELLATION[i + 1];
        g.lineTo((x1 + (x2 - x1) * k) * W, (y1 + (y2 - y1) * k) * H);
      }
      g.stroke(); g.setLineDash([]);
      CONSTELLATION.forEach(([x, y], i) => {
        if (i > f + .01) return;
        g.fillStyle = `rgba(${color.mint},${.7 + .3 * Math.sin((motion ? now / 330 : 0) + i)})`;
        g.shadowColor = `rgb(${color.mint})`; g.shadowBlur = 12; g.beginPath(); g.arc(x * W, y * H, 3.2, 0, 6.28); g.fill(); g.shadowBlur = 0;
      });
    }
    if (s > 1.3) text.classList.add('on');
    if (s > 2.5 && replay) replay.classList.add('on');
  }
  function loop(now) {
    if (video) {
      // 처음엔 차트(왼쪽)를 보이고 로켓이 뜨면 가운데로 천천히 이동
      video.style.objectPosition = `${8 + 42 * ease((video.currentTime - 5.4) / 2.2)}% 60%`;
      const left = video.duration ? video.duration - video.currentTime : Infinity;
      if (spaceStart == null && (left <= FADE || video.ended)) spaceStart = now;
      if (spaceStart != null) video.style.opacity = String(1 - ease((now - spaceStart) / 1300));
    }
    space(now);
    if (motion) raf = requestAnimationFrame(loop);
  }
  function play() {
    spaceStart = null; text.classList.remove('on'); replay.classList.remove('on');
    video.style.opacity = '1'; video.currentTime = 0;
    video.play().catch(() => { spaceStart = performance.now(); });   // 자동 재생이 막히면 바로 우주 장면
  }
  if (video) {
    video.addEventListener('error', () => { if (spaceStart == null) spaceStart = performance.now(); });
    replay.addEventListener('click', play);
    play();
  } else {
    spaceStart = performance.now() - 10000;   // 마지막 장면 그대로
    text.classList.add('on');
  }
  raf = requestAnimationFrame(loop);

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
  };
}
