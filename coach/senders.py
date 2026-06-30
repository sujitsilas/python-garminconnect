#!/usr/bin/env python3
"""Delivery for the daily coach report.

Default provider is ``email`` (Gmail SMTP). CallMeBot (WhatsApp) and Twilio
senders are included as alternatives.
"""
from __future__ import annotations

import smtplib
import urllib.parse
from email.message import EmailMessage

import requests


def send_email(text: str, subject: str, to_addr: str, from_addr: str,
               username: str, password: str, smtp_host: str = "smtp.gmail.com",
               smtp_port: int = 587, timeout: int = 30) -> None:
    """Send a plain-text email via SMTP (defaults to Gmail).

    For Gmail you must use an App Password (with 2-Step Verification on):
    https://myaccount.google.com/apppasswords
    """
    if not (to_addr and username and password):
        raise ValueError("email delivery needs to/username/password")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr or username
    msg["To"] = to_addr
    msg.set_content(text)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def send_callmebot(text: str, phone: str, apikey: str, timeout: int = 30) -> None:
    """Send a WhatsApp message to yourself via CallMeBot.

    One-time setup: add +34 644 84 71 89 to your contacts and message it
    "I allow callmebot to send me messages", then it replies with your apikey.
    https://www.callmebot.com/blog/free-api-whatsapp-messages/
    """
    if not phone or not apikey:
        raise ValueError("CallMeBot needs both a phone number and an apikey")
    url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": phone, "text": text, "apikey": apikey}
    resp = requests.get(f"{url}?{urllib.parse.urlencode(params)}", timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"CallMeBot failed ({resp.status_code}): {resp.text[:300]}")


def send_twilio(text: str, to_whatsapp: str, account_sid: str, auth_token: str,
                from_whatsapp: str, timeout: int = 30) -> None:
    """Send via Twilio's WhatsApp API (numbers like 'whatsapp:+1555...')."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {"To": to_whatsapp, "From": from_whatsapp, "Body": text}
    resp = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=timeout)
    if resp.status_code >= 300:
        raise RuntimeError(f"Twilio failed ({resp.status_code}): {resp.text[:300]}")


def send(text: str, delivery_cfg: dict, subject: str = "Daily training plan") -> None:
    """Dispatch to the configured provider."""
    provider = (delivery_cfg.get("provider") or "email").lower()
    if provider == "email":
        c = delivery_cfg.get("email", {})
        send_email(text, subject, c.get("to"), c.get("from"),
                   c.get("username"), c.get("password"),
                   c.get("smtp_host", "smtp.gmail.com"),
                   int(c.get("smtp_port", 587)))
    elif provider == "callmebot":
        c = delivery_cfg.get("callmebot", {})
        send_callmebot(text, c.get("phone"), c.get("apikey"))
    elif provider == "twilio":
        c = delivery_cfg.get("twilio", {})
        send_twilio(text, c.get("to"), c.get("account_sid"),
                    c.get("auth_token"), c.get("from"))
    else:
        raise ValueError(f"Unknown delivery provider: {provider}")
