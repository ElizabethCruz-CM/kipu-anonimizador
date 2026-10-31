"""Barrido de fuga: busca en la salida todas las formas reales conocidas en las fuentes."""
import json, sys, re, os, csv, io, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.sources import DirSource, load_all
from app.textutil import fold
src, out = sys.argv[1], sys.argv[2]
data = load_all(DirSource(src), log=lambda *a: None)
forms = collections.defaultdict(set)   # forma -> origen
for r in data["tables"].get("clientes", []):
    n = r["nombre_completo"].split()
    forms[r["nombre_completo"]].add("cli"); forms[" ".join(n[:2])].add("cli2")
    for c in ("num_doc", "correo", "celular", "direccion"):
        if r.get(c): forms[r[c]].add("cli-" + c)
for r in data["tables"].get("empleados", []):
    n = r["nombre"].split(); forms[r["nombre"]].add("emp"); forms[" ".join(n[:2])].add("emp2")
    for c in ("correo", "celular", "documento"):
        if r.get(c): forms[r[c]].add("emp-" + c)
for u in data["users"]:
    if u.get("is_bot"): continue
    rn = u.get("real_name")
    if rn and len(rn.split()) >= 2: forms[rn].add("user")
    pe = u.get("profile", {}).get("email")
    if pe: forms[pe].add("user-email")
recs = {}
for f in ("chats", "docs", "tables"):
    for l in open(f"{out}/{f}.jsonl", encoding="utf-8"):
        r = json.loads(l); recs[r["id"]] = r
def flat(v):
    if isinstance(v, dict): return " | ".join(str(k) + " | " + flat(x) for k, x in v.items())
    if isinstance(v, list): return " | ".join(flat(x) for x in v)
    return str(v)
big = "\n".join(flat({a: b for a, b in r.items() if a != "id"}) + " ¶" + k for k, r in recs.items())
fb = fold(big); db = re.sub(r"(?<=\d)[ .,'\-/_](?=\d)", "", big)
n = 0
for f, why in sorted(forms.items()):
    d = re.sub(r"\D", "", f)
    if len(d) >= 6 and not re.search(r"[A-Za-z]", f):
        m = re.search(r"(?<!\d)(57)?" + d + r"(?!\d)", db)
        if m:
            n += 1; ctx = db[max(0, m.start()-80):m.end()+40].replace("\n", " ")
            print("NUM", f, why, "::", ctx)
        continue
    ff = fold(f)
    m = re.search(r"(?<![a-z0-9])" + re.escape(ff).replace(r"\ ", r"[\s_]*") + r"(?![a-z0-9])", fb)
    if m:
        n += 1; ctx = big[max(0, m.start()-80):m.end()+60].replace("\n", " ")
        print("TXT", f, why, "::", ctx)
print("total fugas:", n)
