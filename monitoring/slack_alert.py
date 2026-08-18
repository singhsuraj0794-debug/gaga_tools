import json
from urllib.request import Request, urlopen
from urllib.error import URLError
from config import SLACK_WEBHOOK_URL


def send_alert(title: str, message: str):
    if not SLACK_WEBHOOK_URL:
        print(f"[SLACK] Skipping — no webhook URL configured")
        print(f"[SLACK] Would alert: {title} — {message}")
        return
    payload = json.dumps({"text": f"*{title}*\n{message}"}).encode()
    req = Request(SLACK_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        urlopen(req)
        print(f"[SLACK] Alert sent: {title}")
    except URLError as e:
        print(f"[SLACK] Failed to send alert: {e}")
