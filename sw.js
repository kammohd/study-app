// Service worker — lives on the phone, shows notifications even when
// the app is closed. Installed automatically by index.html.
// Also caches the Pyodide (in-app Python) runtime so practice works
// offline and loads instantly after the first download.

self.addEventListener("push", function (e) {
  let data = { title: "AI Study", body: "Time to study." };
  try { data = e.data.json(); }
  catch (err) { if (e.data) data.body = e.data.text(); }
  e.waitUntil(
    self.registration.showNotification(data.title || "AI Study", {
      body: data.body || "",
      icon: "icon.png",
      badge: "icon.png",
      data: { url: data.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
      for (const c of list) { if ("focus" in c) return c.focus(); }
      return clients.openWindow(e.notification.data.url || "./");
    })
  );
});

// Cache-first for the Python runtime (~10 MB, downloaded once).
const PY_CACHE = "pyodide-v1";
self.addEventListener("fetch", function (e) {
  if (e.request.url.includes("cdn.jsdelivr.net/pyodide/")) {
    e.respondWith(
      caches.open(PY_CACHE).then(function (cache) {
        return cache.match(e.request).then(function (hit) {
          return hit || fetch(e.request).then(function (resp) {
            if (resp.ok) cache.put(e.request, resp.clone());
            return resp;
          });
        });
      })
    );
  }
});
