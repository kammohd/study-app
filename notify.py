import os
import json
import datetime
import requests
from pywebpush import webpush, WebPushException

EXEC_URL = os.environ["EXEC_URL"]


def stage():
    return "2" if datetime.datetime.utcnow().hour >= 17 else "1"


def main():
    s = os.environ.get("STAGE") or stage()
    nudge = requests.get(EXEC_URL, params={"action": "nudge", "stage": s}, timeout=120).json()
    print("Nudge decision:", nudge)
    if not nudge.get("send"):
        print("Nothing to send:", nudge.get("reason"))
        return

    subs = requests.get(EXEC_URL, params={"action": "subs"}, timeout=120).json()["subs"]
    print(len(subs), "device(s) registered")

    payload = json.dumps({
        "title": nudge["title"],
        "body": nudge["message"],
        "url": os.environ.get("APP_URL", "./"),
    })
    with open("pk.pem", "w") as f:
        f.write(os.environ["VAPID_PRIVATE_KEY"])

    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key="pk.pem",
                vapid_claims={"sub": "mailto:mohammad.kamran@talabat.com"},
            )
            sent += 1
        except WebPushException as err:
            print("Push failed for one device:", err)
    print("Sent", sent, "of", len(subs))


if __name__ == "__main__":
    main()
