# Craven unsubscribe service

The public landing for the per-recipient unsubscribe links in Craven's cold email.
Verifies the HMAC token (same format and secret as the v1 engine), writes the
suppression to Supabase (`oe_suppression` + `oe_unsubscribes`), confirms to the
human. Harvey mirrors the list each heartbeat and its gate refuses the address.

- `GET /unsubscribe?t=<token>` — confirmation page (no write: mail clients prefetch links)
- `GET /unsubscribe?t=<token>&confirm=1` — one-tap unsubscribe (writes)
- `POST /unsubscribe` with `t=<token>` (the page's button) or an RFC 8058
  `List-Unsubscribe=One-Click` body — writes
- `GET /healthz`
- Invalid/tampered token → 400, nothing changes. No recipient address is logged.

## Deploy (PVE docker-main, VM 200)
```
# on docker-main, from a checkout of the fork (branch craven):
cp services/unsubscribe/.env.example services/unsubscribe/.env   # then fill in
docker compose -f services/unsubscribe/docker-compose.yml up -d --build
curl -s http://127.0.0.1:8090/healthz
```
Then add a public hostname on the cloudflared tunnel (Cloudflare Zero Trust → Networks →
Tunnels → the LXC 101 connector → Public hostnames): `unsubscribe.cravencooling.services` →
`http://192.168.1.150:8090` and set `craven.compliance.unsubscribe_base_url`
in Harvey's `harvey.local.yaml` to `https://<that hostname>`.
