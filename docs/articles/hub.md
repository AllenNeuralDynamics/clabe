# The clabe hub

The hub is an optional fleet page: every rig's status, and every run waiting for an answer, at one address.

- **Waiting for an answer**: runs waiting on a prompt, longest first, with a link to answer.
- **Rigs**: each rig's status (idle, running, leased, offline), experiment and current step.
- Each rig is proxied under `/rigs/<id>/` (page, run, APIs), so the web UI, [`RunClient`][clabe.server.RunClient]
  and [agents](agents.md) work fleet-wide with one token.

## Configure

`docker/hub/.env` (copy `docker/hub/.env.example`); the names have no prefix, as only the hub's container sees them:

```bash
TOKEN=change-me                                          # the hub's own
RIG_TOKEN=the-rigs-daemon-token                          # the rigs' DAEMON_CLABE_TOKEN
RIGS=rig-3=http://rig-3:8070;rig-4=http://rig-4:8070     # id=url; or JSON, for per-rig tokens
```

| Variable | Default | What it does |
|---|---|---|
| `TOKEN` | none | Required by the hub's API |
| `RIG_TOKEN` | none | The rigs' daemon token, unless a rig has its own |
| `RIGS` | none | `id=url;...`, or `[{"id": ..., "url": ..., "token": ...}]` |
| `PORT` | `8080` | The hub's port |
| `POLL_INTERVAL` | `2` | Seconds between polls of each rig |

Rig daemons need a token: with one, they listen on the network.

## Run it

```bash
cp docker/hub/.env.example docker/hub/.env   # fill it in
docker compose -f docker/hub/compose.yml up -d --build
```

Without Docker: `clabe hub run` in a folder with that `.env` (or those variables set).

## API

Interactive docs at `/docs`.

| Endpoint | Does |
|---|---|
| `GET /api/hub/rigs` | Every rig, as last polled |
| `GET /api/hub/inbox` | Rigs waiting for an answer |
| `POST /api/hub/refresh` | Poll now |
| `/rigs/<id>/...` | That rig's daemon, e.g. `/rigs/rig-3/run/api/events` |
