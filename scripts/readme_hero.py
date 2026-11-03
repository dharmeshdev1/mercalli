"""Draw the README's helicorder: the last 24 hours of incidents across every watched service.

Each row is one hour, oldest at the top, like a seismologist's drum recorder. The height of the trace
follows the weighted number of open incidents at that moment (critical 3, major 2, minor 1). The fine
wiggle is drawn, not measured, and is seeded by time so the picture is stable. Rows ink in one after
another when the image loads.

Usage: python scripts/readme_hero.py DATASET_DIR OUT_DIR
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

W, ROW_H, ROWS, LEFT, TOP, RIGHT = 1200, 19, 24, 96, 112, 36
H = TOP + ROWS * ROW_H + 64
WEIGHT = {"critical": 3.0, "major": 2.0, "minor": 1.0, "none": 0.4}
THEMES = {
    "light": {
        "paper": "#F3F5F4",
        "grid": "#DCE2DF",
        "ink": "#1B2430",
        "soft": "#4A5662",
        "faint": "#7D8891",
        "traces": ["#1B2430", "#C2362F", "#2F5DA8", "#2E7D4F"],
        "flag": "#C2362F",
    },
    "dark": {
        "paper": "#0F151B",
        "grid": "#1F2A33",
        "ink": "#E4E8E3",
        "soft": "#A9B3B8",
        "faint": "#6F7B83",
        "traces": ["#E4E8E3", "#FF6B5E", "#7EA6F5", "#6CC48F"],
        "flag": "#FF6B5E",
    },
}


def signals(incidents: pl.DataFrame, start: datetime, minutes: int) -> tuple[list[float], list[float], int]:
    """Per minute: the weighted count of open incidents, arrivals (new incidents, decaying), and how
    many incidents contributed."""
    level = [0.0] * minutes
    arrive = [0.0] * minutes
    used = 0
    for impact, s, ended in incidents.select("impact", "start", "resolved_at").iter_rows():
        w = WEIGHT.get(impact, 0.0)
        if not w or s is None:
            continue
        e = ended or start + timedelta(minutes=minutes)
        if e - s > timedelta(days=7):
            continue
        a = max(0, int((s - start).total_seconds() // 60))
        b = min(minutes - 1, int((e - start).total_seconds() // 60))
        if b < a:
            continue
        used += 1
        for i in range(a, b + 1):
            level[i] += w
        if s >= start:
            for i in range(a, min(minutes, a + 40)):
                arrive[i] += w * math.exp(-(i - a) / 7.0)
    return level, arrive, used


def trace(level: list[float], arrive: list[float], row: int, scale: tuple[float, float], rng: random.Random) -> str:
    """A seismograph line: a quiet baseline, sustained shaking while many incidents are open, and a
    sharp burst at each new incident, larger for worse ones."""
    base_level, span = scale
    mid = TOP + row * ROW_H + ROW_H / 2
    width = W - LEFT - RIGHT
    pts = []
    n = 60 * 5  # five samples per minute
    for i in range(n):
        m = row * 60 + i // 5
        sustained = max(0.0, (level[m] - base_level) / span) if span else 0.0
        burst = min(1.0, arrive[m] / 3.0)
        amp = 0.55 + 2.2 * math.sqrt(sustained) + 7.8 * burst
        y = mid + amp * (math.sin(i * 2.1 + rng.random() * 1.8) * 0.6 + (rng.random() - 0.5) * 0.8)
        x = LEFT + width * i / (n - 1)
        pts.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(pts)


def render(incidents: pl.DataFrame, end: datetime, theme: str, services: int, names: dict[str, str]) -> str:
    t = THEMES[theme]
    start = end - timedelta(hours=ROWS)
    level, arrive, n_open = signals(incidents, start, ROWS * 60)
    ordered = sorted(level)
    base_level = ordered[len(ordered) // 5]
    scale = (base_level, max(1.0, ordered[-1] - base_level))
    rng = random.Random(int(start.timestamp()))
    rows = []
    for r in range(ROWS):
        color = t["traces"][r % 4]
        delay = 0.25 + r * 0.09
        label = (start + timedelta(hours=r)).strftime("%H:%M")
        rows.append(
            f'<text x="{LEFT - 14}" y="{TOP + r * ROW_H + ROW_H / 2 + 4}" class="axis">{label}</text>'
            f'<path d="{trace(level, arrive, r, scale, rng)}" stroke="{color}" pathLength="1" class="ink" '
            f'style="animation-delay:{delay:.2f}s"/>'
        )
    # Flag major and critical incidents at hand-picked services, skipping labels that would collide.
    flags = []
    big = incidents.filter(
        pl.col("impact").is_in(["major", "critical"])
        & (pl.col("start") >= start)
        & pl.col("service_id").is_in(list(names))
    ).sort("start")
    taken: dict[int, list[float]] = {}
    for sid, s0 in big.select("service_id", "start").iter_rows():
        m = int((s0 - start).total_seconds() // 60)
        r, c = divmod(m, 60)
        x = LEFT + (W - LEFT - RIGHT) * c / 60
        label = names[sid]
        if any(abs(x - other) < 9 + 6.2 * len(label) for other in taken.get(r, [])) or len(flags) >= 10:
            continue
        taken.setdefault(r, []).append(x)
        y = TOP + r * ROW_H + 1
        flags.append(
            f'<g class="flag" style="animation-delay:{0.6 + r * 0.09:.2f}s">'
            f'<line x1="{x:.1f}" y1="{y}" x2="{x:.1f}" y2="{y + ROW_H - 2}" stroke="{t["flag"]}" stroke-width="1.6"/>'
            f'<rect x="{x + 3:.1f}" y="{y - 1}" width="{6.2 * len(label) + 6:.0f}" height="12" rx="2" fill="{t["paper"]}"/>'
            f'<text x="{x + 6:.1f}" y="{y + 8}" class="flagtext">{label}</text></g>'
        )
    grid = "".join(
        f'<line x1="{LEFT + (W - LEFT - RIGHT) * i / 12:.1f}" y1="{TOP - 6}" x2="{LEFT + (W - LEFT - RIGHT) * i / 12:.1f}" '
        f'y2="{TOP + ROWS * ROW_H + 4}" stroke="{t["grid"]}" stroke-width="1"/>'
        for i in range(13)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img"
 aria-label="A helicorder of internet incidents: 24 rows, one per hour, the trace rising when many services had open incidents.">
<style>
  .t1 {{ font: 600 30px ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t["ink"]}; letter-spacing: -0.01em; }}
  .t2 {{ font: 400 15px ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t["soft"]}; }}
  .axis {{ font: 400 11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; fill: {t["faint"]}; text-anchor: end; }}
  .note {{ font: 400 12px ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t["faint"]}; }}
  .flagtext {{ font: 500 10px ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t["flag"]}; }}
  .ink {{ fill: none; stroke-width: 1.15; stroke-linejoin: round; stroke-dasharray: 1; stroke-dashoffset: 1;
          animation: draw 1.4s cubic-bezier(.3,.6,.2,1) forwards; }}
  .flag {{ opacity: 0; animation: show .5s ease-out forwards; }}
  @keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
  @keyframes show {{ to {{ opacity: 1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .ink {{ animation: none; stroke-dashoffset: 0; }} .flag {{ animation: none; opacity: 1; }} }}
</style>
<rect width="{W}" height="{H}" fill="{t["paper"]}" rx="14"/>
<text x="{LEFT - 14 - 46}" y="54" class="t1" text-anchor="start" dx="0">Mercalli</text>
<text x="{LEFT - 60}" y="82" class="t2">Every incident across {services} status pages in the 24 hours to {end:%H:%M UTC, %d %b %Y}, one row per hour.</text>
{grid}
{"".join(rows)}
{"".join(flags)}
<text x="{LEFT - 60}" y="{H - 22}" class="note">Trace height follows the weighted number of open incidents ({n_open} in this window). The fine texture is drawn, not measured. Flags mark major and critical incidents.</text>
</svg>
'''


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    # End at the hour the dataset was built, so the last rows are not empty for lack of data.
    built = (
        json.loads((data / "dataset.json").read_text()).get("built_at") if (data / "dataset.json").exists() else None
    )
    end = (datetime.fromisoformat(built) if built else datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    inc = pl.read_parquet(data / "incidents.parquet").filter(
        (pl.col("start") <= end)
        & (pl.col("resolved_at").is_null() | (pl.col("resolved_at") >= end - timedelta(hours=ROWS)))
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from mercalli.catalog import load_services

    catalog = load_services()
    names = {s.id: s.name for s in catalog if s.curated}
    out.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (out / f"helicorder-{theme}.svg").write_text(render(inc, end, theme, len(catalog), names))
    print(f"wrote {out}/helicorder-light.svg and -dark.svg from {inc.height} incidents")


if __name__ == "__main__":
    main()
