/* prosty service worker: pozwala zainstalować dziennik jak aplikację; zawsze pobiera świeże dane z sieci */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
