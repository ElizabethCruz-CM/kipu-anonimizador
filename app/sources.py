"""Carga las fuentes (desde el API o desde una carpeta local) y las normaliza al esquema de salida."""
import csv, html, io, json, os, re, time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import requests


# ---------------------------------------------------------------- acceso a fuentes
class ApiSource:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, path, binary=False):
        for intento in range(6):
            r = self.s.get(self.base + path, timeout=60)
            if r.status_code == 429:
                time.sleep(2 + intento * 2)
                continue
            r.raise_for_status()
            return r.content if binary else r.json()
        r.raise_for_status()

    def channels(self): return self.get("/chats/channels")
    def users(self): return self.get("/chats/users")
    def dates(self, canal): return self.get(f"/chats/{canal}")
    def messages(self, canal, fecha): return self.get(f"/chats/{canal}/{fecha}")
    def docs_index(self): return self.get("/docs")
    def doc(self, d): return self.get(f"/docs/{d['id']}", binary=True)
    def table(self, name): return self.get(f"/tables/{name}", binary=True)
    def record_index(self): return self.get("/record_index")


class DirSource:
    """Misma interfaz leyendo una carpeta con la estructura del export (fuentes/ o sample/input)."""
    def __init__(self, root):
        self.root = root

    def _j(self, *p):
        with open(os.path.join(self.root, *p), encoding="utf-8") as f:
            return json.load(f)

    def channels(self): return self._j("chats", "channels.json")
    def users(self): return self._j("chats", "users.json")

    def dates(self, canal):
        d = os.path.join(self.root, "chats", canal)
        if not os.path.isdir(d):
            return []
        return sorted(x[:-5] for x in os.listdir(d) if x.endswith(".json"))

    def messages(self, canal, fecha): return self._j("chats", canal, f"{fecha}.json")
    def docs_index(self): return self._j("docs", "index.json")

    def doc(self, d):
        with open(os.path.join(self.root, "docs", d["filename"]), "rb") as f:
            return f.read()

    def table(self, name):
        p = os.path.join(self.root, "tables", name)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def record_index(self):
        for p in ("record_index.json", "../record_index.json"):
            q = os.path.join(self.root, p)
            if os.path.exists(q):
                with open(q, encoding="utf-8") as f:
                    return json.load(f)
        return None


# ---------------------------------------------------------------- html -> markdown
class _Html2Md(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "header", "footer", "ul", "ol", "table", "br", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.meta = {}
        self.title = ""
        self.in_title = False
        self.row = None
        self.rows = None
        self.cell = None
        self.list_stack = []

    def emit(self, s):
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script"):
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "meta" and a.get("name") and a.get("content") is not None:
            self.meta[a["name"]] = a["content"]
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p":
            self.emit("\n\n")
        elif tag == "br":
            self.emit("\n")
        elif tag in ("ul", "ol"):
            self.list_stack.append(tag)
            self.emit("\n")
        elif tag == "li":
            self.emit("\n- ")
        elif tag == "table":
            self.rows = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in ("td", "th") and self.cell is not None:
            txt = " ".join("".join(self.cell).split())
            if self.row is not None:
                self.row.append((txt, tag == "th"))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.rows is not None:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            lines = []
            for i, r in enumerate(self.rows):
                lines.append("| " + " | ".join(c for c, _ in r) + " |")
                if i == 0:
                    lines.append("| " + " | ".join("---" for _ in r) + " |")
            self.out.append("\n\n" + "\n".join(lines) + "\n")
            self.rows = None
        elif tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
            self.emit("\n")
        elif tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.emit("\n\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
            return
        if self.cell is not None:
            self.cell.append(data)
            return
        if not data.strip():
            if self.out and not self.out[-1].endswith((" ", "\n")):
                self.out.append(" ")
            return
        self.out.append(" ".join(data.split()) if "\n" in data else data)

    def handle_comment(self, data):
        self.emit(f"\n\n<!--{data}-->\n\n")


def html_to_md(raw: str):
    p = _Html2Md()
    p.feed(raw)
    txt = "".join(p.out)
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip() + "\n"
    return txt, p.meta, html.unescape(p.title.strip())


# ---------------------------------------------------------------- normalización
def parse_front_matter(text):
    meta = {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def pdf_to_text(data: bytes):
    import pypdf
    r = pypdf.PdfReader(io.BytesIO(data))
    pages = [(pg.extract_text() or "").strip() for pg in r.pages]
    meta = {}
    for k, v in (r.metadata or {}).items():
        meta[k.lstrip("/").lower()] = str(v)
    return "\n\n".join(pages).strip() + "\n", meta


def decode_table(raw: bytes, name: str):
    if name.endswith(".csv"):
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("latin-1")
        sep = ";" if txt.split("\n", 1)[0].count(";") > txt.split("\n", 1)[0].count(",") else ","
        return list(csv.DictReader(io.StringIO(txt), delimiter=sep))
    root = ET.fromstring(raw)
    rows = []
    for el in root:
        if not isinstance(el.tag, str):
            continue
        row = dict(el.attrib)
        for ch in el:
            row[ch.tag] = (ch.text or "").strip()
            for ak, av in ch.attrib.items():
                row[f"{ch.tag}_{ak}"] = av
        rows.append(row)
    return rows


TABLE_KEYS = {"clientes": "cliente_id", "transacciones": "tx_id", "pqrs": "radicado", "empleados": "id"}


def load_all(src, log=print):
    """Devuelve dict con users, channels, chats(list), docs(list), tables(dict name->rows), record_index."""
    data = {"users": src.users(), "channels": src.channels(), "chats": [], "docs": [], "tables": {}}
    users = {u["id"]: u for u in data["users"]}
    for ch in data["channels"]:
        canal = ch["name"]
        msgs = []
        for fecha in src.dates(canal):
            msgs.extend(src.messages(canal, fecha))
        msgs.sort(key=lambda m: float(m["ts"]))
        for m in msgs:
            root = m.get("thread_ts") or m["ts"]
            u = users.get(m.get("user"), {})
            author = (u.get("real_name") or u.get("profile", {}).get("real_name")
                      or m.get("username") or m.get("user", ""))
            data["chats"].append({
                "id": m["client_msg_id"],
                "thread_id": f"{canal}:{root}",
                "author": author,
                "ts": m["ts"],
                "text": m.get("text", ""),
                "attachments": [{"filename": f.get("name") or f.get("title") or ""} for f in m.get("files", [])],
                "_uid": m.get("user"),
                "_channel": canal,
            })
        log(f"chats {canal}: {len(msgs)}")
    for d in src.docs_index():
        raw = src.doc(d)
        fmt = d["format"]
        if fmt == "pdf":
            body, meta = pdf_to_text(raw)
            title = meta.get("title", "")
        elif fmt == "html":
            body, meta, title = html_to_md(raw.decode("utf-8", "replace"))
        else:
            body = raw.decode("utf-8", "replace")
            meta = parse_front_matter(body)
            title = meta.get("title", "")
            if not title:
                m = re.search(r"^# (.+)$", body, re.M)
                title = m.group(1).strip() if m else ""
        data["docs"].append({"id": d["id"], "format": fmt, "filename": d["filename"], "title": title,
                             "body": body, "metadata": {k: str(v) for k, v in meta.items()}})
    log(f"docs: {len(data['docs'])}")
    for name in ["clientes.csv", "transacciones.csv", "pqrs.xml", "empleados.xml"]:
        raw = src.table(name)
        if raw is None:
            continue
        t = name.split(".")[0]
        data["tables"][t] = decode_table(raw, name)
    log("tables: " + ", ".join(f"{k}={len(v)}" for k, v in data["tables"].items()))
    data["record_index"] = src.record_index()
    return data
