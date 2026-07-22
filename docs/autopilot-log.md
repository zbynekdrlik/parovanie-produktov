## 2026-07-22 — #91 auth (worker)
- #91 prihlasovací systém: RED 52ed16a → GREEN 62677d2 (22-test auth suita: gate/session/CSRF/rate-limit/reset-tokeny/admin CRUD/bootstrap); review fixes 79c3592 (dummy-hash timing) + e05c478 (CSRF bytes, SMTP degrade, rate-limit prune, +2 testy). PR #104, merge 9db7adb, nasadené v0.44.0 (DOM overené na živom webe, PRED==PO stores, n8n bearer intact).
- Bonus bug (pre-existing v0.42): [hidden] vs display:flex — search box presakoval do všetkých tabov; RED 76271be → GREEN bb3354f.
- Rozhodnutia: /api/n8n/* mimo session gate (vlastný bearer); reset-token store hashovaný sha256; bootstrap create-if-missing; legacy testy = reálna session (authed_client + store seed), e2e autouse cookie + @pytest.mark.anonymous.
- Otvorené: #113 SMTP údaje pre reset-mail (needs-answer — čaká na Mareka).
