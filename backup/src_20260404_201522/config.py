import os
from pathlib import Path
from typing import List


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_DIR = BASE_DIR / "data" / "docs"
DEFAULT_INDEX_DIR = BASE_DIR / "outputs" / "indexes"


def _path_from_env(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    return Path(raw).expanduser() if raw else default


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", f"{OLLAMA_BASE_URL}/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

EMBED_MODEL_NAME = os.getenv(
    "EMBED_MODEL_NAME",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
)
RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-base")

RAW_DOCS_DIR = _path_from_env("RAW_DOCS_DIR", DEFAULT_DOCS_DIR)
INDEX_DIR = _path_from_env("INDEX_DIR", DEFAULT_INDEX_DIR)

FAISS_INDEX_PATH = INDEX_DIR / "faiss.index"
CHUNKS_META_PATH = INDEX_DIR / "chunks_meta.pkl"
BM25_PATH = INDEX_DIR / "bm25.pkl"

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))

DENSE_TOP_K = int(os.getenv("DENSE_TOP_K", "10"))
SPARSE_TOP_K = int(os.getenv("SPARSE_TOP_K", "10"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "12"))
FINAL_TOP_K = int(os.getenv("FINAL_TOP_K", "4"))
ENABLE_RERANK = os.getenv("ENABLE_RERANK", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_DENSE = os.getenv("ENABLE_DENSE", "true").strip().lower() in {"1", "true", "yes", "on"}

ROUTER_MIN_DENSE_SCORE = float(os.getenv("ROUTER_MIN_DENSE_SCORE", "0.45"))
ACTIVE_DOMAIN = os.getenv("DOMAIN", "auto").strip()


def list_domain_dirs() -> List[Path]:
    if not RAW_DOCS_DIR.exists():
        return []
    return sorted(path for path in RAW_DOCS_DIR.iterdir() if path.is_dir())


def get_domain_name(domain_dir: Path) -> str:
    return domain_dir.name


def get_requested_domain() -> str | None:
    value = ACTIVE_DOMAIN.strip()
    if not value or value.lower() in {"auto", "all"}:
        return None
    return value


def get_domain_index_dir(domain_name: str) -> Path:
    return INDEX_DIR / domain_name


def get_index_file_paths(index_dir: Path):
    return {
        "faiss": index_dir / "faiss.index",
        "chunks": index_dir / "chunks_meta.pkl",
        "bm25": index_dir / "bm25.pkl",
    }
