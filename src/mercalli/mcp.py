"""A Model Context Protocol server, so coding and operations agents can check outages before acting.

It speaks MCP over stdio (JSON-RPC 2.0, one message per line) with no dependencies beyond httpx, and
reads a Mercalli API: the public one by default, or a self-hosted server.

Tools:
- `outages_now`: every open incident, worst first, with forecasts.
- `check_services`: health, open incidents and forecasts for a list of services.
- `incident_detail`: one incident's timeline, forecast history and likely blast radius.
- `dependencies`: what a service depends on and what depends on it.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from mercalli import __version__

PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "outages_now",
        "description": "List incidents open right now across all watched internet services, worst first, "
        "with forecasts of when each will be resolved. Use before deploying or when something "
        "external seems broken.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_impact": {"type": "string", "enum": ["minor", "major", "critical"], "default": "minor"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "check_services",
        "description": "Check the health of specific services by id (for example github, aws, cloudflare, "
        "openai, vercel, stripe-like payment providers). Returns health, open incidents and forecasts.",
        "inputSchema": {
            "type": "object",
            "required": ["services"],
            "properties": {
                "services": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
            },
        },
    },
    {
        "name": "incident_detail",
        "description": "Full detail for one incident id such as 'github:abc123': every vendor update, how the "
        "forecast evolved, similar past incidents and services likely to be affected.",
        "inputSchema": {
            "type": "object",
            "required": ["incident_id"],
            "properties": {"incident_id": {"type": "string"}},
        },
    },
    {
        "name": "dependencies",
        "description": "What a service depends on (DNS, edge network, cloud, providers it has named in its "
        "own incident reports) and which services depend on it.",
        "inputSchema": {"type": "object", "required": ["service"], "properties": {"service": {"type": "string"}}},
    },
]

_RANK = {"none": 0, "maintenance": 0, "minor": 1, "major": 2, "critical": 3}


class Server:
    def __init__(self, api: str, client: httpx.Client | None = None) -> None:
        self.api = api.rstrip("/")
        self.http = client or httpx.Client(timeout=15, headers={"User-Agent": f"mercalli-mcp/{__version__}"})

    def _get(self, path: str) -> Any:
        resp = self.http.get(f"{self.api}/{path}")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _brief(inc: dict[str, Any]) -> dict[str, Any]:
        f = inc.get("forecast")
        return {
            "id": inc["id"],
            "service": inc["service_name"],
            "title": inc["title"],
            "impact": inc["impact"],
            "status": inc["status"],
            "started_at": inc["started_at"],
            "elapsed_min": inc["elapsed_min"],
            "latest_update": (inc.get("latest") or {}).get("body", "")[:400],
            "forecast": {"likely_resolved_by": f["eta_p50"], "range_80": [f["eta_p10"], f["eta_p90"]]} if f else None,
            "likely_affected": [b["service_name"] for b in (inc.get("blast") or [])[:5]],
            "vendor_link": inc.get("url"),
        }

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "outages_now":
            ov = self._get("overview.json")
            floor = _RANK.get(args.get("min_impact", "minor"), 1)
            active = [i for i in ov["active"] if _RANK.get(i["impact"], 0) >= floor]
            return {
                "generated_at": ov["generated_at"],
                "intensity": ov.get("intensity"),
                "open": [self._brief(i) for i in active[: int(args.get("limit", 20))]],
            }
        if name == "check_services":
            ov = self._get("overview.json")
            wanted = [s.lower() for s in args["services"]]
            board = {s["id"]: s for s in ov["services"]}
            incidents = ov["active"] + ov.get("long_running", []) + ov.get("notices", [])
            out = {}
            for sid in wanted:
                svc = board.get(sid)
                if svc is None:
                    matches = [s["id"] for s in ov["services"] if sid in s["id"] or sid in s["name"].lower()][:5]
                    out[sid] = {"error": "unknown service id", "did_you_mean": matches}
                    continue
                out[sid] = {
                    "name": svc["name"],
                    "health": svc["health"],
                    "open_incidents": [self._brief(i) for i in incidents if i["service_id"] == sid],
                }
            return {"generated_at": ov["generated_at"], "services": out}
        if name == "incident_detail":
            service, _, source = str(args["incident_id"]).partition(":")
            doc = self._get(f"incidents/{service}/{source}.json")
            return {
                **self._brief(doc),
                "updates": doc.get("updates", []),
                "forecast_history": doc.get("forecast_history", []),
                "similar": doc.get("similar", []),
                "blast": doc.get("blast"),
            }
        if name == "dependencies":
            doc = self._get(f"services/{args['service']}.json")
            return {
                "service": doc["service"]["name"],
                "depends_on": doc.get("depends_on", []),
                "dependents": doc.get("dependents", [])[:50],
            }
        raise ValueError(f"unknown tool {name}")

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notifications need no reply
            return None
        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mercalli", "version": __version__},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = msg.get("params") or {}
                try:
                    data = self.call(params.get("name", ""), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(data, indent=1)}], "isError": False}
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    result = {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        except Exception as exc:  # never crash the agent's session
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_stdio(api: str) -> None:
    server = Server(api)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = server.handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
