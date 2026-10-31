"""Utilidades de texto: plegado que conserva longitud, estilos de mayúsculas, fechas en español."""
import re, unicodedata

_FOLD_CACHE = {}


def fold_char(c):
    r = _FOLD_CACHE.get(c)
    if r is None:
        b = "".join(x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn").lower()
        r = b if len(b) == 1 else (c.lower() if len(c.lower()) == 1 else c)
        _FOLD_CACHE[c] = r
    return r


def fold(s: str) -> str:
    """Minúsculas sin tildes, misma longitud que la entrada (para mapear posiciones)."""
    return "".join(fold_char(c) for c in s)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def has_accents(s):
    return strip_accents(s) != s


def apply_style(orig: str, new: str) -> str:
    """Copia el estilo del original: MAYÚSCULAS, minúsculas, sin tildes."""
    letters = [c for c in orig if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 1:
        out = new.upper()
        return out if has_accents(orig) else strip_accents(out)
    if letters and all(c.islower() for c in letters):
        out = new.lower()
        return out if has_accents(orig) else strip_accents(out)
    return new


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


def date_forms(iso: str):
    """Formas en que puede aparecer una fecha ISO yyyy-mm-dd."""
    try:
        y, m, d = iso.split("-")
        yi, mi, di = int(y), int(m), int(d)
    except Exception:
        return []
    return [
        iso,
        f"{di} de {MESES[mi-1]} de {yi}",
        f"{di:02d} de {MESES[mi-1]} de {yi}",
        f"{di:02d}/{mi:02d}/{yi}",
        f"{di}/{mi}/{yi}",
        f"{di:02d}-{mi:02d}-{yi}",
    ]


def format_like(form_index: int, iso: str):
    fs = date_forms(iso)
    return fs[form_index] if form_index < len(fs) else iso


WORD = r"(?<![a-z0-9])"
WORD_END = r"(?![a-z0-9])"
