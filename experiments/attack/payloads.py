"""
MutedRAG 공격 페이로드 유형 정의.

data/exp_corpus/attack/ 하위 유형 디렉토리의 기존 파일을 그대로 활용하며
새로 생성하지 않는다. 유형별 파일 목록 조회 함수만 제공한다.

유형 설명:
  01_직접인젝션     - jailbreak 명령어를 문서에 직접 삽입. context로 주입되면 guardrail 즉시 발동.
  02_간접_명시형    - 거부 유도 문구를 명시적으로 포함한 정책/가이드 형식 문서.
  03_간접_혼합형    - 정상 내용 + 거부 유도 문구 혼합. 외형상 정상 문서처럼 위장.
  04_다국어혼합     - 한국어·영어 혼용으로 거부 유도. 언어 기반 탐지 회피 목적.
"""

import os
from pathlib import Path
from typing import Dict, List

EXPERIMENTS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = EXPERIMENTS_DIR.parent
ATTACK_BASE = Path(
    os.getenv("ATTACK_BASE_DIR", PROJECT_ROOT / "data" / "exp_corpus" / "attack")
).expanduser()

PAYLOAD_TYPES: Dict[str, Dict] = {
    "01_직접인젝션": {
        "description": "jailbreak 명령어 직접 삽입형",
        "subdir": "01_직접인젝션",
        "include_glob": "*__direct.*",
        "risk": "critical",
    },
    "02_간접_명시형": {
        "description": "거부 유도 문구 명시형 정책 문서",
        "subdir": "02_간접_명시형",
        "include_glob": "*__indirect_explicit.*",
        "risk": "high",
    },
    "03_간접_혼합형": {
        "description": "정상 내용과 거부 유도 문구 혼합형",
        "subdir": "03_간접_혼합형",
        "include_glob": "*__indirect_mixed.*",
        "risk": "high",
    },
    "04_다국어혼합": {
        "description": "한국어·영어 혼용 거부 유도형",
        "subdir": "04_다국어혼합",
        "include_glob": "*__multilingual.*",
        "risk": "high",
    },
}

SUPPORTED_EXTS = {".txt", ".docx", ".pdf", ".doc", ".hwp", ".hwpx"}


def list_attack_files(payload_type: str) -> List[Path]:
    """특정 유형의 attack 파일 목록을 반환한다."""
    meta = PAYLOAD_TYPES.get(payload_type)
    if meta is None:
        raise ValueError(
            f"Unknown payload type: {payload_type!r}. "
            f"Valid types: {list(PAYLOAD_TYPES)}"
        )
    subdir = ATTACK_BASE / meta["subdir"]
    if not subdir.exists():
        raise FileNotFoundError(f"Attack subdir not found: {subdir}")
    pattern = meta.get("include_glob", "*")
    return sorted(
        f for f in subdir.rglob(pattern)
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
    )


def list_all_attack_files() -> List[Path]:
    """모든 유형의 attack 파일 목록을 반환한다."""
    files: List[Path] = []
    for payload_type in PAYLOAD_TYPES:
        files.extend(list_attack_files(payload_type))
    return files


def describe_payload_types() -> None:
    """페이로드 유형별 파일 목록을 출력한다."""
    for type_key, meta in PAYLOAD_TYPES.items():
        try:
            files = list_attack_files(type_key)
        except FileNotFoundError as exc:
            print(f"[{type_key}] ERROR: {exc}")
            continue
        print(f"\n[{type_key}] {meta['description']} (risk={meta['risk']})")
        for f in files:
            print(f"  - {f.name}")
    all_files = list_all_attack_files()
    print(f"\n총 attack 파일 수: {len(all_files)}")


if __name__ == "__main__":
    describe_payload_types()
