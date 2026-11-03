"""Parsing and validating the YAML config, with environment interpolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercalli.notify.config import (
    Config,
    ConfigError,
    EmailChannel,
    PagerDutyChannel,
    SlackChannel,
    WebhookChannel,
    interpolate,
    load_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / "mercalli.example.yaml"

FULL = """
public_url: https://status.example.com/
stack: [github, cloudflare, github, vercel]
blast_threshold: 0.25
channels:
  - {type: slack, name: ops, url: "${SLACK_URL}", services: [github], min_impact: major}
  - {type: discord, name: dc, url: "${DISCORD_URL:-https://discord.com/api/webhooks/1/x}"}
  - {type: teams, name: tm, url: "https://example.webhook.office.com/x", events: [incident.opened]}
  - {type: webhook, name: hook, url: "https://hooks.example.com/m", secret: "${HOOK_SECRET}",
     headers: {Authorization: "Bearer $${NOT_INTERPOLATED}"}}
  - type: email
    name: mail
    settings: {host: smtp.example.com, security: ssl, from: alerts@example.com, to: "a@example.com, b@example.com"}
  - {type: pagerduty, name: pd, settings: {routing_key: "${PD_KEY}"}}
retention: {polls_days: 3}
"""

ENV = {"SLACK_URL": "https://hooks.slack.com/services/T/B/X", "HOOK_SECRET": "hush", "PD_KEY": "R0UT1NG"}


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "mercalli.yaml"
    path.write_text(text)
    return path


def test_every_channel_type_parses(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, FULL), ENV)
    assert cfg.public_url == "https://status.example.com"
    assert cfg.stack == ["github", "cloudflare", "vercel"]
    assert [ch.type for ch in cfg.channels] == ["slack", "discord", "teams", "webhook", "email", "pagerduty"]
    assert all(ch.active for ch in cfg.channels)
    slack, discord, _, hook, mail, pd = cfg.channels
    assert isinstance(slack, SlackChannel) and slack.url == ENV["SLACK_URL"]
    assert discord.target == "https://discord.com/api/webhooks/1/x"
    assert isinstance(hook, WebhookChannel)
    assert hook.secret == "hush"
    assert hook.headers == {"Authorization": "Bearer ${NOT_INTERPOLATED}"}
    assert isinstance(mail, EmailChannel)
    assert mail.settings.to == ["a@example.com", "b@example.com"]
    assert mail.settings.effective_port == 465
    assert isinstance(pd, PagerDutyChannel)
    assert pd.events == ["incident.opened", "incident.resolved"]
    assert pd.rate.per_minute == 120
    assert cfg.retention.polls_days == 3
    assert cfg.retention.events_days == 30


def test_interpolation_rules() -> None:
    missing: set[str] = set()
    doc = {
        "a": "${SET}",
        "b": "${UNSET:-fb}",
        "c": "${EMPTY:-fb}",
        "d": "${EMPTY-fb}",
        "e": "$$5 and $${X}",
        "f": ["${SET}", 3, None],
        "g": "${UNSET}",
    }
    out = interpolate(doc, {"SET": "v", "EMPTY": ""}, missing)
    assert out == {"a": "v", "b": "fb", "c": "fb", "d": "", "e": "$5 and ${X}", "f": ["v", 3, None], "g": ""}
    assert missing == {"UNSET"}


def test_unset_variables_are_named_together(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="HOOK_SECRET, PD_KEY, SLACK_URL"):
        load_config(write(tmp_path, FULL), {})


def test_errors_never_echo_secret_values(tmp_path: Path) -> None:
    text = "channels:\n  - {type: slack, name: s, url: 'ftp://xoxb-very-secret@example.com'}\n"
    with pytest.raises(ConfigError) as err:
        load_config(write(tmp_path, text), {})
    assert "channels.0.slack.url" in str(err.value)
    assert "very-secret" not in str(err.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("channels:\n  - {type: slack, name: s, url: '', enabled: true}\n", "enabled but url is empty"),
        ("stack: [github]\nchannels:\n  - {type: slack, name: s, services: [stripe]}\n", "not in the stack: stripe"),
        ("channels:\n  - {type: slack, name: s}\n  - {type: discord, name: s}\n", "two channels are named 's'"),
        ("channels:\n  - {type: sms, name: s}\n", "channels.0"),
        ("blast_threshold: 2\n", "blast_threshold"),
        ("stack: [github]\nunknown_key: 1\n", "unknown_key"),
        ("channels:\n  - {type: email, name: m, settings: {host: smtp.x, to: [a@b.c]}}\n", "needs settings.from"),
        ("- just\n- a list\n", "mapping at the top level"),
        ("stack: [unclosed\n", "not valid YAML"),
    ],
)
def test_invalid_configs_are_explained(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text), {})


def test_channels_without_a_destination_stay_off() -> None:
    cfg = Config.model_validate(
        {
            "channels": [
                {"type": "slack", "name": "s", "url": ""},
                {"type": "pagerduty", "name": "p", "settings": {"routing_key": ""}},
                {"type": "email", "name": "e", "settings": {"host": "smtp.x", "to": []}},
                {"type": "webhook", "name": "w", "url": "https://h.example.com", "enabled": False},
            ]
        }
    )
    assert cfg.active_channels == []


def test_the_shipped_example_loads(tmp_path: Path) -> None:
    quiet = load_config(EXAMPLE, {})
    assert quiet.active_channels == []
    assert quiet.public_url == "http://localhost:8080"
    assert "github" in quiet.stack
    live = load_config(
        EXAMPLE,
        {
            "MERCALLI_WEBHOOK_URL": "http://receiver:9000/hook",
            "MERCALLI_WEBHOOK_SECRET": "x",
            "PAGERDUTY_ROUTING_KEY": "key",
        },
    )
    assert [ch.name for ch in live.active_channels] == ["webhook", "pagerduty"]
