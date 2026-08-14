/*
 * 止血用 service worker —— 不在正常部署流程內使用。
 *
 * 用途：若 sw.js 在 HTTPS 正式環境下的實際行為與本機驗證不符（PLAN 5.5
 * 節手動驗證失敗），且無法在窗口內快速排除，把這支檔案的內容複製覆蓋
 * frontend/sw.js 並單獨部署（不影響 app.py，可獨立回滾），讓所有裝置
 * 解除註冊 service worker——PWA 退化成普通網頁，跨帳號快取洩漏風險歸零。
 *
 * 用法：
 *   cp frontend/sw-kill.js frontend/sw.js
 *   git add frontend/sw.js && git commit -m "hotfix: 停用 service worker"
 *   git push origin main
 *
 * 之後要恢復正常 sw.js，從 git 歷史取回本輪的 sw.js 版本即可。
 */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.map((n) => caches.delete(n)));
      await self.registration.unregister();
      const clientsList = await self.clients.matchAll({ type: 'window' });
      clientsList.forEach((client) => client.navigate(client.url));
    })()
  );
});
