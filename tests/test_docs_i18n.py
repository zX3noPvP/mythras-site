import os, sys, html, re
os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:1"
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import app as m, docs
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
expect = {"en": ["10. What's new", "Mythras Cloud AI (Pro)", "/ac ai review", "/ac lang [code]", "/ac settings", "1. Requirements", "Mythras: customer guide", "Turn your alerts on/off", "Hits from too far away"],
          "it": ["10. Novità", "Revisione dei sospetti", "Mostra o cambia la lingua", "1. Requisiti", "Mythras: guida per i clienti", "Attiva/disattiva i tuoi alert", "Colpi da troppo lontano", "Cerca nei Docs", "Per usare Pro"],
          "de": ["1. Voraussetzungen", "Kundenanleitung", "Deine Alarme ein/aus"],
          "fr": ["1. Prérequis", "guide client", "Activer/désactiver tes alertes"],
          "es": ["1. Requisitos", "guía para clientes", "Activar/desactivar tus alertas"]}
for lang, words in expect.items():
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    r = c.get("/docs"); raw = r.data.decode(); t = html.unescape(raw)
    check(f"{lang}: 200", r.status_code == 200)
    check(f"{lang}: translated texts", all(w in t for w in words))
    check(f"{lang}: markup rendered, not escaped", "&lt;code&gt;plugins" not in raw and "&lt;b&gt;" not in raw and "<code>plugins/Mythras/license.yml</code>" in raw)
    check(f"{lang}: commands unchanged", "mlicense MYTH-XXXX-XXXX-XXXX-XXXX" in t and "/ac ai review" in t and "%mythras_checked%" in t and "banwave" not in t.lower() and "ban wave" not in t.lower())
    check(f"{lang}: all {len(docs.CHECKS)} checks listed", len(re.findall(r'<tr data-plan=', raw)) == len(docs.CHECKS))
c = m.app.test_client(); c.get("/lang/it")
t = html.unescape(c.get("/docs").data.decode())
check("it: no English guide sentences left", not any(s in t for s in ["The key is saved in", "On our Discord server", "Main options", "What it detects", "Players menu"]))
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
