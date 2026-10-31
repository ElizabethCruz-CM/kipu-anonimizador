"""Motor de reemplazo: encuentra PII en un texto (con contexto) y lo sustituye por seudónimos."""
import re
from collections import defaultdict

from .anonymizer import digits, norm_phone, pseudo_attr, Person
from .lexicon import GIVEN, KEPT, OCUPACION_SECTOR
from .textutil import fold, apply_style, date_forms, MESES
from . import pseudo as P

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# correo deletreado: "juliana punto mejia arroba gmail punto com"
SPELLED_RX = re.compile(
    r"(?<![a-z0-9])([a-z0-9_]+(?:\s+(?:punto|guion bajo|guion|raya al piso)\s+[a-z0-9_]+)*)\s+(?:arroba|\(at\)|\[at\])\s+([a-z0-9]+(?:\s+punto\s+[a-z0-9]+)+)(?![a-z0-9])")
MENTION_RX = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
DIGRUN_RX = re.compile(r"(?<![\w])\+?\d[\d .\-]{4,}\d(?![\w])")
PLATE_RX = re.compile(r"(?<![A-Za-z0-9\-])([A-Z]{3})[- ]?(\d{3}|\d{2}[A-Z])(?![A-Za-z0-9\-])")
PASSPORT_RX = re.compile(r"(?<![A-Za-z0-9\-])([A-Z]{2}\d{6,7})(?![A-Za-z0-9])")
ADDR_RX = re.compile(
    r"(?<![A-Za-z])(?:Calle|Carrera|Cl|Cll|Cra|Kr|Kra|Av|Avenida|Tv|Transversal|Diagonal|Dg)\.?\s+\d+[A-Za-z]?(?:\s?Bis)?(?:\s+Sur)?\s*(?:#|No\.?|N°)\s*\d+[A-Za-z]?\s*-\s*\d+(?:\s+(?:apto|apartamento|casa|of|oficina|int|interior|torre)\s+\d+)?",
    re.I)
PLATE_BLOCK = {"PQR", "INC", "PSE", "CL", "TX", "KP", "SFC", "COP", "USD", "DOC", "ID", "BOG", "MDE", "CTG", "CLO", "API", "PDF"}
HONOR_RX = r"(?:se[ñn]or|se[ñn]ora|sr\.?|sra\.?|srta\.?|don|do[ñn]a|dr\.?|dra\.?|doctor|doctora|ingeniero|ingeniera|estimado|estimada|apreciado|apreciada)"
CEDULA_CUE = re.compile(r"(c[eé]dula|\bc\.\s?c\.|\bcc\b|\bce\b|documento|identificaci[oó]n|n[uú]mero de doc|\bnit\b|\bes\s*$|\bes la\s*$|\bes el\s*$)", re.I)
PHONE_CUE = re.compile(r"(cel|tel|whats|llam|n[uú]mero|m[oó]vil|contacto|\+57)", re.I)
ACCOUNT_CUE = re.compile(r"(cuenta|ahorros|corriente|llave|daviplata|nequi)", re.I)
YEAR_CTX = re.compile(r"(vinculad[oa]s?|cliente|entr[eé]|abr[ií]|estoy con ustedes|me vincul[eé]|soy cliente|empec[eé])[^.\n]{0,40}?\b(desde|en)\s+(20[0-2]\d)\b", re.I)


class Hit:
    __slots__ = ("start", "end", "new", "prio", "pkey", "orig")

    def __init__(self, start, end, new, prio, pkey, orig):
        self.start, self.end, self.new, self.prio, self.pkey, self.orig = start, end, new, prio, pkey, orig


class Replacer:
    def __init__(self, reg, barrios_by_city, kept_extra=()):
        self.reg = reg
        self.barrios_by_city = barrios_by_city
        self.city_of_barrio = defaultdict(set)
        for c, bs in barrios_by_city.items():
            for b in bs:
                self.city_of_barrio[fold(b)].add(c)
        self.kept = list(KEPT) + list(kept_extra)
        self.kept_f = [fold(k) for k in self.kept]
        self.kept_hits = defaultdict(set)
        self._build_forms()
        self.orphans = {}   # identificadores sin dueño conocido -> Person sintética

    # ---------------------------------------------------------- índice de formas
    def _build_forms(self):
        self.global_forms = defaultdict(set)   # forma plegada -> keys
        self.ctx_forms = defaultdict(set)      # solo con contexto (nombre de pila, apodos)
        for k, p in self.reg.persons.items():
            for f in p.name_forms():
                toks = f.split()
                ff = fold(f)
                if len(toks) >= 2:
                    self.global_forms[ff].add(k)
                else:
                    self.ctx_forms[ff].add(k)
            for n in p.nicks:
                if len(n) >= 3:
                    self.ctx_forms[fold(n)].add(k)
        # una forma global que coincide con algo conservado no se usa
        for ff in list(self.global_forms):
            if ff in self.kept_f:
                del self.global_forms[ff]
        cnt = defaultdict(set)
        for k, p in self.reg.persons.items():
            if p.tokens and p.surn:
                cnt[f"{fold(p.tokens[0][0])}. {fold(p.surn[0])}"].add(k)
        self.initial_unique = {f: next(iter(ks)) for f, ks in cnt.items() if len(ks) == 1}
        self.initial_rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(f).replace(r"\.\ ", r"\.\s?") for f in sorted(self.initial_unique, key=len, reverse=True)) + r")(?![a-z0-9])") if self.initial_unique else None
        alts = sorted(self.global_forms, key=len, reverse=True)
        self.global_rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(
            re.escape(a).replace(r"\ ", r"[\s_]+") for a in alts) + r")(?![a-z0-9])") if alts else None
        self.handle_rx = None
        hs = sorted(self.reg.by_handle, key=len, reverse=True)
        hs = [h for h in hs if len(h) >= 5 and ("." in h or "_" in h or not h.isalpha() or h in self._handle_safe())]
        if hs:
            self.handle_rx = re.compile(r"(?<![a-z0-9.])(?:" + "|".join(re.escape(fold(h)) for h in hs) + r")(?![a-z0-9]|@)")
        addrs = sorted(self.reg.by_address, key=len, reverse=True)
        self.addr_rx = re.compile("|".join(re.escape(a) for a in addrs)) if addrs else None

    def _handle_safe(self):
        # handles sin punto (p. ej. "glondono") se buscan solo si no son palabras comunes
        return {h for h in self.reg.by_handle if len(h) >= 6}

    # ---------------------------------------------------------- utilidades
    def person(self, key) -> Person:
        return self.reg.persons[key]

    def _orphan(self, kind, value, ctx):
        """Dueño de un identificador desconocido: el cliente en contexto o una persona sintética."""
        owner = ctx.get("owner") or ctx.get("author")
        if owner and self.person(owner).tokens:
            return self.person(owner)
        k = f"orphan:{kind}:{value}"
        if k not in self.reg.persons:
            p = self.reg.get(k, "orphan")
            p.tokens = []
            p.pid = "p_" + format(P.h("pid", k) % 16**8, "08x")
            p.pseudo_tokens = []
        return self.reg.persons[k]

    def _table_column(self, text, pos):
        """Si pos cae dentro de una tabla markdown, devuelve el encabezado de su columna."""
        ls = text.rfind("\n", 0, pos) + 1
        le = text.find("\n", pos); le = len(text) if le == -1 else le
        line = text[ls:le]
        if not line.lstrip().startswith("|"):
            return ""
        col = line[:pos - ls].count("|") - 1
        # sube hasta el encabezado (la línea antes del separador ---)
        lines = text[:ls].rstrip("\n").split("\n")
        lines.append(line)
        for i in range(len(lines) - 2, 0, -1):
            if re.match(r"^\s*\|?\s*:?-{3,}", lines[i]):
                hdr = [c.strip() for c in lines[i - 1].strip().strip("|").split("|")]
                return hdr[col].lower() if 0 <= col < len(hdr) else ""
            if not lines[i].lstrip().startswith("|"):
                break
        return ""

    def _choose(self, keys, ctx, text, end, start=None):
        keys = list(keys)
        if len(keys) == 1:
            return keys[0]
        start = end if start is None else start
        window = text[max(0, start - 60):start] + " " + text[end:end + 60]
        for m in re.finditer(r"PQR-\d{4}-\d{3,5}", window):
            k = self.reg.radicados.get(m.group(0))
            if k in keys:
                return k
        for m in re.finditer(r"TX-\d{4,8}", window):
            k = self.reg.tx_client.get(m.group(0))
            if k in keys:
                return k
        col = self._table_column(text, start)
        if col:
            want = None
            if re.search(r"agente|asesor|responsable|analista|atendi|empleado", col):
                want = "employee"
            elif re.search(r"cliente|titular|afectad|usuario", col):
                want = "client"
            if want:
                sel = [k for k in keys if self.person(k).staff == (want == "employee")]
                if len(sel) == 1:
                    return sel[0]
        near = text[end:end + 40]
        m = re.search(r"CL-\d+", near)
        if m and m.group(0) in self.reg.by_cliente and self.reg.by_cliente[m.group(0)] in keys:
            return self.reg.by_cliente[m.group(0)]
        m = re.search(r"KP-\d+", near)
        if m and m.group(0) in self.reg.by_emp and self.reg.by_emp[m.group(0)] in keys:
            return self.reg.by_emp[m.group(0)]
        # últimos dígitos de cédula/celular cerca del nombre ("cédula terminada en 2961")
        for m4 in re.finditer(r"(?<!\d)(\d{4})(?!\d)", text[end:end + 70]):
            l4 = m4.group(1)
            hit = [k for k in keys if any(digits(c).endswith(l4) for c in self.person(k).cedulas | self.person(k).phones)]
            if len(hit) == 1:
                return hit[0]
        inctx = [k for k in keys if k in ctx.get("persons", ())]
        if len(inctx) >= 1:
            # prefiere el autor / dueño del registro
            if ctx.get("owner") in inctx:
                return ctx["owner"]
            return sorted(inctx)[0]
        # sin contexto: prioriza empleados/clientes del maestro
        keys.sort(key=lambda k: ({"client": 0, "employee": 1, "user": 2, "text": 3}.get(self.person(k).kind, 4), k))
        return keys[0]

    # ---------------------------------------------------------- detección
    def find(self, text, ctx):
        hits = []
        if not text:
            return hits
        ft = fold(text)
        persons_ctx = ctx.get("persons", set())

        # 1. menciones de Slack
        for m in MENTION_RX.finditer(text):
            k = self.reg.by_uid.get(m.group(1))
            if k:
                p = self.person(k)
                hits.append(Hit(m.start(), m.end(), "@" + p.ps["name"], 100, k, m.group(0)))

        # 2. correos
        for m in EMAIL_RX.finditer(text):
            e = m.group(0).rstrip(".").lower()
            end = m.start() + len(e)
            k = self.reg.by_email.get(e)
            if k:
                p = self.person(k)
                new = p.map_email.get(e)
                hits.append(Hit(m.start(), end, new, 95, k, m.group(0)[:len(e)]))
            else:
                loc, _, dom = e.partition("@")
                k2 = self.reg.by_handle.get(loc)
                if k2:
                    p = self.person(k2)
                    new = p.map_handle.get(loc, loc) + "@" + dom
                    p.map_email[e] = new
                    p.emails.add(e)
                    self.reg.by_email[e] = k2
                    hits.append(Hit(m.start(), end, new, 95, k2, text[m.start():end]))
                else:
                    p = None
                    for kk in sorted(persons_ctx):
                        pp = self.person(kk)
                        if pp.tokens and len(pp.tokens) >= 2 and self._local_matches(pp, loc):
                            p = pp; break
                    if p is None:
                        p = self._orphan("email", e, ctx)
                    if p.pseudo_tokens:
                        pg = p.pseudo_tokens[:max(p.n_given, 1)]; ps = p.pseudo_tokens[p.n_given:] or p.pseudo_tokens[-1:]
                        new = self.reg._pseudo_local(p, loc, pg, ps) + "@" + dom
                    else:
                        new = "usuario" + P.digitos(e, "em", 4) + "@" + dom
                    p.map_email[e] = new; p.emails.add(e)
                    hits.append(Hit(m.start(), end, new, 94, p.key, text[m.start():end]))

        # 3. correos deletreados
        for m in SPELLED_RX.finditer(ft):
            loc_sp, dom_sp = m.group(1), m.group(2)
            loc = re.sub(r"\s+punto\s+", ".", loc_sp)
            loc = re.sub(r"\s+(guion bajo|raya al piso)\s+", "_", loc)
            loc = re.sub(r"\s+guion\s+", "-", loc)
            dom = re.sub(r"\s+punto\s+", ".", dom_sp)
            e = f"{loc}@{dom}"
            k = self.reg.by_email.get(e) or self.reg.by_handle.get(loc)
            if k:
                p = self.person(k)
                newe = p.map_email.get(e) or (p.map_handle.get(loc, loc) + "@" + dom)
            else:
                p = self._orphan("email", e, ctx)
                if p.pseudo_tokens:
                    pg = p.pseudo_tokens[:max(p.n_given, 1)]; ps = p.pseudo_tokens[p.n_given:] or p.pseudo_tokens[-1:]
                    newe = self.reg._pseudo_local(p, loc, pg, ps) + "@" + dom
                else:
                    newe = "usuario" + P.digitos(e, "em", 4) + "@" + dom
            p.map_email[e] = newe; p.emails.add(e)
            nloc, _, ndom = newe.partition("@")
            spoken = nloc.replace(".", " punto ").replace("_", " guion bajo ").replace("-", " guion ") + " arroba " + ndom.replace(".", " punto ")
            orig = text[m.start():m.end()]
            hits.append(Hit(m.start(), m.end(), spoken, 93, p.key, orig))
            p.originals.add(orig)

        # 4. números (cédulas, celulares, cuentas)
        for m in DIGRUN_RX.finditer(text):
            raw = m.group(0)
            d = digits(raw)
            if len(d) < 6:
                continue
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{4}", raw.strip()):
                if d not in self.reg.by_digits:
                    continue
            key = None; kind = None
            for cand in (d, d[2:] if d.startswith("57") and len(d) == 12 else None):
                if cand and cand in self.reg.by_digits:
                    key, kind = self.reg.by_digits[cand]; break
            before = text[max(0, m.start() - 30):m.start()]
            after = text[m.end():m.end() + 12].lower()
            money = before.rstrip().endswith("$") or after.lstrip().startswith(("pesos", "mil", "cop", "millones")) or re.search(r"\b(por|de|valor|monto|cuota|pago|pagu[eé]|compra|cobro|cobraron|saldo|total|deuda|cupo|abono|intereses|mora)\b[^.\n]{0,25}$", before.lower())
            if re.search(r"\bes\s*(la|el)?\s*$", before.lower()) and not re.search(r"(c[eé]dula|documento|\bcc\b|identificaci)", before.lower()) and len(d) < 8:
                money = True
            if key is None:
                looks_phone = (len(d) == 10 and d.startswith("3")) or (len(d) == 12 and d.startswith("573"))
                if looks_phone and (raw.startswith("+") or PHONE_CUE.search(before) or " " in raw or True):
                    kind = "phone"
                elif 6 <= len(d) <= 10 and CEDULA_CUE.search(before) and not money:
                    kind = "cedula"
                elif 10 <= len(d) <= 16 and ACCOUNT_CUE.search(before):
                    kind = "account"
                else:
                    continue
                p = self._orphan(kind, d, ctx)
                key = p.key
                if kind == "cedula": p.cedulas.add(raw)
                elif kind == "phone": p.phones.add(raw)
                else: p.accounts.add(raw)
                if len(d) >= 8 and kind != "account":
                    self.reg.by_digits[d if kind != "phone" else norm_phone(d)] = (key, kind)
            p = self.person(key)
            new = pseudo_attr(p, kind, raw)
            hits.append(Hit(m.start(), m.end(), new, 90, key, raw))

        # 4b. números conocidos pegados a letras (CC1022649507_certificado.pdf)
        for m in re.finditer(r"(?<!\d)\d{6,12}(?!\d)", text):
            d = m.group(0)
            ent = self.reg.by_digits.get(d) or (self.reg.by_digits.get(d[2:]) if d.startswith("57") and len(d) == 12 else None)
            if ent:
                key, kind = ent
                hits.append(Hit(m.start(), m.end(), pseudo_attr(self.person(key), kind, d), 89, key, d))

        # 4c. últimos 4 dígitos de cédula/celular de personas en contexto ("termina en 9899")
        last4 = {}
        for k in persons_ctx:
            p = self.person(k)
            for kind, vals in (("cedula", p.cedulas), ("phone", p.phones)):
                for v in vals:
                    dv = digits(v)
                    if len(dv) >= 6:
                        last4.setdefault(dv[-4:], (k, kind, v))
        if last4:
            for m in re.finditer(r"(?<![\d.,])(\d{4})(?![\d.,]\d|\d)", text):
                l4 = m.group(1)
                if l4 not in last4:
                    continue
                before = text[max(0, m.start() - 8):m.start()].lower()
                if 1900 <= int(l4) <= 2030 and re.search(r"(de|en|desde|del)\s*$", before):
                    continue
                k, kind, v = last4[l4]
                newfull = digits(pseudo_attr(self.person(k), kind, v))
                hits.append(Hit(m.start(), m.end(), newfull[-4:], 85, k, None))
        for m in re.finditer(r"(?:terminad[oa]s?|termina|finaliza|acaba)\s+en\s+(\d{4})(?!\d)", text, re.I):
            l4 = m.group(1)
            own = [(k, kind, v) for k, p in self.reg.persons.items() for kind, vals in (("cedula", p.cedulas), ("phone", p.phones))
                   for v in vals if len(digits(v)) >= 6 and digits(v).endswith(l4)]
            if len({o[0] for o in own}) == 1:
                k, kind, v = own[0]
                new4 = digits(pseudo_attr(self.person(k), kind, v))[-4:]
            else:
                k = ctx.get("owner"); new4 = P.digitos(l4 + str(ctx.get("owner")), "l4", 4)
            hits.append(Hit(m.start(1), m.end(1), new4, 84, k, None))

        # 5. direcciones conocidas y genéricas
        if self.addr_rx:
            for m in self.addr_rx.finditer(ft):
                k = self.reg.by_address[m.group(0)]
                orig = text[m.start():m.end()]
                hits.append(Hit(m.start(), m.end(), pseudo_attr(self.person(k), "address", orig), 89, k, orig))
        for m in ADDR_RX.finditer(text):
            orig = m.group(0)
            k = self.reg.by_address.get(fold(orig))
            if k is None:
                p = self._orphan("address", fold(orig), ctx)
                p.addresses.add(orig)
                k = p.key
            hits.append(Hit(m.start(), m.end(), pseudo_attr(self.person(k), "address", orig), 88, k, orig))

        # 6. fechas de nacimiento de personas en contexto (o de cualquiera en formato ISO)
        for k in persons_ctx:
            p = self.person(k)
            for b in p.births:
                for i, form in enumerate(date_forms(b)):
                    for m in re.finditer(r"(?<![0-9])" + re.escape(fold(form)) + r"(?![0-9])", ft):
                        nb = pseudo_attr(p, "birth", b)
                        new = date_forms(nb)[i]
                        hits.append(Hit(m.start(), m.end(), new, 87, k, text[m.start():m.end()]))

        # 6b. cumpleaños sin año ("cumple años el 20 de julio") -> persona con esa fecha (si es única)
        for m in re.finditer(r"(cumple\s*anos|cumpleanos|nacio|naci|fecha de nacimiento)[^.\n]{0,30}?\b(\d{1,2}) de (" + "|".join(MESES) + r")\b(?!\s+de\s+\d)", ft):
            dd, mes = int(m.group(2)), MESES.index(m.group(3)) + 1
            own = sorted({k for k, p in self.reg.persons.items() for b in p.births if b[5:] == f"{mes:02d}-{dd:02d}"})
            if len(own) > 1:
                own = [k for k in own if self.person(k).staff] or own
            if len(own) == 1:
                p = self.person(own[0]); b = next(b for b in p.births if b[5:] == f"{mes:02d}-{dd:02d}")
                nb = pseudo_attr(p, "birth", b)
                new = f"{int(nb[8:10])} de {MESES[int(nb[5:7]) - 1]}"
                hits.append(Hit(m.start(2), m.end(3), new, 87, own[0], None))
            else:
                x = P.h("bday", m.group(0))
                hits.append(Hit(m.start(2), m.end(3), f"{1 + x % 28} de {MESES[(x // 28) % 12]}", 87, None, None))

        # 7. placas y pasaportes
        for m in PLATE_RX.finditer(text):
            if m.group(1) in PLATE_BLOCK:
                continue
            orig = m.group(0)
            k = self.reg.by_plate.get(orig.replace("-", "").replace(" ", ""))
            p = self.person(k) if k else self._orphan("plate", orig, ctx)
            self.reg.by_plate[orig.replace("-", "").replace(" ", "")] = p.key
            p.plates.add(orig)
            hits.append(Hit(m.start(), m.end(), pseudo_attr(p, "plate", orig), 86, p.key, orig))
        for m in PASSPORT_RX.finditer(text):
            orig = m.group(1)
            if orig[:2] in ("CL", "KP", "TX"):
                continue
            p = self._orphan("passport", orig, ctx)
            p.passports.add(orig)
            hits.append(Hit(m.start(), m.end(), pseudo_attr(p, "passport", orig), 86, p.key, orig))

        # 8. nombres completos (globales)
        if self.global_rx:
            for m in self.global_rx.finditer(ft):
                form_f = re.sub(r"[\s_]+", " ", m.group(0))
                keys = self.global_forms.get(form_f)
                if not keys:
                    continue
                orig = text[m.start():m.end()]
                if self._is_kept_at(ft, m.start(), m.end()):
                    continue
                k = self._choose(keys, ctx, text, m.end(), m.start())
                p = self.person(k)
                new = p.pseudo_for(re.sub(r"[\s_]+", " ", orig))
                if "_" in orig:
                    new = P.slug(new).replace(".", "_")
                hits.append(Hit(m.start(), m.end(), apply_style(orig, new), 80, k, orig))

        # 9. handles (fernanda.vargas, glondono)
        if self.handle_rx:
            for m in self.handle_rx.finditer(ft):
                h = m.group(0)
                k = self.reg.by_handle.get(h)
                if not k:
                    continue
                p = self.person(k)
                hits.append(Hit(m.start(), m.end(), p.map_handle.get(h, h), 79, k, text[m.start():m.end()]))

        # 10. nombres de pila / apodos / "señor Apellido" de personas en contexto
        ctx_tokens = {}
        ctx_multi = defaultdict(set)
        author = ctx.get("author")
        for k in sorted(persons_ctx, key=lambda k: (k != ctx.get("owner"), k)):
            p = self.person(k)
            if not p.tokens:
                continue
            for f in p.name_forms():
                ctx_tokens.setdefault(fold(f), k); ctx_multi[fold(f)].add(k)
            for n in p.nicks:
                if len(n) >= 3:
                    ctx_tokens.setdefault(fold(n), k); ctx_multi[fold(n)].add(k)
        if ctx_tokens:
            rx = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in sorted(ctx_tokens, key=len, reverse=True)) + r")(?![a-z0-9])")
            for m in rx.finditer(ft):
                orig = text[m.start():m.end()]
                if not orig[:1].isupper():
                    continue
                if self._is_kept_at(ft, m.start(), m.end()):
                    continue
                k = ctx_tokens[m.group(1)]
                cands = ctx_multi.get(m.group(1), {k})
                if len(cands) > 1 and author in cands:
                    pre = ft[max(0, m.start() - 22):m.start()]
                    if re.search(r"(soy|atiende|habla|hablas con|te escribe|—|-)\s*$", pre) or re.search(r"(soy|atiende|habla)\s+\S*\s*$", pre):
                        k = author
                    else:
                        others = sorted(c for c in cands if c != author)
                        k = ctx.get("owner") if ctx.get("owner") in others else others[0]
                p = self.person(k)
                if fold(orig) in {fold(n) for n in p.nicks}:
                    new = p.pseudo_tokens[0]
                else:
                    new = p.pseudo_for(orig)
                hits.append(Hit(m.start(), m.end(), apply_style(orig, new), 70, k, orig))
        # inicial + apellido: "A. Marulanda" (de alguien en contexto o único en todo el registro)
        if self.initial_rx is not None:
            for m in self.initial_rx.finditer(ft):
                key_f = re.sub(r"\.\s?", ". ", m.group(0))
                k = self.initial_unique.get(key_f)
                if k:
                    p = self.person(k)
                    new = p.pseudo_tokens[0][0] + ". " + p.pseudo_tokens[p.n_given] if len(p.pseudo_tokens) > p.n_given else p.pseudo_tokens[0]
                    hits.append(Hit(m.start(), m.end(), new, 71, k, text[m.start():m.end()]))
        if True:
            for k in persons_ctx:
                p = self.person(k)
                if not p.tokens or not p.surn:
                    continue
                ini = fold(p.tokens[0][0]); s1 = fold(p.surn[0])
                for m in re.finditer(r"(?<![a-z0-9])" + re.escape(ini) + r"\.\s?" + re.escape(s1) + r"(?![a-z0-9])", ft):
                    orig = text[m.start():m.end()]
                    new = p.pseudo_tokens[0][0] + ". " + p.pseudo_tokens[p.n_given] if len(p.pseudo_tokens) > p.n_given else p.pseudo_tokens[0]
                    hits.append(Hit(m.start(), m.end(), new, 71, k, orig))
            # apellido tras tratamiento: "señor Santander", "Sra. Castaño"
            for k in persons_ctx:
                p = self.person(k)
                for i, s in enumerate(p.surn):
                    for m in re.finditer(r"(?<![a-z])" + HONOR_RX + r"\s+(" + re.escape(fold(s)) + r")(?![a-z0-9])", ft):
                        orig = text[m.start(1):m.end(1)]
                        hits.append(Hit(m.start(1), m.end(1), apply_style(orig, p.pseudo_for(orig)), 69, k, orig))

        # 11. cuasi-identificadores: ocupación, barrio, año de vinculación
        if ctx.get("quasi"):
            hits.extend(self._quasi(text, ft, ctx))
        return hits

    def _local_matches(self, p, loc):
        g = fold(p.tokens[0]); sn = [fold(t) for t in p.surn]
        if not sn:
            return False
        loc = fold(loc)
        cands = {f"{g}.{sn[0]}", f"{g}{sn[0]}", f"{g[0]}{sn[0]}", f"{g}_{sn[0]}", f"{g[0]}.{sn[0]}"}
        if len(sn) > 1:
            cands |= {f"{g[0]}{sn[0]}{sn[1][0]}", f"{g}.{sn[0]}.{sn[1]}"}
        return loc in cands

    def _is_kept_at(self, ft, s, e):
        for kf in self.kept_f:
            i = ft.find(kf, max(0, s - len(kf)), e + len(kf))
            if i != -1 and i <= s and i + len(kf) >= e:
                return True
        return False

    def _quasi(self, text, ft, ctx):
        hits = []
        for occ, sec in OCUPACION_SECTOR.items():
            for m in re.finditer(r"(?<![a-z])" + re.escape(fold(occ)) + r"(?![a-z])", ft):
                orig = text[m.start():m.end()]
                if orig[:1].isupper() and not ctx.get("quasi_caps"):
                    continue  # "Contadora" como cargo en firmas
                pre = ft[max(0, m.start() - 30):m.start()]
                if not re.search(r"(\bsoy|\bes|\bera|\bsea|\bfue|trabaj\w*\s+(como|de)|\bcomo|profesion:?|ocupacion:?|oficio:?|,|\(|\ba|\ban|\bun|\buna|\bes un[a]?|\bsoy un[a]?|\by)\s*$", pre):
                    continue
                hits.append(Hit(m.start(), m.end(), sec, 60, ctx.get("owner"), orig))
        for m in YEAR_CTX.finditer(ft):
            hits.append(Hit(m.start(3), m.end(3), "hace unos años", 60, ctx.get("owner"), text[m.start(3):m.end(3)]))
        for bf, cities in self.city_of_barrio.items():
            for m in re.finditer(r"(?<![a-z])(en|barrio|sector|de|por|cerca de)\s+(el\s+|la\s+)?(" + re.escape(bf) + r")(?![a-z])", ft):
                orig = text[m.start(3):m.end(3)]
                if not orig[:1].isupper():
                    continue
                city = None
                for k in ctx.get("persons", ()):
                    p = self.person(k)
                    if p.city in cities:
                        city = p.city; break
                if city is None:
                    city = sorted(cities)[0]
                if fold(city) == bf:
                    continue
                s = m.start(2) if m.group(2) else m.start(3)
                hits.append(Hit(s, m.end(3), city, 59, ctx.get("owner"), text[s:m.end(3)]))
        return hits

    # ---------------------------------------------------------- aplicación
    def apply(self, text, ctx, rid=None):
        hits = self.find(text, ctx)
        if not hits:
            self._mark_kept(text, rid)
            return text
        hits.sort(key=lambda h: (-h.prio, -(h.end - h.start), h.start))
        taken = []
        chosen = []
        for h in hits:
            if h.new is None or h.start >= h.end:
                continue
            if any(not (h.end <= a or h.start >= b) for a, b in taken):
                continue
            taken.append((h.start, h.end))
            chosen.append(h)
        chosen.sort(key=lambda h: h.start)
        out = []; pos = 0
        for h in chosen:
            out.append(text[pos:h.start]); out.append(h.new); pos = h.end
            if h.pkey and rid:
                p = self.reg.persons.get(h.pkey)
                if p is not None:
                    p.records.add(rid)
                    if h.prio >= 69 and h.orig:
                        p.originals.add(h.orig)
        out.append(text[pos:])
        self._mark_kept(text, rid)
        return "".join(out)

    def _mark_kept(self, text, rid):
        if not rid:
            return
        for k in self.kept:
            if len(k) >= 3 and k in text and re.search(r"(?<![\w@.])" + re.escape(k) + r"(?![\w])", text):
                self.kept_hits[k].add(rid)
