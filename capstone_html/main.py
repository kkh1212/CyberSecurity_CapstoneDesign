"""
MutedRAG - FastAPI Backend
MutedRAG 방어 시스템이 내장된 회사 AI 어시스턴트

RAG 파이프라인: 조원 코드(chunking / retrievers / reranker / prompts / router) 통합
보안 레이어:   MutedRAG 탐지 + 3-Way Cleansing (detector 패키지 업그레이드)
"""

import asyncio
import json
import os
import secrets
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Cookie, FastAPI, Header, Response, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
import httpx
import numpy as np

from auth import init_db, verify_user

# ── RAG 패키지 ────────────────────────────────────────────────
from rag.index_builder import build_index, load_index, index_is_stale, INDEX_DIR
from rag.retrievers   import hybrid_search
from rag.reranker     import rerank_results
from rag.router       import is_general_chat, should_use_rag
from rag.prompts      import build_rag_system_prompt, build_general_system_prompt
from rag.query_analysis import build_query_profile

# ── MutedRAG Detector (업그레이드) ───────────────────────────
from rag.detector import MutedRAGDetector, estimate_corpus_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("availrag")

# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

CORPUS_DIRS   = [Path("../data/docs")]
SESSIONS_DIR  = Path("sessions")
PENDING_DIR   = Path("pending_docs")   # 관리자 승인 대기 문서
REGISTRY_FILE = Path("doc_registry.json")  # 문서 등록 현황 영속화

# ─────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────
session_histories: dict[str, list] = {}

_faiss_index  = None
_chunks       = None
_bm25         = None
_is_rebuilding = False

# 문서 등록 레지스트리
# {doc_id: {filename, file_path, status, upload_time, doc_type, risk_summary, chunks[]}}
_doc_registry: dict[str, dict] = {}

# 보안 이벤트 로그 (최근 100건)
_security_events: list = []
MAX_SECURITY_EVENTS = 100


# ─────────────────────────────────────────
# Pydantic 모델
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# 인메모리 토큰 저장소
# ─────────────────────────────────────────
_tokens: dict[str, dict] = {}  # token -> {username, role}


def _create_token(username: str, role: str) -> str:
    token = secrets.token_hex(32)
    _tokens[token] = {"username": username, "role": role}
    return token


def _verify_token(token: str) -> dict | None:
    return _tokens.get(token)


def _revoke_token(token: str):
    _tokens.pop(token, None)


class LoginRequest(BaseModel):
    username: str
    password: str


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
# MutedRAG 탐지 로직 (detector 패키지 기반 업그레이드)
# ─────────────────────────────────────────

def compute_muted_rag_score(chunk_text: str, corpus_stats: dict | None = None) -> dict:
    """
    MutedRAGDetector로 청크 위험도 분석.
    반환: verdict (clean/suspicious/malicious), risk_level, adjusted_risk, triggered_rules
    """
    detector = MutedRAGDetector(corpus_stats=corpus_stats)
    result = detector.analyze(chunk_text)

    risk_level = result.get("risk_level", "low")
    if risk_level in ("high", "critical"):
        verdict = "malicious"
    elif risk_level == "medium":
        verdict = "suspicious"
    else:
        verdict = "clean"

    return {
        "verdict":          verdict,
        "risk_level":       risk_level,
        "adjusted_risk":    result.get("adjusted_risk", 0.0),
        "instructionality": result.get("instructionality", {}).get("normalized_score", 0.0),
        "refusal_inducing": result.get("refusal_inducing", {}).get("normalized_score", 0.0),
        "outlier":          result.get("outlier", {}).get("normalized_score", 0.0),
        "triggered_rules":  result.get("triggered_rules", []),
        "explanation":      result.get("explanation", ""),
    }


# ─────────────────────────────────────────
# 보안 이벤트 로그
# ─────────────────────────────────────────

def _log_security_event(event_type: str, source: str, score: dict):
    _security_events.append({
        "time":             datetime.now().strftime("%H:%M:%S"),
        "type":             event_type,           # "suspicious" | "malicious"
        "source":           source,
        "risk_level":       score.get("risk_level", ""),
        "adjusted_risk":    round(score.get("adjusted_risk", 0.0), 3),
        "instructionality": round(score.get("instructionality", 0.0), 3),
        "refusal_inducing": round(score.get("refusal_inducing", 0.0), 3),
        "outlier":          round(score.get("outlier", 0.0), 3),
        "triggered_rules":  score.get("triggered_rules", []),
        "explanation":      score.get("explanation", ""),
    })
    if len(_security_events) > MAX_SECURITY_EVENTS:
        _security_events.pop(0)


# ─────────────────────────────────────────
# 3-Way Cleansing (detector 패키지 기반)
# ─────────────────────────────────────────

def _mask_with_redacted(chunk_text: str, triggered_rules: list) -> str:
    """Way B: 위험 문장 [REDACTED] 마스킹"""
    if not triggered_rules:
        return chunk_text
    # 위험 패턴 이름이 포함된 라인 마스킹
    lines = chunk_text.split("\n")
    masked = []
    for line in lines:
        line_lower = line.lower()
        # 명확한 지시 전환 키워드 포함 라인 마스킹
        dangerous = any(kw in line_lower for kw in [
            "ignore", "forget", "pretend", "override",
            "무시", "잊어", "지금부터", "이제부터", "대신",
        ])
        masked.append("[REDACTED]" if dangerous else line)
    return "\n".join(masked)


def cleanse_chunks(items: list) -> tuple[list, bool]:
    """
    검색 결과 아이템 목록에 MutedRAG 필터 적용.

    흐름:
    1. corpus_stats 추정 (배치)
    2. MutedRAGDetector로 각 청크 분석
    3. low → 유지 / medium → Way B 마스킹 / high/critical → Way C 제거
    """
    if not items:
        return [], False

    cleaned  = []
    filtered = False
    texts    = [item["chunk"]["text"] for item in items]

    # 코퍼스 통계 계산 (outlier 검출 정확도 향상)
    try:
        corpus_stats = estimate_corpus_stats(texts)
    except Exception:
        corpus_stats = None

    for i, item in enumerate(items):
        chunk_text = texts[i]
        source     = item["chunk"].get("source", "unknown")

        try:
            result = compute_muted_rag_score(chunk_text, corpus_stats=corpus_stats)
        except Exception as e:
            logger.warning(f"[MutedRAG] 분석 실패, 청크 통과: {e}")
            cleaned.append(item)
            continue

        verdict = result["verdict"]

        if verdict == "clean":
            cleaned.append(item)

        elif verdict == "suspicious":
            # Way B: 의심 구간 마스킹 후 유지
            filtered = True
            logger.warning(
                f"[MutedRAG] 의심 청크: source={source} "
                f"risk={result['risk_level']} score={result['adjusted_risk']:.3f} "
                f"rules={result['triggered_rules']}"
            )
            _log_security_event("suspicious", source, result)
            new_item = dict(item)
            new_item["chunk"] = dict(item["chunk"])
            new_item["chunk"]["text"] = _mask_with_redacted(chunk_text, result["triggered_rules"])
            cleaned.append(new_item)

        else:  # malicious (high/critical)
            # Way C: 제거
            filtered = True
            logger.error(
                f"[MutedRAG] 악성 청크 차단: source={source} "
                f"risk={result['risk_level']} score={result['adjusted_risk']:.3f} "
                f"rules={result['triggered_rules']}"
            )
            _log_security_event("malicious", source, result)

    return cleaned, filtered


# ─────────────────────────────────────────
# 세션 영속성
# ─────────────────────────────────────────

# ─────────────────────────────────────────
# 문서 레지스트리 관리
# ─────────────────────────────────────────

def load_registry():
    """doc_registry.json에서 문서 등록 현황 복원"""
    global _doc_registry
    if REGISTRY_FILE.exists():
        try:
            _doc_registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            logger.info(f"문서 레지스트리 복원: {len(_doc_registry)}개")
        except Exception as e:
            logger.warning(f"레지스트리 로드 실패: {e}")
            _doc_registry = {}


def save_registry():
    """문서 레지스트리를 파일에 저장"""
    try:
        REGISTRY_FILE.write_text(
            json.dumps(_doc_registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"레지스트리 저장 실패: {e}")


def analyze_document_chunks(chunks: list) -> tuple[list, dict]:
    """
    청크 목록에 MutedRAG 분석 적용.
    반환: (chunk_results, risk_summary)
    """
    texts = [c.get("text", "") for c in chunks]
    try:
        corpus_stats = estimate_corpus_stats(texts)
    except Exception:
        corpus_stats = None

    chunk_results = []
    risk_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}

    for chunk in chunks:
        try:
            result = compute_muted_rag_score(chunk.get("text", ""), corpus_stats=corpus_stats)
        except Exception:
            result = {"verdict": "clean", "risk_level": "low", "adjusted_risk": 0.0,
                      "instructionality": 0.0, "refusal_inducing": 0.0, "outlier": 0.0,
                      "triggered_rules": [], "explanation": ""}

        risk_level = result["risk_level"]
        risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

        chunk_results.append({
            "chunk_id":        chunk.get("chunk_id", ""),
            "text_preview":    chunk.get("text", "")[:300],
            "text_full":       chunk.get("text", ""),
            "risk_level":      risk_level,
            "adjusted_risk":   round(result["adjusted_risk"], 3),
            "instructionality": round(result["instructionality"], 3),
            "refusal_inducing": round(result["refusal_inducing"], 3),
            "outlier":         round(result["outlier"], 3),
            "triggered_rules": result["triggered_rules"],
            "explanation":     result["explanation"],
        })

    # 전체 위험도 = 가장 높은 위험도
    overall = "low"
    for level in ["critical", "high", "medium", "low"]:
        if risk_counts[level] > 0:
            overall = level
            break

    risk_summary = {**risk_counts, "overall": overall, "total": len(chunks)}
    return chunk_results, risk_summary


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
    init_db()
    load_sessions()
    load_registry()
    PENDING_DIR.mkdir(exist_ok=True)
    init_index()
    logger.info("MutedRAG 서버 시작 완료")
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

# ─────────────────────────────────────────
# 인증 엔드포인트
# ─────────────────────────────────────────

@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    user = verify_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀렸습니다.")
    token = _create_token(user["username"], user["role"])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,   # JS에서 document.cookie로 접근 불가 (XSS 방어)
        samesite="lax",  # CSRF 기본 방어
        max_age=3600 * 8,
        path="/",
    )
    return {"role": user["role"], "username": user["username"], "name": user.get("name")}


@app.get("/api/me")
async def me(session: str = Cookie(None)):
    if not session:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    user = _verify_token(session)
    if not user:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    return user


@app.post("/api/logout")
async def logout(response: Response, session: str = Cookie(None)):
    if session:
        _revoke_token(session)
    response.delete_cookie("session", path="/")
    return {"success": True}


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
        profile     = build_query_profile(req.message)
        system      = build_rag_system_prompt(final_items, query=req.message, profile=profile)
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
async def upload_file(
    file: UploadFile = File(...),
    doc_type: str = "internal",   # internal | external_trusted | external_untrusted
):
    """
    파일 업로드 → pending_docs에 저장 → MutedRAG 분석 → 관리자 승인 대기
    (자동 인덱싱 없음, 관리자가 승인해야 검색에 반영)
    """
    allowed = {".pdf", ".docx", ".txt"}
    ext     = Path(file.filename or "").suffix.lower()

    if ext not in allowed:
        raise HTTPException(status_code=400,
            detail=f"지원하지 않는 파일 형식. 허용: {', '.join(allowed)}")

    PENDING_DIR.mkdir(exist_ok=True)
    doc_id    = str(uuid.uuid4())[:8]
    save_path = PENDING_DIR / f"{doc_id}_{file.filename or f'upload{ext}'}"

    content = await file.read()
    save_path.write_bytes(content)

    # 청크 분석
    from rag.chunking import load_document_blocks, chunk_blocks
    blocks     = load_document_blocks(save_path)
    new_chunks = chunk_blocks(blocks, save_path)

    # MutedRAG 분석 (백그라운드)
    loop = asyncio.get_event_loop()
    chunk_results, risk_summary = await loop.run_in_executor(
        None, lambda: analyze_document_chunks(new_chunks)
    )

    # 레지스트리 등록
    _doc_registry[doc_id] = {
        "doc_id":       doc_id,
        "filename":     file.filename or f"upload{ext}",
        "file_path":    str(save_path),
        "status":       "pending",
        "doc_type":     doc_type,
        "upload_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "risk_summary": risk_summary,
        "chunks":       chunk_results,
    }
    save_registry()

    # 보안 이벤트 로깅
    if risk_summary["high"] > 0 or risk_summary["critical"] > 0:
        logger.warning(f"[Upload] 고위험 청크 감지: {file.filename} "
                       f"high={risk_summary['high']} critical={risk_summary['critical']}")

    logger.info(f"[Upload] {file.filename}: {len(new_chunks)}개 청크, "
                f"overall_risk={risk_summary['overall']}, 관리자 승인 대기")

    return UploadResponse(
        success=True,
        filename=file.filename or "unknown",
        chunk_count=len(new_chunks),
    )


# ─────────────────────────────────────────
# 관리자 API
# ─────────────────────────────────────────

@app.get("/api/admin/documents")
async def admin_list_documents():
    """등록된 모든 문서 목록 반환 (청크 내용 제외, 요약만)"""
    docs = []
    for doc in _doc_registry.values():
        docs.append({
            "doc_id":       doc["doc_id"],
            "filename":     doc["filename"],
            "status":       doc["status"],
            "doc_type":     doc["doc_type"],
            "upload_time":  doc["upload_time"],
            "risk_summary": doc["risk_summary"],
        })
    docs.sort(key=lambda d: d["upload_time"], reverse=True)
    return {"documents": docs}


@app.get("/api/admin/documents/{doc_id}")
async def admin_get_document(doc_id: str):
    """문서 상세 조회 (청크별 위험도 포함)"""
    doc = _doc_registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return doc


@app.post("/api/admin/documents/{doc_id}/approve")
async def admin_approve_document(doc_id: str):
    """문서 승인 → corpus_docs로 이동 → 인덱스 재빌드"""
    doc = _doc_registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    if doc["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"이미 처리된 문서입니다: {doc['status']}")

    src = Path(doc["file_path"])
    if not src.exists():
        raise HTTPException(status_code=400, detail="파일이 존재하지 않습니다.")

    # corpus_docs로 이동
    dest_dir = Path("corpus_docs")
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / doc["filename"]
    src.rename(dest)

    _doc_registry[doc_id]["status"]    = "approved"
    _doc_registry[doc_id]["file_path"] = str(dest)
    save_registry()

    # 인덱스 재빌드
    asyncio.create_task(async_rebuild_index())
    logger.info(f"[Admin] 승인: {doc['filename']} → corpus_docs, 재빌드 시작")

    return {"success": True, "message": f"'{doc['filename']}' 승인 완료. 인덱스 재빌드 중..."}


@app.post("/api/admin/documents/{doc_id}/reject")
async def admin_reject_document(doc_id: str):
    """문서 거부 → 파일 삭제"""
    doc = _doc_registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    if doc["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"이미 처리된 문서입니다: {doc['status']}")

    src = Path(doc["file_path"])
    if src.exists():
        src.unlink()

    _doc_registry[doc_id]["status"] = "rejected"
    save_registry()
    logger.info(f"[Admin] 거부: {doc['filename']} 삭제됨")

    return {"success": True, "message": f"'{doc['filename']}' 거부 및 삭제 완료."}


# ─────────────────────────────────────────
# 정적 파일 서빙
# ─────────────────────────────────────────

@app.get("/login")
async def serve_login(session: str = Cookie(None)):
    # 이미 로그인된 경우 역할에 맞는 페이지로 이동
    if session:
        user = _verify_token(session)
        if user:
            return RedirectResponse("/admin" if user["role"] == "admin" else "/")
    return FileResponse("login.html")


@app.get("/")
async def serve_index(session: str = Cookie(None)):
    # 서버사이드 인증 검사 — 미인증이면 로그인 페이지로
    if not session or not _verify_token(session):
        return RedirectResponse("/login")
    return FileResponse("index.html")


@app.get("/admin")
async def serve_admin(session: str = Cookie(None)):
    # 서버사이드 인증 검사 — 미인증이면 로그인 페이지로
    if not session:
        return RedirectResponse("/login")
    user = _verify_token(session)
    if not user:
        return RedirectResponse("/login")
    # 관리자가 아니면 메인 페이지로
    if user["role"] != "admin":
        return RedirectResponse("/")
    return FileResponse("admin.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
