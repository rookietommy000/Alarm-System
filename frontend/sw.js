/*
 * 設備警報代碼查詢 — service worker
 *
 * Strategy:
 *   - HTML pages (/、/admin、/login): always network — auth redirects must reach Flask
 *   - Static assets (CSS/icons/manifest): cache-first
 *   - External CDN: cache-first
 *   - API (/api/...): network only，不落快取（PLAN 5.4 節）——工廠共用平板
 *     場景下，A 部門登出、B 部門登入時若網路不穩，network-first + cache
 *     fallback 可能把 A 部門殘留在快取裡的 API 回應吐給 B 部門，造成跨
 *     部門資料洩漏。改為 network 失敗就回錯誤，不回舊快取；不需要為了
 *     離線容錯犧牲多租戶安全，工廠內網環境穩定性通常足夠。
 */

const CACHE = 'alarm-query-v11';

const STATIC_SHELL = [
  '/style.css',
  '/manifest.webmanifest',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
  '/js/api.js'
];

const HTML_PATHS = ['/', '/app', '/admin', '/admin/dashboard', '/login', '/admin/login'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(STATIC_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    // 1. 刪掉所有舊版 cache（既有裝置上可能還存著改造前的 API 回應，排除
    //    規則只約束新版接管之後的請求，既有快取要主動清掉，見 PLAN 5.4 節）
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));

    // 2. 清掉當前 cache 裡殘留的 API 回應（防禦性，理論上 fetch handler
    //    已經不會再寫入 /api/* 的快取，這裡是雙重保險）
    const cache = await caches.open(CACHE);
    const reqs = await cache.keys();
    await Promise.all(
      reqs.filter(req => new URL(req.url).pathname.startsWith('/api/'))
          .map(req => cache.delete(req))
    );

    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // HTML pages — always fetch from network so Flask auth runs
  if (url.origin === self.location.origin && HTML_PATHS.includes(url.pathname)) {
    event.respondWith(fetch(req));
    return;
  }

  // API — network only，絕不讀寫快取（見檔案頂端說明）
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(req));
    return;
  }

  // Static assets — cache-first
  event.respondWith(
    caches.match(req).then(cached => {
      if (cached) return cached;
      return fetch(req).then(res => {
        if (res && res.status === 200 && (res.type === 'basic' || res.type === 'cors')) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone)).catch(() => {});
        }
        return res;
      });
    })
  );
});
