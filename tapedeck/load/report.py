"""Load-test results as JSON (for machines) or one self-contained HTML page with charts (for people)."""

from __future__ import annotations

import html
import math

from .runner import LoadResult

COLORS = {
    "p50": "#3ecf8e",
    "p95": "#f5a524",
    "p99": "#ff6b62",
    "rps": "#5aa9ff",
    "errors": "#ff6b62",
    "users": "#a78bfa",
}


def to_dict(result: LoadResult, tape: str = "") -> dict:
    return {
        "tape": tape,
        "model": result.model,
        "options": result.options,
        "summary": result.summary,
        "knee": result.knee,
        "skipped_requests": result.skipped,
        "dropped_requests": result.dropped,
        "endpoints": result.endpoints,
        "ticks": [
            {
                "t": t.t,
                "requests": t.requests,
                "errors": t.errors,
                "p50": t.p50,
                "p95": t.p95,
                "p99": t.p99,
                "users": t.users,
            }
            for t in result.ticks
        ],
    }


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def line_chart(
    title: str, unit: str, series: dict[str, list[tuple[float, float | None]]], width: int = 520, height: int = 210
) -> str:
    left, right, top, bottom = 52, 14, 30, 26
    points = [(x, y) for pts in series.values() for x, y in pts if y is not None]
    xmax = max((x for x, _ in points), default=1) or 1
    ymax = _nice_max(max((y for _, y in points), default=1))

    def px(x: float) -> float:
        return left + x / xmax * (width - left - right)

    def py(y: float) -> float:
        return height - bottom - y / ymax * (height - top - bottom)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    parts.append(f'<text x="{left}" y="16" fill="#e8ebf2" font-size="13" font-weight="600">{html.escape(title)}</text>')
    for i in range(5):
        value = ymax * i / 4
        y = py(value)
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#262d3d"/>')
        label = f"{value:g}"
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" fill="#8b94a8" font-size="10" text-anchor="end">{label}</text>'
        )
    for i in range(5):
        x = xmax * i / 4
        parts.append(
            f'<text x="{px(x):.1f}" y="{height - 8}" fill="#8b94a8" font-size="10" text-anchor="middle">{x:.0f}s</text>'
        )
    legend_x = width - right
    for label, pts in reversed(list(series.items())):
        color = COLORS.get(label, "#ccc")
        coords = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts if y is not None)
        if coords:
            parts.append(
                f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
            )
        legend_x -= 8 * len(label) + 22
        parts.append(f'<rect x="{legend_x}" y="9" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{legend_x + 14}" y="18" fill="#8b94a8" font-size="11">{html.escape(label)}</text>')
    parts.append(
        f'<text x="10" y="{(top + height - bottom) / 2:.0f}" fill="#8b94a8" font-size="10" transform="rotate(-90 10 {(top + height - bottom) / 2:.0f})">{html.escape(unit)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


CSS = """
body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#0b0d12;color:#e8ebf2;margin:0;padding:32px;max-width:1150px;margin:auto}
h1{margin:0 0 4px;font-size:24px}h2{font-size:16px;margin:28px 0 8px}.muted{color:#8b94a8}
.cards{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}.card{background:#141821;border:1px solid #262d3d;border-radius:12px;padding:14px 18px;min-width:120px}
.card b{display:block;font-size:24px}.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px}
.chart{background:#141821;border:1px solid #262d3d;border-radius:12px;padding:8px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #262d3d}th{color:#8b94a8;font-weight:500}
.knee{background:#3d2a12;border:1px solid #6b4a1a;color:#ffd08a;border-radius:10px;padding:10px 14px;margin:14px 0}
code{font:12.5px Consolas,monospace;background:#1b2130;padding:1px 6px;border-radius:5px}
"""


def _ms(value: float | None) -> str:
    return "–" if value is None else f"{value:.0f} ms"


def render_html(result: LoadResult, tape: str = "") -> str:
    s, o = result.summary, result.options
    ticks = result.ticks
    chart_latency = line_chart(
        "Latency",
        "ms",
        {k: [(t.t, getattr(t, k)) for t in ticks] for k in ("p50", "p95", "p99")},
    )
    chart_rps = line_chart("Throughput", "requests / s", {"rps": [(t.t, t.rps) for t in ticks]})
    chart_err = line_chart("Errors", "per second", {"errors": [(t.t, float(t.errors)) for t in ticks]})
    chart_users = line_chart(
        "Users" if result.model == "closed" else "In flight", "count", {"users": [(t.t, float(t.users)) for t in ticks]}
    )

    load = (
        f"{o['rate']:g} requests/s (open model)"
        if result.model == "open"
        else f"{o['users']} users (closed model), ramp {o['ramp']:g}s"
    )
    cards = "".join(
        f'<div class="card"><b>{value}</b><span class="muted">{label}</span></div>'
        for value, label in [
            (f"{s['requests']:,}", "requests"),
            (f"{s['rps']:.1f}", "requests / s"),
            (_ms(s["p50_ms"]), "median"),
            (_ms(s["p95_ms"]), "95th percentile"),
            (_ms(s["p99_ms"]), "99th percentile"),
            (_ms(s["max_ms"]), "slowest"),
            (f"{s['error_rate'] * 100:.1f}%", "errors"),
        ]
    )
    knee = ""
    if result.knee:
        k = result.knee
        knee = (
            f'<div class="knee">⚠ Latency took off at about <b>{k["users"]} users</b> ({k["t"]} s into the test): '
            f"p95 rose from {k['baseline_p95_ms']:.0f} ms to {k['p95_ms']:.0f} ms.</div>"
        )
    notes = []
    if result.skipped:
        notes.append(f"{result.skipped} tape request(s) were not sent (writes, or secrets hidden).")
    if result.dropped:
        notes.append(f"{result.dropped} request(s) were dropped because too many were already in flight.")
    rows = "".join(
        f"<tr><td><code>{html.escape(e['endpoint'])}</code></td><td>{e['requests']}</td><td>{e['errors']}</td>"
        f"<td>{_ms(e['p50_ms'])}</td><td>{_ms(e['p95_ms'])}</td><td>{_ms(e['max_ms'])}</td></tr>"
        for e in result.endpoints
    )
    codes = ", ".join(f"{code or 'no answer'}: {n}" for code, n in s["status_codes"].items())
    return (
        f"<!doctype html><meta charset='utf-8'><title>Load test report</title><style>{CSS}</style>"
        f"<h1>Load test: {html.escape(tape or 'tape')} → {html.escape(str(o['target']))}</h1>"
        f"<div class='muted'>{load}, {o['duration']:g}s"
        + (f", first {s['warmup_s']:g}s left out" if s["warmup_s"] else "")
        + "</div>"
        f"<div class='cards'>{cards}</div>{knee}"
        + "".join(f"<div class='muted'>{html.escape(n)}</div>" for n in notes)
        + f"<h2>Over time</h2><div class='charts'><div class='chart'>{chart_latency}</div><div class='chart'>{chart_rps}</div>"
        f"<div class='chart'>{chart_users}</div><div class='chart'>{chart_err}</div></div>"
        "<h2>Slowest endpoints (by 95th percentile)</h2>"
        "<table><tr><th>Endpoint</th><th>Requests</th><th>Errors</th><th>Median</th><th>95th</th><th>Slowest</th></tr>"
        f"{rows}</table><h2>Status codes</h2><div class='muted'>{html.escape(codes)}</div>"
    )
