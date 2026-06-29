# Discord import — vytiahnutie párov z vlákna cez n8n

Load BEFORE preberaním manažérových párovaní z Discord vlákna (napr. „forestshop nové produkty na kontrolu" — vlákno `1485971625592492153`, guild `1169565132616118293`).

## Vzor vlákna (overené)

Vlákno = `weppy`/radek hádže **link každého nového forestshop produktu** (na kontrolu) + **forwardnutú notifikáciu s dodávateľským linkom**. Manažér: *„tieto z discordu sú správne, označiť ako napárované"* → sú to autoritatívne páry forestshop↔dodávateľ, **netreba AI ani gather**, len zapísať link.

## KĽÚČOVÝ GOTCHA — dodávateľský link je v `message_snapshots`, NIE v `content`

Forwardnuté notifikácie Discord drží v `message_snapshots[].message` (`.content` / `.embeds`), **nie** v top-level `content` správy (to je prázdne). Prvý sweep cez `content`+`embeds` videl len forestshop linky a 0 dodávateľov — dodávatelia (wetland/odimon/lasting/knifestock…) vyskočili až z `message_snapshots`. **Vždy extrahuj aj `message_snapshots[].message.{content,embeds}`.**

## Párovanie podľa susednosti

Časová os (sort podľa `timestamp`): **SUP správa (forward, dodávateľ) → o ~1 s FS správa (weppy, forestshop link)**. Páruj: každý SUP s NASLEDUJÚCIM FS v okne (≤600 s), použitý SUP vynuluj. (537 správ → 209 čistých párov.)

## n8n extraktor (Discord MCP nemá prístup do vlákna → cez n8n bota)

Discord MCP `fetch_messages` na vlákno = `Missing Access` (access.json allowlist). n8n má **Discord Bot credential `discordBotApi` (id `xe68aBPJTWMMucwP`, projekt Marek Drlik)** — bot je v guilde. Wf „Extract Discord Thread" (`fotzFgz48O3t5dSk`, manuálny trigger):

- Node `n8n-nodes-base.discord` v2, `resource=message, operation=getAll`, `authentication=botToken`.
- **`channelId` (mode id) = vlákno** AJ **`guildId` (mode id) = guild** — *guildId je POVINNÝ* aj keď ťaháš podľa channel ID (inak „Parameter Server is required").
- `returnAll: true`, `options.simplify: false` (plné raw dáta vrátane snapshots).
- Spusti cez MCP `execute_workflow(manual)` → výstup je veľký (~900 KB / 537 správ) → `get_execution(includeData)` padne na limit, dáta sa uložia do súboru; extrahuj cez `jq`/python z `.data.resultData.runData["Fetch Thread Messages"][0].data.main[0]`.

## Zápis do párovača

Páry sú `forestshop_url → supplier_url`. Slug→kód cez marketing XML `ORIG_URL` (len vlastný CODE + VARIANT, NIE RELATED). Potom podľa toho či je produkt v review_data → viď skill `webreview` (decisions vs order_pairings, ako pridať pre-napárovaný produkt).
