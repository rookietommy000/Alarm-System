/*
 * 設備警報代碼查詢 — service worker
 *
 * Strategy:
 *   - HTML pages (/、/admin、/login): always network — auth redirects must reach Flask
 *   - Static assets (CSS/icons/manifest): cache-first
 *   - External CDN: cache-first
 *   - API (/api/...): network-first with cache fallback
 */

const CACHE = 'alarm-query-v8';

const STATIC_SHELL = [
  '/style.css',
  '/manifest.webmanifest',
  '/icon.svg'
];

const HTML_PATHS = ['/', '/admin', '/login', '/admin/login'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(STATIC_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
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

  // API — network-first with cache fallback
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req).then(res => {
        if (res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match(req))
    );
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
