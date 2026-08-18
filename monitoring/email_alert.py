"""
Email alerting for monitoring RCA reports via Gmail SMTP.

Config (env / .env):
  EMAIL_FROM       — sender Gmail address (e.g. monitor@gmail.com)
  EMAIL_PASSWORD   — Gmail app password (NOT your normal password)
  EMAIL_TO         — recipient email address
  EMAIL_SMTP_HOST  — smtp.gmail.com (default)
  EMAIL_SMTP_PORT  — 587 (default)
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO, EMAIL_SMTP_HOST, EMAIL_SMTP_PORT


def _plain_text(message: str) -> str:
    """Strip Slack markdown so the email body reads cleanly."""
    text = message.replace("*", "").replace("`", "")
    return text


def send_email(subject: str, message: str):
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        print(f"[EMAIL] Skipping — missing email config (from={bool(EMAIL_FROM)}, to={bool(EMAIL_TO)})")
        return

    body = _plain_text(message)
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print(f"[EMAIL] Sent: {subject}")
    except Exception as e:
        print(f"[EMAIL] Failed to send: {e}")
