"""Pipeline completo: fuentes -> output/ (chats, docs, tables, entities)."""
import copy, json, os, re, time
from collections import defaultdict

from .anonymizer import Registry, pseudo_attr, pseudo_barrio, digits, norm_phone
from .lexicon import sector, KEPT
from .replacer import Replacer
from .textutil import fold, date_forms
from . import pseudo as P

STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(__file__), "..", "state", "pseudonyms.json"))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


class Anonymizer:
    """Mantiene el registro y el replacer de la última corrida (lo usa /anonymize)."""

    def __init__(self, data, log=print):
        self.log = log
        self.data = data
        self.reg = Registry(load_state())
        self.reg.build(data)
        from . import anonymizer as A
        for t in data["tables"].values():
            for r in t:
                for v in r.values():
                    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                        A.REAL_DATES.add(v)
        texts = [m["text"] for m in data["chats"]] + [d["body"] for d in data["docs"]]
        texts += [v for d in data["docs"] for v in d["metadata"].values()]
        for t in data["tables"].values():
            for r in t:
                texts.extend(v for k, v in r.items() if k in ("descripcion", "respuesta", "notas", "reporta_a"))
        self.reg.add_text_persons(texts)
        self.reg.assign_pseudonyms()
        save_state(self.reg.state)
        self.barrios = defaultdict(set)
        for r in data["tables"].get("clientes", []):
            if r.get("ciudad") and r.get("barrio"):
                self.barrios[r["ciudad"]].add(r["barrio"])
        self.rep = Replacer(self.reg, self.barrios)
        # contexto por hilo
        self.thread_persons = defaultdict(set)
        self.thread_client = {}
        for m in data["chats"]:
            k = self.reg.by_uid.get(m.get("_uid"))
            if k:
                self.thread_persons[m["thread_id"]].add(k)
                if not self.reg.persons[k].staff and m["thread_id"] not in self.thread_client:
                    self.thread_client[m["thread_id"]] = k
        thread_text = defaultdict(list)
        for m in data["chats"]:
            thread_text[m["thread_id"]].append(m["text"])
        for th, ts in thread_text.items():
            self.thread_persons[th] |= self._strong_ids("\n".join(ts)) | self._name_ids("\n".join(ts))

    # ---------------------------------------------------------- contexto
    def _name_ids(self, text):
        """Personas nombradas con nombre completo sin ambigüedad en el texto."""
        out = set()
        rx = self.rep.global_rx
        if rx is None:
            return out
        for m in rx.finditer(fold(text)):
            ks = self.rep.global_forms.get(re.sub(r"[\s_]+", " ", m.group(0)))
            if ks and len(ks) == 1:
                out |= ks
        return out

    def _strong_ids(self, text):
        out = set()
        for m in re.finditer(r"CL-\d{4,6}", text):
            k = self.reg.by_cliente.get(m.group(0))
            if k: out.add(k)
        for m in re.finditer(r"PQR-\d{4}-\d{3,5}", text):
            k = self.reg.radicados.get(m.group(0))
            if k: out.add(k)
        for m in re.finditer(r"KP-\d{3}", text):
            k = self.reg.by_emp.get(m.group(0))
            if k: out.add(k)
        d = digits(text)
        if d:
            for m in re.finditer(r"\+?\d[\d .\-]{4,}\d", text):
                dd = digits(m.group(0))
                for c in (dd, dd[2:] if dd.startswith("57") else None):
                    if c and c in self.reg.by_digits:
                        out.add(self.reg.by_digits[c][0])
        tl = text.lower()
        for m in re.finditer(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", tl):
            k = self.reg.by_email.get(m.group(0))
            if k: out.add(k)
        return out

    # ---------------------------------------------------------- registros
    def chat(self, rec):
        th = rec["thread_id"]
        uid = rec.get("_uid")
        author_key = self.reg.by_uid.get(uid)
        persons = set(self.thread_persons.get(th, set())) | self._strong_ids(rec.get("text", ""))
        if author_key:
            persons.add(author_key)
        owner = None
        if author_key and not self.reg.persons[author_key].staff:
            owner = author_key
        else:
            owner = self.thread_client.get(th)
        ctx = {"persons": persons, "owner": owner, "quasi": True, "author": author_key}
        out = {"id": rec["id"], "thread_id": th}
        if author_key:
            p = self.reg.persons[author_key]
            out["author"] = p.pseudo_for(rec["author"]) if rec["author"] else p.ps["name"]
            p.records.add(rec["id"]); p.originals.add(rec["author"])
        else:
            out["author"] = self.rep.apply(rec["author"], ctx, rec["id"])
        out["ts"] = rec["ts"]
        out["text"] = self.rep.apply(rec.get("text", ""), ctx, rec["id"])
        out["attachments"] = [{"filename": self.filename(a.get("filename", ""), ctx, rec["id"])} for a in rec.get("attachments", [])]
        return out

    def filename(self, fn, ctx, rid):
        if not fn:
            return fn
        return self.rep.apply(fn, dict(ctx, quasi=False), rid)

    def doc(self, rec):
        text_all = rec["body"] + "\n" + "\n".join(rec["metadata"].values()) + "\n" + rec["filename"]
        persons = self._strong_ids(text_all) | self._name_ids(text_all)
        ctx = {"persons": persons, "owner": None, "quasi": True}
        rid = rec["id"]
        out = {"id": rid, "format": rec["format"]}
        out["filename"] = self.filename(rec["filename"], ctx, rid)
        out["title"] = self.rep.apply(rec["title"], ctx, rid)
        out["body"] = self.rep.apply(rec["body"], ctx, rid)
        meta = {}
        for k, v in rec["metadata"].items():
            meta[k] = self.rep.apply(v, ctx, rid) if isinstance(v, str) else v
        out["metadata"] = meta
        return out

    def table_row(self, table, row):
        keycol = {"clientes": "cliente_id", "transacciones": "tx_id", "pqrs": "radicado", "empleados": "id"}[table]
        rid = f"{table}:{row.get(keycol, '')}"
        cols = {k: ("" if v is None else str(v)) for k, v in row.items()}
        reg = self.reg
        if table == "clientes":
            k = reg.by_cliente.get(row["cliente_id"])
            p = reg.persons[k]
            p.records.add(rid)
            ctx = {"persons": {k}, "owner": k, "quasi": True}
            for c in ("nombre_completo", "num_doc", "correo", "celular", "direccion", "fecha_nacimiento"):
                if cols.get(c): p.originals.add(cols[c])
            cols["nombre_completo"] = " ".join(p.pseudo_tokens) if cols.get("nombre_completo") else ""
            if cols.get("num_doc"): cols["num_doc"] = pseudo_attr(p, "cedula", cols["num_doc"])
            if cols.get("correo"): cols["correo"] = p.map_email.get(cols["correo"].lower(), cols["correo"])
            if cols.get("celular"): cols["celular"] = pseudo_attr(p, "phone", cols["celular"])
            if cols.get("direccion"): cols["direccion"] = pseudo_attr(p, "address", cols["direccion"])
            if cols.get("fecha_nacimiento"): cols["fecha_nacimiento"] = pseudo_attr(p, "birth", cols["fecha_nacimiento"])
            if cols.get("barrio"):
                cols["barrio"] = pseudo_barrio(p.key, cols.get("ciudad", ""), cols["barrio"], self.barrios)
            if cols.get("ocupacion"): cols["ocupacion"] = sector(cols["ocupacion"])
            if cols.get("notas"): cols["notas"] = self.rep.apply(cols["notas"], ctx, rid)
        elif table == "empleados":
            k = reg.by_emp.get(row["id"])
            p = reg.persons[k]
            p.records.add(rid)
            ctx = {"persons": {k}, "owner": None, "quasi": False}
            for c in ("nombre", "correo", "celular", "documento", "fecha_nacimiento"):
                if cols.get(c): p.originals.add(cols[c])
            cols["nombre"] = " ".join(p.pseudo_tokens)
            if cols.get("correo"): cols["correo"] = p.map_email.get(cols["correo"].lower(), cols["correo"])
            if cols.get("celular"): cols["celular"] = pseudo_attr(p, "phone", cols["celular"])
            if cols.get("documento"): cols["documento"] = pseudo_attr(p, "cedula", cols["documento"])
            if cols.get("fecha_nacimiento"): cols["fecha_nacimiento"] = pseudo_attr(p, "birth", cols["fecha_nacimiento"])
            if cols.get("reporta_a"):
                staff = {k2 for k2, p2 in reg.persons.items() if p2.kind == "employee"}
                cols["reporta_a"] = self.rep.apply(cols["reporta_a"], {"persons": staff, "owner": None}, rid)
        elif table == "pqrs":
            k = reg.by_cliente.get(row.get("cliente_id", ""))
            persons = {k} if k else set()
            ag = reg.by_handle.get((row.get("agente") or "").lower())
            if ag: persons.add(ag)
            ctx = {"persons": persons, "owner": k, "quasi": True}
            if k:
                p = reg.persons[k]; p.records.add(rid)
                if cols.get("cedula"):
                    p.originals.add(cols["cedula"])
                    cols["cedula"] = pseudo_attr(p, "cedula", cols["cedula"])
            if ag and cols.get("agente"):
                pa = reg.persons[ag]; pa.records.add(rid); pa.originals.add(cols["agente"])
                cols["agente"] = pa.map_handle.get(cols["agente"].lower(), cols["agente"])
            for c in ("descripcion", "respuesta"):
                if cols.get(c): cols[c] = self.rep.apply(cols[c], ctx, rid)
        elif table == "transacciones":
            k = reg.by_cliente.get(row.get("cliente_id", ""))
            ctx = {"persons": {k} if k else set(), "owner": k, "quasi": False}
            if cols.get("descripcion"):
                cols["descripcion"] = self.rep.apply(cols["descripcion"], ctx, rid)
        return {"id": rid, "table": table, "columns": cols}

    # ---------------------------------------------------------- entidades
    def entities(self, out_text=None):
        ents = []
        for key in sorted(self.reg.persons):
            p = self.reg.persons[key]
            if not p.records:
                continue
            if p.tokens:
                p.ps.setdefault("name", " ".join(p.pseudo_tokens[:2]))
            originals = set(p.originals)
            originals |= set(p.cedulas) | set(p.phones) | set(p.emails) | set(p.addresses) | set(p.births)
            originals |= set(p.plates) | set(p.passports) | set(p.accounts)
            for b in p.births:
                pass
            ps = {k: v for k, v in p.ps.items() if v}
            if out_text is not None:
                blobs = [fold(out_text.get(r, "")) for r in p.records]
                def used(v):
                    fv = fold(v)
                    dv = digits(v)
                    for b in blobs:
                        if fv in b:
                            return True
                        if len(dv) >= 6 and dv in digits(b):
                            return True
                    return False
                ps = {k: v for k, v in ps.items() if used(v) or (k == "name" and not any(used(x) for x in ps.values()))}
            if not ps:
                continue
            ents.append({"entity_id": p.pid, "type": "person",
                         "originals": sorted(o for o in originals if o),
                         "pseudonyms": ps, "records": sorted(p.records)})
        for i, k in enumerate(sorted(self.rep.kept_hits)):
            ents.append({"entity_id": f"kept_{i:03d}", "type": "organization_or_place", "originals": [k],
                         "pseudonyms": {}, "kept": True,
                         "reason": "organización, lugar, marca o figura pública; no es persona natural",
                         "records": sorted(self.rep.kept_hits[k])})
        return ents


def flat(rec):
    parts = []
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                parts.append(str(k)); walk(x)
        elif isinstance(v, list):
            for x in v: walk(x)
        elif v is not None:
            parts.append(str(v))
    walk({k: v for k, v in rec.items() if k != "id"})
    return " | ".join(parts)


def run_pipeline(data, outdir, log=print):
    t0 = time.time()
    out_text = {}
    an = Anonymizer(data, log)
    log(f"personas: {len(an.reg.persons)}")
    os.makedirs(outdir, exist_ok=True)
    counts = {}
    with open(os.path.join(outdir, "chats.jsonl.tmp"), "w", encoding="utf-8") as f:
        for m in data["chats"]:
            o = an.chat(m); out_text[o["id"]] = flat(o)
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    counts["chats"] = len(data["chats"])
    with open(os.path.join(outdir, "docs.jsonl.tmp"), "w", encoding="utf-8") as f:
        for d in data["docs"]:
            o = an.doc(d); out_text[o["id"]] = flat(o)
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    counts["docs"] = len(data["docs"])
    n = 0
    with open(os.path.join(outdir, "tables.jsonl.tmp"), "w", encoding="utf-8") as f:
        for t in ("clientes", "empleados", "pqrs", "transacciones"):
            for r in data["tables"].get(t, []):
                o = an.table_row(t, r); out_text[o["id"]] = flat(o)
                f.write(json.dumps(o, ensure_ascii=False) + "\n"); n += 1
    counts["tables"] = n
    ents = an.entities(out_text)
    with open(os.path.join(outdir, "entities.json.tmp"), "w", encoding="utf-8") as f:
        json.dump({"entities": ents}, f, ensure_ascii=False, indent=1)
    for name in ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json"):
        os.replace(os.path.join(outdir, name + ".tmp"), os.path.join(outdir, name))
    save_state(an.reg.state)
    log(f"listo en {time.time() - t0:.1f}s: {counts}")
    return an, counts, time.time() - t0


# ======================================================================= modo en vivo (/anonymize)
def _unknown_ids(reg, text):
    """Identificadores fuertes del texto que no pertenecen a nadie conocido (desempatan homónimos nuevos)."""
    out = []
    for m in re.finditer(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or ""):
        if m.group(0).lower() not in reg.by_email:
            out.append(m.group(0).lower())
    for m in re.finditer(r"(?<![\w])\+?\d[\d .\-]{4,}\d(?![\w])", text or ""):
        d = digits(m.group(0))
        if 7 <= len(d) <= 12 and norm_phone(d) not in reg.by_digits and d not in reg.by_digits:
            before = (text or "")[max(0, m.start() - 30):m.start()].lower()
            if re.search(r"c[eé]dula|\bcc\b|documento|cel|tel|n[uú]mero|whats", before):
                out.append(d)
    return "|".join(sorted(out)[:1])


def _record_people(an, text, suf):
    """Personas nombradas en el registro; si un nombre es ambiguo, prefiere la variante con el identificador del registro."""
    out = set()
    rx = an.rep.global_rx
    if rx is None:
        return out
    for m in rx.finditer(fold(text)):
        ks = an.rep.global_forms.get(re.sub(r"[\s_]+", " ", m.group(0))) or set()
        if len(ks) == 1:
            out |= ks
        elif ks:
            pref = [k for k in ks if suf and k.endswith("|" + suf)]
            if not pref:
                pref = [k for k in ks if not k.startswith("txt:")] or [k for k in ks if "|" not in k]
            if len(pref) == 1:
                out.add(pref[0])
    return out


def _attach_suffix(reg, p, suf):
    """El identificador nuevo que desempata a un homónimo queda como parte de esa persona."""
    if not suf:
        return
    if "@" in suf:
        p.emails.add(suf); reg.by_email[suf] = p.key
    else:
        kind = "phone" if (len(suf) == 10 and suf.startswith("3")) else "cedula"
        (p.phones if kind == "phone" else p.cedulas).add(suf)
        reg.by_digits[norm_phone(suf) if kind == "phone" else suf] = (p.key, kind)


def anonymize_records(an, source, records):
    """Anonimiza registros nuevos reutilizando el registro de la corrida. Devuelve (records, entities)."""
    reg = an.reg
    # 1. personas nuevas mencionadas en los registros
    texts, sufs = [], []
    for r in records:
        if source == "chats":
            blob = (r.get("author") or "") + " . " + (r.get("text") or "")
        elif source == "docs":
            blob = " . ".join([r.get("title") or "", r.get("body") or ""] + [str(v) for v in (r.get("metadata") or {}).values()])
        else:
            blob = " . ".join(str(v) for v in (r.get("columns") or {}).values())
        texts.append(blob); sufs.append(_unknown_ids(reg, blob))
    before = set(reg.persons)
    # autores de chat desconocidos (nombre completo en author)
    for r, suf in zip(records, sufs):
        if source == "chats" and r.get("author"):
            a = r["author"].strip()
            if len(a.split()) >= 2 and not an.rep.global_forms.get(fold(a)) and fold(a) not in [fold(k) for k in KEPT]:
                key = "txt:" + fold(a) + ("|" + suf if suf else "")
                p = reg.get(key, "text"); p.set_name(a)
                _attach_suffix(reg, p, suf)
    for k in reg.add_text_persons(texts, sufs):
        _attach_suffix(reg, reg.persons[k], k.split("|", 1)[1] if "|" in k else "")
    if set(reg.persons) != before:
        reg.assign_pseudonyms()
        save_state(reg.state)
        an.rep._build_forms()
    # 2. reemplazo
    for p in reg.persons.values():
        p._live_records = set(); p._live_orig = set()
    out = []
    touched = defaultdict(set)
    for r in records:
        rid = r.get("id")
        snap = {k: (set(p.records), set(p.originals)) for k, p in reg.persons.items()}
        if source == "chats":
            th = r.get("thread_id", "")
            persons = set(an.thread_persons.get(th, set())) | an._strong_ids(r.get("text", "")) | an._name_ids((r.get("author") or "") + "\n" + (r.get("text") or ""))
            persons |= _record_people(an, (r.get("author") or "") + "\n" + (r.get("text") or ""), sufs[records.index(r)])
            author = r.get("author") or ""
            ak = None
            ks = an.rep.global_forms.get(fold(author))
            if ks:
                ak = an.rep._choose(ks, {"persons": persons}, author, len(author))
            elif author:
                ks = [k for k in reg.persons if fold(" ".join(reg.persons[k].tokens)) == fold(author)]
                ak = ks[0] if ks else None
            if ak: persons.add(ak)
            owner = ak if ak and not reg.persons[ak].staff else an.thread_client.get(th)
            ctx = {"persons": persons, "owner": owner, "quasi": True, "author": ak}
            o = {"id": rid, "thread_id": th, "ts": r.get("ts", "")}
            if ak:
                pa = reg.persons[ak]; o["author"] = pa.pseudo_for(author); pa.records.add(rid); pa.originals.add(author)
            else:
                o["author"] = an.rep.apply(author, ctx, rid)
            o["text"] = an.rep.apply(r.get("text", ""), ctx, rid)
            o["attachments"] = [{"filename": an.filename(a.get("filename", ""), ctx, rid)} for a in (r.get("attachments") or [])]
        elif source == "docs":
            rec = {"id": rid, "format": r.get("format", "md"), "filename": r.get("filename", ""), "title": r.get("title", ""),
                   "body": r.get("body", ""), "metadata": {k: str(v) for k, v in (r.get("metadata") or {}).items()}}
            o = an.doc(rec)
        else:
            table = r.get("table") or (rid or "").split(":")[0]
            cols = dict(r.get("columns") or {})
            known_table = table in ("clientes", "empleados", "pqrs", "transacciones")
            keycol = {"clientes": "cliente_id", "transacciones": "tx_id", "pqrs": "radicado", "empleados": "id"}.get(table)
            if table == "clientes" and cols.get("cliente_id") not in reg.by_cliente:
                o = _new_table_row(an, table, rid, cols)
            elif table == "empleados" and cols.get("id") not in reg.by_emp:
                o = _new_table_row(an, table, rid, cols)
            elif known_table:
                o = an.table_row(table, cols)
                o["id"] = rid or o["id"]
            else:
                ctx = {"persons": an._strong_ids(" ".join(map(str, cols.values()))), "owner": None, "quasi": True}
                o = {"id": rid, "table": table, "columns": {k: an.rep.apply(str(v), ctx, rid) for k, v in cols.items()}}
        out.append(o)
        for k, p in reg.persons.items():
            old_r, old_o = snap.get(k, (set(), set()))
            if rid in p.records:
                touched[k].add(rid)
                p._live_orig = getattr(p, "_live_orig", set()) | (p.originals - old_o)
    ents = []
    for k, rids in touched.items():
        p = reg.persons[k]
        ps = {a: b for a, b in p.ps.items() if b}
        if p.tokens and "name" not in ps:
            ps["name"] = " ".join(p.pseudo_tokens[:2])
        if not ps and p.map_email:
            ps["email"] = sorted(p.map_email.values())[0]
        if not ps:
            continue
        ents.append({"entity_id": p.pid, "type": "person", "originals": sorted(getattr(p, "_live_orig", set()) or p.originals),
                     "pseudonyms": ps, "records": sorted(rids)})
    return out, ents


def _new_table_row(an, table, rid, cols):
    """Fila nueva de clientes/empleados que no estaba en la corrida: persona nueva con identidad seudónima."""
    reg = an.reg
    if table == "clientes":
        key = cols.get("cliente_id") or rid
        p = reg.get(key, "client"); p.cliente_id = cols.get("cliente_id", "")
        p.set_name(cols.get("nombre_completo", ""))
        if cols.get("correo"): p.emails.add(cols["correo"])
        if cols.get("num_doc"): p.cedulas.add(cols["num_doc"])
        if cols.get("celular"): p.phones.add(cols["celular"])
        if cols.get("direccion"): p.addresses.add(cols["direccion"])
        if cols.get("fecha_nacimiento"): p.births.add(cols["fecha_nacimiento"])
        p.city, p.barrio, p.occupation = cols.get("ciudad", ""), cols.get("barrio", ""), cols.get("ocupacion", "")
        if p.cliente_id: reg.by_cliente[p.cliente_id] = key
    else:
        key = cols.get("id") or rid
        p = reg.get(key, "employee"); p.emp_id = cols.get("id", "")
        p.set_name(cols.get("nombre", ""))
        if cols.get("correo"): p.emails.add(cols["correo"])
        if cols.get("documento"): p.cedulas.add(cols["documento"])
        if cols.get("celular"): p.phones.add(cols["celular"])
        if cols.get("fecha_nacimiento"): p.births.add(cols["fecha_nacimiento"])
        if p.emp_id: reg.by_emp[p.emp_id] = key
    for e in p.emails:
        loc = e.split("@")[0]
        if "." in loc or e.endswith("@kipupay.co"):
            p.handles.add(loc)
    reg._index_basic()
    reg.assign_pseudonyms(); save_state(reg.state)
    an.rep._build_forms()
    o = an.table_row(table, cols)
    o["id"] = rid or o["id"]
    return o
