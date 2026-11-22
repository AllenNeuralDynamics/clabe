# The clabe hub

The hub is an optional service for a fleet of rigs. It follows each rig through its [daemon](daemon.md) and shows,
on one page:

- **Waiting for an answer** — every run that waits on a prompt, longest-waiting first, with a link to answer it;
- **Rigs** — each rig's status (idle, running, leased by another rig's run, offline), experiment and current step.

Each rig is re-exposed under `/rigs/<id>/`: its page, its run (`/rigs/<id>/run/`) and both APIs. The web UI, scripts
using [`RunClient`][clabe.server.RunClient] and agents therefore work fleet-wide through one address and one token.
Rigs never depend on the hub: it dials in as an ordinary client of their daemons.

## Configure

```yaml
hub:
  port: 8080
  poll_interval: 2
  rigs:
    - {id: rig-3, url: "http://rig-3:8070"}
    - {id: rig-4, url: "http://rig-4:8070", token: "<rig-4's own token>"}
```

The hub's own token comes from `$CLABE_HUB_TOKEN` (or `token:`); a rig's token from its entry, else `$CLABE_TOKEN`.
The rigs' daemons must listen on the network (`daemon: host: 0.0.0.0`).

## Run it with Docker

The image builds the web UI and installs clabe; it needs no hardware and runs on any Linux server.

```bash
cp docker/hub/hub.example.yml docker/hub/hub.yml   # list your rigs
CLABE_HUB_TOKEN=... CLABE_TOKEN=... docker compose -f docker/hub/compose.yml up -d
```

Or without Docker: `clabe hub run` (it reads the `hub:` section of `clabe.yml`).

## API

Described at `/docs`: `GET /api/hub/rigs` (every rig, as last polled), `GET /api/hub/inbox` (the rigs waiting for
an answer), `POST /api/hub/refresh` (poll now), and each rig's daemon under `/rigs/<id>/` — e.g.
`/rigs/rig-3/run/api/events` follows rig-3's run.
