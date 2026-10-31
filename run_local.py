import sys, json, os, shutil
from app.sources import DirSource, load_all
from app.pipeline import run_pipeline
src, out = sys.argv[1], sys.argv[2]
os.environ.setdefault("STATE_FILE", out + "_state.json")
data = load_all(DirSource(src), log=lambda *a: None)
run_pipeline(data, out)
