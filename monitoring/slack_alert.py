import json
from urllib.request import Request, urlopen
from urllib.error import URLError
from config import SLACK_WEBHOOK_URL
from email_alert import send_email


def send_alert(title: str, message: str):
    if SLACK_WEBHOOK_URL:
        payload = json.dumps({"text": f"*{title}*\n{message}"}).encode()
        req = Request(SLACK_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
        try:
            urlopen(req)
            print(f"[SLACK] Alert sent: {title}")
        except URLError as e:
            print(f"[SLACK] Failed to send alert: {e}")
    else:
        print(f"[SLACK] Skipping — no webhook URL configured")
        print(f"[SLACK] Would alert: {title} — {message}")

    # Also email the RCA report
    send_email(f"[GAJAB Monitor] {title}", message)
