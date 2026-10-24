# Mythras AntiCheat – sito

Sito Flask collegato al **server delle licenze Mythras** (`MYTHRAS_API_URL`):

- **Login / registrazione con Discord** (OAuth2; l'account si crea al primo accesso)
- **Area clienti** (`/dashboard`): le mie licenze (piano, stato, scadenza, server attivi/massimo, lista server), "Link a key" (claim) e "Reset servers" (disattivato fino a `nextReset`)
- **Dashboard admin** (`/admin`, solo `ADMIN_DISCORD_IDS` + **2FA obbligatoria**): Overview (stats + ultimi 20 eventi), Keys (ricerca, filtri piano/stato), Create license (chiave creata con pulsante copia), dettaglio chiave (server, azioni, eventi), Log, Admins (stato 2FA, reset della propria 2FA)
- Tutti i testi del sito sono in inglese

## Sicurezza

- Tutte le chiamate al server licenze partono **solo dal backend** (`mythras_api.py`) con `Authorization: Bearer $MYTHRAS_API_TOKEN`. Nessuna chiamata dal JavaScript del browser; il token non viene mai mostrato nelle pagine.
- Nell'area clienti l'ID Discord arriva **solo dalla sessione** del server, mai dal browser.
- Ogni azione admin manda `actor = "nome (id discord)"`, visibile nel log e nel canale Discord.
- Chiavi e ID vengono validati e codificati prima di entrare nel percorso dell'API.
- Tutti i form POST hanno protezione CSRF; revoca e reset chiedono conferma.
- **Admin**: chi non è in `ADMIN_DISCORD_IDS` riceve 403 su ogni pagina e ogni POST admin (controllo lato server). Poi 2FA TOTP (`admin_2fa.py`):
  - primo accesso: QR + segreto, il segreto viene salvato (cifrato con `TOTP_ENC_KEY`) solo dopo il primo codice corretto; 10 codici di recupero mostrati una volta e salvati con bcrypt
  - ogni nuova sessione: login Discord, poi codice TOTP o di recupero; nessuna pagina admin funziona prima
  - tolleranza ±1 step (30 s), codici già usati rifiutati (anche tra worker diversi), 5 errori = blocco 15 minuti, tentativi falliti nel log
  - 2FA valida max 12 ore; creare una chiave, revocare e cambiare piano richiedono un codice degli ultimi 15 minuti
- Se un'azione viene rifiutata, il sito mostra il messaggio `error` dell'API.

## Variabili d'ambiente

Vedi `.env.example`. Sull'hosting impostale come variabili d'ambiente; in locale puoi copiarle in `.env` (escluso da git).

| Variabile | Note |
|---|---|
| `MYTHRAS_API_URL` | `http://127.0.0.1:8080` sullo stesso VPS del server licenze (`api-local-only=true`); altrimenti l'indirizzo HTTPS con `api-local-only=false` |
| `MYTHRAS_API_TOKEN` | `api-token` di `license.properties`. Mai nel codice o nel browser |
| `ADMIN_DISCORD_IDS` | ID Discord admin, separati da virgola |
| `TOTP_ENC_KEY` | chiave Fernet per cifrare i segreti 2FA: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Fanne un backup |
| `TRUST_PROXY` | `1` dietro Caddy sulla stessa macchina |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI` | app OAuth2 su Discord |
| `SECRET_KEY` | stringa casuale lunga (sessioni) |
| `COOKIE_SECURE` | `1` in produzione (HTTPS) |
| `BUY_URL_*`, `SUPPORT_DISCORD_URL` | link acquisto e supporto |

## Avvio locale

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Apri <http://localhost:5000>.

## Deploy su VPS

Guida completa (HTTPS con Caddy, gunicorn, systemd, firewall, sicurezza): [deploy/DEPLOY.md](deploy/DEPLOY.md).

## App Discord

1. <https://discord.com/developers/applications> → **New Application**.
2. **OAuth2**: copia Client ID e Client Secret.
3. **OAuth2 → Redirects**: aggiungi `http://localhost:5000/auth/discord/callback` (in produzione il tuo dominio + `/auth/discord/callback`).

## Video del replay

Il video nella sezione Replay (`static/video/replay-demo.mp4` + `replay-poster.jpg`) è una **simulazione generata da codice**, non gameplay reale. Per rigenerarlo:

```bash
.venv\Scripts\pip install -r tools/requirements.txt
.venv\Scripts\python tools/render_replay.py
```

I tempi degli eventi nello script devono corrispondere ai `data-t` della lista eventi in `templates/index.html`. Per un video vero registra il replay del plugin (es. OBS, 1280x720, ~12 s) e sostituisci i due file con gli stessi nomi.

## Note

- Il database SQLite locale (`data/mythras.db`) contiene solo gli utenti Discord che hanno fatto login (nomi mostrati in admin). Le licenze stanno sul server Mythras.
- I piani del server licenze sono `free` e `pro`; gli add-on `antixray` e `antiesp` stanno sulla chiave (`addons`). Vedi PROMPT-PIANI-ADDON.md.
