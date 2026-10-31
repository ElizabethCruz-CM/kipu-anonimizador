"""Núcleo: registro de personas, seudónimos deterministas y reemplazo en texto."""
import json, os, re
from collections import defaultdict

from . import pseudo as P
from .lexicon import GIVEN, GIVEN_F, GIVEN_M, KEPT, NOT_NAME_AFTER, sector, OCUPACION_SECTOR
from .textutil import fold, apply_style, date_forms, strip_accents, MESES

DIG = re.compile(r"\d")


def digits(s):
    return "".join(c for c in s if c.isdigit())


def norm_phone(s):
    d = digits(s)
    if len(d) == 12 and d.startswith("57"):
        d = d[2:]
    return d


# ======================================================================= personas
class Person:
    def __init__(self, key, kind):
        self.key = key            # llave estable para el seudónimo (CL-xxx, KP-xxx, uid, txt:...)
        self.kind = kind          # client / employee / user / text
        self.tokens = []          # tokens del nombre completo original
        self.n_given = 1
        self.gender = "F"
        self.names = set()        # formas completas vistas en fuentes
        self.nicks = set()        # apodos / display names (solo en contexto)
        self.emails = set(); self.phones = set(); self.cedulas = set(); self.addresses = set()
        self.births = set(); self.handles = set(); self.uids = set(); self.plates = set()
        self.passports = set(); self.accounts = set()
        self.city = ""; self.barrio = ""; self.occupation = ""; self.vinc_year = ""
        self.cliente_id = ""; self.emp_id = ""
        self.staff = False
        self.records = set()
        self.originals = set()    # formas que de verdad aparecieron en la entrada
        self.pseudo_tokens = []
        self.ps = {}              # seudónimos por tipo (para entities.json)
        self.map_email = {}; self.map_handle = {}
        self.pid = None

    # --- nombre
    def set_name(self, full):
        toks = full.split()
        if not toks:
            return
        if len(toks) > len(self.tokens):
            self.tokens = toks
            n = len(toks)
            if n == 1:
                self.n_given = 1
            elif n == 2:
                self.n_given = 1
            elif n == 3:
                self.n_given = 2 if (toks[1] in GIVEN) else 1
            else:
                self.n_given = n - 2
            first = toks[0]
            if first in GIVEN_F:
                self.gender = "F"
            elif first in GIVEN_M:
                self.gender = "M"
            else:
                self.gender = "F" if strip_accents(first).lower().endswith("a") else "M"
        self.names.add(full)

    @property
    def given(self):
        return self.tokens[: self.n_given]

    @property
    def surn(self):
        return self.tokens[self.n_given:]

    def full(self):
        return " ".join(self.tokens)

    def name_forms(self):
        """(forma, es_global). Global = se reemplaza en cualquier registro."""
        if not self.tokens:
            return {}
        g, s = self.given, self.surn
        out = set()
        for n in self.names:
            out.add((n, len(n.split()) >= 2 and not all(t in GIVEN for t in n.split()[:2]) or len(n.split()) >= 3))
        if s:
            out.add((" ".join(g + s), True))
            out.add((" ".join(g + s[:1]), True))
            out.add((" ".join(g[:1] + s[:1]), True))
            if len(s) >= 2:
                out.add((" ".join(g[:1] + s), True))
            if len(g) >= 2:
                out.add((" ".join(g[1:2] + s[:1]), True))
        if len(g) >= 2:
            out.add((" ".join(g), False))
        out.add((g[0], False))
        res = {}
        for f, gl in out:
            res[f] = res.get(f, False) or gl
        return res

    def pseudo_for(self, form):
        """Mapea una forma (subsecuencia de tokens) al seudónimo alineado."""
        ftoks = form.split()
        ftf = [fold(t) for t in ftoks]
        otf = [fold(t) for t in self.tokens]
        res = []
        used = set()
        for t in ftf:
            idx = None
            for i, o in enumerate(otf):
                if o == t and i not in used:
                    idx = i; break
            if idx is None:
                # token desconocido (variante): usa la posición relativa
                idx = min(len(res), len(self.pseudo_tokens) - 1)
            used.add(idx)
            res.append(self.pseudo_tokens[idx])
        return " ".join(res)


SURNAMES_EXTRA = {fold(x) for x in """Gómez González Rodríguez Martínez García López Hernández Pérez Sánchez Ramírez Torres Díaz Vargas Rojas
Moreno Jiménez Muñoz Castro Ruiz Ortiz Gutiérrez Romero Álvarez Herrera Osorio Cardona Giraldo Henao Arango Ospina Mejía Castaño
Marulanda Hoyos Zapata Velásquez Correa Montoya Londoño Restrepo Betancur Quintero Salazar Agudelo Flórez Ordóñez Aristizábal
Quintanilla Santander Bolívar Carulla Castilla Galán Robledo Cuesta Bermúdez Pineda Naranjo Arias Cortés Rincón Castillo""".split()}
COMMON_CAPS = {fold(x) for x in """Hola Gracias Buenas Buenos Compra Pago Cuenta Tarjeta Crédito Banco Cliente Clienta Señor Señora
Estimado Estimada Querido Querida Asunto Para Por Con Del Las Los Una Uno Este Esta Ese Esa Mi Tu Su Nuestro Nuestra Calle Carrera
Avenida Transferencia Retiro Abono Cuota Informe Acta Minuta Equipo Gerencia Soporte Reclamo Queja Petición Caso Incidente
Política Manual Comunicado Plan Total Saldo Valor Monto Fecha Ciudad Barrio Dirección Correo Celular Teléfono Lunes Martes
Miércoles Jueves Viernes Sábado Domingo Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre""".split()}


# ======================================================================= registro
class Registry:
    def __init__(self, state=None):
        self.persons = {}        # key -> Person
        self.by_email = {}; self.by_digits = {}; self.by_uid = {}; self.by_handle = {}
        self.by_cliente = {}; self.by_emp = {}; self.by_address = {}; self.by_plate = {}
        self.form_index = defaultdict(set)   # forma plegada -> keys
        self.state = state or {}             # seudónimos persistidos {key: tokens}
        self.used_names = set(); self.used_pairs = set()
        self.real_tokens = set()
        self.radicados = {}; self.tx_client = {}

    def get(self, key, kind):
        p = self.persons.get(key)
        if p is None:
            p = self.persons[key] = Person(key, kind)
        return p

    # ------------------------------------------------------------ construcción
    def build(self, data):
        users = data["users"]
        clientes = data["tables"].get("clientes", [])
        empleados = data["tables"].get("empleados", [])
        for r in clientes:
            p = self.get(r["cliente_id"], "client")
            p.cliente_id = r["cliente_id"]
            p.set_name(r.get("nombre_completo", ""))
            for k, tgt in (("correo", p.emails), ("direccion", p.addresses), ("fecha_nacimiento", p.births)):
                if r.get(k):
                    tgt.add(r[k].strip())
            if r.get("num_doc"):
                p.cedulas.add(r["num_doc"].strip())
            if r.get("celular"):
                p.phones.add(r["celular"].strip())
            p.city, p.barrio, p.occupation = r.get("ciudad", ""), r.get("barrio", ""), r.get("ocupacion", "")
            p.vinc_year = (r.get("fecha_vinculacion") or "")[:4]
            self.by_cliente[r["cliente_id"]] = p.key
        for r in empleados:
            p = self.get(r["id"], "employee")
            p.emp_id = r["id"]
            p.staff = True
            p.set_name(r.get("nombre", ""))
            if r.get("correo"): p.emails.add(r["correo"].strip())
            if r.get("celular"): p.phones.add(r["celular"].strip())
            if r.get("documento"): p.cedulas.add(r["documento"].strip())
            if r.get("fecha_nacimiento"): p.births.add(r["fecha_nacimiento"].strip())
            p.city = r.get("ciudad", "")
            self.by_emp[r["id"]] = p.key
        for r in data["tables"].get("pqrs", []):
            if r.get("radicado") and r.get("cliente_id") in self.by_cliente:
                self.radicados[r["radicado"]] = self.by_cliente[r["cliente_id"]]
        for r in data["tables"].get("transacciones", []):
            if r.get("tx_id") and r.get("cliente_id") in self.by_cliente:
                self.tx_client[r["tx_id"]] = self.by_cliente[r["cliente_id"]]
        self._index_basic()
        # usuarios del chat
        thread_text = defaultdict(list); uid_threads = defaultdict(set)
        for m in data["chats"]:
            thread_text[m["thread_id"]].append(m["text"])
            if m.get("_uid"):
                uid_threads[m["_uid"]].add(m["thread_id"])
        # vinculación en dos pasadas: evidencia fuerte primero, luego por eliminación
        pending = []
        taken = set()
        linked = {}
        for u in users:
            if u.get("is_bot") or not (u.get("real_name") or u.get("profile", {}).get("real_name")):
                continue
            prof = u.get("profile", {})
            real = u.get("real_name") or prof.get("real_name")
            email = (prof.get("email") or "").strip()
            key = None
            if email and email.lower() in self.by_email:
                key = self.by_email[email.lower()]
            if key is None:
                key = self._link_user(real, prof.get("title", ""),
                                      [t for th in uid_threads[u["id"]] for t in thread_text[th]], taken, strong_only=True)
            if key is not None:
                taken.add(key); linked[u["id"]] = key
            else:
                pending.append(u)
        for u in pending:
            prof = u.get("profile", {})
            real = u.get("real_name") or prof.get("real_name")
            key = self._link_user(real, prof.get("title", ""),
                                  [t for th in uid_threads[u["id"]] for t in thread_text[th]], taken, strong_only=False)
            if key is not None:
                taken.add(key)
            linked[u["id"]] = key
        for u in users:
            if u["id"] not in linked:
                continue
            prof = u.get("profile", {})
            real = u.get("real_name") or prof.get("real_name")
            email = (prof.get("email") or "").strip()
            key = linked[u["id"]]
            if key is None:
                key = u["id"]
                p = self.get(key, "user")
            else:
                p = self.persons[key]
            p.set_name(real)
            p.uids.add(u["id"])
            if p.kind == "employee" or (prof.get("title") and prof.get("title") != "Cliente"):
                p.staff = True
            if email: p.emails.add(email)
            if prof.get("phone"): p.phones.add(prof["phone"])
            if u.get("name") and u["name"] not in ("deactivateduser",):
                p.handles.add(u["name"])
            dn = prof.get("display_name") or ""
            if dn and fold(dn) not in {fold(t) for t in p.tokens}:
                p.nicks.add(dn)
        # handles derivados de correos corporativos y reporta_a
        for p in self.persons.values():
            for e in p.emails:
                loc = e.split("@")[0]
                if "." in loc or e.endswith("@kipupay.co"):
                    p.handles.add(loc)
        self._index_basic()

    def _link_user(self, real, title, texts, taken=(), strong_only=False):
        """Vincula un usuario cliente del chat con una fila del maestro usando evidencia de sus hilos."""
        f = fold(real)
        cands = [k for k, p in self.persons.items() if p.kind in ("client", "employee")
                 and (fold(p.full()) == f or fold(p.full()).startswith(f + " "))]
        if title and title != "Cliente":
            cands = [k for k in cands if self.persons[k].kind == "employee"] or cands
        else:
            cands = [k for k in cands if self.persons[k].kind == "client"]
        cands = [k for k in cands if k not in taken]
        if not cands:
            return None
        blob = "\n".join(texts)
        fblob = fold(blob); dblob = digits(blob)
        best, best_score = None, 0
        for k in cands:
            p = self.persons[k]
            sc = 0
            for c in p.cedulas:
                if digits(c) and digits(c) in dblob: sc += 5
            for ph in p.phones:
                if norm_phone(ph) and norm_phone(ph) in dblob: sc += 4
            for e in p.emails:
                if e.lower() in blob.lower(): sc += 4
            if fold(p.full()) in fblob: sc += 3
            if p.cliente_id and p.cliente_id in blob: sc += 5
            if sc > best_score:
                best, best_score = k, sc
        if best is not None:
            return best
        if strong_only:
            # nombre completo exacto y único: también es evidencia suficiente
            exact = [k for k in cands if fold(self.persons[k].full()) == f]
            if len(exact) == 1 and len(cands) == 1:
                return exact[0]
            return None
        if len(cands) == 1 and fold(self.persons[cands[0]].full()) != f:
            # nombre corto único (p. ej. "Luis Santander" -> "Luis Santander Moreno"): solo si nadie más lo comparte
            return cands[0]
        if len(cands) == 1:
            return cands[0]
        return None

    def _index_basic(self):
        for k, p in self.persons.items():
            for e in p.emails: self.by_email[e.lower()] = k
            for c in p.cedulas:
                if len(digits(c)) >= 6: self.by_digits.setdefault(digits(c), (k, "cedula"))
            for ph in p.phones:
                if len(norm_phone(ph)) >= 7: self.by_digits.setdefault(norm_phone(ph), (k, "phone"))
            for u in p.uids: self.by_uid[u] = k
            for h in p.handles: self.by_handle[h.lower()] = k
            for a in p.addresses: self.by_address[fold(a)] = k

    # ------------------------------------------------------------ personas solo-texto
    NAME_RX = re.compile(r"(?<![\wÁÉÍÓÚÑáéíóúñ])((?:[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+|[A-ZÁÉÍÓÚÑ]{2,})(?:[ \t]+(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+|[A-ZÁÉÍÓÚÑ]{2,})){1,3})")

    def add_text_persons(self, texts, suffixes=None):
        """Crea personas para nombres completos que no están en ninguna fuente estructurada.
        suffixes: lista paralela a texts con un desambiguador (p. ej. una cédula nueva en el mismo registro)."""
        new_keys = []
        kept_f = {fold(k) for k in KEPT}
        known = {f for f in self.form_index_all() if len(f.split()) >= 2}
        given_f = {fold(g) for g in GIVEN}
        surnames_f = set()
        for p in self.persons.values():
            for t in p.surn: surnames_f.add(fold(t))
        surnames_f |= {fold(a) for a in P.APELLIDOS} | SURNAMES_EXTRA
        for i, t in enumerate(texts):
            for m in self.NAME_RX.finditer(t or ""):
                toks = m.group(1).split()
                pre = fold(t[max(0, m.start() - 25):m.start()])
                cue = re.search(r"(me llamo|mi nombre es|soy|habla|a nombre de|nombre:|titular:|cliente:|clienta:|se[nñ]or|se[nñ]ora|sr\.|sra\.|don|dona|firma:|atentamente,?|cordialmente,?)\s*$", pre)
                # recorta tokens iniciales que no son nombre (Hola, Gracias, Compra...)
                if not cue:
                    while toks and fold(toks[0]) not in given_f:
                        toks = toks[1:]
                    # nombre de pila desconocido seguido de un apellido conocido
                else:
                    toks = toks[:4]
                if len(toks) < 2:
                    continue
                # descarta si el resto no parece apellido (palabra común capitalizada)
                if len(toks) == 2 and fold(toks[1]) in given_f and fold(toks[1]) not in surnames_f:
                    continue  # "Juan Pablo", "Luis Felipe": solo nombres de pila
                full = " ".join(toks)
                ff = fold(full)
                if any(ff == k or k.startswith(ff + " ") or ff.startswith(k + " ") for k in kept_f):
                    continue
                if any(ff == k or k.startswith(ff + " ") for k in known):
                    continue
                if any(ff.startswith(k + " ") for k in known):
                    continue  # "Juliana Mejía Doble": nombre conocido seguido de otra palabra
                key = "txt:" + ff
                if suffixes and suffixes[i]:
                    key += "|" + suffixes[i]
                p = self.get(key, "text")
                p.set_name(full if not full.isupper() else full.title())
                if not (suffixes and suffixes[i]):
                    known.add(ff)
                new_keys.append(key)
        self._index_basic()
        return new_keys

    def form_index_all(self):
        out = {}
        for k, p in self.persons.items():
            for f in p.name_forms():
                out.setdefault(fold(f), set()).add(k)
        return out

    # ------------------------------------------------------------ seudónimos
    def assign_pseudonyms(self):
        # lista fija (no depende de los datos de la corrida) para que el seudónimo no cambie cuando crecen las fuentes
        if not self.real_tokens:
            try:
                with open(os.path.join(os.path.dirname(__file__), "excluded_tokens.json"), encoding="utf-8") as f:
                    self.real_tokens = set(json.load(f))
            except Exception:
                self.real_tokens = set()
        # primero los persistidos
        for key in sorted(self.persons):
            p = self.persons[key]
            if key in self.state and len(self.state[key]) == len(p.tokens):
                p.pseudo_tokens = self.state[key]
                self._reserve(p)
        for key in sorted(self.persons):
            p = self.persons[key]
            if p.pseudo_tokens:
                continue
            attempt = 0
            while True:
                toks = self._gen_tokens(p, attempt)
                full = fold(" ".join(toks)); pair = (fold(toks[0]), fold(toks[min(1, len(toks) - 1)]))
                if full not in self.used_names and pair not in self.used_pairs and not any(fold(t) in self.real_tokens for t in toks):
                    break
                attempt += 1
                if attempt > 60:
                    break
            p.pseudo_tokens = toks
            self._reserve(p)
            self.state[key] = toks
        for i, key in enumerate(sorted(self.persons)):
            p = self.persons[key]
            p.pid = "p_" + format(P.h("pid", key) % 16**8, "08x")
            self._derive(p)

    def _reserve(self, p):
        t = p.pseudo_tokens
        self.used_names.add(fold(" ".join(t)))
        self.used_pairs.add((fold(t[0]), fold(t[min(1, len(t) - 1)])))

    def _gen_tokens(self, p, attempt):
        n = max(len(p.tokens), 1)
        pool = P.NOMBRES_F if p.gender == "F" else P.NOMBRES_M
        out = []
        x = P.h("tok", p.key, attempt)
        for i in range(n):
            if i < p.n_given:
                cand = [g for g in pool if g not in out]
            else:
                cand = [a for a in P.APELLIDOS if a not in out]
            out.append(cand[x % len(cand)]); x //= len(cand)
            if x < 1000: x = P.h("tok", p.key, attempt, i)
        return out

    def _derive(self, p):
        key = p.key
        pt = p.pseudo_tokens
        pg = pt[: p.n_given] or pt[:1]
        psn = pt[p.n_given:] or pt[-1:]
        p.ps["name"] = " ".join(pt[: p.n_given] + pt[p.n_given: p.n_given + 1]) if len(pt) > 1 else pt[0]
        for e in sorted(p.emails):
            p.map_email[e.lower()] = self._pseudo_email(p, e, pg, psn)
        if p.map_email:
            p.ps["email"] = p.map_email[sorted(p.map_email)[0]]
        for h in sorted(p.handles):
            p.map_handle[h.lower()] = self._pseudo_local(p, h, pg, psn)
        if p.handles:
            p.ps["handle"] = p.map_handle[sorted(p.map_handle)[0]]

    def _pseudo_local(self, p, loc, pg, psn):
        lf = fold(loc)
        g0 = fold(p.tokens[0]) if p.tokens else ""
        s = [fold(t) for t in p.surn]
        G = P.slug(pg[0]).replace(".", ""); S = P.slug(psn[0]).replace(".", "") if psn else ""
        seps = [c for c in lf if c in "._-"]
        sep = seps[0] if seps else ""
        if s and lf == f"{g0}{sep}{s[0]}":
            return f"{G}{sep}{S}"
        if s and lf == f"{g0[:1]}{s[0]}":
            return f"{G[:1]}{S}"
        if s and lf.startswith(g0[:1]) and s[0] in lf:
            return f"{G[:1]}{S}"
        if lf == g0:
            return G
        if g0 and lf.startswith(g0) and sep:
            return f"{G}{sep}{S}"
        return f"{G}{sep or '.'}{S}"

    def _pseudo_email(self, p, e, pg, psn):
        loc, _, dom = e.partition("@")
        new = self._pseudo_local(p, loc, pg, psn)
        return f"{new}@{dom}"


# ======================================================================= seudónimos de atributos
def pseudo_attr(p: Person, kind: str, orig: str) -> str:
    """Seudónimo determinista para un identificador de la persona p."""
    key = p.key
    if kind == "cedula":
        v = P.cedula(key + "|" + digits(orig), orig)
        p.ps.setdefault("cedula", digits(v))
        return v
    if kind == "phone":
        v = P.celular(key + "|" + norm_phone(orig), orig)
        p.ps.setdefault("phone", digits(v)[-10:])
        return v
    if kind == "account":
        v = P.generico_alnum(key + "|" + digits(orig), orig, "acct")
        p.ps.setdefault("account", v)
        return v
    if kind == "plate":
        v = P.placa(key + "|" + re.sub(r"[^A-Z0-9]", "", orig.upper()), orig)
        p.ps.setdefault("plate", v)
        return v
    if kind == "passport":
        v = P.pasaporte(key + "|" + re.sub(r"[^A-Z0-9]", "", orig.upper()), orig)
        p.ps.setdefault("passport", v)
        return v
    if kind == "address":
        v = pseudo_address(key + "|" + fold(orig), orig)
        p.ps.setdefault("address", v)
        return v
    if kind == "birth":
        v = pseudo_birth(key, orig)
        p.ps.setdefault("birth_iso", v)
        return v
    raise ValueError(kind)


VIAS = ["Calle", "Carrera", "Cl", "Cra", "Av", "Tv", "Diagonal", "Transversal", "Avenida", "Kr", "Dg"]


def pseudo_address(key, orig):
    x = P.h("addr", key)
    m = re.match(r"\s*([A-Za-zÁÉÍÓÚáéíóú\.]+)", orig)
    via = m.group(1) if m else "Calle"
    if via.lower().rstrip(".") not in [v.lower() for v in VIAS]:
        via = "Calle"
    n1 = 2 + x % 160; x //= 160
    letra = ["", "", "", "A", "B", "C", " Bis"][x % 7]; x //= 7
    n2 = 1 + x % 140; x //= 140
    n3 = 1 + x % 99; x //= 99
    suf = ""
    sm = re.search(r"\b(apto|apartamento|casa|of|oficina|int|interior|torre)\b", orig, re.I)
    if sm:
        suf = f" {sm.group(1)} {101 + x % 1400}"
    return f"{via} {n1}{letra} # {n2}-{n3}{suf}"


REAL_DATES = set()


def pseudo_birth(key, iso, attempt=0):
    try:
        y = int(iso[:4])
    except Exception:
        return iso
    x = P.h("birth", key, attempt) if attempt else P.h("birth", key)
    dy = [-4, -3, -2, 2, 3, 4][x % 6]; x //= 6
    m = 1 + x % 12; x //= 12
    d = 1 + x % 28
    out = f"{y + dy:04d}-{m:02d}-{d:02d}"
    if out in REAL_DATES and attempt < 20:
        return pseudo_birth(key, iso, attempt + 1)
    return out


def pseudo_barrio(key, city, barrio, barrios_by_city):
    opts = sorted(b for b in barrios_by_city.get(city, []) if b != barrio)
    if not opts:
        return barrio
    return opts[P.h("barrio", key) % len(opts)]
