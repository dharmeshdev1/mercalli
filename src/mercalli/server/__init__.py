"""The self-hosted edition: a long running server with a live API, a live stream and alerts.

Nothing is imported here on purpose. The main CLI imports `mercalli.server.cli` to register
`serve`, and that must stay cheap and work on installs without the `server` extra; the app itself
is imported from `mercalli.server.app` when the command runs.
"""
