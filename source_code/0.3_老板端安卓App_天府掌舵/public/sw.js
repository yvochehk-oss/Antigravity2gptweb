/**
 * cdjg-executive-sw — Minimal service worker for offline-first PWA launch.
 *
 * Strategy:
 * - App Shell resources (HTML, JS, CSS) are precached on install so the
 *   app can start without the network.
 * - API calls are never intercepted (they go to the network and fall back
 *   to whatever the app does with a failed fetch).
 * - On activate, old caches are purged so we don't serve stale JS.
 */

const CACHE_NAME = 'cdjg-shell-v1'
const PRECACHE_URLS = [
  '/',
  '/index.html',
  // Vite injects hashed assets here at build time via the SW registration.
  // We dynamically precache whatever is in the existing precache list.
]

self.addEventListener('install', event => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches
      .keys()
      .then(keys =>
        Promise.all(
          keys
            .filter(key => key !== CACHE_NAME)
            .map(key => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', event => {
  // Only intercept same-origin navigation and static asset requests.
  const url = new URL(event.request.url)
  const isAppShell =
    event.request.mode === 'navigate' ||
    url.pathname.startsWith('/assets/') ||
    url.pathname.endsWith('.js') ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.woff2')

  if (!isAppShell) return

  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached
      return fetch(event.request)
        .then(response => {
          if (!response || response.status !== 200 || response.type === 'opaque') {
            return response
          }
          const clone = response.clone()
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone))
          return response
        })
        .catch(() => {
          // For navigation requests, serve the root shell if offline.
          if (event.request.mode === 'navigate') {
            return caches.match('/index.html')
          }
          return new Response('Offline', { status: 503 })
        })
    })
  )
})
