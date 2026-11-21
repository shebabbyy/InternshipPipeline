"""Workday email-verification links from Gmail (optional, phase 2).

Off unless .env has GMAIL_APP_PASSWORD (a Google "app password"; needs 2-Step Verification on the Gmail account).
The mailbox is opened read-only (IMAP EXAMINE): nothing is sent, moved, deleted or marked read. Only links on the
same Workday host as the application are ever returned. Without the app password the filler pauses and he clicks
the link in Gmail himself.
"""
from __future__ import annotations

import email
import html
import imaplib
import os
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from urllib.parse import urlparse

from common import PROFILE

LINK = re.compile(r"https://[^\s\"'<>]+", re.I)
VERIFY_WORDS = re.compile(r"verif|activat|confirm|validate", re.I)


def enabled() -> bool:
    return bool(os.environ.get("GMAIL_APP_PASSWORD"))


def pick_link(text: str, host: str) -> str | None:
    """Pure: the verification link for this Workday host, or None. Never a link on another host."""
    host = (host or "").lower()
    for raw in LINK.findall(html.unescape(text or "")):
        url = raw.rstrip(").,;")
        h = (urlparse(url).hostname or "").lower()
        if h == host and VERIFY_WORDS.search(url):
            return url
    return None


def _body(msg) -> str:
    parts = []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(parts)


def find_link(host: str, since: float, wait_s: int = 180) -> str | None:
    """Poll Gmail (read-only) for a verification email that arrived after `since`; return its link."""
    if not enabled():
        return None
    user = os.environ.get("GMAIL_ADDRESS") or PROFILE.get("candidate", {}).get("email", "")
    end = time.time() + wait_s
    while time.time() < end:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as m:
                m.login(user, os.environ["GMAIL_APP_PASSWORD"])
                m.select("INBOX", readonly=True)
                day = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
                _, ids = m.search(None, f'(SINCE {day})')
                for num in reversed(ids[0].split()[-30:]):
                    _, data = m.fetch(num, "(BODY.PEEK[])")
                    msg = email.message_from_bytes(data[0][1])
                    when = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
                    if when and when.timestamp() < since - 120:
                        continue
                    subj = str(make_header(decode_header(msg.get("Subject", ""))))
                    link = pick_link(_body(msg), host)
                    if link and (VERIFY_WORDS.search(subj) or "workday" in (msg.get("From", "").lower())):
                        return link
        except Exception as exc:  # noqa: BLE001  (never print the password)
            print(f"Gmail check failed ({exc.__class__.__name__}); click the verification link yourself.", flush=True)
            return None
        time.sleep(15)
    return None
