# Tapedeck

**Record an API's traffic once. Then mock it, replay it and load-test it, all from the same recording.**

Think of it as a voice recorder for the conversation between an app and its server. Once you have the recording (a *tape*) you can:

| | |
|---|---|
| **Mock** | play the recorded answers back as a fake server, so the frontend keeps working when the backend is down or unfinished |
| **Replay** | send the same requests to a new version of the server and see exactly which answers changed |
| **Load test** | send the same requests as a crowd and see when the server starts to struggle |

Everything runs locally. Tapes are single SQLite files, and tokens, cookies and passwords are hidden *before* a tape is saved.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Project setup, tape storage, CI, sample API | done |
| 1 | HAR import, secret scrubbing, smart matcher, mock server, CLI | done |
| 2 | Recording proxy, noise detection | done |
| 3 | Replay and response diff | done |
| 4 | Load test with live chart | done |
| 5 | Web UI, Docker, benchmarks | **done** (joint test next) |

## The web page

```bash
tapedeck ui            # opens http://localhost:8700
```

Import a HAR, browse every call, start a fake server or recorder, run a replay (changed fields shown side by side) or a load test (live charts over a WebSocket). To build the page from source: `cd web && npm install && npm run build`.

## Docker

```bash
docker compose up -d --build
```

The page is at http://localhost:8700 and a sample API at http://localhost:8000. Fake servers and recorders started from the page must use ports 9000-9010. To test an API running on your own computer, use `http://host.docker.internal:<port>` as the target. Tapes live in the `tapedeck-data` volume.

## Benchmarks

Real numbers for proxy overhead, fake-server throughput and load-generator accuracy are in [docs/benchmarks.md](docs/benchmarks.md); re-run with `python benchmarks/run_benchmarks.py`.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                            # everything should pass
```

**Turn a browser session into a fake server**

1. Open your app, then DevTools, then the **Network** tab, use the app, right-click and choose **Save all as HAR**.
2. Import it and serve it:

```bash
tapedeck import-har session.har --name myapp
tapedeck show myapp                       # what was recorded
tapedeck mock myapp --port 9100           # fake server at http://127.0.0.1:9100
```

3. Point your frontend at `http://127.0.0.1:9100` instead of the real backend.

Useful options: `--latency recorded` (answer as slowly as the real server did) or `--latency 300`, and `--fail-rate 0.1` (10% of calls fail with 503, to see how your app copes).

**Try it on the sample API** (no browser needed)

```bash
uvicorn examples.sample_api.app:app --port 8000          # 1. the "real" server
python examples/make_demo_har.py                          # 2. record a session of real calls -> demo.har
tapedeck import-har demo.har --name demo                  # 3. make a tape (secrets hidden)
tapedeck mock demo --port 9100                            # 4. fake server
```

Now stop step 1 and open http://127.0.0.1:9100/items in your browser: it still answers. Try `/items/999` (matched by shape), `/nope` (404 with a hint) and `/__tapedeck/log` (how each answer was chosen).

## How the mock decides which recording to use

Each request gets three fingerprints, strictest first:

| Level | Looks at | Example |
|---|---|---|
| `exact` | method, path, query, body | `GET /videos/123?page=1` |
| `shape` | same, but ids replaced (`123`, UUIDs, long hex) | `GET /videos/456?page=1` also matches |
| `loose` | method and path shape only | `GET /videos/456?page=9` also matches |

Values that change on every call (`?_=1699999`, a `timestamp` field) are ignored. If the same request was recorded more than once with different answers, they are served in order and the last one repeats. Every answer carries an `x-tapedeck-match` header (`exact`, `shape`, `loose`, `none` or `injected`), and `/__tapedeck/log` lists what was matched.

## What is hidden by default

Headers `Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key` and others; JSON values whose key is `password`, `secret`, `api_key`, `token`, `access_token`, `refresh_token`... (but not `token_type`). Use `--no-scrub` to keep everything. Never commit tapes (`*.db`, `*.har` are git-ignored).

## Commands

```
tapedeck import-har FILE [--name N] [--keep-static] [--no-scrub]
tapedeck tapes
tapedeck show NAME
tapedeck mock NAME [--port 9100] [--latency recorded|MS] [--fail-rate 0..1]
tapedeck delete NAME [--yes]
```

All commands take `--db PATH` (or the `TAPEDECK_DB` variable); the default is `tapedeck.db` in the current folder.

## Limits (version 0.1)

Plain HTTP with JSON-style APIs. No gRPC, WebSockets or streaming responses yet; static files (scripts, images, CSS) are skipped on import unless you pass `--keep-static`.

