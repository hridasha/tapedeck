"""Command line: tapedeck import-har / tapes / show / mock / delete."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from tapedeck import __version__
from tapedeck.core.har import parse_har
from tapedeck.core.storage import Storage, TapeExistsError, TapeNotFoundError

app = typer.Typer(help="Record an API's traffic once, then mock it, replay it and load-test it.", no_args_is_help=True)

DbOption = typer.Option(
    "tapedeck.db", "--db", envvar="TAPEDECK_DB", help="Where tapes are stored (a single SQLite file)."
)


def _tape_or_exit(store: Storage, name: str):
    try:
        return store.get_tape(name)
    except TapeNotFoundError:
        typer.secho(f"No tape called '{name}'. Run `tapedeck tapes` to see what exists.", fg="red", err=True)
        raise typer.Exit(1) from None


def _parse_latency(value: str) -> str | float:
    v = value.strip().lower()
    if v == "recorded":
        return "recorded"
    try:
        return float(v.removesuffix("ms"))
    except ValueError:
        raise typer.BadParameter("use 'recorded', or a number of milliseconds such as 120 or 120ms") from None


def _version(show: bool) -> None:
    if show:
        typer.echo(f"tapedeck {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", callback=_version, is_eager=True)) -> None:
    pass


@app.command("import-har")
def import_har(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="A .har file saved from browser DevTools."),
    name: str = typer.Option(None, "--name", "-n", help="Tape name (default: the file name)."),
    keep_static: bool = typer.Option(False, "--keep-static", help="Also keep scripts, images, fonts, CSS."),
    no_scrub: bool = typer.Option(False, "--no-scrub", help="Do NOT hide tokens, cookies and passwords."),
    db: str = DbOption,
) -> None:
    """Turn a browser recording (HAR) into a tape."""
    try:
        entries = parse_har(file.read_bytes(), keep_static=keep_static, scrub=not no_scrub)
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    if not entries:
        typer.secho("That HAR file has no API calls to import (try --keep-static).", fg="yellow", err=True)
        raise typer.Exit(1)
    with Storage(db) as store:
        try:
            tape = store.create_tape(name or file.stem, source="har")
        except TapeExistsError as exc:
            typer.secho(f"{exc}. Pick another --name or delete it first.", fg="red", err=True)
            raise typer.Exit(1) from None
        store.add_entries(tape.id, entries)
    typer.echo(f"Imported {len(entries)} requests into tape '{tape.name}'" + ("" if no_scrub else " (secrets hidden)."))


@app.command("tapes")
def list_tapes(db: str = DbOption) -> None:
    """List saved tapes."""
    with Storage(db) as store:
        tapes = store.list_tapes()
    if not tapes:
        typer.echo("No tapes yet. Try: tapedeck import-har session.har")
        return
    typer.echo(f"{'NAME':<24}{'REQUESTS':>9}  {'SOURCE':<7}  CREATED")
    for t in tapes:
        typer.echo(f"{t.name:<24}{t.entry_count:>9}  {t.source:<7}  {t.created_at}")


@app.command("show")
def show(name: str, db: str = DbOption) -> None:
    """Print every request in a tape."""
    with Storage(db) as store:
        tape = _tape_or_exit(store, name)
        entries = store.get_entries(tape.id)
    typer.echo(f"{'#':>3}  {'METHOD':<7} {'STATUS':>6} {'TIME':>8}  PATH")
    for e in entries:
        path = e.path + (f"?{e.query}" if e.query else "")
        typer.echo(f"{e.order:>3}  {e.method:<7} {e.status:>6} {e.duration_ms:>6.0f}ms  {path}")


@app.command("mock")
def mock(
    name: str,
    port: int = typer.Option(9100, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    latency: str = typer.Option("0", help="'recorded', or milliseconds to wait before every answer."),
    fail_rate: float = typer.Option(
        0.0, "--fail-rate", min=0.0, max=1.0, help="Share of calls that fail with 503 (0 to 1)."
    ),
    no_cors: bool = typer.Option(False, "--no-cors"),
    db: str = DbOption,
) -> None:
    """Serve a tape as a fake server."""
    from tapedeck.mock.server import serve_mock

    with Storage(db) as store:
        tape = _tape_or_exit(store, name)
        entries = store.get_entries(tape.id)
    typer.echo(f"Mocking tape '{tape.name}' ({len(entries)} requests) at http://{host}:{port}")
    typer.echo("Log of matches: /__tapedeck/log   Press Ctrl+C to stop.")
    serve_mock(entries, host=host, port=port, latency=_parse_latency(latency), fail_rate=fail_rate, cors=not no_cors)


@app.command("record")
def record(
    target: str = typer.Option(..., "--target", "-t", help="The real server, e.g. http://localhost:8000"),
    name: str = typer.Option(..., "--name", "-n", help="Tape to write to."),
    listen: int = typer.Option(9000, "--listen", "-p", help="Port your app should talk to instead."),
    host: str = typer.Option("127.0.0.1", "--host"),
    append: bool = typer.Option(False, "--append", help="Keep recording into an existing tape."),
    no_scrub: bool = typer.Option(False, "--no-scrub", help="Do NOT hide tokens, cookies and passwords."),
    max_body_mb: float = typer.Option(5.0, "--max-body-mb", help="Bigger responses are passed through, not recorded."),
    db: str = DbOption,
) -> None:
    """Sit in front of a real server and record every request and answer."""
    from tapedeck.core.http import require_allowed_target
    from tapedeck.recorder.proxy import create_recorder_app

    try:
        require_allowed_target(target, allow_remote=True)
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    import uvicorn

    store = Storage(db)
    try:
        try:
            tape = store.create_tape(name, source="record", target_url=target)
        except TapeExistsError:
            if not append:
                typer.secho(f"A tape called '{name}' already exists. Use --append to add to it.", fg="red", err=True)
                raise typer.Exit(1) from None
            tape = store.get_tape(name)
        recorder = create_recorder_app(
            store, tape.id, target, scrub=not no_scrub, max_body_bytes=int(max_body_mb * 1024 * 1024)
        )
        typer.echo(f"Recording http://{host}:{listen}  ->  {target}   (tape '{name}')")
        typer.echo("Point your app at the address on the left. Press Ctrl+C to stop.")
        try:
            uvicorn.run(recorder, host=host, port=listen, log_level="warning")
        finally:
            stats = recorder.state.stats
            typer.echo(
                f"\nRecorded {stats['recorded']} requests into '{name}'"
                + (
                    f" ({stats['skipped_streaming'] + stats['skipped_large']} streamed or too large, not recorded)"
                    if stats["skipped_streaming"] + stats["skipped_large"]
                    else ""
                )
                + "."
            )
    finally:
        store.close()


@app.command("noise")
def noise(
    name: str,
    save: bool = typer.Option(False, "--save", help="Store the candidates as this tape's ignore list."),
    patterns: bool = typer.Option(False, "--patterns", help="Also flag values that look like timestamps or UUIDs."),
    db: str = DbOption,
) -> None:
    """Find fields that change on every call (so replays can ignore them)."""
    from tapedeck.core.noise import detect_noise

    with Storage(db) as store:
        tape = _tape_or_exit(store, name)
        found = sorted(detect_noise(store.get_entries(tape.id), by_pattern=patterns))
        if save:
            store.set_noise(name, found)
        saved = store.get_noise(name)
    if not found:
        typer.echo("No changing fields found. (Record the same request twice, or try --patterns.)")
    else:
        typer.echo(
            "Fields that differ between identical requests" + (" or look like timestamps/ids" if patterns else "") + ":"
        )
        for p in found:
            typer.echo(f"  {p}")
        typer.echo(
            "\nCheck that these are really noise, not real data changes. "
            + ("Saved." if save else "Add --save to keep them.")
        )
    if saved and not save:
        typer.echo(f"Already saved for this tape: {', '.join(saved)}")


@app.command("replay")
def replay_cmd(
    name: str,
    target: str = typer.Option(..., "--target", "-t", help="Server to test, e.g. http://localhost:8000"),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="Extra header, e.g. 'Authorization: Bearer TOKEN'. Repeatable."
    ),
    ignore: list[str] = typer.Option(
        None, "--ignore", "-i", help="JSON path to ignore, e.g. '$.items[].created_at'. Repeatable."
    ),
    allow_writes: bool = typer.Option(False, "--allow-writes", help="Also send POST/PUT/PATCH/DELETE."),
    allow_remote: bool = typer.Option(
        False, "--allow-remote", help="Allow a target that is not on this machine or network."
    ),
    learn_noise: bool = typer.Option(
        False, "--learn-noise", help="Ask each GET twice and ignore fields that change on their own."
    ),
    fail_on_slow: bool = typer.Option(False, "--fail-on-slow"),
    report: Path = typer.Option(None, "--report", help="Write an HTML report here."),
    json_out: Path = typer.Option(None, "--json", help="Write a JSON report here."),
    db: str = DbOption,
) -> None:
    """Send a tape's requests to a server and show which answers changed. Exit code 1 if any did."""
    import asyncio

    from tapedeck.core.http import parse_header_args
    from tapedeck.replay.report import render_html, to_dict
    from tapedeck.replay.runner import ReplayOptions, replay

    with Storage(db) as store:
        tape = _tape_or_exit(store, name)
        entries = store.get_entries(tape.id)
        saved_noise = set(store.get_noise(name))
    try:
        opts = ReplayOptions(
            target=target,
            headers=parse_header_args(header),
            ignore=saved_noise | set(ignore or []),
            allow_writes=allow_writes,
            allow_remote=allow_remote,
            learn_noise=learn_noise,
            fail_on_slow=fail_on_slow,
        )
        typer.echo(f"Replaying '{name}' ({len(entries)} requests) against {target}")
        result = asyncio.run(replay(entries, opts))
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from None

    for r in result.results:
        mark = {"pass": "ok  ", "fail": "FAIL", "skipped": "skip", "error": "ERR "}[r.outcome]
        path = r.path + (f"?{r.query}" if r.query else "")
        typer.secho(
            f"  {mark} {r.method:<6} {path}", fg={"fail": "red", "error": "red", "skipped": "yellow"}.get(r.outcome)
        )
        for reason in r.reasons:
            typer.echo(f"         {reason}")
        for d in [d for d in r.diffs if not d.volatile][:5]:
            typer.echo(f"         {d.path}: {d.kind}  {str(d.expected)[:60]} -> {str(d.actual)[:60]}")
        if r.detail:
            typer.echo(f"         {r.detail}")
    s = result.summary()
    typer.echo(
        f"\n{s['pass']} passed, {s['fail']} failed, {s['skipped']} skipped, "
        f"{s['error']} errors, {s['warnings']} warnings"
    )
    if report:
        report.write_text(render_html(result, name), encoding="utf-8")
        typer.echo(f"HTML report: {report}")
    if json_out:
        import json

        json_out.write_text(json.dumps(to_dict(result, name), indent=2), encoding="utf-8")
        typer.echo(f"JSON report: {json_out}")
    raise typer.Exit(0 if result.ok else 1)


@app.command("load")
def load_cmd(
    name: str,
    target: str = typer.Option(..., "--target", "-t", help="Server to test, e.g. http://localhost:8000"),
    users: int = typer.Option(
        10, "--users", "-u", help="Virtual users (each waits for its answer before asking again)."
    ),
    rate: float = typer.Option(
        None, "--rate", "-r", help="Requests per second, no matter how slow the server is. Replaces --users."
    ),
    duration: float = typer.Option(30.0, "--duration", "-d", help="Seconds, ramp-up included."),
    ramp: float = typer.Option(0.0, "--ramp", help="Seconds over which the users are started."),
    warmup: float = typer.Option(0.0, "--warmup", help="Seconds left out of the summary."),
    think: float = typer.Option(0.0, "--think", help="Milliseconds a user pauses between requests."),
    header: list[str] = typer.Option(None, "--header", "-H", help="Extra header, e.g. 'Authorization: Bearer TOKEN'."),
    allow_writes: bool = typer.Option(False, "--allow-writes", help="Also send POST/PUT/PATCH/DELETE."),
    allow_remote: bool = typer.Option(
        False, "--allow-remote", help="Allow a target that is not on this machine or network."
    ),
    report: Path = typer.Option(None, "--report", help="Write an HTML report here."),
    json_out: Path = typer.Option(None, "--json", help="Write a JSON report here."),
    fail_p95: float = typer.Option(None, "--fail-p95", help="Exit with 1 if the 95th percentile (ms) is above this."),
    fail_errors: float = typer.Option(
        None, "--fail-errors", help="Exit with 1 if more than this percent of requests fail."
    ),
    db: str = DbOption,
) -> None:
    """Send a tape's requests as a crowd and measure how the server copes."""
    import asyncio

    from tapedeck.core.http import parse_header_args
    from tapedeck.load.report import render_html, to_dict
    from tapedeck.load.runner import LoadOptions, run_load

    with Storage(db) as store:
        tape = _tape_or_exit(store, name)
        entries = store.get_entries(tape.id)

    def on_tick(t) -> None:
        p95 = f"{t.p95:.0f}ms" if t.p95 is not None else "-"
        typer.echo(f"  t={t.t + 1:>3}s  load={t.users:>4}  {t.requests:>5} req/s  p95={p95:>7}  errors={t.errors}")

    try:
        opts = LoadOptions(
            target=target,
            users=users,
            rate=rate,
            duration=duration,
            ramp=ramp,
            warmup=warmup,
            think_ms=think,
            headers=parse_header_args(header),
            allow_writes=allow_writes,
            allow_remote=allow_remote,
            on_tick=on_tick,
        )
        shape = f"{rate:g} requests/s" if rate else f"{users} users" + (f", ramping up over {ramp:g}s" if ramp else "")
        typer.echo(f"Load test '{name}' -> {target}: {shape} for {duration:g}s. Only test servers you own.")
        result = asyncio.run(run_load(entries, opts))
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from None
    except KeyboardInterrupt:
        typer.secho("Stopped.", fg="yellow")
        raise typer.Exit(130) from None

    s = result.summary
    fmt = lambda v: "-" if v is None else f"{v:.0f} ms"  # noqa: E731
    typer.echo(f"\n{s['requests']} requests, {s['rps']:.1f}/s, errors {s['error_rate'] * 100:.1f}%")
    typer.echo(
        f"latency: median {fmt(s['p50_ms'])}, p95 {fmt(s['p95_ms'])}, p99 {fmt(s['p99_ms'])}, slowest {fmt(s['max_ms'])}"
    )
    if result.knee:
        k = result.knee
        typer.secho(
            f"Latency took off at about {k['users']} users ({k['t']}s in): p95 {k['baseline_p95_ms']:.0f} -> {k['p95_ms']:.0f} ms",
            fg="yellow",
        )
    if result.skipped:
        typer.echo(f"{result.skipped} tape request(s) not sent (writes or hidden secrets).")
    if report:
        report.write_text(render_html(result, name), encoding="utf-8")
        typer.echo(f"HTML report: {report}")
    if json_out:
        import json

        json_out.write_text(json.dumps(to_dict(result, name), indent=2), encoding="utf-8")
        typer.echo(f"JSON report: {json_out}")

    failed = False
    if fail_p95 is not None and (s["p95_ms"] is None or s["p95_ms"] > fail_p95):
        typer.secho(f"FAIL: p95 {fmt(s['p95_ms'])} is above {fail_p95:g} ms", fg="red")
        failed = True
    if fail_errors is not None and s["error_rate"] * 100 > fail_errors:
        typer.secho(f"FAIL: error rate {s['error_rate'] * 100:.1f}% is above {fail_errors:g}%", fg="red")
        failed = True
    raise typer.Exit(1 if failed else 0)


@app.command("delete")
def delete(name: str, yes: bool = typer.Option(False, "--yes", "-y"), db: str = DbOption) -> None:
    """Delete a tape."""
    with Storage(db) as store:
        _tape_or_exit(store, name)
        if not yes and not typer.confirm(f"Delete tape '{name}'?"):
            raise typer.Exit()
        store.delete_tape(name)
    typer.echo(f"Deleted '{name}'.")


@app.command("ui")
def ui(
    port: int = typer.Option(8700, "--port", "-p", help="Port for the web page."),
    host: str = typer.Option("127.0.0.1", "--host", help="Use 0.0.0.0 inside Docker."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page in your browser."),
    db: str = DbOption,
) -> None:
    """Open the web page: browse tapes, start mocks and recorders, run replays and load tests."""
    import threading
    import webbrowser

    import uvicorn

    from tapedeck.api.app import create_api_app, find_web_dir

    web_dir = find_web_dir()
    if web_dir is None:
        typer.echo("The web page is not built yet (run `npm install && npm run build` in web/). The API still works.")
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    typer.echo(f"Tapedeck is at {url}   (tapes: {db})")
    if open_browser and web_dir is not None:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_api_app(db, web_dir=web_dir), host=host, port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    app(prog_name=os.path.basename(__file__))
