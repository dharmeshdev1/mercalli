"""How a Message looks on each channel, and how it gets there.

Formatting functions are pure so tests can pin every payload. Transports do the sending and turn
every result into an Outcome that says whether trying again could help.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parsedate_to_datetime
from html import escape
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import orjson

from mercalli.domain import utcnow
from mercalli.notify.config import (
    ChannelBase,
    DiscordChannel,
    EmailChannel,
    EmailSettings,
    PagerDutyChannel,
    SlackChannel,
    TeamsChannel,
    WebhookChannel,
)
from mercalli.notify.message import Message, thread_token

JSON_HEADERS = {"Content-Type": "application/json"}


@dataclass(frozen=True)
class Outcome:
    ok: bool
    detail: str
    retryable: bool = False
    retry_after: float | None = None


class Transport(Protocol):
    def wants(self, msg: Message) -> bool: ...

    async def send(self, msg: Message) -> Outcome: ...


# Formatting ---------------------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _slack_text(text: str) -> str:
    # Slack treats these three as markup; escaping them also stops vendor text from pinging @channel.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_link(url: str, label: str) -> str:
    return f"<{url.replace('|', '%7C').replace('>', '%3E')}|{_slack_text(label)}>"


def slack_payload(msg: Message) -> dict[str, Any]:
    """An incoming webhook message with Block Kit blocks and a plain fallback for notifications."""
    section = f"*{_slack_text(_cut(msg.lead, 300))}*"
    if msg.body:
        section += "\n" + _slack_text(_cut(msg.body, 2500))
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": _cut(msg.headline, 150), "emoji": False}},
        {"type": "section", "text": {"type": "mrkdwn", "text": section}},
    ]
    if msg.facts:
        fields = [f"*{_slack_text(k)}*\n{_slack_text(_cut(v, 1900))}" for k, v in msg.facts[:10]]
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields]})
    if msg.links:
        links = "   ".join(_slack_link(url, label) for label, url in msg.links)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": links}]})
    return {
        # The fallback drives push notifications and is parsed as mrkdwn too, so it is escaped as well.
        "text": _slack_text(_cut(_one_line(f"{msg.headline}: {msg.lead}"), 300)),
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def discord_payload(msg: Message, username: str = "Mercalli") -> dict[str, Any]:
    """A webhook message with one embed. Mentions are disabled so vendor text cannot ping anyone."""
    description = f"**{_cut(msg.lead, 300)}**"
    if msg.body:
        description += "\n" + _cut(msg.body, 3000)
    if msg.links:
        description += "\n\n" + " | ".join(f"[{label}]({url})" for label, url in msg.links)
    embed: dict[str, Any] = {
        "title": _cut(msg.headline, 256),
        "description": _cut(description, 4096),
        "color": msg.color,
        "fields": [
            {"name": _cut(k, 256), "value": _cut(v, 1024) or "n/a", "inline": len(v) <= 40} for k, v in msg.facts[:25]
        ],
        "timestamp": _iso(msg.at),
        "footer": {"text": f"Mercalli {msg.kind}"},
    }
    if msg.mercalli_url or msg.vendor_url:
        embed["url"] = msg.mercalli_url or msg.vendor_url
    return {"username": username, "embeds": [embed], "allowed_mentions": {"parse": []}}


def _teams_color(msg: Message) -> str:
    if msg.kind == "incident.resolved":
        return "Good"
    if msg.kind in ("forecast", "blast"):
        return "Accent"
    return {"critical": "Attention", "major": "Attention", "minor": "Warning"}.get(msg.impact, "Default")


def teams_payload(msg: Message) -> dict[str, Any]:
    """An Adaptive Card, accepted by Teams incoming webhooks and by Workflows webhook triggers."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": msg.headline,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "color": _teams_color(msg),
        },
        {"type": "TextBlock", "text": _cut(msg.lead, 300), "weight": "Bolder", "wrap": True, "spacing": "Small"},
    ]
    if msg.body:
        body.append({"type": "TextBlock", "text": _cut(msg.body, 2000), "wrap": True})
    if msg.facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": _cut(v, 500)} for k, v in msg.facts]})
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if msg.links:
        card["actions"] = [{"type": "Action.OpenUrl", "title": label, "url": url} for label, url in msg.links]
    attachment = {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
    return {"type": "message", "attachments": [attachment]}


def webhook_body(msg: Message, sent_at: datetime) -> bytes:
    """The JSON a generic webhook receives. These exact bytes are what the signature covers."""
    doc: dict[str, Any] = {
        "event": msg.kind,
        "event_id": msg.event_id,
        "occurred_at": _iso(msg.at),
        "sent_at": _iso(sent_at),
        "service": {"id": msg.service_id, "name": msg.service_name},
        "incident": msg.summary,
        "data": msg.payload,
        "message": {"title": msg.headline, "text": msg.text},
        "links": {"vendor": msg.vendor_url, "mercalli": msg.mercalli_url},
    }
    if msg.kind == "blast":
        doc["target"] = {"id": msg.target_service_id, "name": msg.target_service_name}
    return orjson.dumps(doc, option=orjson.OPT_NON_STR_KEYS)


def signature(secret: str, body: bytes) -> str:
    """The X-Mercalli-Signature value: an HMAC SHA-256 of the raw body, hex encoded."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def email_message(msg: Message, settings: EmailSettings) -> EmailMessage:
    """A plain text and HTML email. Messages about one incident share a thread in mail clients."""
    em = EmailMessage()
    em["Subject"] = _cut(_one_line(f"[Mercalli] {msg.headline}: {msg.lead}"), 200)
    em["From"] = settings.sender
    em["To"] = ", ".join(settings.to)
    em["Date"] = format_datetime(utcnow())
    domain = settings.sender.rpartition("@")[2] or "mercalli.invalid"
    em["Message-ID"] = make_msgid(idstring=f"mercalli.{msg.event_id}", domain=domain)
    em["References"] = f"<incident.{thread_token(msg.incident_id, msg.event_id)}@{domain}>"
    em["X-Mercalli-Event"] = msg.kind
    em.set_content(msg.text)
    em.add_alternative(email_html(msg), subtype="html")
    return em


def email_html(msg: Message) -> str:
    rows = "".join(
        f'<tr><td style="padding:2px 16px 2px 0;color:#555;vertical-align:top">{escape(k)}</td>'
        f'<td style="padding:2px 0">{escape(v)}</td></tr>'
        for k, v in msg.facts
    )
    body = "<br>".join(escape(line) for line in msg.body.splitlines())
    paragraph = f'<p style="margin:0 0 12px">{body}</p>' if body else ""
    anchors = [f'<a href="{escape(url, quote=True)}">{escape(label)}</a>' for label, url in msg.links]
    links = " &nbsp;|&nbsp; ".join(anchors)
    return (
        "<!doctype html><html><body style=\"font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
        'font-size:14px;color:#1a1a1a;line-height:1.45">'
        f'<div style="border-left:4px solid #{msg.color:06x};padding:4px 0 4px 14px">'
        f'<h2 style="margin:0 0 6px;font-size:18px">{escape(msg.headline)}</h2>'
        f'<p style="margin:0 0 12px;font-weight:600">{escape(msg.lead)}</p>'
        f"{paragraph}"
        f'<table style="border-collapse:collapse;margin:0 0 12px">{rows}</table>'
        f'<p style="margin:0">{links}</p>'
        "</div></body></html>"
    )


def _pager_key(key: str) -> str:
    # The Events API caps dedup keys at 255 characters.
    return key if len(key) <= 255 else "mercalli:" + hashlib.sha256(key.encode()).hexdigest()


def pagerduty_events(msg: Message, routing_key: str) -> list[dict[str, Any]]:
    """Events API v2 events: a trigger keyed by incident, or resolves for every alert it raised."""
    if msg.kind == "incident.resolved":
        keys = [k for k in dict.fromkeys([msg.incident_id or "", *msg.resolve_keys]) if k]
        return [{"routing_key": routing_key, "event_action": "resolve", "dedup_key": _pager_key(k)} for k in keys]
    if not msg.is_open or not msg.incident_id:
        # A trigger for an incident that is already over would open a fresh alert nobody can act on.
        return []
    dedup = f"blast:{msg.incident_id}:{msg.target_service_id}" if msg.kind == "blast" else msg.incident_id
    source = urlsplit(msg.vendor_url).hostname if msg.vendor_url else None
    details: dict[str, Any] = {"service": msg.service_name, "title": msg.title, "event": msg.kind}
    details.update({k.lower(): v for k, v in msg.facts})
    if msg.body:
        details["latest_update"] = _cut(msg.body, 2000)
    payload: dict[str, Any] = {
        "summary": _cut(_one_line(f"{msg.headline}: {msg.lead}"), 1024),
        "source": source or f"mercalli/{msg.service_id}",
        "severity": msg.severity,
        "timestamp": _iso(msg.at),
        "class": msg.kind,
        "custom_details": details,
    }
    if msg.components:
        payload["component"] = msg.components[0]
    if msg.category:
        payload["group"] = msg.category
    event: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": _pager_key(dedup),
        "payload": payload,
        "client": "Mercalli",
        "links": [{"href": url, "text": label} for label, url in msg.links],
    }
    if msg.mercalli_url:
        event["client_url"] = msg.mercalli_url
    return [event]


# Delivery -----------------------------------------------------------------------------------------


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def classify(resp: httpx.Response) -> Outcome:
    code = resp.status_code
    if 200 <= code < 300:
        return Outcome(True, f"HTTP {code}")
    snippet = _cut(_one_line(resp.text), 160)
    detail = f"HTTP {code} {snippet}".rstrip()
    retryable = code in (408, 425, 429) or code >= 500
    return Outcome(False, detail, retryable=retryable, retry_after=_retry_after(resp) if retryable else None)


class _HttpTransport:
    def __init__(self, channel: ChannelBase, http: httpx.AsyncClient) -> None:
        self.channel = channel
        self.http = http

    def wants(self, msg: Message) -> bool:
        return True

    async def _post(self, url: str, content: bytes, headers: dict[str, str]) -> Outcome:
        try:
            resp = await self.http.post(url, content=content, headers=headers, timeout=self.channel.timeout)
        except httpx.TimeoutException:
            return Outcome(False, "timed out", retryable=True)
        except httpx.TransportError as exc:
            return Outcome(False, type(exc).__name__, retryable=True)
        except httpx.HTTPError as exc:
            return Outcome(False, type(exc).__name__)
        return classify(resp)


class SlackTransport(_HttpTransport):
    channel: SlackChannel

    async def send(self, msg: Message) -> Outcome:
        return await self._post(self.channel.url, orjson.dumps(slack_payload(msg)), JSON_HEADERS)


class DiscordTransport(_HttpTransport):
    channel: DiscordChannel

    async def send(self, msg: Message) -> Outcome:
        payload = discord_payload(msg, self.channel.username)
        return await self._post(self.channel.url, orjson.dumps(payload), JSON_HEADERS)


class TeamsTransport(_HttpTransport):
    channel: TeamsChannel

    async def send(self, msg: Message) -> Outcome:
        return await self._post(self.channel.url, orjson.dumps(teams_payload(msg)), JSON_HEADERS)


class WebhookTransport(_HttpTransport):
    channel: WebhookChannel

    async def send(self, msg: Message) -> Outcome:
        body = webhook_body(msg, utcnow())
        headers = {
            **self.channel.headers,
            "Content-Type": "application/json",
            "X-Mercalli-Event": msg.kind,
            "X-Mercalli-Delivery": str(msg.event_id),
        }
        if self.channel.secret:
            headers["X-Mercalli-Signature"] = signature(self.channel.secret, body)
        return await self._post(self.channel.url, body, headers)


class PagerDutyTransport(_HttpTransport):
    channel: PagerDutyChannel

    def wants(self, msg: Message) -> bool:
        return bool(pagerduty_events(msg, self.channel.settings.routing_key))

    async def send(self, msg: Message) -> Outcome:
        details = []
        for event in pagerduty_events(msg, self.channel.settings.routing_key):
            out = await self._post(self.channel.settings.url, orjson.dumps(event), JSON_HEADERS)
            details.append(f"{event['event_action']} {event['dedup_key']}: {out.detail}")
            if not out.ok:
                # Triggers and resolves are idempotent per dedup key, so resending the whole set is safe.
                return Outcome(False, "; ".join(details), out.retryable, out.retry_after)
        return Outcome(True, "; ".join(details))


class EmailTransport:
    def __init__(self, channel: EmailChannel) -> None:
        self.channel = channel

    def wants(self, msg: Message) -> bool:
        return True

    async def send(self, msg: Message) -> Outcome:
        message = email_message(msg, self.channel.settings)
        try:
            await asyncio.to_thread(send_email, self.channel.settings, message)
        except smtplib.SMTPRecipientsRefused:
            return Outcome(False, "every recipient was refused")
        except smtplib.SMTPResponseException as exc:
            return Outcome(False, f"SMTP {exc.smtp_code}", retryable=exc.smtp_code < 500)
        except (smtplib.SMTPException, OSError) as exc:
            return Outcome(False, type(exc).__name__, retryable=True)
        return Outcome(True, "accepted by the mail server")


def send_email(settings: EmailSettings, message: EmailMessage) -> None:
    """Blocking SMTP delivery, run in a worker thread."""
    context = ssl.create_default_context()
    client: smtplib.SMTP
    if settings.security == "ssl":
        client = smtplib.SMTP_SSL(settings.host, settings.effective_port, timeout=settings.timeout, context=context)
    else:
        client = smtplib.SMTP(settings.host, settings.effective_port, timeout=settings.timeout)
    with client:
        if settings.security == "starttls":
            client.starttls(context=context)
        if settings.username:
            client.login(settings.username, settings.password)
        client.send_message(message)


def transport_for(channel: ChannelBase, http: httpx.AsyncClient) -> Transport:
    match channel:
        case SlackChannel():
            return SlackTransport(channel, http)
        case DiscordChannel():
            return DiscordTransport(channel, http)
        case TeamsChannel():
            return TeamsTransport(channel, http)
        case WebhookChannel():
            return WebhookTransport(channel, http)
        case PagerDutyChannel():
            return PagerDutyTransport(channel, http)
        case EmailChannel():
            return EmailTransport(channel)
    raise ValueError(f"no transport for channel type {type(channel).__name__}")
