/**
 * 統一 fetch 封裝（PLAN 5.1 節）：401 全域攔截、429 讀 Retry-After 倒數、
 * whoami 結果快取。純 <script src>，無 build step，掛 window.AlarmApi。
 */
(function () {
  let whoamiCache = null;
  let whoamiPromise = null;

  function loginUrlFor(path) {
    const admin = path.startsWith('/admin');
    const next = encodeURIComponent(location.pathname + location.search);
    return admin ? '/admin/login' : `/login?next=${next}`;
  }

  /**
   * 統一 fetch：401 直接導向對應登入頁；429 回傳結構化的剩餘秒數，
   * 呼叫端自行決定怎麼顯示倒數，不在這裡耦合 UI。
   */
  async function apiFetch(url, options) {
    const r = await fetch(url, options);
    if (r.status === 401) {
      location.href = loginUrlFor(location.pathname);
      // 導頁是非同步的，讓呼叫端的 await 停在這裡不再往下執行
      return new Promise(() => {});
    }
    if (r.status === 429) {
      const retryAfter = parseInt(r.headers.get('Retry-After') || '0', 10);
      const err = new Error('登入嘗試過於頻繁');
      err.status = 429;
      err.retryAfter = retryAfter || 0;
      throw err;
    }
    return r;
  }

  function get(url) { return apiFetch(url); }
  function post(url, body) {
    return apiFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }
  function put(url, body) {
    return apiFetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }
  function del(url, body) {
    return apiFetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  /** multipart/form-data 專用（批次匯入上傳檔案）——不設 Content-Type，
   * 讓瀏覽器自己帶上正確的 boundary，手動設反而會壞掉。 */
  function postForm(url, formData) {
    return apiFetch(url, { method: 'POST', body: formData });
  }

  /** whoami 快取：一個頁面生命週期內不太會變，避免每個元件掛載都重打一次。
   * force=true 可強制重新查詢（例如部門切換器操作後）。 */
  async function whoami(force) {
    if (whoamiCache && !force) return whoamiCache;
    if (whoamiPromise && !force) return whoamiPromise;
    whoamiPromise = fetch('/api/whoami').then(r => r.json()).then(data => {
      whoamiCache = data;
      whoamiPromise = null;
      return data;
    });
    return whoamiPromise;
  }

  window.AlarmApi = { get, post, put, delete: del, postForm, whoami };
})();
