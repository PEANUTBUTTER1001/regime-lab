// 세션 상태: 로그인 UI 흐름, 마지막 run_id, 빌더 입력 초안. 분석 결과는 저장하지 않는다 (항상 API 에서 읽음).
const get = (k) => { try { return sessionStorage.getItem(k); } catch { return null; } };
const set = (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* 저장 불가 */ } };
// 이 브라우저의 화면 편의 설정 (design.md §6.4): 테마·열 너비·자리 바꾸기. 서버로 보내지 않고, 저장소가 막혀도 기본값으로 동작한다
// 저장이 막히면 이번 페이지 동안만 메모리에 기억한다 (자리 바꾸기·폭이 바로 되돌아가지 않게)
const mem = {};
const lget = (k) => { try { const v = localStorage.getItem(k); return v == null ? (mem[k] ?? null) : JSON.parse(v); } catch { return mem[k] ?? null; } };
const lset = (k, v) => { mem[k] = v; try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch { /* 저장 불가 */ } };
const LAYOUT0 = { c1: null, c3: null, swap: false };

export const state = {
  // 고른 테마 ('light' | 'dark'). 없으면 null → 기기 설정을 따른다
  get theme() { const v = lget('rl.theme'); return v === 'light' || v === 'dark' ? v : null; },
  set theme(v) { lset('rl.theme', v); },
  // 작업면 열 너비(px, null = 기본)와 가운데·오른쪽 자리 바꾸기
  get layout() { const v = lget('rl.layout'); return { ...LAYOUT0, ...(v && typeof v === 'object' ? v : {}) }; },
  set layout(v) { lset('rl.layout', v); },
  // 화면 보기 설정: 요약의 자세한 지표·지표 정의 펼침, 종목별 거래 정렬 (design.md §6.4)
  get prefs() { const v = lget('rl.prefs'); return { detailOpen: true, defsOpen: true, stockSort: 'trades_desc', ...(v && typeof v === 'object' ? v : {}) }; },
  set prefs(v) { lset('rl.prefs', v); },
  // 종목 즐겨찾기·최근 조회 종목: 이 브라우저에 사용자별로 (게스트는 guest). 종목 코드 배열
  get favs() { const v = lget(`rl.favs:${this.who}`); return Array.isArray(v) ? v : []; },
  set favs(v) { lset(`rl.favs:${this.who}`, v); },
  get recentStocks() { const v = lget(`rl.recentStocks:${this.who}`); return Array.isArray(v) ? v : []; },
  set recentStocks(v) { lset(`rl.recentStocks:${this.who}`, v); },
  get who() { return this.user?.id || 'guest'; },
  get signedIn() { return this.authConfig?.enabled ? !!this.user : get('rl.signedIn') === '1'; },
  set signedIn(v) { set('rl.signedIn', v ? '1' : null); },
  // 게스트 모드: 가입·로그인 없이 진행 (UI 흐름만, 실제 인증은 원래 없음)
  get guest() { return get('rl.guest') === '1'; },
  set guest(v) { set('rl.guest', v ? '1' : null); },
  get lastRun() { return get('rl.lastRun'); },
  set lastRun(v) { set('rl.lastRun', v); },
  get draft() { try { return JSON.parse(get('rl.draft') || 'null'); } catch { return null; } },
  set draft(v) { set('rl.draft', v ? JSON.stringify(v) : null); },
  // 빌더에 불러온 저장 조합 {id, name, revision, body} — 덮어쓰기·변경 여부 판단용 (P1-10)
  get loadedPreset() { try { return JSON.parse(get('rl.loadedPreset') || 'null'); } catch { return null; } },
  set loadedPreset(v) { set('rl.loadedPreset', v ? JSON.stringify(v) : null); },
  user: null,
  authConfig: null,
  setUser(user) {
    if (get('rl.userId') !== user?.id) this.clearWorkspace();
    this.user = user;
    set('rl.userId', user?.id || null);
  },
  clearWorkspace() {
    this.lastRun = null; this.draft = null; this.loadedPreset = null; this.meta = null;
  },
  clearAuth() {
    this.setUser(null); this.signedIn = false; this.guest = false;
  },
  meta: null,
};
