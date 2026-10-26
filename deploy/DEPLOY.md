# Deploy su VPS (Ubuntu 22.04 / 24.04) – tutto da root

Schema finale:

```
Internet ──443──▶ Caddy (HTTPS automatico, file statici; serve anche license.mythras.eu)
                    └──▶ 127.0.0.1:8000 gunicorn (utente di servizio "mythras", systemd blindato)
                              └──▶ 127.0.0.1:8080 server licenze (stesso VPS, token solo lato server)
```

- VPS: `85.155.151.23`, sito: **www.mythras.eu** (solo con www), server licenze: **license.mythras.eu**.
- Tutti i comandi si lanciano **sul VPS come root**, tranne quelli segnati **PC** (PowerShell su Windows).
- Il sito va sullo **stesso VPS** del server licenze: con `api-local-only=true` l'API accetta solo chiamate da `127.0.0.1`. Le porte **8080 e 8000 non si aprono** nel firewall.

---

## 0. DNS

Nel pannello del dominio (OVH):
- `A` · `www` → `85.155.151.23` (il sito)
- `A` · `license` → `85.155.151.23` (server licenze, se non c'è già)

## 1. Entrare nel VPS

**PC**:

```bash
ssh root@85.155.151.23
```

Password di root lunga e sicura (16+ caratteri):

```bash
passwd
```

## 2. Aggiornamenti, ora, firewall

```bash
apt update && apt -y upgrade
```

```bash
apt -y install ufw unattended-upgrades sqlite3 python3 python3-venv python3-pip
```

```bash
timedatectl set-timezone Europe/Rome && timedatectl set-ntp true
```

```bash
timedatectl
```
(deve dire `System clock synchronized: yes`)

Firewall: **prima** apri SSH, poi attivalo:

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
```

```bash
ufw enable
```

```bash
ufw status
```

Aggiornamenti di sicurezza automatici (rispondi **Yes**):

```bash
dpkg-reconfigure -plow unattended-upgrades
```

### 2b. (Consigliato) Swap da 2 GB – evita blocchi/riavvii per RAM finita

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
```

```bash
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

```bash
free -h
```

## 3. Caricare il sito

**PC** (l'archivio è già pronto in `C:\Users\Luca\Desktop\MC-AI\mythras-site.tgz`):

```bash
scp C:\Users\Luca\Desktop\MC-AI\mythras-site.tgz root@85.155.151.23:/tmp/
```

**VPS**:

```bash
useradd --system --home /opt/mythras-site --shell /usr/sbin/nologin mythras
```

```bash
mkdir -p /opt/mythras-site && tar -xzf /tmp/mythras-site.tgz -C /opt/mythras-site --strip-components=1
```

```bash
python3 -m venv /opt/mythras-site/.venv
```

```bash
/opt/mythras-site/.venv/bin/pip install -r /opt/mythras-site/requirements.txt
```

Permessi (codice in sola lettura per il sito, solo `data/` scrivibile, Caddy legge solo `static/`):

```bash
mkdir -p /opt/mythras-site/data && chown -R root:mythras /opt/mythras-site && chmod -R u=rwX,g=rX,o= /opt/mythras-site && chown mythras:mythras /opt/mythras-site/data && chmod 750 /opt/mythras-site/data && chmod o+x /opt/mythras-site && chmod -R o+rX /opt/mythras-site/static
```

## 4. Segreti

Genera **SECRET_KEY** (copia il risultato):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Genera **TOTP_ENC_KEY** (copia il risultato e salvalo anche nel tuo password manager):

```bash
/opt/mythras-site/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Crea il file:

```bash
nano /etc/mythras-site.env
```

Incolla e sostituisci i valori `INCOLLA_...`, poi `Ctrl+O`, Invio, `Ctrl+X`:

```
MYTHRAS_API_URL=http://127.0.0.1:8080
MYTHRAS_API_TOKEN=INCOLLA_IL_TOKEN_DI_license.properties
ADMIN_DISCORD_IDS=1102163747524591636
TOTP_ENC_KEY=INCOLLA_LA_CHIAVE_FERNET
DISCORD_CLIENT_ID=INCOLLA
DISCORD_CLIENT_SECRET=INCOLLA
DISCORD_REDIRECT_URI=https://www.mythras.eu/auth/discord/callback
SECRET_KEY=INCOLLA_LA_SECRET_KEY
COOKIE_SECURE=1
TRUST_PROXY=1
SUPPORT_DISCORD_URL=
FREE_URL_MODRINTH=
FREE_URL_SPIGOT=
FREE_URL_MCMARKET=
LEGAL_CONTROLLER=INCOLLA_NOME_O_RAGIONE_SOCIALE
LEGAL_EMAIL=INCOLLA_EMAIL_DI_CONTATTO
```

```bash
chown root:mythras /etc/mythras-site.env && chmod 640 /etc/mythras-site.env
```

## 5. Discord + avvio del sito

Su <https://discord.com/developers/applications> → la tua app → **OAuth2 → Redirects** aggiungi:
`https://www.mythras.eu/auth/discord/callback`

```bash
cp /opt/mythras-site/deploy/mythras-site.service /etc/systemd/system/ && systemctl daemon-reload
```

```bash
systemctl enable --now mythras-site
```

```bash
systemctl status mythras-site --no-pager
```
(deve dire `active (running)`)

```bash
curl -sI http://127.0.0.1:8000 | head -1
```
(deve dire `200 OK`; se no: `journalctl -u mythras-site -n 50 --no-pager`)

## 6. HTTPS con Caddy

Sul VPS **Caddy** (quello del server licenze) usa già le porte 80/443 e gestisce i certificati da solo:
il sito si aggiunge a Caddy, **niente nginx né certbot**.

nginx non serve (e non deve occupare le porte):

```bash
systemctl disable --now nginx
```

Controlla che `www.mythras.eu` non sia già configurato in Caddy:

```bash
grep -n "www.mythras.eu" /etc/caddy/Caddyfile
```

Backup del Caddyfile:

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
```

Copia la configurazione del sito in `/etc/caddy/` (l'utente `caddy` deve poterla leggere):

```bash
cp /opt/mythras-site/deploy/caddy/mythras.caddy /etc/caddy/mythras.caddy && chmod 644 /etc/caddy/mythras.caddy
```

Aggiungila al Caddyfile:

```bash
echo 'import /etc/caddy/mythras.caddy' >> /etc/caddy/Caddyfile
```

Controlla e ricarica (Caddy chiede i certificati in automatico, il DNS deve già puntare al VPS):

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

```bash
systemctl reload caddy
```

Se qualcosa non va: `journalctl -u caddy -n 50 --no-pager`. Per tornare indietro:
`cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile && systemctl reload caddy`.

## 8. Verifiche

```bash
curl -sI https://www.mythras.eu | grep -iE "HTTP/|strict-transport|content-security|x-frame"
```

```bash
curl -sI http://www.mythras.eu | head -1
```
(deve essere un redirect `308` verso https)

Dal browser:
1. <https://www.mythras.eu> → login con Discord
2. `/admin` → scansiona il QR (Google Authenticator / Authy / 2FAS), inserisci il codice, **salva i 10 codici di recupero**
3. **Create license** → crea una licenza di prova → controlla **Keys** e **Log**
4. <https://www.ssllabs.com/ssltest/> e <https://securityheaders.com> → obiettivo **A**

## 9. Ultimo passo di sicurezza

Rigenera `api-token` in `license.properties` (server licenze), riavvialo, poi:

```bash
nano /etc/mythras-site.env
```

```bash
systemctl restart mythras-site
```

---

## Log conservati al massimo 30 giorni (come dichiarato nella Privacy Policy)

```bash
mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]
MaxRetentionSec=30day
' > /etc/systemd/journald.conf.d/retention.conf && systemctl restart systemd-journald
```

## Shop PayPal e vendite su Discord

PayPal lo gestisce il **server delle licenze** (versione 1.11+), non il sito: Client ID, Secret, piano Pro e webhook
stanno in `license.properties` (`paypal.*`), vedi LICENZE.md "Pagamenti con PayPal". Il sito non ha chiavi PayPal.

- **Pro** = abbonamento PayPal mensile; **Anti-XRay / Anti-ESP** = pagamento unico, per sempre.
- Lo shop è su `/shop`; i prezzi arrivano da `GET /api/shop`. Se il server licenze dice `enabled: false`, i
  pulsanti diventano "Coming soon" e restano le vendite con ticket su Discord.
- In `license.properties` gli indirizzi di ritorno devono puntare al sito:
  `paypal.return-url=https://www.mythras.eu/shop/done` e `paypal.cancel-url=https://www.mythras.eu/shop`.
- **Admin → Acquisti**: abbonamenti e add-on, con **Sync from PayPal** e **Annulla** (solo Pro).
- Vendite su Discord restano possibili: **Admin → Crea licenza** (piano + add-on), **Rinnova (+1 mese)**.

## Link di setup (domande per piano) e IP della chiave

1. Il cliente compra nel ticket; tu crei la chiave (**Admin → Crea licenza**).
2. **Admin → Link di setup** → piano, chiave, (facoltativo) il suo Discord ID → **Crea link** → copialo e mandalo nel ticket.
   Il link funziona **una sola volta**: il cliente entra con Discord, risponde alle domande del suo piano e la
   configurazione va al server licenze, che la passa al plugin al prossimo avvio.
3. Apri le risposte (**Admin → Link di setup → Risposte → Apri**) e premi **Usa <IP>** per far funzionare la chiave solo da
   quell'IP (oppure scrivi gli IP a mano nella pagina della chiave, riquadro **IP consentito**).
4. Le domande le modifichi in **Admin → Domande** (testo, piano, predefinito, avviso in rosso, "mostra solo se").

**Bot Discord**: metti `BOT_API_TOKEN=` (stringa casuale) in `/etc/mythras-site.env`, riavvia, e dal bot chiama:

```bash
curl -s -X POST https://www.mythras.eu/api/bot/invites -H "Authorization: Bearer IL_TOKEN" -H "Content-Type: application/json" -d '{"plan":"pro","discord_id":"123456789012345678","key":"MYTH-XXXX-XXXX-XXXX-XXXX","note":"ticket 12"}'
```

Risposta: `{"ok": true, "url": "https://www.mythras.eu/setup/..."}` (`discord_id`, `key`, `note` e `hours` facoltativi).

IP e configurazione funzionano davvero solo quando il server licenze e il plugin sono aggiornati con
`PROMPT-LICENZE-SETUP.md`.

## Pubblicare una nuova versione del plugin

I clienti con una licenza attiva vedono il pulsante **Scarica** nella dashboard e ricevono sempre **l'ultima versione** pubblicata.
Nome e versione vengono letti dal `plugin.yml` dentro il jar. Il file non è pubblico: si scarica solo da loggati con licenza attiva.

**Dal sito (consigliato):** `https://www.mythras.eu/admin/downloads` → scegli il `.jar` → note di rilascio (facoltative) → **Publish**.
Serve un codice 2FA recente (meno di 15 minuti).

**Da PowerShell sul PC (alternativa):** carica il jar con un nome temporaneo e poi rinominalo, così nessuno scarica un file a metà:

```bash
scp C:\percorso\Mythras.jar root@85.155.151.23:/opt/mythras-site/data/downloads/upload.jar.part
```

```bash
ssh root@85.155.151.23 'mv /opt/mythras-site/data/downloads/upload.jar.part /opt/mythras-site/data/downloads/Mythras-$(date +%Y%m%d-%H%M%S).jar'
```

Il sito usa il jar più recente nella cartella `/opt/mythras-site/data/downloads/` e conserva le ultime 5 versioni.

**MythrasProxy** (Velocity / BungeeCord): stessa pagina, in **Cosa** scegli *MythrasProxy*. La versione viene letta da
`velocity-plugin.json` o `bungee.yml`. I file stanno in `/opt/mythras-site/data/downloads/proxy/` (ultime 5 versioni) e
lo scaricano dalla dashboard solo i clienti con una licenza **Pro** attiva.

## Aggiornare il sito

**PC** – ricrea l'archivio e caricalo:

```bash
tar --exclude=.venv --exclude=data --exclude=.env --exclude=__pycache__ -czf C:\Users\Luca\Desktop\MC-AI\mythras-site.tgz -C C:\Users\Luca\Desktop\MC-AI mythras-anticheat-site
```

```bash
scp C:\Users\Luca\Desktop\MC-AI\mythras-site.tgz root@85.155.151.23:/tmp/
```

**VPS**:

```bash
tar -xzf /tmp/mythras-site.tgz -C /opt/mythras-site --strip-components=1
```

```bash
/opt/mythras-site/.venv/bin/pip install -r /opt/mythras-site/requirements.txt
```

```bash
chown -R root:mythras /opt/mythras-site && chown mythras:mythras /opt/mythras-site/data && chmod -R o+rX /opt/mythras-site/static
```

```bash
systemctl restart mythras-site
```

## Emergenza 2FA

- **Telefono perso**: entra con un codice di recupero → **Admins → Reset my 2FA** → riconfigura.
- **Telefono e codici persi** (o `TOTP_ENC_KEY` persa):

```bash
sqlite3 /opt/mythras-site/data/mythras.db "UPDATE admin_mfa SET secret_enc=NULL, enabled_at=NULL, last_step=0, failed_count=0, locked_until=0 WHERE discord_id='1102163747524591636'; DELETE FROM admin_recovery_codes WHERE discord_id='1102163747524591636';"
```

- **Bloccato 15 minuti** dopo 5 codici sbagliati:

```bash
sqlite3 /opt/mythras-site/data/mythras.db "UPDATE admin_mfa SET failed_count=0, locked_until=0 WHERE discord_id='1102163747524591636';"
```

## Backup

**Prima di ogni aggiornamento** `update.sh` salva il database del sito in `/var/backups/mythras-site/mythras-*.db.gz` (ultime 10).

**Ogni notte alle 04:00** (timer `mythras-backup.timer`, installato da `update.sh`) un backup completo in
`/var/backups/mythras-site/nightly-*.tgz` (ultimi 14): database del sito, `/etc/mythras-site.env`, database,
`license.properties` e **chiavi di firma** del server licenze (`/opt/mythras-license/keys`: senza `private.key` non puoi più creare licenze).
Contengono segreti: restano leggibili solo da root.

Farne uno subito:

```bash
bash /opt/mythras-site/deploy/backup.sh
```

**Copia fuori dal VPS** (se il VPS si rompe, i backup lì sopra spariscono con lui). Dal PC, in PowerShell nella cartella del sito:

```bash
powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1
```

Scarica l'ultimo backup notturno in `Documenti\MythrasBackups` (tiene gli ultimi 30). Fallo almeno una volta a settimana.

Tornare a un backup del database del sito:

```bash
systemctl stop mythras-site && gunzip -c /var/backups/mythras-site/mythras-2026-10-17-134830.db.gz > /opt/mythras-site/data/mythras.db && chown mythras:mythras /opt/mythras-site/data/mythras.db && systemctl start mythras-site
```

## Monitoraggio

Ogni 2 minuti `mythras-healthcheck.timer` controlla `http://127.0.0.1:8000/healthz` (sito, suo database, server licenze)
e il servizio `mythras-license`. Dopo 2 controlli falliti di fila scrive su Discord, e scrive di nuovo quando torna tutto su.

1. Su Discord: Impostazioni server → Integrazioni → Webhook → Nuovo webhook (canale privato dello staff) → Copia URL.
2. In `/etc/mythras-site.env`: `MONITOR_DISCORD_WEBHOOK=URL_COPIATO`.

Se cade **tutto il VPS** questo controllo non può avvisarti: aggiungi anche un monitor esterno gratuito su
<https://uptimerobot.com> → New monitor → HTTP(s) → `https://www.mythras.eu/healthz`, controllo ogni 5 minuti.

## Comandi utili

| Cosa | Comando |
|---|---|
| Stato del sito | `systemctl status mythras-site --no-pager` |
| Log del sito | `journalctl -u mythras-site -f` |
| Riavvio del sito | `systemctl restart mythras-site` |
| Tentativi 2FA falliti | `journalctl -u mythras-site \| grep "2FA failed"` |
| Riavvii del VPS | `last -x reboot shutdown \| head` |
| Memoria | `free -h` |

## Sicurezza attiva

| Livello | Protezione |
|---|---|
| VPS | firewall solo 22/80/443, aggiornamenti di sicurezza automatici, password root lunga |
| Caddy | HTTPS automatico (TLS 1.2/1.3, rinnovo certificati), redirect HTTP→HTTPS, HSTS, body max 64 KB |
| App | CSP con nonce, X-Frame-Options, nosniff, cookie Secure/HttpOnly/SameSite, CSRF su tutti i POST, pagine private `no-store` |
| Admin | 403 per chi non è admin, 2FA TOTP obbligatoria (segreto cifrato, codici di recupero bcrypt, 5 errori = blocco 15 min, sessione max 12 h, codice recente per creare/revocare/cambiare piano) |
| Servizio | utente `mythras` senza shell né privilegi, filesystem in sola lettura tranne `data/`, gunicorn solo su 127.0.0.1 |
| Segreti | solo in `/etc/mythras-site.env` (640), mai nel codice o nel browser |

> Senza chiave SSH e senza fail2ban l'unica difesa di SSH è la password di root: tienila lunga e unica.
