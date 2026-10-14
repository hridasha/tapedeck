"""Turn a replay into something a person can read: a JSON file for machines, one HTML page for people."""

from __future__ import annotations

import datetime as dt
import html
import json

from .runner import EntryResult, ReplayResult


def _fmt(value: object, limit: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def to_dict(result: ReplayResult, tape: str = "") -> dict:
    return {
        "tape": tape,
        "target": result.target,
        "started_at": dt.datetime.fromtimestamp(result.started_at, dt.UTC).isoformat(timespec="seconds"),
        "duration_s": round(result.finished_at - result.started_at, 2),
        "ok": result.ok,
        "summary": result.summary(),
        "results": [
            {
                "order": r.order,
                "method": r.method,
                "path": r.path,
                "query": r.query,
                "outcome": r.outcome,
                "status_expected": r.status_expected,
                "status_actual": r.status_actual,
                "ms_expected": round(r.ms_expected, 1),
                "ms_actual": None if r.ms_actual is None else round(r.ms_actual, 1),
                "reasons": r.reasons,
                "warnings": r.warnings,
                "detail": r.detail,
                "diffs": [
                    {"path": d.path, "kind": d.kind, "expected": d.expected, "actual": d.actual, "volatile": d.volatile}
                    for d in r.diffs
                ],
            }
            for r in result.results
        ],
    }


CSS = """
body{font:14px/1.5 system-ui,Segoe UI,sans-serif;background:#0b0d12;color:#e8ebf2;margin:0;padding:32px;max-width:1100px;margin:auto}
h1{margin:0 0 4px;font-size:24px} .muted{color:#8b94a8} .cards{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}
.card{background:#141821;border:1px solid #262d3d;border-radius:12px;padding:14px 18px;min-width:110px}
.card b{display:block;font-size:26px} table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #262d3d;vertical-align:top} th{color:#8b94a8;font-weight:500}
.badge{padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}
.pass{background:#12331f;color:#3ecf8e}.fail{background:#3d1818;color:#ff6b62}.skipped{background:#2a2a2e;color:#a1a1aa}
.error{background:#3d2a12;color:#f5a524} code{font:12.5px Consolas,monospace;background:#1b2130;padding:1px 6px;border-radius:5px}
.diff{margin:6px 0 2px;background:#10131b;border-radius:8px;padding:8px 10px}
.diff div{margin:3px 0}.old{color:#ff8a80}.new{color:#69f0ae}.warn{color:#f5a524;font-size:12.5px}
"""


def _row(r: EntryResult) -> str:
    path = html.escape(r.path + (f"?{r.query}" if r.query else ""))
    timing = f"{r.ms_actual:.0f} ms (was {r.ms_expected:.0f})" if r.ms_actual is not None else ""
    status = (
        f"{r.status_expected}"
        if r.status_actual is None
        else (
            f"{r.status_actual}" if r.status_actual == r.status_expected else f"{r.status_expected} → {r.status_actual}"
        )
    )
    extra = ""
    for reason in r.reasons:
        extra += f'<div class="old">✗ {html.escape(reason)}</div>'
    if r.detail:
        extra += f'<div class="muted">{html.escape(r.detail)}</div>'
    for d in r.diffs:
        label = "volatile" if d.volatile else d.kind
        extra += (
            f'<div class="diff"><div><code>{html.escape(d.path)}</code> <span class="muted">{label}</span></div>'
            + (f'<div class="old">− {html.escape(_fmt(d.expected))}</div>' if d.kind != "extra" else "")
            + (f'<div class="new">+ {html.escape(_fmt(d.actual))}</div>' if d.kind != "missing" else "")
            + "</div>"
        )
    for w in r.warnings:
        extra += f'<div class="warn">⚠ {html.escape(w)}</div>'
    return (
        f"<tr><td>{r.order}</td><td><code>{r.method}</code></td><td>{path}{extra}</td><td>{status}</td>"
        f'<td class="muted">{timing}</td><td><span class="badge {r.outcome}">{r.outcome}</span></td></tr>'
    )


def render_html(result: ReplayResult, tape: str = "") -> str:
    s = result.summary()
    cards = "".join(
        f'<div class="card"><b>{s[k]}</b><span class="muted">{label}</span></div>'
        for k, label in [
            ("total", "requests"),
            ("pass", "passed"),
            ("fail", "failed"),
            ("skipped", "skipped"),
            ("error", "errors"),
            ("warnings", "warnings"),
        ]
    )
    rows = "".join(_row(r) for r in result.results)
    verdict = "All answers match the recording." if result.ok else "Some answers changed. See the red rows."
    return (
        f"<!doctype html><meta charset='utf-8'><title>Replay report</title><style>{CSS}</style>"
        f"<h1>Replay: {html.escape(tape or 'tape')} → {html.escape(result.target)}</h1>"
        f"<div class='muted'>{verdict} Took {result.finished_at - result.started_at:.1f}s.</div>"
        f"<div class='cards'>{cards}</div>"
        "<table><tr><th>#</th><th>Method</th><th>Request</th><th>Status</th><th>Time</th><th>Result</th></tr>"
        f"{rows}</table>"
    )
