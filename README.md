# SpecWatch

**SpecWatch watches your OpenAPI specs over time and tells you when a change could break existing clients.**

## What it does

- Fetches OpenAPI 3.x documents in JSON or YAML over HTTPS.
- Stores each changed or unchanged snapshot history in SQLite, using ETag and Last-Modified validators when available.
- Detects common breaking changes: removed operations or response codes, newly required parameters or request fields, removed request fields, changed schema types, and removed enum values.
- Reports response-field removals as breaking because clients can rely on fields present in the documented contract.
- Polls monitored documents in the background and supports manual checks.
- Can send newly detected breaking changes to a configured HookRelay endpoint.
- Serves a dashboard and a documented REST API at `/docs`.
- Rejects non-HTTPS URLs, credentials, redirects, non-public IPs, and oversized documents.
- Accepts an optional hostname allowlist to limit which APIs can be monitored.

SpecWatch covers the most common breaking changes, not every possible one. External `$ref` documents and some advanced JSON Schema patterns are outside its current scope. Treat findings as a useful signal, not a guarantee.

## Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn specwatch.main:app --app-dir src --reload
```

Open `http://127.0.0.1:8000` for the dashboard and `http://127.0.0.1:8000/docs` for the API reference. Set `SPECWATCH_API_KEY` to require an `Authorization: Bearer <key>` header on all `/api/` routes; leave it unset for local development without authentication. The application creates `./data/specwatch.sqlite3` on first start. Use `SPECWATCH_DATABASE_PATH` to choose a different location.

### Docker

```bash
docker compose up --build
```

The compose file binds the app to localhost and persists its database in a named volume. When `SPECWATCH_API_KEY` is set, API clients must send it as a Bearer token. The dashboard itself remains unauthenticated, so keep the service on a trusted network and apply network egress controls before any public deployment.

## Offline compatibility demo

The repository includes two small example contracts so the analyzer can be tried without a network call:

```bash
python -m specwatch examples/before.json examples/after.json
```

The command prints JSON findings and exits with code `1` when it detects a breaking change, which makes it usable in a shell workflow.

## HTTP API

```bash
curl -X POST http://127.0.0.1:8000/api/monitors \
  -H "Content-Type: application/json" \
  -d '{"name":"Payments API","spec_url":"https://api.example.com/openapi.json"}'
```

The create call registers a monitor. The first check establishes its baseline; later checks compare the new document with the last stored snapshot.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Process health |
| `GET` | `/api/overview` | Counts for monitors, snapshots, findings, and current errors |
| `GET` | `/api/monitors` | List monitors with latest status and findings |
| `POST` | `/api/monitors` | Register a named OpenAPI URL |
| `GET` | `/api/monitors/{id}` | Read one monitor |
| `DELETE` | `/api/monitors/{id}` | Remove a monitor and its history |
| `POST` | `/api/monitors/{id}/check` | Fetch and compare the current contract |
| `GET` | `/api/monitors/{id}/snapshots` | Read recent snapshot metadata |
| `GET` | `/api/monitors/{id}/diff` | Return a unified diff between the two latest snapshots |
| `GET` | `/api/monitors/{id}/findings` | Read findings from the latest snapshot |

## GitHub Action

Compare two OpenAPI files in a workflow. The action exits with code `1` when it finds a breaking change, which fails the workflow step; otherwise it exits with code `0`. Its `findings` output contains the JSON array of compatibility findings.

```yaml
- uses: actions/checkout@v4
- uses: devduties/specwatch@main
  id: specwatch
  with:
    before: specs/openapi-v1.json
    after: specs/openapi-v2.json
```

Use `steps.specwatch.outputs.findings` in a later workflow step when you need the findings JSON.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `SPECWATCH_DATABASE_PATH` | `./data/specwatch.sqlite3` | SQLite database location |
| `SPECWATCH_POLL_INTERVAL_SECONDS` | `300` | Background polling interval; set `0` to disable |
| `SPECWATCH_MAX_SPEC_BYTES` | `2097152` | Maximum downloaded document size |
| `SPECWATCH_ALLOWED_HOSTS` | unset | Optional comma-separated exact hostname allowlist |
| `SPECWATCH_API_KEY` | unset | Optional Bearer token required on all `/api/` routes |
| `SPECWATCH_ALERT_URL` | unset | Optional HookRelay event-ingestion URL for breaking-change notifications |
| `SPECWATCH_ALERT_API_KEY` | unset | Optional Bearer token for the alert destination |

The fetcher has a bounded timeout, retries transient errors up to twice, uses conditional requests to avoid re-downloading unchanged specs, and never follows redirects. The hostname allowlist is useful when you know the full set of APIs upfront. Before any public deployment, set an API key, restrict outbound network access, and put the service behind a reverse proxy.

When `SPECWATCH_ALERT_URL` is set, each new snapshot with breaking findings triggers a POST to that URL. For HookRelay, use the endpoint's event-ingestion URL, such as `http://127.0.0.1:8000/v1/endpoints/ENDPOINT_ID/events`. The payload contains the monitor, snapshot, and breaking findings. Set `SPECWATCH_ALERT_API_KEY` if HookRelay requires a Bearer key. If the POST fails, SpecWatch logs the error and keeps the snapshot. The alert not landing doesn't affect the check result.

## Design

```text
Browser / API client
        │
        ▼
FastAPI routes ─── SpecWatch service ─── bounded HTTPS fetcher ─── OpenAPI source
                         │
                         ├── parser and compatibility analyzer
                         └── SQLite repository (monitors, snapshots, findings)
```

The data layer uses SQLite directly to keep the project easy to run. The comparison logic has no dependency on HTTP or the database, so every breaking-change rule can be tested with plain Python dicts.

## Quality checks

```bash
ruff check .
pytest -q
```

GitHub Actions runs these checks on Python 3.11 and 3.12 for pushes and pull requests.

## License

MIT. See [LICENSE](LICENSE).
