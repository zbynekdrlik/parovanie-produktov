# parovanie_produktov

Nástroj: páruje forestshop (Shoptet) produkty na produktové stránky dodávateľov a píše URL do `textProperty10` (pre auto-doobjednávanie). Detail: `README.md`, spec v `docs/superpowers/specs/`.

## Playbook router
Load the matching skill BEFORE working on that area (don't re-derive):
- dodávatelia / recon webu / pridanie dodávateľa / parsovanie výsledkov → load `.claude/skills/suppliers`
- deploy / verejná linka / cloudflare tunel / systemd služby → load `.claude/skills/deploy`

## Always
- Kódovanie I/O = **cp1250**, `;`, CRLF (Shoptet import).
- Testy bez živej siete (uložené HTML fixtúry): `.venv/bin/pytest`. Beh: `PYTHONPATH=src`.
- Dáta (`data/`) a `.venv/` sú gitignored; veľký export sa necommituje.
