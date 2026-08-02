import os, json
from pathlib import Path

PROJECT_ROOT = Path(r"D:\EnglishWeb")
DATA_DIR = PROJECT_ROOT / "data"
PROJECTS_DIR = DATA_DIR / "projects"
CONFIG_FILE = DATA_DIR / "sources.json"

DEFAULT_SOURCES = [
    str(PROJECT_ROOT / "downloads" / "input"),
]

def load_sources():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return DEFAULT_SOURCES

def save_sources(sources):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")

PROCESSING_SERVER = os.getenv("PROCESSING_SERVER", "http://100.99.214.73:8005")
HOST = "0.0.0.0"
PORT = 8001
DEBUG = True
