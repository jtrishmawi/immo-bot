"""
immo-bot — WhatsApp adapter (Baileys sidecar).

Calls the Node.js Baileys service via HTTP.
No direct WhatsApp connection here — all protocol handled by the sidecar.
"""
import re
import logging
import requests as http

logger = logging.getLogger(__name__)

_sidecar_warned = False  # log unreachable sidecar once per session, then silence

_HTML_BOLD = re.compile(r"<b>(.*?)</b>", re.S)
_HTML_LINK = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_HTML_TAG  = re.compile(r"<[^>]+>")


def to_whatsapp(html: str) -> str:
    """Convert Telegram HTML formatting to WhatsApp markdown."""
    text = _HTML_BOLD.sub(r"*\1*", html)
    text = _HTML_LINK.sub(r"\2 (\1)", text)
    text = _HTML_TAG.sub("", text)
    return text.strip()


def send(text: str, service_url: str, to: str, media_url: str = None) -> bool:
    """POST a message to the Baileys sidecar. Returns True on success."""
    global _sidecar_warned
    payload = {"to": to, "text": to_whatsapp(text)}
    if media_url:
        payload["mediaUrl"] = media_url
    try:
        r = http.post(f"{service_url}/send", json=payload, timeout=15)
        if r.ok:
            _sidecar_warned = False  # reset if sidecar becomes reachable again
            return True
        logger.error("WhatsApp send failed %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        if not _sidecar_warned:
            logger.warning("WhatsApp sidecar not reachable at %s — silencing further errors this session", service_url)
            _sidecar_warned = True
    return False


def health(service_url: str) -> str:
    """Return sidecar status: 'connected', 'pairing_pending', or 'disconnected'."""
    try:
        r = http.get(f"{service_url}/health", timeout=5)
        return r.json().get("status", "disconnected") if r.ok else "disconnected"
    except Exception:
        return "disconnected"
