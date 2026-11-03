# Security

Please report vulnerabilities privately through GitHub's security advisory form for this repository
(Security tab, "Report a vulnerability"), not in public issues.

## What Mercalli trusts and what it does not

- **Status page responses are untrusted input.** They are parsed as JSON or XML, never executed,
  and incident text is rendered as text in the web app, never as HTML.
- **The public edition has no server.** It is static files on GitHub Pages, so the usual server
  side attacks do not apply. Its only credential is the workflow's own scoped token.
- **The self-hosted server** keeps alert secrets (webhook URLs, SMTP and PagerDuty keys) in its
  config through environment variables. Generic webhooks are signed with HMAC SHA-256 so receivers
  can verify that a request came from your Mercalli instance.
- **Model artifacts** are loaded with joblib, which can execute code. Only load artifacts you built
  or that come from this repository's own `artifacts` release.

## Supported versions

Fixes land on `main` and in the next tagged release.
