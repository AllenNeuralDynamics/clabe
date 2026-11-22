# The clabe hub

The hub is an optional fleet page: every rig's status, and every run waiting for an answer, at one address.

- **Waiting for an answer**: runs waiting on a prompt, longest first, with a link to answer.
- **Rigs**: each rig's status (idle, running, leased, offline), experiment and current step.
- Each rig is proxied under `/rigs/<id>/` (page, run, APIs), so the web UI, [`RunClient`][clabe.server.RunClient]
  and [agents](agents.md) work fleet-wide with one token.

## Configure

```yaml
hub:
  port: 8080
  poll_interval: 2
  rigs:
    - {id: rig-3, url: "http://rig-3:8070"}
    - {id: rig-4, url: "http://rig-4:8070", token: "<rig-4's own token>"}
```

- Hub token: `$CLABE_HUB_TOKEN` (or `token:`).
- Rig token: the rig's `token:`, else `$CLABE_TOKEN`.
- Rig daemons need a token: with one, they listen on the network.

## Run it

```bash
cp docker/hub/hub.example.yml docker/hub/hub.yml   # list your rigs
CLABE_HUB_TOKEN=... CLABE_TOKEN=... docker compose -f docker/hub/compose.yml up -d --build
```

Without Docker: `clabe hub run` (reads the `hub:` section of `clabe.yml`).

## API

Interactive docs at `/docs`.

| Endpoint | Does |
|---|---|
| `GET /api/hub/rigs` | Every rig, as last polled |
| `GET /api/hub/inbox` | Rigs waiting for an answer |
| `POST /api/hub/refresh` | Poll now |
| `/rigs/<id>/...` | That rig's daemon, e.g. `/rigs/rig-3/run/api/events` |
