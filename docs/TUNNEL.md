# Cloudflare quick tunnel — public demo URL in one command

EarningsEdge uses `cloudflared`'s free quick-tunnel feature to expose
the local FastAPI server at a public `*.trycloudflare.com` URL. No
account, no DNS, no certificates. Two-minute setup.

## Why this and not Cloud Run / Render?

- **Render** is the persistent deploy path (`render.yaml` in repo root).
  Use it for keeping the demo URL alive across days.
- **Cloud Run** works for stateless services but its egress NAT range is
  intermittently blocked by Atlas free tier — we have hit this before
  with sibling projects. Skip unless you have an M10+ cluster.
- **Cloudflare quick tunnel** is the fastest path to a hackathon-judgeable
  URL for a local dev session. No paid tier; URL changes per process,
  which is fine for a live judging session.

## Install (one time)

```bash
brew install cloudflared           # macOS
# or download the binary for your OS:
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

## Run

```bash
cd <repo-root>
./scripts/start_demo.sh
# Logs:
#   tail -f .cloudflared/uvicorn.log  .cloudflared/mcp.log  .cloudflared/tunnel.log
# Waiting for tunnel URL …
#
#  PUBLIC URL → https://regulations-invitation-insert-calendar.trycloudflare.com
```

That URL stays alive until you `Ctrl-C`. Each restart of the script gets
a new URL (paste the fresh one into the Devpost submission's *Try it
out* field on submission day).

## Stop

`Ctrl-C` in the foreground terminal — the script's trap kills uvicorn,
the MCP server, and cloudflared cleanly.

If a previous run crashed and left orphans:

```bash
cat .cloudflared/{uvicorn,mcp,tunnel}.pid | xargs kill 2>/dev/null
rm -f .cloudflared/*.pid
```

## Common issues

| Symptom | Fix |
| --- | --- |
| `cloudflared not found` | `brew install cloudflared` |
| Tunnel URL never appears | `cat .cloudflared/tunnel.log` — usually a transient Cloudflare edge issue. Re-run. |
| `Application startup complete.` doesn't show | `cat .cloudflared/uvicorn.log` — check `.env` is present and complete. |
| `mongodb-mcp-server` fails to start | `cat .cloudflared/mcp.log`. The script tolerates this — uvicorn falls back to direct pymongo for writes. |
| Browser blocks WebSocket on the tunnel URL | Chrome enforces strict mixed-content rules on `https://*.trycloudflare.com`. Make sure you're hitting the tunnel URL, not `http://`. |
