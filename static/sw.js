'use strict';
// Cache only the public shell, never configuration, API/SSE, uploads or outputs.
const CACHE_NAME = 'jozve-saz-shell-v2';
const SHELL = ['/', '/index.html', '/app.js', '/manifest.json'];
self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('jozve-saz-') && key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.includes(url.pathname) || url.search) return;
    event.respondWith(fetch(request).then(response => {
        if (response.ok && response.type === 'basic') {
            const copy = response.clone();
            event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(request, copy)));
        }
        return response;
    }).catch(async () => {
        const cached = await caches.match(request);
        return cached || new Response('سرور محلی در دسترس نیست. برنامهٔ پایتون را دوباره اجرا کنید.', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
    }));
});
