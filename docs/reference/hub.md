# Hub reference

Settings, enrollment and API of the hub. [The hub](../rigs/hub.md) explains what it does.

## Configure

Settings go in `docker/hub/.env` (copy `docker/hub/.env.example`). The names have no prefix because only the hub's
container sees them:

```bash
TOKEN=...                                                # the hub's own token
```

| Variable | Default | What it does |
|---|---|---|
| `TOKEN` | none | The hub's page and API require it. Set it: the hub can drive every rig it follows |
| `PORT` | `8080` | The hub's port |
| `POLL_INTERVAL` | `2` | Seconds between polls of each rig |
| `DATA_DIR` | `./hub-data` (`/data` in Docker) | Where enrolled rigs, exclusions and collected issue reports are kept |
| `REPORT_INTERVAL` | `5` | Seconds between collections of a rig's reports |
| `REPORT_RETENTION_DAYS` | none | Optional: delete reports collected longer ago; kept forever if unset |
| `LOG_LEVEL` | `INFO` | What the hub logs to stdout: rigs enrolling, reachable or lost, runs starting and finishing, prompts waiting, reports collected |
| `ACCESS_LOG` | `false` | Also log every HTTP request |

Without Docker, run `clabe hub run` in a folder that has that `.env` (or with those variables set).

## Rigs enroll themselves

The hub has no list of rigs. Each rig's [daemon](daemon.md#hubs) enrolls itself: when it starts, and every 5 minutes
after that, it sends the hub its name and where to reach it. Adding a rig is deploying its daemon with the hub's
address:

```bash
DAEMON_CLABE_HUBS=https://hub-vm/clabe;https://other-hub          # one or more hubs
```

- The hub follows the rig at `http://<host>:<port>` from then on, polling it like any client. The rig never sends
  its status, only that it exists.
- A rig's name and host default to its computer's name, and the port is the daemon's own. Set
  `DAEMON_CLABE_ENROLL_NAME` for another name, and `DAEMON_CLABE_ENROLL_HOST` when the hub cannot reach the rig by
  its computer's name (e.g. a hub in Docker on the same PC: `host.docker.internal`).
- Enrolling needs no secret, so a rig can enroll with any number of hubs. A hub that restarted, moved or lost its
  data fills up again within minutes, without touching the rigs.
- **Moves are confirmed.** A known rig that enrolls from another address shows **moved**, with both addresses. Until
  someone clicks **Confirm move** on its card (or `POST /api/hub/rigs/<id>/confirm`), the hub does not poll it, send
  it its token or forward requests to it. A machine cannot take over a rig's name and collect its token or answers.
  A rig that comes back to its confirmed address is followed again without a click.
- Rigs that answer are listed first. Those that do not are grouped under **Offline**.
- **Remove** a rig with the bin on its card (or `DELETE /api/hub/rigs/<id>`). A rig whose daemon still enrolls with
  this hub comes back within minutes; **Remove and exclude** keeps it out.
- **Exclude** rigs with glob patterns (`BEH.*`, `rig-3`, `wxvs-*`), matched without case against a rig's name and its
  host. Excluding removes the matching rigs and refuses them when they enroll, until the exclusion is removed.
  Manage exclusions under **Rigs ▸ Excluded** on the page, or with the API below.
- Enrolled rigs and exclusions are kept in `<DATA_DIR>/hub.db`, with the issue reports.

### Rig tokens

A rig with a `DAEMON_CLABE_TOKEN` requires it from every client, the hub included. Enter it in the hub with **Token**
on the rig's card (or `PUT /api/hub/rigs/<id>/token`). People using the hub only need the hub's `TOKEN`: the hub
presents each rig's token for them.

- A rig that refuses the hub's token shows **needs token**.
- The page and the API never show a token back, only whether one is entered ("Token set").
- Tokens are kept in `hub.db` as plain text: the hub must send them to the rigs. Protect the data volume like the
  `.env` file.
- Enrolling again keeps a rig's token. Removing a rig deletes it.

## Issue reports

The hub collects the reports filed on each rig ("Report an issue"). It downloads each report, stores it, and only then
deletes it on the rig. If a report with the same id is already stored, the hub deletes it on the rig without storing
it again. A failed collection is retried at the next poll.

| Kept in | What |
|---|---|
| `<DATA_DIR>/hub.db` | SQLite: one row per report (its `report.json`, rig id, received time, status, size) |
| `<DATA_DIR>/reports/<id>.zip` | The report's zip, as collected |

- In Docker, `DATA_DIR` is `/data`, the volume `hub_hub-data`. It survives `docker compose down`, `up` and rebuilds.
- `docker compose down -v` deletes the volume: every report, enrolled rig and exclusion in it.
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
| `POST /api/hub/enroll` | A rig enrolls: `{"id", "host", "port", "clabe_version"}` (no token); 403 if excluded |
| `DELETE /api/hub/rigs/{id}` | Remove a rig and its token (it comes back if it enrolls again) |
| `POST /api/hub/rigs/{id}/confirm` | Confirm a rig's move to the address it enrolled from |
| `PUT /api/hub/rigs/{id}/token` | `{"token": "..."}`: the rig's `DAEMON_CLABE_TOKEN`; `null` deletes it |
| `PUT /api/hub/rigs/{id}/frontend` | `{"frontend": "none" \| "tui" \| "browser"}`: what the rig's page offers next |
| `POST /api/hub/rigs/{id}/revive` | `{"username", "password"}`: restart its daemon over SSH |
| `GET /api/hub/exclusions` | The exclusions: `[{pattern, comment, added_at}]` |
| `POST /api/hub/exclusions` | `{"pattern", "comment"}`: exclude → `{exclusion, removed}` (the rigs removed) |
| `DELETE /api/hub/exclusions?pattern=...` | Remove an exclusion |
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
