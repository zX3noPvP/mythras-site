"""Run every test file and the translation check. A file passes only if it exits with 0 and prints ALL PASSED.

    .venv\\Scripts\\python.exe tests\\run_all.py        (Windows)
    .venv/bin/python tests/run_all.py                   (Linux)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = dict(os.environ, PYTHONIOENCODING="utf-8")
# the tests start their own fake license server: never inherit real settings
for name in list(env):
    if name.startswith(("MYTHRAS_", "DISCORD_", "SUPPORT_", "LEGAL_", "FREE_URL_")) or name in ("COOKIE_SECURE", "SECRET_KEY"):
        env.pop(name)

failed = []
for test in sorted((ROOT / "tests").glob("test_*.py")):
    run = subprocess.run([sys.executable, str(test)], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    ok = run.returncode == 0 and "ALL PASSED" in run.stdout
    print(f"{'ok  ' if ok else 'FAIL'} {test.name}")
    if not ok:
        failed.append(test.name)
        lines = [l for l in (run.stdout + run.stderr).splitlines() if l.startswith("FAIL") or "Error" in l or "Traceback" in l]
        for line in lines[:10]:
            print("     " + line)

run = subprocess.run([sys.executable, str(ROOT / "tools" / "check_translations.py")], cwd=ROOT, env=env,
                     capture_output=True, text=True, encoding="utf-8")
print(f"{'ok  ' if run.returncode == 0 else 'FAIL'} translations")
if run.returncode:
    failed.append("translations")
    print(run.stdout[-2000:])

print(f"\n{len(failed)} failed: {', '.join(failed)}" if failed else "\nEverything passed")
sys.exit(1 if failed else 0)
