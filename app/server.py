"""Servicio HTTP del reto: /health, /run, /run/{job_id}, /output/{archivo}, /anonymize, /entities."""
import json, os, threading, time, traceback, uuid
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route, Mount

from .pipeline import Anonymizer, run_pipeline, anonymize_records
from .sources import ApiSource, DirSource, load_all

TEAM = os.environ.get("TEAM_NAME", "kipu-anonimizador")
VERSION = "1.0.0"
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "var"))
OUT_DIR = os.path.join(DATA_DIR, "output")
RAW_CACHE = os.path.join(DATA_DIR, "raw.json")
os.makedirs(OUT_DIR, exist_ok=True)
os.environ.setdefault("STATE_FILE", os.path.join(DATA_DIR, "pseudonyms.json"))

LOCK = threading.RLock()
JOBS = {}
STATE = {"an": None, "last_done": None}
EMPTY = {"users": [], "channels": [], "chats": [], "docs": [], "tables": {}, "record_index": None}


def log(*a):
    print(datetime.now().isoformat(timespec="seconds"), *a, flush=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def boot():
    """Al arrancar: reconstruye el estado de la última corrida si hay caché; si no, uno vacío."""
    data = EMPTY
    if os.path.exists(RAW_CACHE):
        try:
            with open(RAW_CACHE, encoding="utf-8") as f:
                data = json.load(f)
            log("estado reconstruido desde caché")
        except Exception:
            data = EMPTY
    with LOCK:
        STATE["an"] = Anonymizer(data, log)


def _job(job_id, sources_url, token):
    job = JOBS[job_id]
    job["status"] = "running"; job["started_at"] = now()
    t0 = time.time()
    try:
        if sources_url.startswith("file://"):
            src = DirSource(sources_url[7:])
        else:
            src = ApiSource(sources_url, token)
        data = load_all(src, log)
        with LOCK:
            an, counts, secs = run_pipeline(data, OUT_DIR, log)
            STATE["an"] = an
            STATE["last_done"] = job_id
        tmp = RAW_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, RAW_CACHE)
        job.update(status="done", finished_at=now(), records=counts,
                   cost={"tokens_in": 0, "tokens_out": 0, "seconds": round(time.time() - t0, 2)})
    except Exception as e:
        log("error en corrida", traceback.format_exc())
        job.update(status="failed", finished_at=now(), error=str(e)[:500],
                   cost={"tokens_in": 0, "tokens_out": 0, "seconds": round(time.time() - t0, 2)})


async def health(request):
    return JSONResponse({"status": "ok", "team": TEAM, "version": VERSION, "models": []})


async def run(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    sources_url = body.get("sources_url") or os.environ.get("SOURCES_URL", "")
    token = body.get("token") or os.environ.get("SOURCES_TOKEN", "")
    if not sources_url:
        return JSONResponse({"error": "falta sources_url"}, status_code=422)
    # idempotente: si ya hay una corrida en curso con las mismas fuentes, devuelve esa
    for jid, j in JOBS.items():
        if j["status"] in ("queued", "running") and j["_src"] == sources_url:
            return JSONResponse({"job_id": jid}, status_code=202)
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "_src": sources_url, "started_at": None, "finished_at": None,
                    "records": {}, "cost": {"tokens_in": 0, "tokens_out": 0, "seconds": 0}}
    threading.Thread(target=_job, args=(job_id, sources_url, token), daemon=True).start()
    return JSONResponse({"job_id": job_id}, status_code=202)


async def run_status(request):
    j = JOBS.get(request.path_params["job_id"])
    if not j:
        return JSONResponse({"error": "job no encontrado"}, status_code=404)
    return JSONResponse({k: v for k, v in j.items() if not k.startswith("_") and v is not None})


async def output(request):
    name = request.path_params["archivo"]
    if name not in ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json"):
        return JSONResponse({"error": "archivo no válido"}, status_code=404)
    p = os.path.join(OUT_DIR, name)
    if not os.path.exists(p):
        return JSONResponse({"error": "no hay corrida terminada"}, status_code=404)
    mt = "application/json" if name.endswith(".json") else "application/x-ndjson"
    return FileResponse(p, media_type=mt + "; charset=utf-8")


async def entities(request):
    p = os.path.join(OUT_DIR, "entities.json")
    if not os.path.exists(p):
        return JSONResponse({"entities": []})
    return FileResponse(p, media_type="application/json; charset=utf-8")


async def anonymize(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=422)
    source = body.get("source")
    records = body.get("records")
    if source not in ("chats", "docs", "tables") or not isinstance(records, list):
        return JSONResponse({"error": "se espera {source: chats|docs|tables, records: [...]}"}, status_code=422)
    if len(records) > 50:
        return JSONResponse({"error": "máximo 50 registros por lote"}, status_code=422)
    for r in records:
        if not isinstance(r, dict) or "id" not in r:
            return JSONResponse({"error": "registro fuera de esquema"}, status_code=422)
    with LOCK:
        an = STATE["an"]
        try:
            recs, ents = anonymize_records(an, source, records)
        except Exception as e:
            log("error en /anonymize", traceback.format_exc())
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
    return JSONResponse({"records": recs, "entities": ents})


async def root(request):
    return PlainTextResponse("Kipu Pay anonimizador. Rutas en /v1: health, run, output, anonymize, entities.")


def routes():
    return [
        Route("/health", health),
        Route("/run", run, methods=["POST"]),
        Route("/run/{job_id}", run_status),
        Route("/output/{archivo}", output),
        Route("/anonymize", anonymize, methods=["POST"]),
        Route("/entities", entities),
    ]


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    boot()
    yield


app = Starlette(routes=[Route("/", root), Mount("/v1", routes=routes())] + routes(), lifespan=lifespan)
