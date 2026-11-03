"""`python -m mercalli.server serve ...`, the server without going through the main CLI."""

import typer

from mercalli.server.cli import register

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Mercalli self-hosted server."""


register(app)

if __name__ == "__main__":
    app()
