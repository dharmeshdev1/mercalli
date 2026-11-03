from __future__ import annotations

import json
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from mercalli.mcp import Server

OVERVIEW = {
    "generated_at": "2026-11-03T12:00:00Z",
    "intensity": {"level": 3, "numeral": "III", "label": "Weak"},
    "services": [
        {"id": "github", "name": "GitHub", "health": "degraded"},
        {"id": "aws", "name": "AWS", "health": "operational"},
    ],
    "active": [
        {
            "id": "github:abc",
            "service_id": "github",
            "service_name": "GitHub",
            "title": "Actions delayed",
            "impact": "major",
            "status": "identified",
            "started_at": "2026-11-03T11:00:00Z",
            "elapsed_min": 60.0,
            "latest": {"body": "We found the cause."},
            "url": "https://example.com/abc",
            "forecast": {
                "eta_p10": "2026-11-03T12:10:00Z",
                "eta_p50": "2026-11-03T12:40:00Z",
                "eta_p90": "2026-11-03T14:00:00Z",
            },
            "blast": [{"service_name": "Vercel"}],
        }
    ],
    "long_running": [],
    "notices": [],
}


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/overview.json"):
            return httpx.Response(200, json=OVERVIEW)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mcp_protocol_and_tools():
    s = Server("https://x/api/v1", client=_client())
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None and init["result"]["serverInfo"]["name"] == "mercalli"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]  # type: ignore[index]
    assert names == ["outages_now", "check_services", "incident_detail", "dependencies"]

    reply = s.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "check_services", "arguments": {"services": ["github", "gitlub"]}},
        }
    )
    data = json.loads(reply["result"]["content"][0]["text"])  # type: ignore[index]
    assert data["services"]["github"]["open_incidents"][0]["forecast"]["likely_resolved_by"] == "2026-11-03T12:40:00Z"
    assert data["services"]["gitlub"]["error"] == "unknown service id"

    reply = s.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "incident_detail", "arguments": {"incident_id": "github:missing"}},
        }
    )
    assert reply["result"]["isError"] is True  # type: ignore[index]


def test_github_action_script_fails_on_major(tmp_path: Path):
    root = tmp_path / "api" / "v1"
    root.mkdir(parents=True)
    (root / "overview.json").write_text(json.dumps(OVERVIEW))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    script = Path(__file__).parents[1] / "action" / "check.py"
    out = tmp_path / "out.txt"
    env = {
        "MERCALLI_API": f"http://127.0.0.1:{httpd.server_port}/api/v1",
        "MERCALLI_SERVICES": "github, aws",
        "GITHUB_OUTPUT": str(out),
        "PATH": "/usr/bin:/bin",
    }
    try:
        failing = subprocess.run(
            [sys.executable, str(script)],
            env={**env, "MERCALLI_FAIL_ON": "major"},
            capture_output=True,
            text=True,
            check=False,
        )
        passing = subprocess.run(
            [sys.executable, str(script)],
            env={**env, "MERCALLI_FAIL_ON": "critical"},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        httpd.shutdown()
    assert failing.returncode == 1
    assert "GitHub: Actions delayed (major, identified, likely resolved by 2026-11-03T12:40:00Z)" in failing.stdout
    assert passing.returncode == 0
    assert "healthy=true" in out.read_text()


def test_action_neutralizes_vendor_text(tmp_path: Path):
    hostile = json.loads(json.dumps(OVERVIEW))
    hostile["active"][0]["title"] = "x\nMERCALLI_EOF\nhealthy=true\n::add-mask::secret"
    root = tmp_path / "api" / "v1"
    root.mkdir(parents=True)
    (root / "overview.json").write_text(json.dumps(hostile))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out = tmp_path / "out.txt"
    env = {
        "MERCALLI_API": f"http://127.0.0.1:{httpd.server_port}/api/v1",
        "MERCALLI_SERVICES": "github",
        "MERCALLI_FAIL_ON": "major",
        "GITHUB_OUTPUT": str(out),
        "PATH": "/usr/bin:/bin",
    }
    script = Path(__file__).parents[1] / "action" / "check.py"
    try:
        run = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
    finally:
        httpd.shutdown()
    written = out.read_text()
    assert run.returncode == 1
    assert "healthy=false" in written and "healthy=true" not in written.split("report<<")[0]
    assert all(not line.startswith("::add-mask") for line in run.stdout.splitlines())
    report_lines = written.split("\n")
    assert sum(1 for line in report_lines if line.startswith("healthy=")) == 1
