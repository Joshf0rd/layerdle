// Makes Layerdle work offline.
// - App files: network first (so updates show up), cached copy when offline or slow.
// - Song clips: cache first. Clips get cached when played or when a category is saved offline.

const SHELL = 'layerdle-shell-v1';
const CLIPS = 'layerdle-clips'; // shared with index.html's "Save offline"
const SHELL_FILES = ['./', 'manifest.json', 'icon-180.png', 'icon-512.png', 'songs.json'];
const NETWORK_TIMEOUT_MS = 4000; // patchy signal in the car: don't wait forever

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith('layerdle-shell-') && k !== SHELL).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  event.respondWith(url.pathname.startsWith('/songs/') ? clipFirst(req) : networkFirst(req));
});

async function clipFirst(req) {
  const cache = await caches.open(CLIPS);
  const hit = await cache.match(req.url);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req.url, res.clone());
  return res;
}

async function networkFirst(req) {
  const cache = await caches.open(SHELL);
  const key = req.mode === 'navigate' ? './' : req.url.split('?')[0];
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), NETWORK_TIMEOUT_MS)),
    ]);
    if (res.ok && !res.redirected) cache.put(key, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(key);
    if (hit) return hit;
    throw err;
  }
}
