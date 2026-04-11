"""
FAISS + BM25 인덱스 빌드 및 로드
출처: 조원 코드 src/index_builder.py 기반, 단일 도메인 구조로 단순화
"""
import logging
import pickle
from pathlib import Path
from typing import List, Optional, Tuple

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from rag.chunking import load_documents_from_dirs
from rag.embedder import embed_texts
from rag.retrievers import tokenize_text

logger = logging.getLogger("availrag.index")

INDEX_DIR = Path("indexes") / "company"


def build_index(doc_dirs: List[Path], index_dir: Path = INDEX_DIR) -> int:
    """
    여러 문서 폴더로부터 FAISS + BM25 인덱스를 빌드하고 디스크에 저장.
    반환: 생성된 청크 수
    """
    index_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"문서 로딩 중: {[str(d) for d in doc_dirs]}")
    chunks = load_documents_from_dirs(doc_dirs)

    if not chunks:
        logger.warning("인덱싱할 문서 없음 - 인덱스 빌드 생략")
        return 0

    texts = [c["text"] for c in chunks]
    logger.info(f"임베딩 생성 중 ({len(texts)}개 청크)...")

    embeddings = np.asarray(embed_texts(texts), dtype="float32")
    dim = embeddings.shape[1]

    # FAISS
    faiss_index = faiss.IndexFlatIP(dim)
    faiss_index.add(embeddings)
    faiss.write_index(faiss_index, str(index_dir / "faiss.index"))

    # BM25
    tokenized = [tokenize_text(t) for t in texts]
    bm25 = BM25Okapi(tokenized)

    with open(index_dir / "chunks_meta.pkl", "wb") as f:
        pickle.dump(chunks, f)
    with open(index_dir / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)

    logger.info(f"인덱스 빌드 완료: {len(chunks)}개 청크, dim={dim}")
    return len(chunks)


def load_index(index_dir: Path = INDEX_DIR):
    """
    디스크에서 FAISS 인덱스, 청크 메타, BM25 모델 로드.
    인덱스가 없으면 (None, None, None) 반환.
    """
    faiss_path  = index_dir / "faiss.index"
    chunks_path = index_dir / "chunks_meta.pkl"
    bm25_path   = index_dir / "bm25.pkl"

    if not all(p.exists() for p in [faiss_path, chunks_path, bm25_path]):
        return None, None, None

    faiss_index = faiss.read_index(str(faiss_path))
    with open(chunks_path, "rb") as f:
        chunks = pickle.load(f)
    with open(bm25_path, "rb") as f:
        bm25 = pickle.load(f)

    logger.info(f"인덱스 로드 완료: {len(chunks)}개 청크")
    return faiss_index, chunks, bm25


def index_is_stale(doc_dirs: List[Path], index_dir: Path = INDEX_DIR) -> bool:
    """문서가 인덱스보다 최신이면 True (재빌드 필요)"""
    index_file = index_dir / "faiss.index"
    if not index_file.exists():
        return True

    index_mtime = index_file.stat().st_mtime
    supported = {".txt", ".pdf", ".docx"}

    for doc_dir in doc_dirs:
        if not doc_dir.exists():
            continue
        for fp in doc_dir.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in supported:
                if fp.stat().st_mtime > index_mtime:
                    return True
    return False
