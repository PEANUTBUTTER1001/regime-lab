// 세션 상태: 로그인 UI 흐름, 마지막 run_id, 빌더 입력 초안. 분석 결과는 저장하지 않는다 (항상 API 에서 읽음).
const get = (k) => { try { return sessionStorage.getItem(k); } catch { return null; } };
const set = (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* 저장 불가 */ } };

export const state = {
  get signedIn() { return get('rl.signedIn') === '1'; },
  set signedIn(v) { set('rl.signedIn', v ? '1' : null); },
  // 게스트 모드: 가입·로그인 없이 진행 (UI 흐름만, 실제 인증은 원래 없음)
  get guest() { return get('rl.guest') === '1'; },
  set guest(v) { set('rl.guest', v ? '1' : null); },
  get lastRun() { return get('rl.lastRun'); },
  set lastRun(v) { set('rl.lastRun', v); },
  get draft() { try { return JSON.parse(get('rl.draft') || 'null'); } catch { return null; } },
  set draft(v) { set('rl.draft', v ? JSON.stringify(v) : null); },
  meta: null,
};
