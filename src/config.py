import hashlib
import os
import re
from pathlib import Path
from typing import List


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_DIR = BASE_DIR / "data" / "docs"
DEFAULT_INDEX_DIR = BASE_DIR / "outputs" / "indexes"


def _path_from_env(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    return Path(raw).expanduser() if raw else default


def _bool_from_env(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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

DETECTOR_ENABLED = _bool_from_env("DETECTOR_ENABLED", True)
DETECTOR_DEBUG = _bool_from_env("DETECTOR_DEBUG", True)
DETECTOR_VERSION = os.getenv("DETECTOR_VERSION", "mutedrag-detector-v1")
DETECTOR_FAIL_MODE = os.getenv("DETECTOR_FAIL_MODE", "allow").strip().lower()
DETECTOR_PROFILE = os.getenv("DETECTOR_PROFILE", "balanced").strip().lower()

DETECTOR_POLICY_ACTIONS = {
    "low": os.getenv("DETECTOR_ACTION_LOW", "index").strip().lower(),
    "medium": os.getenv("DETECTOR_ACTION_MEDIUM", "review").strip().lower(),
    "high": os.getenv("DETECTOR_ACTION_HIGH", "quarantine").strip().lower(),
    "critical": os.getenv("DETECTOR_ACTION_CRITICAL", "quarantine").strip().lower(),
}

RETRIEVAL_INCLUDE_FLAGGED_DEFAULT = _bool_from_env("INCLUDE_FLAGGED", False)
RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT = _bool_from_env("INCLUDE_QUARANTINED", False)
RETRIEVAL_FLAGGED_SCORE_MULTIPLIER = float(os.getenv("RETRIEVAL_FLAGGED_SCORE_MULTIPLIER", "0.85"))

RUNTIME_DETECTOR_PROFILE = os.getenv("RUNTIME_DETECTOR_PROFILE", DETECTOR_PROFILE).strip().lower()
RUNTIME_SANITIZER_ENABLED = _bool_from_env("RUNTIME_SANITIZER_ENABLED", True)
RUNTIME_REQUERY_MAX_ATTEMPTS = int(os.getenv("RUNTIME_REQUERY_MAX_ATTEMPTS", "2"))
RUNTIME_REQUERY_EXCLUDE_SOURCE_MIN = int(os.getenv("RUNTIME_REQUERY_EXCLUDE_SOURCE_MIN", "2"))
DEBUG_CONTEXT_PREVIEW = _bool_from_env("DEBUG_CONTEXT_PREVIEW", False)

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
    ascii_stub = re.sub(r"[^A-Za-z0-9._-]+", "_", domain_name).strip("._-")
    digest = hashlib.sha1(domain_name.encode("utf-8")).hexdigest()[:12]
    safe_name = f"{ascii_stub}__{digest}" if ascii_stub else f"domain__{digest}"
    return INDEX_DIR / safe_name


def get_index_file_paths(index_dir: Path):
    return {
        "faiss": index_dir / "faiss.index",
        "chunks": index_dir / "chunks_meta.pkl",
        "bm25": index_dir / "bm25.pkl",
    }


def get_detector_file_paths(index_dir: Path):
    return {
        "summary": index_dir / "detector_summary.json",
        "review": index_dir / "flagged_chunks.jsonl",
        "quarantine": index_dir / "quarantine_chunks.jsonl",
        "corpus_stats": index_dir / "detector_corpus_stats.json",
    }
