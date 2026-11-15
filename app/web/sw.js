// Centrum dowodzenia – powiadomienia (post się rozkręca, raport tygodnia)
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = {title: 'Centrum dowodzenia', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Centrum dowodzenia', {
    body: d.body || '', icon: '/ikona-192.png', badge: '/ikona-192.png', data: {url: d.url || '/'}, tag: d.url || 'centrum'}));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(cs => {
    for (const c of cs) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});

// Po aktualizacji serwer restartuje się kilkanaście sekund – zamiast białego ekranu (502) pokazujemy „Aktualizuję…” i sami ponawiamy.
const WAIT = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Centrum</title><style>html,body{height:100%;margin:0;background:#0b0b10;color:#c9c9d6;font:16px -apple-system,system-ui,sans-serif;
display:grid;place-items:center;text-align:center}i{display:block;width:28px;height:28px;margin:0 auto 14px;border:3px solid #444;
border-top-color:#8b7cc7;border-radius:50%;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}</style>
<div><i></i>Centrum się aktualizuje…<br><small style="color:#777">odświeżę samo za chwilę</small></div>
<script>setInterval(()=>fetch('/api/version',{cache:'no-store'}).then(r=>{if(r.ok)location.reload()}).catch(()=>{}),3000)</script>`;
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;          // tylko otwieranie stron (apka i zakładki w ramkach)
  e.respondWith(fetch(e.request).then(r => (r.status >= 502 && r.status <= 504) ? new Response(WAIT, {headers: {'Content-Type': 'text/html; charset=utf-8'}}) : r)
    .catch(() => new Response(WAIT, {headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
