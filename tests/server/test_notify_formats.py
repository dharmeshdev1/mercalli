"""What each channel receives: Slack blocks, Discord embeds, Teams cards, webhooks, email and PagerDuty."""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
from dataclasses import replace
from email.message import EmailMessage
from typing import Any, ClassVar

import httpx
import pytest
import respx

from mercalli.api import ApiBuilder
from mercalli.notify.channels import (
    EmailTransport,
    PagerDutyTransport,
    WebhookTransport,
    classify,
    discord_payload,
    pagerduty_events,
    slack_payload,
    teams_payload,
)
from mercalli.notify.config import Config, EmailChannel, PagerDutyChannel, WebhookChannel
from mercalli.notify.events import Summaries
from mercalli.notify.message import RESOLVED_COLOR, Message, build_message, fmt_minutes, forecast_sentence
from mercalli.store import Database, EventRow

from .support import NOW, SITE

BLAST = {
    "upstream_incident_id": "cloudflare:cf1",
    "service_id": "vercel",
    "probability": 0.42,
    "evidence": ["edge:cloudflare", "co-failed 3 of 41 times"],
}


def message(db: Database, kind: str, incident_id: str, payload: dict[str, Any] | None = None) -> Message:
    service = incident_id.partition(":")[0] if kind != "blast" else "vercel"
    ev = EventRow(id=42, at=NOW, kind=kind, service_id=service, incident_id=incident_id, payload=payload or {})
    summaries = Summaries(db, ApiBuilder(db, now=NOW, edition="self-hosted"))
    subject = (payload or {}).get("upstream_incident_id") or incident_id
    return build_message(ev, summaries.get(subject), name_of=summaries.service_name, public_url=SITE, now=NOW)


def test_an_opened_message_says_what_where_how_bad_and_how_long(db: Database) -> None:
    msg = message(
        db,
        "incident.opened",
        "github:gh1",
        {"title": "Elevated errors on Actions", "impact": "minor", "status": "investigating"},
    )
    assert msg.headline == "New incident: GitHub"
    assert msg.lead == "Elevated errors on Actions"
    assert msg.body == "We are investigating reports of degraded performance for Actions."
    facts = dict(msg.facts)
    assert facts["Impact"] == "minor"
    assert facts["Status"] == "investigating"
    assert facts["Started"] == "2026-11-03 11:20 UTC (40 min ago)"
    assert facts["Components"] == "Actions"
    assert facts["Forecast"] == "Likely resolved in about 25 min (80% range 10 min to 1 h 30 min), around 12:25 UTC."
    assert msg.links == [
        ("Vendor incident page", "https://stspg.io/gh1"),
        ("Open in Mercalli", f"{SITE}/incident/?id=github:gh1"),
    ]
    assert msg.severity == "warning"


def test_resolved_and_blast_messages(db: Database) -> None:
    resolved = message(db, "incident.resolved", "vercel:vc1", {"title": "Build delays", "impact": "minor"})
    assert resolved.headline == "Resolved: Vercel"
    assert dict(resolved.facts)["Duration"] == "2 h"
    assert resolved.color == RESOLVED_COLOR
    assert not resolved.is_open
    blast = message(db, "blast", "cloudflare:cf1", BLAST)
    assert blast.headline == "May be affected: Vercel"
    assert blast.lead == "Cloudflare incident: Elevated 5xx errors in Frankfurt"
    assert dict(blast.facts)["Probability"] == "42%"
    assert dict(blast.facts)["Evidence"] == "edge:cloudflare; co-failed 3 of 41 times"
    assert blast.dedupe_key == "blast:cloudflare:cf1:vercel"


def test_slack_blocks_escape_vendor_text(db: Database) -> None:
    msg = replace(message(db, "incident.opened", "github:gh1"), lead="Outage <!channel> & <b>more</b>")
    payload = slack_payload(msg)
    header, section, fields, context = payload["blocks"]
    assert header == {"type": "header", "text": {"type": "plain_text", "text": "New incident: GitHub", "emoji": False}}
    assert section["text"]["text"].startswith("*Outage &lt;!channel&gt; &amp; &lt;b&gt;more&lt;/b&gt;*\n")
    assert {"type": "mrkdwn", "text": "*Impact*\nminor"} in fields["fields"]
    assert context["elements"][0]["text"] == (
        f"<https://stspg.io/gh1|Vendor incident page>   <{SITE}/incident/?id=github:gh1|Open in Mercalli>"
    )
    assert payload["text"] == "New incident: GitHub: Outage &lt;!channel&gt; &amp; &lt;b&gt;more&lt;/b&gt;"
    long = slack_payload(replace(msg, headline="x" * 400))
    assert len(long["blocks"][0]["text"]["text"]) == 150


def test_discord_embed_cannot_ping_anyone(db: Database) -> None:
    msg = message(db, "incident.opened", "cloudflare:cf1")
    payload = discord_payload(msg)
    assert payload["allowed_mentions"] == {"parse": []}
    (embed,) = payload["embeds"]
    assert embed["title"] == "New incident: Cloudflare"
    assert embed["color"] == 0xE65100
    assert embed["url"] == f"{SITE}/incident/?id=cloudflare:cf1"
    assert {"name": "Impact", "value": "major", "inline": True} in embed["fields"]
    assert embed["timestamp"] == "2026-11-03T12:00:00Z"


def test_teams_gets_an_adaptive_card(db: Database) -> None:
    payload = teams_payload(message(db, "incident.opened", "cloudflare:cf1"))
    (attachment,) = payload["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert (card["type"], card["version"]) == ("AdaptiveCard", "1.4")
    assert card["body"][0]["text"] == "New incident: Cloudflare"
    assert card["body"][0]["color"] == "Attention"
    facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
    assert {"title": "Status", "value": "identified"} in facts
    assert [a["title"] for a in card["actions"]] == ["Vendor incident page", "Open in Mercalli"]


async def test_webhook_body_is_signed_with_hmac_sha256(db: Database) -> None:
    channel = WebhookChannel(
        type="webhook",
        name="hook",
        url="https://hooks.example.com/in",
        secret="s3cret",
        headers={"Authorization": "Bearer t"},
    )
    msg = message(db, "blast", "cloudflare:cf1", BLAST)
    async with httpx.AsyncClient() as http:
        with respx.mock() as mock:
            route = mock.post("https://hooks.example.com/in").mock(return_value=httpx.Response(202))
            outcome = await WebhookTransport(channel, http).send(msg)
    assert outcome.ok
    request = route.calls[0].request
    digest = hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-mercalli-signature"] == f"sha256={digest}"
    assert request.headers["x-mercalli-event"] == "blast"
    assert request.headers["x-mercalli-delivery"] == "42"
    assert request.headers["authorization"] == "Bearer t"
    body = json.loads(request.content)
    assert body["event"] == "blast"
    assert body["target"] == {"id": "vercel", "name": "Vercel"}
    assert body["incident"]["id"] == "cloudflare:cf1"
    assert body["data"]["probability"] == 0.42
    assert body["message"]["title"] == "May be affected: Vercel"


class FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []
    calls: ClassVar[list[str]] = []
    fail: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        FakeSMTP.calls.append(f"connect {host}:{port}")

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        FakeSMTP.calls.append("quit")

    def starttls(self, context: object) -> None:
        FakeSMTP.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        FakeSMTP.calls.append(f"login {user}")

    def send_message(self, msg: EmailMessage) -> None:
        if FakeSMTP.fail is not None:
            raise FakeSMTP.fail
        FakeSMTP.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail = [], [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


async def test_email_has_text_and_html_and_threads_by_incident(db: Database, fake_smtp: type[FakeSMTP]) -> None:
    channel = EmailChannel.model_validate(
        {
            "type": "email",
            "name": "mail",
            "settings": {
                "host": "smtp.example.com",
                "username": "u",
                "password": "p",
                "from": "alerts@example.com",
                "to": ["oncall@example.com"],
            },
        }
    )
    transport = EmailTransport(channel)
    assert (await transport.send(message(db, "incident.opened", "github:gh1"))).ok
    assert (await transport.send(message(db, "incident.resolved", "github:gh1"))).ok
    assert fake_smtp.calls[:4] == ["connect smtp.example.com:587", "starttls", "login u", "quit"]
    first, second = fake_smtp.sent
    assert first["Subject"] == "[Mercalli] New incident: GitHub: Elevated errors on Actions"
    assert (first["From"], first["To"]) == ("alerts@example.com", "oncall@example.com")
    assert first["References"] == second["References"]
    assert first["Message-ID"] != second["Message-ID"]
    plain = first.get_body(("plain",))
    html = first.get_body(("html",))
    assert plain is not None and "Forecast: Likely resolved in about 25 min" in plain.get_content()
    assert html is not None and "Elevated errors on Actions" in html.get_content()


async def test_email_errors_say_whether_to_retry(db: Database, fake_smtp: type[FakeSMTP]) -> None:
    channel = EmailChannel.model_validate(
        {
            "type": "email",
            "name": "mail",
            "settings": {"host": "smtp.example.com", "from": "a@example.com", "to": ["b@example.com"]},
        }
    )
    msg = message(db, "incident.opened", "github:gh1")
    fake_smtp.fail = smtplib.SMTPResponseException(451, b"try later")
    assert (await EmailTransport(channel).send(msg)).retryable
    fake_smtp.fail = smtplib.SMTPResponseException(550, b"no such user")
    outcome = await EmailTransport(channel).send(msg)
    assert (outcome.ok, outcome.retryable, outcome.detail) == (False, False, "SMTP 550")


def test_pagerduty_triggers_on_open_and_resolves_everything_it_raised(db: Database) -> None:
    opened = message(
        db,
        "incident.opened",
        "cloudflare:cf1",
        {"title": "Elevated 5xx errors in Frankfurt", "impact": "major", "status": "identified"},
    )
    (trigger,) = pagerduty_events(opened, "KEY")
    assert trigger["routing_key"] == "KEY"
    assert trigger["event_action"] == "trigger"
    assert trigger["dedup_key"] == "cloudflare:cf1"
    assert trigger["client_url"] == f"{SITE}/incident/?id=cloudflare:cf1"
    payload = trigger["payload"]
    assert payload["summary"] == "New incident: Cloudflare: Elevated 5xx errors in Frankfurt"
    assert (payload["severity"], payload["source"], payload["component"]) == ("error", "stspg.io", "CDN/Cache")
    assert payload["custom_details"]["impact"] == "major"
    blast = pagerduty_events(message(db, "blast", "cloudflare:cf1", BLAST), "KEY")
    assert blast[0]["dedup_key"] == "blast:cloudflare:cf1:vercel"
    assert blast[0]["payload"]["severity"] == "warning"
    resolved = replace(
        message(db, "incident.resolved", "cloudflare:cf1"), resolve_keys=("blast:cloudflare:cf1:vercel",)
    )
    assert pagerduty_events(resolved, "KEY") == [
        {"routing_key": "KEY", "event_action": "resolve", "dedup_key": "cloudflare:cf1"},
        {"routing_key": "KEY", "event_action": "resolve", "dedup_key": "blast:cloudflare:cf1:vercel"},
    ]


def test_pagerduty_never_reopens_a_finished_incident(db: Database) -> None:
    channel = PagerDutyChannel.model_validate({"type": "pagerduty", "name": "pd", "settings": {"routing_key": "K"}})
    transport = PagerDutyTransport(channel, httpx.AsyncClient())
    late_update = message(db, "incident.updated", "vercel:vc1", {"impact": "minor", "status": "monitoring"})
    assert pagerduty_events(late_update, "K") == []
    assert not transport.wants(late_update)


def test_http_results_are_classified() -> None:
    def resp(code: int, **headers: str) -> httpx.Response:
        return httpx.Response(code, headers=headers, text="no_service" if code == 404 else "")

    assert classify(resp(204)).ok
    limited = classify(resp(429, **{"Retry-After": "7"}))
    assert (limited.ok, limited.retryable, limited.retry_after) == (False, True, 7.0)
    assert classify(resp(503)).retryable
    gone = classify(resp(404))
    assert (gone.retryable, gone.detail) == (False, "HTTP 404 no_service")


def test_durations_and_forecasts_read_naturally() -> None:
    assert [fmt_minutes(m) for m in (0.4, 41, 60, 125, 1440, 1530)] == [
        "0 min",
        "41 min",
        "1 h",
        "2 h 5 min",
        "1 d",
        "1 d 1 h",
    ]
    assert forecast_sentence({"p50": 41}, NOW) == "Likely resolved in about 41 min."
    assert forecast_sentence({}, NOW) is None
    eta = "2026-11-04T09:30:00Z"
    assert forecast_sentence({"p10": 600, "p50": 1290, "p90": 2000, "eta_p50": eta}, NOW) == (
        "Likely resolved in about 21 h 30 min (80% range 10 h to 1 d 9 h), around Nov 04 09:30 UTC."
    )


def test_a_config_drives_message_links() -> None:
    assert Config.model_validate({"public_url": "https://m.example.com/"}).public_url == "https://m.example.com"
