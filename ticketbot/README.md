# Bot dei ticket

- In **#tickets** c'è un pannello con due pulsanti: **❓ General Questions** e **💳 Billing & Sales**.
- Il cliente preme un pulsante, compila un breve modulo e si apre un **canale privato** in **💰 BILLING & SALES**.
- Il canale lo vedono **solo il cliente, lo staff e il bot**.
- Lo staff preme **🙋 Claim** per prendere il ticket in carico, e **🔒 Close** per chiuderlo (può farlo anche il cliente).
- Alla chiusura la conversazione (con immagini e file) viene salvata sul sito. In **#transcripts** arriva il link
  `https://www.mythras.eu/transcripts/...`, e il cliente lo riceve in privato.
- La pagina della trascrizione si apre solo con il login Discord. La vedono:
  - chi ha aperto il ticket;
  - gli admin del sito (`ADMIN_DISCORD_IDS`);
  - chi sul server Discord ha Amministratore o uno dei ruoli dello staff.

  Tutti gli altri ricevono un errore 403.
- Se lo staff ha risposto e il cliente non scrive più, dopo 24 ore arriva un promemoria e dopo 48 ore il ticket si
  chiude da solo. Un ticket a cui lo staff non ha ancora risposto non si chiude mai da solo.
- Tutte le trascrizioni si trovano anche in **Admin → Tickets** sul sito.

## 1. Crea un'applicazione Discord nuova per i ticket

Il bot del server licenze riceve i suoi pulsanti tramite l'"Interactions Endpoint URL", quindi non può gestire anche questi.
Il bot dei ticket deve essere **un'applicazione a parte**.

1. Vai su https://discord.com/developers/applications → **New Application** → chiamala `Mythras Tickets`.
2. Apri la pagina **Bot**:
   - **Reset Token** → **Copy** e conservalo (serve al punto 4);
   - attiva **Message Content Intent**: senza, le trascrizioni restano vuote;
   - disattiva **Public Bot**, così nessun altro può aggiungerlo al proprio server.
3. Invitalo nel server aprendo questo link (metti l'ID della nuova applicazione, lo trovi in **General Information**):
   ```
   https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot%20applications.commands&permissions=268692496
   ```
   Questi sono solo i permessi che servono: vedere, scrivere, gestire canali e permessi, gestire messaggi,
   allegati e menzioni dei ruoli dello staff. **Non serve Amministratore.**
4. Nel server: **Impostazioni server → Membri** → dai al bot il ruolo **🤖 Bots**. È il ruolo che può creare canali in
   💰 BILLING & SALES e scrivere in #transcripts.

## 2. Impostazioni sul VPS

Aggiungi queste righe in fondo a `/etc/mythras-site.env` (`nano /etc/mythras-site.env`):

```
TICKETS_BOT_TOKEN=IL_TOKEN_DEL_PUNTO_1
TICKETS_GUILD_ID=1425194804106629234
TICKETS_CATEGORY=💰 BILLING & SALES
TICKETS_TRANSCRIPTS_CHANNEL=📑┃transcripts
TICKETS_STAFF_ROLES=🎫 Support,🔧 Developer,🛡️ Admin,👑 Founder
TICKETS_BILLING_ROLES=
TICKETS_INACTIVE_HOURS=48
SITE_URL=https://www.mythras.eu
```

- Canali e ruoli puoi scriverli **col nome esatto** (come sopra) o con l'**ID**.
- `TICKETS_BILLING_ROLES`:
  - vuoto = i ticket di pagamento li vede tutto lo staff;
  - con un ruolo (per esempio `TICKETS_BILLING_ROLES=🛡️ Admin,👑 Founder`) = i ticket di pagamento li vedono solo quei
    ruoli e gli amministratori, sia su Discord sia sul sito.

Il token serve anche al sito: lo usa per controllare chi è staff quando apre una trascrizione. Resta solo nel backend,
non arriva mai al browser.

## 3. Avvio

Aggiorna il sito come sempre (sezione "Aggiornare il sito" di `deploy/DEPLOY.md`), oppure sul VPS:

```bash
bash /opt/mythras-site/deploy/update.sh /tmp/mythras-site.tgz
```

`update.sh` installa discord.py, copia il servizio `mythras-tickets` e lo avvia, perché trova `TICKETS_BOT_TOKEN`.
Se aggiorni a mano:

```bash
/opt/mythras-site/.venv/bin/pip install -r /opt/mythras-site/ticketbot/requirements.txt
cp /opt/mythras-site/deploy/systemd/mythras-tickets.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now mythras-tickets
systemctl restart mythras-site
```

Controlla che sia partito:

```bash
journalctl -u mythras-tickets -n 20 --no-pager
```

Devi vedere:

```
category: #💰 BILLING & SALES (...)
transcripts channel: #📑┃transcripts (...)
general staff: 🎫 Support, 🔧 Developer, 🛡️ Admin, 👑 Founder
billing staff: ...
ready as Mythras Tickets#...
```

Se c'è `NOT FOUND`, il nome in `/etc/mythras-site.env` non è identico a quello su Discord: correggilo e poi lancia
`systemctl restart mythras-tickets`.

## 4. Pubblica il pannello

1. In **#🎫┃tickets** scrivi `/ticketpanel`. Lo vedono e lo possono usare solo gli amministratori.
2. Il pannello appare sotto il messaggio fissato con le regole dei ticket.

Il pannello va pubblicato **una volta sola**: i pulsanti continuano a funzionare anche dopo i riavvii del bot.

## 5. Prova

Con un account **normale**, senza ruoli dello staff:

1. Premi **💳 Billing & Sales** e compila il modulo: si apre `billing-0001-nome` in 💰 BILLING & SALES.
2. Prova a premere di nuovo: il bot deve rispondere che hai già un ticket aperto.
3. Premi **🙋 Claim**: deve dire che solo lo staff può farlo.
4. Con l'account dello staff premi **🙋 Claim**, poi **🔒 Close** e scrivi un motivo.
5. In #📑┃transcripts deve arrivare il riepilogo con il pulsante **Open transcript**.
6. Apri il link con l'account normale: vedi il tuo ticket. Con un terzo account senza ruoli: errore 403.

## Comandi per lo staff (dentro un ticket)

| Comando | Cosa fa |
|---|---|
| pulsante **🙋 Claim** | Prendi in carico il ticket (solo staff) |
| pulsante **🔒 Close** | Chiude e salva la trascrizione (staff o chi ha aperto il ticket) |
| `/ticket add @utente` | Aggiunge un'altra persona al ticket |
| `/ticket remove @utente` | La toglie |
| `/ticket close motivo` | Come il pulsante Close |
| `/ticketpanel` | Pubblica il pannello nel canale (solo amministratori) |

## Problemi

| Cosa vedi | Soluzione |
|---|---|
| "Tickets are not set up yet" | `TICKETS_CATEGORY` non trovata: guarda i log, punto 3 |
| "Something went wrong" all'apertura | Il bot non ha il ruolo 🤖 Bots o la categoria non gli dà "Gestisci canali" |
| Trascrizioni senza testo | Attiva **Message Content Intent** (punto 1.2) e riavvia il bot |
| Niente in #transcripts | `TICKETS_TRANSCRIPTS_CHANNEL` sbagliato o il bot non può scrivere lì (ruolo 🤖 Bots) |
| `/ticketpanel` non compare | Hai invitato il bot senza `applications.commands`: rifai il link del punto 1.3 |
| Lo staff ha 403 sulla trascrizione | Deve fare il login sul sito con lo stesso account Discord e avere uno dei `TICKETS_STAFF_ROLES` |
