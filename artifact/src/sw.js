/* Hobby Hero service worker, site edition only (the pattern is Kestrel Nine's). build.py --site
 * fills in the cache name (a content hash of the build) and the file list. Cache-first for the
 * precached build, so the page starts offline; old caches are deleted on activate. */
'use strict';
const CACHE = '/*CACHE*/';
const FILES = /*FILES*/[];

// cache: 'reload' fetches each file from the server. Without it a new build could precache the
// old files out of the browser's HTTP cache and keep serving them under the new cache name.
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(FILES.map((f) => new Request(f, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('hh-') && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(caches.open(CACHE)
    .then((c) => c.match(req, { ignoreSearch: true }))
    .then((hit) => hit || fetch(req)));
});
