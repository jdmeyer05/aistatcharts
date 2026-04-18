// AI Statcharts — service worker.
//
// Minimal footprint: satisfies Chrome's installability criterion (a registered
// SW with a fetch handler) and gives the app shell a cache-first fallback so
// re-launches are instant. Live data / API / Plotly chunks always go to the
// network — this app is useless offline and we don't pretend otherwise.

const VERSION = "v3";
const SHELL_CACHE = `shell-${VERSION}`;

// Pre-cache only stable, already-built static assets. Don't pre-cache pages —
// Next.js hashes chunks per build, so stale entries would break on deploy.
const SHELL_ASSETS = [
  "/",
  "/favicon.png",
  "/icon-192.png",
  "/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Never cache API calls, auth, or Next dev chunks. Always network.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/_next/") ||
    url.origin !== self.location.origin
  ) {
    return;
  }

  // Navigation requests: network first, fall back to cached root.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/") || Response.error()),
    );
    return;
  }

  // Static assets from our origin: cache first, revalidate in background.
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        // Revalidate async; don't block.
        fetch(request)
          .then((fresh) => {
            if (fresh.ok) {
              caches.open(SHELL_CACHE).then((c) => c.put(request, fresh)).catch(() => undefined);
            }
          })
          .catch(() => undefined);
        return cached;
      }
      return fetch(request).then((fresh) => {
        if (fresh.ok && fresh.type === "basic") {
          const clone = fresh.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(request, clone)).catch(() => undefined);
        }
        return fresh;
      });
    }),
  );
});
