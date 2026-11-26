# The clabe hub

The hub is an optional page for the whole fleet. It shows every rig's status and every run waiting for an answer at
one address.

- "Waiting for an answer" lists the runs waiting on a prompt, longest first, each with a link to answer it.
- "Rigs" shows each rig's status (idle, running, leased, offline), experiment and current step.
- "Live sessions" shows the session each rig is running now, filled in as it progresses: subject, experimenter,
  session id (once registered), progress and duration. A row opens its run page. The hub stores none of this: the
  data comes from each poll (`session` in `GET /api/hub/rigs`, read from the run's `/run/api/session`).
- "Reports" lists the issue reports collected from the rigs ([below](#issue-reports)).
- The hub serves MCP at `/mcp`, so agents can act on every rig through one address ([Agents](agents.md#mcp)).
- The page asks for the hub's token (Sign in) and keeps it in a cookie. The browser tab shows "(n)" while runs wait.
- Times are stored in UTC everywhere (records, logs, reports). The pages show them in local time, or in UTC with the
  clock toggle in the top bar. The browser remembers the choice. On the run page, the toggle is under View ▸ Times.
- Each rig is proxied under `/rigs/<id>/` (page, run, APIs), so the web UI, [`RunClient`][clabe.server.RunClient]
  and [agents](agents.md) work across the fleet with one token.

## Configure

Settings go in `docker/hub/.env` (copy `docker/hub/.env.example`). The names have no prefix because only the hub's
container sees them:

```bash
RIGS=rig-3=http://rig-3:24070;rig-4=http://rig-4:24070     # id=url; or JSON, for per-rig tokens
# TOKEN=...                                              # optional: the hub's own token
# RIG_TOKEN=...                                          # optional: the rigs' DAEMON_CLABE_TOKEN
```

| Variable | Default | What it does |
|---|---|---|
| `TOKEN` | none | Optional: when set, the hub's API requires it |
| `RIG_TOKEN` | none | The rigs' daemon token, when they have one |
| `RIGS` | none | `id=url;...`, or `[{"id": ..., "url": ..., "token": ...}]` |
| `PORT` | `8080` | The hub's port |
| `POLL_INTERVAL` | `2` | Seconds between polls of each rig |
| `DATA_DIR` | `./hub-data` (`/data` in Docker) | Where collected issue reports are kept |
| `REPORT_INTERVAL` | `5` | Seconds between collections of a rig's reports |
| `REPORT_RETENTION_DAYS` | none | Optional: delete reports collected longer ago; kept forever if unset |


## Run it

```bash
cp docker/hub/.env.example docker/hub/.env   # then fill it in
docker compose -f docker/hub/compose.yml up -d --build
```

Without Docker, run `clabe hub run` in a folder that has that `.env` (or with those variables set).

## Issue reports

The hub collects the reports filed on each rig ("Report an issue"). It downloads each report, stores it, and only then
deletes it on the rig. If a report with the same id is already stored, the hub deletes it on the rig without storing
it again. A failed collection is retried at the next poll.

| Kept in | What |
|---|---|
| `<DATA_DIR>/hub.db` | SQLite: one row per report (its `report.json`, rig id, received time, status, size) |
| `<DATA_DIR>/reports/<id>.zip` | The report's zip, as collected |

- In Docker, `DATA_DIR` is `/data`, the named volume `hub-data`. It survives `docker compose down` and rebuilds.
- `docker compose down -v` deletes the volume and every report in it.
- Delete reports from the page or the API. `REPORT_RETENTION_DAYS` deletes old reports at start and then every hour.
- Each report has its own address (`/?tab=reports&report=<id>`): **Copy link** on the report copies it to share.
  Whoever opens it signs in if the hub has a token, then sees the report.

## API

Interactive docs at `/docs`.

| Endpoint | Does |
|---|---|
| `GET /api/hub/rigs` | Every rig, as last polled (with its live `session`) |
| `GET /api/hub/inbox` | Rigs waiting for an answer |
| `POST /api/hub/refresh` | Poll now (and collect reports) |
| `GET /api/hub/reports` | Reports: `rig`, `experiment`, `status`, `q` (text anywhere), `sort`, `order`, `limit`, `offset` → `{total, items}` |
| `GET /api/hub/reports/{id}` | One report |
| `PATCH /api/hub/reports/{id}` | `{"status": "new" \| "looking" \| "closed"}` |
| `DELETE /api/hub/reports/{id}` | Delete one |
| `POST /api/hub/reports/delete` | `{"ids": [...]}`: delete several → `{deleted}` |
| `GET /api/hub/reports/{id}/zip` | Its zip |
| `GET /api/hub/reports/{id}/files` | Its files: `[{path, size}]` |
| `GET /api/hub/reports/{id}/files/{path}` | A file's text (up to 20 MB) |
| `POST /api/hub/inspect` | Open an export without storing it (zip as body or form `file`) → `{inspect_id, files, report}` |
| `GET /api/hub/inspect/{inspect_id}/files/{path}` | A file of it (kept 1 h after last use) |
| `POST /api/login`, `POST /api/logout` | Sign a browser in (`{"token"}`: sets a cookie) or out |
| `/mcp` | MCP (streamable HTTP), with the hub's token |
| `/rigs/<id>/...` | That rig's daemon, e.g. `/rigs/rig-3/run/api/events` |
