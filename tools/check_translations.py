"""Check that every UI string has a translation in every language.

Usage:  .venv\\Scripts\\python.exe tools\\check_translations.py
Finds _("...") in templates and t("...") in app.py, plus the TIERS texts, and
reports missing entries and broken %(placeholders)s in translations/<lang>.json.
To add a language string: add the English text as a key in each JSON file.
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"%\(\w+\)s")
TEMPLATE_CALL = re.compile(r"""_\(\s*(["'])((?:\\.|(?!\1).)*)\1""")


def collect():
    found = set()
    for tpl in (ROOT / "templates").rglob("*.html"):
        for m in TEMPLATE_CALL.finditer(tpl.read_text(encoding="utf-8")):
            found.add(m.group(2).replace("\\'", "'"))
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t" and node.args \
                and isinstance(node.args[0], ast.Constant):
            found.add(node.args[0].value)
        if isinstance(node, ast.Tuple):
            for el in node.elts:
                if isinstance(el, ast.Constant) and isinstance(el.value, str) and "%(n)s" in el.value:
                    found.add(el.value)
    for py in ("app.py", "downloads.py"):
        for node in ast.walk(ast.parse((ROOT / py).read_text(encoding="utf-8"))):
            # raise ValueError("...") and parse_int(raw, min, "message")
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                    and getattr(node.exc.func, "id", None) == "ValueError" and node.exc.args \
                    and isinstance(node.exc.args[0], ast.Constant):
                found.add(node.exc.args[0].value)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "parse_int" \
                    and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value:
                found.add(node.args[2].value)
            # ACTION_MESSAGES = {...}
            if isinstance(node, ast.Assign) and any(getattr(tg, "id", None) == "ACTION_MESSAGES" for tg in node.targets):
                found.update(v.value for v in node.value.values if isinstance(v, ast.Constant))
    def literal(name):  # a top-level dict literal of app.py (TIERS, ADDONS)
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(getattr(tg, "id", None) == name for tg in node.targets):
                return ast.literal_eval(node.value)
        return {}
    for tier in list(literal("TIERS").values()) + list(literal("ADDONS").values()):
        found.update([tier["description"], tier["period"], *tier["features"]])
    found.update({"Active", "Revoked", "Expired", "Unknown", "Never"})
    sys.path.insert(0, str(ROOT))
    import docs
    import setup_forms
    found.update(docs.texts())
    found.update(setup_forms.texts())
    import changelog
    found.update(changelog.KINDS)  # badge names of the official changelog
    return found - {""}


def main():
    needed = collect()
    ok = True
    for path in sorted((ROOT / "translations").glob("*.json")):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        missing = sorted(needed - set(catalog))
        broken = [k for k, v in catalog.items() if sorted(PLACEHOLDER.findall(k)) != sorted(PLACEHOLDER.findall(v))]
        print(f"{path.stem}: {len(catalog)} strings, {len(missing)} missing, {len(broken)} broken placeholders")
        for k in missing:
            print("   missing:", k)
        for k in broken:
            print("   placeholder:", k)
        ok = ok and not missing and not broken
    print("OK" if ok else "PROBLEMS FOUND")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
