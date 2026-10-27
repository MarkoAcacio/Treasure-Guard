"""Sending alert emails.

With TREASURY_GUARD_SMTP_HOST set, mail goes out through that SMTP server (also set
TREASURY_GUARD_SMTP_PORT, _SMTP_USER, _SMTP_PASSWORD and TREASURY_GUARD_MAIL_FROM).
Without it (development and testing), nothing is sent: each email is saved as an .eml
file in the `outbox` folder next to the database, so it can be opened and checked.
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


def is_live():
    return bool(os.environ.get("TREASURY_GUARD_SMTP_HOST"))


def send(outbox, to, subject, text):
    """Send (or save) one email. Returns 'sent' or the path of the saved file."""
    msg = EmailMessage()
    msg["From"] = os.environ.get("TREASURY_GUARD_MAIL_FROM", "Treasury Guard <alerts@treasuryguard.local>")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if is_live():
        env = os.environ
        with smtplib.SMTP(env["TREASURY_GUARD_SMTP_HOST"], int(env.get("TREASURY_GUARD_SMTP_PORT", 587)),
                          timeout=20) as smtp:
            smtp.starttls()
            if env.get("TREASURY_GUARD_SMTP_USER"):
                smtp.login(env["TREASURY_GUARD_SMTP_USER"], env.get("TREASURY_GUARD_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return "sent"
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{datetime.now():%Y%m%d-%H%M%S-%f}.eml"
    path.write_bytes(bytes(msg))
    return str(path)


def recent_outbox(outbox, limit=5):
    """The newest saved emails as (filename, subject, to, saved_at), for the alerts page in development."""
    files = sorted(Path(outbox).glob("*.eml"), reverse=True)[:limit] if Path(outbox).exists() else []
    out = []
    for f in files:
        headers = {}
        for line in f.read_text(errors="replace").splitlines():
            if not line:
                break
            key, _, value = line.partition(": ")
            headers[key] = value
        out.append({"file": f.name, "subject": headers.get("Subject", ""), "to": headers.get("To", ""),
                    "saved_at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out
