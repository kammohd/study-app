"""Send web-push notifications.

The subscription endpoint is a URL the browser gave us, so a signed-in user could hand us any address.
We only ever POST to the real push services; anything else is refused (otherwise this server could be
pointed at internal addresses such as the cloud metadata server).
"""
from urllib.parse import urlparse

from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .config import Settings

PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com",
              "push.apple.com", "notify.windows.com")


def endpoint_ok(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and not p.username and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def send_one(sub: dict, payload: str, cfg: Settings) -> str:
    """Blocking. Returns "ok", "gone" (the device unsubscribed: delete it) or "error" (try again next time)."""
    if not endpoint_ok(str(sub.get("endpoint", ""))):
        return "gone"
    try:
        webpush(subscription_info=sub, data=payload, vapid_private_key=Vapid.from_pem(cfg.vapid_private_key.encode()),
                vapid_claims={"sub": cfg.vapid_subject}, timeout=10)     # fresh claims: pywebpush mutates the dict
        return "ok"
    except WebPushException as e:
        return "gone" if getattr(e.response, "status_code", None) in (404, 410) else "error"
    except Exception:       # a bad key or a network failure must not stop the other users' nudges
        return "error"
