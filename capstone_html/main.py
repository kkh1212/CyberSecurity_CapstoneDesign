"""
AvailRAG - FastAPI Backend
MutedRAG 방어 시스템이 내장된 회사 AI 어시스턴트

RAG 파이프라인: 조원 코드(chunking / retrievers / reranker / prompts / router) 통합
보안 레이어:   MutedRAG 탐지 + 3-Way Cleansing (AvailRAG 고유 기능)
"""

import asyncio
import json
import os
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
import numpy as np

# ── RAG 패키지 (조원 코드 통합) ──────────────────────────────
from rag.index_builder import build_index, load_index, index_is_stale, INDEX_DIR
from rag.retrievers   import hybrid_search
from rag.reranker     import rerank_results
from rag.router       import is_general_chat, should_use_rag
from rag.prompts      import build_rag_system_prompt, build_general_system_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("availrag")

# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

CORPUS_DIRS  = [Path("corpus_docs"), Path("malicious_docs")]
SESSIONS_DIR = Path("sessions")

# ─────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────
session_histories: dict[str, list] = {}

_faiss_index  = None
_chunks       = None
_bm25         = None
_is_rebuilding = False

# 보안 이벤트 로그 (최근 100건)
_security_events: list = []
MAX_SECURITY_EVENTS = 100


# ─────────────────────────────────────────
# Pydantic 모델
# ─────────────────────────────────────────
class ChatRequest(BaseModel):
    message:    str
    history:    list = []
    session_id: str  = ""

class ChatResponse(BaseModel):
    answer:   str
    sources:  list = []
    filtered: bool = False

class UploadResponse(BaseModel):
    success:     bool
    filename:    str
    chunk_count: int


# ─────────────────────────────────────────
# MutedRAG 탐지 로직 (AvailRAG 핵심 기여)
# ─────────────────────────────────────────

JAILBREAK_KEYWORDS = [
    # 영어 패턴 — 반드시 명확한 지시 전환 문구
    "forget the previous",
    "forget all previous",
    "focus on my new question",
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the above",
    "disregard previous instructions",
    "new instruction:",
    "new instructions:",
    "ignore your instructions",
    "you are now a",
    "pretend you are",
    "forget everything above",
    "your new task is",
    "instead, tell me",
    "instead answer the following",
    "answer the following instead",
    # 한국어 패턴 — 명확한 지시 전환 문구
    "이전 내용을 무시하고",
    "이전 지시를 무시하고",
    "이전 지시를 잊어",
    "지금부터 너는",
    "이제부터 너는",
    "대신 다음 질문에 답해",
    "새 지시사항:",
    "시스템 프롬프트:",
]


def keyword_score(chunk: str) -> int:
    """키워드 스코어링 (0~30점)"""
    text_lower = chunk.lower()
    score = sum(15 for kw in JAILBREAK_KEYWORDS if kw.lower() in text_lower)
    return min(score, 30)


def _cos(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def semantic_inconsistency_score_from_vecs(vecs) -> int:
    """
    미리 계산된 임베딩으로 의미적 불일치 점수 계산 (0~10점)
    vecs: 2개(짧은 청크) 또는 3개(긴 청크) numpy 벡터
    """
    if len(vecs) == 2:
        cos = _cos(vecs[0], vecs[1])
        if cos < 0.35: return 10
        if cos < 0.55: return 5
        return 0

    c_01 = _cos(vecs[0], vecs[1])
    c_12 = _cos(vecs[1], vecs[2])
    c_02 = _cos(vecs[0], vecs[2])
    # 앞/뒤 유사하지만 중간이 이질적 → 중간에 주입 의심
    if c_02 > 0.55 and min(c_01, c_12) < 0.35:
        return 10
    if min(c_01, c_12, c_02) < 0.25:
        return 10
    if min(c_01, c_12, c_02) < 0.45:
        return 5
    return 0


def _chunk_segments(chunk: str):
    """청크를 2~3개 세그먼트로 분할"""
    lines = [l.strip() for l in chunk.split('\n') if l.strip()]
    if len(lines) < 4:
        mid = len(chunk) // 2
        return [chunk[:mid], chunk[mid:]]
    seg = max(1, len(lines) // 3)
    return [
        '\n'.join(lines[:seg]),
        '\n'.join(lines[seg:2*seg]),
        '\n'.join(lines[2*seg:]),
    ]


def ppl_score(chunk: str) -> int:
    """PPL 탐지 (0~10점) — 명확한 지시 전환 패턴만 탐지"""
    transition_kws = [
        "however, your new task",
        "your new task is",
        "ignore the above and",
        "from now on you must",
        "starting now you are",
        "지금부터 너는",
        "이제부터 너는",
        "대신 다음에 답해",
    ]
    for line in chunk.split('\n'):
        if any(kw in line.lower() for kw in transition_kws):
            return 7
    return 0


def compute_muted_rag_score(chunk: str, semantic_score: int = 0) -> dict:
    """
    semantic_score는 cleanse_chunks()에서 배치 임베딩 후 외부에서 전달.
    단독 호출 시에는 0으로 처리 (fast path).
    """
    k = keyword_score(chunk)
    p = ppl_score(chunk)
    s = semantic_score
    total = k + s + p

    verdict = "clean"
    if total >= 20:
        verdict = "malicious"
    elif total >= 10:
        verdict = "suspicious"

    return {"keyword_score": k, "semantic_score": s, "ppl_score": p,
            "total_score": total, "verdict": verdict}


# ─────────────────────────────────────────
# 보안 이벤트 로그
# ─────────────────────────────────────────

def _log_security_event(event_type: str, source: str, score: dict):
    _security_events.append({
        "time":           datetime.now().strftime("%H:%M:%S"),
        "type":           event_type,           # "suspicious" | "malicious"
        "source":         source,
        "total_score":    score["total_score"],
        "keyword_score":  score["keyword_score"],
        "semantic_score": score["semantic_score"],
        "ppl_score":      score["ppl_score"],
    })
    if len(_security_events) > MAX_SECURITY_EVENTS:
        _security_events.pop(0)


# ─────────────────────────────────────────
# 3-Way Cleansing
# ─────────────────────────────────────────

def _mask_suspicious_region(chunk: str) -> str:
    """Way B: 의심 구간 [REDACTED] 마스킹"""
    return "\n".join(
        "[REDACTED]" if any(kw.lower() in line.lower() for kw in JAILBREAK_KEYWORDS) else line
        for line in chunk.split("\n")
    )


def cleanse_chunks(items: list) -> tuple[list, bool]:
    """
    검색 결과 아이템 목록에 MutedRAG 필터 적용 (배치 임베딩으로 최적화)

    흐름:
    1. 모든 청크에 keyword + ppl 빠른 검사
    2. 의심 신호가 있는 청크만 배치 임베딩 → semantic 검사
    3. 최종 verdict 결정
    """
    cleaned  = []
    filtered = False

    texts       = [item["chunk"]["text"] for item in items]
    kw_scores   = [keyword_score(t) for t in texts]
    ppl_scores  = [ppl_score(t) for t in texts]

    # 의심 신호가 있는 청크 인덱스만 semantic 검사 대상
    need_semantic = [
        i for i, (k, p) in enumerate(zip(kw_scores, ppl_scores)) if k > 0 or p > 0
    ]

    sem_scores = [0] * len(items)
    if need_semantic:
        try:
            from rag.embedder import embed_texts as _embed
            # 세그먼트 목록 구성 (배치 한 번에)
            seg_map   = []   # (item_idx, seg_count)
            all_segs  = []
            for i in need_semantic:
                segs = _chunk_segments(texts[i])
                seg_map.append((i, len(segs)))
                all_segs.extend(segs)

            all_vecs = _embed(all_segs)  # GPU에서 한 번에 처리

            ptr = 0
            for item_idx, seg_cnt in seg_map:
                vecs = all_vecs[ptr:ptr + seg_cnt]
                sem_scores[item_idx] = semantic_inconsistency_score_from_vecs(vecs)
                ptr += seg_cnt
        except Exception as e:
            logger.warning(f"[MutedRAG] 배치 임베딩 실패, semantic 검사 건너뜀: {e}")

    for i, item in enumerate(items):
        chunk_text = texts[i]
        result     = compute_muted_rag_score(chunk_text, semantic_score=sem_scores[i])
        verdict    = result["verdict"]
        source     = item["chunk"]["source"]

        if verdict == "clean":
            cleaned.append(item)

        elif verdict == "suspicious":
            # Way B: 의심 구간 마스킹 후 유지
            filtered = True
            logger.warning(f"[MutedRAG] 의심 청크: source={source} score={result['total_score']}")
            _log_security_event("suspicious", source, result)
            new_item = dict(item)
            new_item["chunk"] = dict(item["chunk"])
            new_item["chunk"]["text"] = _mask_suspicious_region(chunk_text)
            cleaned.append(new_item)

        else:  # malicious
            # Way C: 제거
            filtered = True
            logger.error(f"[MutedRAG] 악성 청크 차단: source={source} score={result['total_score']}")
            _log_security_event("malicious", source, result)

    return cleaned, filtered


# ─────────────────────────────────────────
# 세션 영속성
# ─────────────────────────────────────────

def load_sessions():
    """sessions/ 디렉토리에서 이전 대화 이력 복원"""
    global session_histories
    if not SESSIONS_DIR.exists():
        return
    loaded = 0
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            session_histories[f.stem] = data
            loaded += 1
        except Exception:
            pass
    if loaded:
        logger.info(f"세션 복원: {loaded}개")


def persist_session(session_id: str):
    """세션 이력을 파일로 저장"""
    SESSIONS_DIR.mkdir(exist_ok=True)
    path = SESSIONS_DIR / f"{session_id}.json"
    try:
        path.write_text(
            json.dumps(session_histories[session_id], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"세션 저장 실패: {e}")


# ─────────────────────────────────────────
# 인덱스 관리
# ─────────────────────────────────────────

def _has_docs() -> bool:
    supported = {"*.pdf", "*.docx", "*.txt"}
    for d in CORPUS_DIRS:
        if not d.exists():
            continue
        for pattern in supported:
            if any(d.rglob(pattern)):
                return True
    return False


def init_index(force_rebuild: bool = False):
    """서버 시작 시 인덱스 로드 or 빌드"""
    global _faiss_index, _chunks, _bm25

    needs_build = force_rebuild or index_is_stale(CORPUS_DIRS, INDEX_DIR)

    if not needs_build:
        _faiss_index, _chunks, _bm25 = load_index(INDEX_DIR)
        if _faiss_index is not None:
            logger.info(f"인덱스 로드 완료: {len(_chunks)}개 청크")
            return

    if not _has_docs():
        logger.info("corpus_docs/ 비어있음 — 인덱스 빌드 생략")
        return

    logger.info("인덱스 빌드 시작...")
    count = build_index(CORPUS_DIRS, INDEX_DIR)
    if count > 0:
        _faiss_index, _chunks, _bm25 = load_index(INDEX_DIR)
        logger.info(f"인덱스 빌드 완료: {count}개 청크")


async def async_rebuild_index():
    """백그라운드 비동기 인덱스 재빌드 (업로드 후 사용)"""
    global _faiss_index, _chunks, _bm25, _is_rebuilding
    if _is_rebuilding:
        return
    _is_rebuilding = True
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: init_index(force_rebuild=True))
        logger.info("백그라운드 인덱스 재빌드 완료")
    except Exception as e:
        logger.error(f"백그라운드 재빌드 실패: {e}")
    finally:
        _is_rebuilding = False


# ─────────────────────────────────────────
# Ollama LLM 호출
# ─────────────────────────────────────────

async def call_ollama(
    system_prompt: str,
    user_query:    str,
    history:       list,
    temperature:   float = 0.5,
) -> str:
    """
    Ollama /api/chat 호출 (멀티턴 지원)
    system_prompt : RAG 컨텍스트 + 지시사항
    user_query    : 현재 사용자 질문 (user 메시지로 명확히 분리)
    history       : 이전 대화 이력
    """
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_query})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": -1,          # 모델을 메모리에서 해제하지 않음
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "num_ctx": 2048,       # KV 캐시 절약 → 전체 레이어 GPU 유지
            "num_gpu": 99,         # 모든 레이어를 GPU에 올림
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
    except httpx.ConnectError:
        raise HTTPException(status_code=503,
            detail=f"Ollama 서버({OLLAMA_HOST})에 연결할 수 없습니다.")
    except httpx.HTTPStatusError as e:
        err = ""
        try:
            err = e.response.json().get("error", "")
        except Exception:
            pass
        if e.response.status_code == 404 and "model" in err.lower():
            raise HTTPException(status_code=503,
                detail=f"모델 `{OLLAMA_MODEL}`을 찾을 수 없습니다. "
                       f"`ollama pull {OLLAMA_MODEL}`로 먼저 받아주세요.")
        raise HTTPException(status_code=500, detail=f"LLM 오류: {err or str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 오류: {str(e)}")


# ─────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from rag.embedder import get_embedder
    from rag.reranker import get_reranker
    logger.info("임베딩 모델 로딩...")
    get_embedder()
    logger.info("리랭커 로딩...")
    get_reranker()
    load_sessions()
    init_index()
    logger.info("AvailRAG 서버 시작 완료")
    yield


app = FastAPI(title="AvailRAG", version="2.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────
# API 엔드포인트
# ─────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_HOST}/api/tags")
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False

    return {
        "status":      "ok",
        "ollama":      "connected" if ollama_ok else "disconnected",
        "model":       OLLAMA_MODEL,
        "index_ready": _faiss_index is not None,
        "chunk_count": len(_chunks) if _chunks else 0,
        "rebuilding":  _is_rebuilding,
    }


@app.get("/api/security-events")
async def security_events(limit: int = 50):
    """최근 보안 이벤트 반환 (UI 보안 로그용)"""
    return {"events": _security_events[-limit:]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    채팅 엔드포인트
    흐름: 라우터 → 하이브리드 검색 → MutedRAG 필터 → 리랭킹 → LLM
    """
    session_id = req.session_id or str(uuid.uuid4())
    if session_id not in session_histories:
        session_histories[session_id] = []
    history = session_histories[session_id][-10:]

    # ── 1. 일반 대화 라우팅 ──────────────────────────────────
    if is_general_chat(req.message):
        system = build_general_system_prompt()
        answer = await call_ollama(system, req.message, history, temperature=0.7)
        _save_history(session_id, req.message, answer)
        return ChatResponse(answer=answer, sources=[], filtered=False)

    # ── 2. 인덱스 없으면 일반 대화 fallback ──────────────────
    if _faiss_index is None or _chunks is None or _bm25 is None:
        logger.warning("인덱스 없음 — 일반 대화 모드 fallback")
        system = build_general_system_prompt()
        answer = await call_ollama(system, req.message, history, temperature=0.7)
        _save_history(session_id, req.message, answer)
        return ChatResponse(answer=answer, sources=[], filtered=False)

    # ── 3. 하이브리드 검색 ───────────────────────────────────
    dense_results, sparse_results, merged = hybrid_search(
        req.message, _faiss_index, _chunks, _bm25
    )

    # ── 4. RAG 사용 여부 판단 ────────────────────────────────
    if not should_use_rag(req.message, dense_results, sparse_results):
        system = build_general_system_prompt()
        answer = await call_ollama(system, req.message, history, temperature=0.7)
        _save_history(session_id, req.message, answer)
        return ChatResponse(answer=answer, sources=[], filtered=False)

    # ── 5. MutedRAG 필터 ─────────────────────────────────────
    candidate_pool = merged[:30]
    clean_items, was_filtered = cleanse_chunks(candidate_pool)

    # Way C 보충: 관련성 있는 후순위 문서로 보충 (최소 임계값 0.005)
    if was_filtered and len(clean_items) < 3:
        fallback = [
            item for item in merged[30:60]
            if item["score"] >= 0.005
        ]
        extra_clean, _ = cleanse_chunks(fallback)
        clean_items.extend(extra_clean)
        if extra_clean:
            logger.info(f"[MutedRAG Way C] {len(extra_clean)}개 후순위 청크 보충")

    # ── 6. CrossEncoder 리랭킹 ───────────────────────────────
    final_items = rerank_results(req.message, clean_items, rerank_top_k=10, final_top_k=5) \
                  if clean_items else []

    sources = list({item["chunk"]["source"] for item in final_items})

    # ── 7. 프롬프트 빌드 & LLM 호출 ─────────────────────────
    if final_items:
        system      = build_rag_system_prompt(final_items)
        temperature = 0.4
    else:
        system      = build_general_system_prompt()
        temperature = 0.7

    answer = await call_ollama(system, req.message, history, temperature=temperature)

    # ── 8. 이력 저장 ─────────────────────────────────────────
    _save_history(session_id, req.message, answer)

    logger.info(
        f"[Chat] session={session_id[:8]} "
        f"retrieved={len(merged)} filtered={was_filtered} final={len(final_items)}"
    )

    return ChatResponse(answer=answer, sources=sources, filtered=was_filtered)


def _save_history(session_id: str, user_msg: str, assistant_msg: str):
    session_histories.setdefault(session_id, [])
    session_histories[session_id].append({"role": "user",      "content": user_msg})
    session_histories[session_id].append({"role": "assistant",  "content": assistant_msg})
    persist_session(session_id)


@app.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """파일 업로드 → corpus_docs에 저장 → 백그라운드 인덱스 재빌드"""
    allowed = {".pdf", ".docx", ".txt"}
    ext     = Path(file.filename or "").suffix.lower()

    if ext not in allowed:
        raise HTTPException(status_code=400,
            detail=f"지원하지 않는 파일 형식. 허용: {', '.join(allowed)}")

    save_dir  = Path("corpus_docs")
    save_dir.mkdir(exist_ok=True)
    save_path = save_dir / (file.filename or f"upload{ext}")

    content = await file.read()
    save_path.write_bytes(content)

    # 청크 수 계산 (표시용)
    from rag.chunking import load_document_blocks, chunk_blocks
    blocks      = load_document_blocks(save_path)
    new_chunks  = chunk_blocks(blocks, save_path)
    chunk_count = len(new_chunks)

    # 백그라운드 재빌드 (즉시 응답 반환)
    asyncio.create_task(async_rebuild_index())
    logger.info(f"[Upload] {file.filename}: {chunk_count}개 청크, 백그라운드 재빌드 시작")

    return UploadResponse(
        success=True,
        filename=file.filename or "unknown",
        chunk_count=chunk_count,
    )


# ─────────────────────────────────────────
# 정적 파일 서빙
# ─────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse("index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
