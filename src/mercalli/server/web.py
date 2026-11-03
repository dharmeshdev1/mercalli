"""The exported web app (a Next.js static export with trailing slashes).

Starlette's StaticFiles in html mode already does what such an export needs: a directory URL
serves its index.html, a directory URL without the slash redirects to add it, and anything missing
gets 404.html with a 404 status. This adds cache headers. Next.js puts a content hash in every
file under _next/static, so those can be cached forever, while pages and the RSC payloads next to
them change with every build and must be revalidated.
"""

from __future__ import annotations

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
SHORT = "public, max-age=3600"


def cache_control(path: str, response: Response) -> str:
    if path.startswith("_next/static/") and response.status_code < 300:
        return IMMUTABLE
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 300 or content_type.startswith("text/html") or path.endswith((".txt", ".html", "/")):
        return REVALIDATE
    if path in ("", "."):
        return REVALIDATE
    return SHORT


class WebApp(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if "cache-control" not in response.headers:
            response.headers["Cache-Control"] = cache_control(path, response)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
