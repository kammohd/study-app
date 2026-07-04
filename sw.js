// Service worker — lives on the phone, shows notifications even when
// the app is closed. Installed automatically by index.html.

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

self.addEventListener("fetch", function () {});
