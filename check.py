"""Chequeos locales: validador + tests públicos/muestra + barrido de fuga global."""
import json, sys, subprocess, re, os
sys.path.insert(0, os.path.dirname(__file__))
from app.textutil import fold
out, tests_path = sys.argv[1], sys.argv[2]
recs = {}
for f in ("chats", "docs", "tables"):
    for l in open(f"{out}/{f}.jsonl", encoding="utf-8"):
        r = json.loads(l); recs[r["id"]] = r
ents = json.load(open(f"{out}/entities.json"))["entities"]
def flat(v):
    if isinstance(v, dict): return " | ".join(str(k) + " | " + flat(x) for k, x in v.items())
    if isinstance(v, list): return " | ".join(flat(x) for x in v)
    return str(v)
txt = {k: flat({a: b for a, b in r.items() if a != "id"}) for k, r in recs.items()}
def contains(h, n):
    d = re.sub(r"\D", "", n)
    if len(d) >= 6 and not re.search(r"[a-zA-Z]", n):
        return d in re.sub(r"\D", "", h) and re.search(r"(?<!\d)(57)?" + d + r"(?!\d)", re.sub(r"(?<=\d)[ .,'\-/_](?=\d)", "", h)) is not None
    return re.search(r"(?<![a-z0-9])" + re.escape(fold(n)).replace(r"\ ", r"[\s_]*") + r"(?![a-z0-9])", fold(h)) is not None
tests = json.load(open(tests_path))
for t in tests:
    fam = t["family"]; ok = True; info = ""
    if fam == "indirecta":
        recs_ = [t["locator"]["record"]] + t.get("join_records", [])
        blob = " ".join(txt.get(r, "") for r in recs_)
        present = [pl for pl in t["payload"] if contains(blob, pl)]
        ok = len(present) < len(t["payload"]); info = f"presentes {present}"
    elif fam in ("directa", "oculta"):
        for pl in t["payload"]:
            where = [k for k, v in txt.items() if contains(v, pl)]
            if where: ok = False; info = f"{pl!r} en {where[:3]}"
    elif fam == "falso_positivo":
        r = t["locator"]["record"]
        for pl in t["payload"]:
            if pl not in txt.get(r, ""): ok = False; info = f"{pl!r} desapareció"
    elif fam == "homonimos":
        info = "manual"
    elif fam == "consistencia":
        lf = t.get("lookup_forms") or []
        es = [e for e in ents if any(o in lf for o in e.get("originals", []))]
        info = f"{len(es)} entidades: " + "; ".join(json.dumps(e['pseudonyms'], ensure_ascii=False) for e in es)
        ok = len(es) == 1
    print(("OK  " if ok else "FAIL"), t["id"], fam, t["mechanism"], info[:300])
