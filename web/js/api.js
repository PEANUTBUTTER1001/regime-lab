// API 호출 래퍼. 모든 오류를 {status, code, message, detail, retryable} 로 통일한다 (구현_계획 §6.4).
export class ApiError extends Error {
  constructor(status, code, message, detail = null, retryable = false) {
    super(message);
    Object.assign(this, { status, code, detail, retryable });
  }
}

let authConfig = null;
async function request(method, path, body, retried = false) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(authConfig?.enabled && !['GET', 'HEAD'].includes(method) ? { 'X-CSRF-Token': authConfig.csrf_token } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, 'network_error', 'Cannot reach the analysis server. Check the connection and retry.', null, true);
  }
  let data = null;
  try { data = await res.json(); } catch { /* 본문 없음 */ }
  if (!res.ok) {
    const d = data || {};
    if (res.status === 403 && d.code === 'auth_csrf' && !retried) {
      await api.authConfig();
      return request(method, path, body, true);
    }
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('rl:unauthorized'));
    throw new ApiError(res.status, d.code || 'server_error', d.message || `Server error (${res.status})`, d.detail ?? null,
      d.retryable ?? res.status >= 500);
  }
  return data;
}

export const api = {
  authConfig: async () => { authConfig = await request('GET', '/auth/config'); return authConfig; },
  me: () => request('GET', '/auth/me'),
  login: (email, password) => request('POST', '/auth/login', {email, password}),
  startOAuth: (provider) => request('POST', '/auth/oauth', {provider}),
  signup: (email, password) => request('POST', '/auth/signup', {email, password}),
  logout: () => request('POST', '/auth/logout'),
  recover: (email) => request('POST', '/auth/recover', {email}),
  password: (password) => request('POST', '/auth/password', {password}),
  meta: () => request('GET', '/meta'),
  preview: (filters) => request('POST', '/universe/preview', filters),
  submit: (strategies) => request('POST', '/runs', { strategies }),
  runs: () => request('GET', '/runs'),
  status: (id) => request('GET', `/runs/${encodeURIComponent(id)}`),
  cancel: (id) => request('POST', `/runs/${encodeURIComponent(id)}/cancel`),
  result: (id) => request('GET', `/runs/${encodeURIComponent(id)}/result`),
  stock: (id, ticker, strategy) =>
    request('GET', `/runs/${encodeURIComponent(id)}/stocks/${encodeURIComponent(ticker)}${strategy ? `?strategy=${encodeURIComponent(strategy)}` : ''}`),
  report: (id, strategy) =>
    request('POST', `/runs/${encodeURIComponent(id)}/report${strategy ? `?strategy=${encodeURIComponent(strategy)}` : ''}`),
  briefing: () => request('GET', '/briefing'),
  llmConfig: () => request('GET', '/llm/config'),
  setLlmConfig: (body) => request('PUT', '/llm/config', body),
  clearLlmConfig: () => request('DELETE', '/llm/config'),
  // 역방향 탐색 (P1-7·P1-8)
  searchOptions: () => request('GET', '/searches/options'),
  searchPreview: (body) => request('POST', '/searches/preview', body),
  submitSearch: (body) => request('POST', '/searches', body),
  searches: () => request('GET', '/searches'),
  searchStatus: (id) => request('GET', `/searches/${encodeURIComponent(id)}`),
  cancelSearch: (id) => request('POST', `/searches/${encodeURIComponent(id)}/cancel`),
  searchResult: (id) => request('GET', `/searches/${encodeURIComponent(id)}/result`),
  // 찾은 조합 저장 (P1-9)
  presets: () => request('GET', '/presets'),
  savePreset: (body) => request('POST', '/presets', body),
  preset: (id) => request('GET', `/presets/${encodeURIComponent(id)}`),
  updatePreset: (id, body) => request('PUT', `/presets/${encodeURIComponent(id)}`, body),
  deletePreset: (id) => request('DELETE', `/presets/${encodeURIComponent(id)}`),
  // 근거 검색 (P3-11)
  evidence: (params) => request('GET', `/evidence/search?${new URLSearchParams(params)}`),
  // 자료 보관함·자료 상세 (P3-13)
  documents: (params) => request('GET', `/evidence/documents?${new URLSearchParams(params)}`),
  document: (id, params) => request('GET', `/evidence/documents/${encodeURIComponent(id)}?${new URLSearchParams(params)}`),
  coverage: (params) => request('GET', `/evidence/coverage?${new URLSearchParams(params)}`),
};
