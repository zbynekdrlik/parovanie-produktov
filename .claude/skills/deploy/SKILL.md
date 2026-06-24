# Deploy — verejné nasadenie webu cez Cloudflare Tunnel

Load BEFORE vystavovaním lokálneho webu na verejnú doménu (newlevel.media).

## Hotové: parovanie-forestshop.newlevel.media

- Flask web na dev1 `:8801` → vystavený cez `cloudflared` tunel (žiadny otvorený port navonok).
- Dve `systemd --user` služby (Restart=always, `loginctl enable-linger newlevel`):
  - `parovanie-web.service` — Flask app
  - `parovanie-tunnel.service` — `cloudflared tunnel run --token ${TUNNEL_TOKEN}` (token z `data/.cf_env`)
- Reštart: `systemctl --user restart parovanie-web parovanie-tunnel`; logy: `journalctl --user -u parovanie-web -f`

## Postup (API-only, bez interaktívneho login)

Cloudflare account `8f3efbc0edbe05bd6fdcab10cd63876a`, zóna `newlevel.media`. Meta-token
(account-owned, „API Tokens" management) → vytvor scoped token, ním sprav zvyšok.

1. **Scoped token** (POST `/accounts/{acct}/tokens` meta-tokenom). Permission groups:
   Cloudflare Tunnel Write `c07321b023e944ff818fec44d8203567`, DNS Write `4755a26eedb94da69e1066d98aa820be`,
   Zone Read `c8fed203ed3043cba015a93ad1616f1f`, Account Settings Read `c1fde68c7bcc44588cbb6ddbc16d6480`.
   **Zónu vnor pod account resource** (inak chyba 1001):
   `"resources":{"com.cloudflare.api.account.{acct}":{"com.cloudflare.api.account.zone.*":"*"}}`.
2. **Tunel** (remotely-managed): POST `/accounts/{acct}/cfd_tunnel` `{"name":...,"config_src":"cloudflare"}` → vráti `id` + `token`.
3. **Ingress**: PUT `/accounts/{acct}/cfd_tunnel/{id}/configurations`
   `{"config":{"ingress":[{"hostname":"<host>","service":"http://localhost:<port>"},{"service":"http_status:404"}]}}`.
4. **DNS**: POST `/zones/{zid}/dns_records` `{"type":"CNAME","name":"<host>","content":"{id}.cfargotunnel.com","proxied":true}`.
5. **Run**: `cloudflared tunnel run --token <tunnel-token>` (systemd --user, Restart=always).

## Pravidlá

- **Secrety NIKDY do gitu** — tokeny v `data/.cf_*` / `data/.cf_env` (gitignored, `chmod 600`). Skill/CLAUDE.md sú v gite.
- Token sa odovzdáva službe cez `EnvironmentFile`, nie inline v unit súbore.
- DNS/tunel operácie sú aditívne (nový subdomain) — neprepisujú existujúce záznamy.
