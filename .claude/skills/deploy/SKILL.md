# Deploy — verejné nasadenie webu cez Cloudflare Tunnel

Load BEFORE vystavovaním lokálneho webu na verejnú doménu (newlevel.media).

## Hotové: DVE verejné adresy → tá istá appka (`:8801`)

- **`forestaci.newlevel.media`** (názov appky „Forestaci", #94) **aj** pôvodná **`parovanie-forestshop.newlevel.media`** (manažér ju používa denne) — OBE cez ten istý tunel na `http://localhost:8801`. Nová sa pridala ADITÍVNE (viď „Pridanie ďalšieho hostname" nižšie); stará ostáva.
- Flask web na dev1 `:8801` → vystavený cez `cloudflared` tunel (žiadny otvorený port navonok).
- Dve `systemd --user` služby (Restart=always, `loginctl enable-linger newlevel`):
  - `parovanie-web.service` — Flask app (WorkingDirectory == repo, `.venv/bin/python webreview/app.py`)
  - `parovanie-tunnel.service` — `cloudflared tunnel run --token ${TUNNEL_TOKEN}` (token z `data/.cf_env`)
- Reštart: `systemctl --user restart parovanie-web parovanie-tunnel`; logy: `journalctl --user -u parovanie-web -f`
- **IDs/token na disku (gitignored)**: `data/.cf_token` = scoped API token (Bearer), `data/.cf_tunnel_id` = tunnel id, `data/.cf_zone` = zone id; account id vytiahni `GET /accounts` tým tokenom (jediný NEWLEVELMEDIA účet). Do skillu NIKDY nedávaj hodnotu tokenu ani account id.

## Pridanie ĎALŠIEHO hostname na EXISTUJÚCI tunel (aditívne — #94)

Appka počúva na ľubovoľný Host (Flask default), takže ďalší verejný názov = len tunel-ingress + DNS, appku reštartovať netreba. `TOKEN=$(cat data/.cf_token)`, `ACCT=<account id — z GET /accounts tokenom>`, `TID=$(cat data/.cf_tunnel_id)`, `ZID=$(cat data/.cf_zone)`:

1. **GET súčasný ingress** `GET /accounts/$ACCT/cfd_tunnel/$TID/configurations` — PUT ho CELÝ prepíše, takže si najprv vytiahni existujúce položky.
2. **PUT** `…/configurations` s CELÝM zoznamom = staré hostnames + NOVÝ `{"hostname":"<host>","service":"http://localhost:8801"}` VLOŽENÝ PRED catch-all `{"service":"http_status:404"}` (ten musí ostať posledný), plus `"warp-routing":{"enabled":false}`.
3. **POST DNS** `POST /zones/$ZID/dns_records` `{"type":"CNAME","name":"<host>","content":"$TID.cfargotunnel.com","proxied":true}`.
4. **Over**: `curl -sI https://<host>/login` → 200 a `https://<starý-host>/login` STÁLE 200 (aditívnosť). Reštart tunela/appky NETREBA (remotely-managed config sa aplikuje sám).

## Postup (API-only, bez interaktívneho login)

Cloudflare account `<ACCOUNT_ID>`, zóna `newlevel.media`. Meta-token
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
