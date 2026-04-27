"""
MutedRAG attack 파일 주입 스크립트.

data/exp_corpus/benign/ 전체를 staging dir로 복사한 뒤,
attack/ 하위 지정 유형에서 파일을 샘플링해 99_injected/ 도메인으로 추가한다.
생성된 staging dir을 RAW_DOCS_DIR로 지정하면 ingest_app이 해당 코퍼스를 인덱싱한다.

기본 전략:
  blackbox: 논문 black-box 설정처럼 target query당 malicious text 1개를 주입한다.

오염률 전략(rate) 계산 (chunk 기준 근사):
  benign_chunks ≈ 4125 (실측값)
  attack_chunks = round(benign_chunks * rate / (1 - rate))
  attack_files  = max(1, ceil(attack_chunks / AVG_CHUNKS_PER_ATTACK_FILE))
  rate=0.01 → attack_chunks≈42 → ceil(42/2)=21 → min(21,15)=15 파일 전체 사용

사용:
  python -m experiments.attack.inject --no-attack
  python -m experiments.attack.inject --strategy blackbox
  python -m experiments.attack.inject --strategy rate --rate 0.01
  python -m experiments.attack.inject --rate 0.01 --types 02_간접_명시형 03_간접_혼합형
  python -m experiments.attack.inject --rate 0.01 --stage-dir data/exp_stage --seed 42
"""

import argparse
import json
import math
import random
import shutil
from pathlib import Path

from experiments.attack.payloads import PAYLOAD_TYPES, list_attack_files, list_all_attack_files
from src.chunking import chunk_blocks, load_document_blocks

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENIGN_BASE = PROJECT_ROOT / "data" / "exp_corpus" / "benign"
DEFAULT_STAGE_DIR = PROJECT_ROOT / "data" / "exp_stage"

KNOWN_BENIGN_CHUNKS = 4125


def stage_benign(stage_dir: Path, benign_dir: Path) -> int:
    """benign 도메인 디렉토리를 stage_dir로 복사한다. 복사된 파일 수를 반환한다."""
    if not benign_dir.exists():
        raise FileNotFoundError(f"Benign dir not found: {benign_dir}")

    file_count = 0
    for domain_dir in sorted(benign_dir.iterdir()):
        if not domain_dir.is_dir():
            continue
        target = stage_dir / domain_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(domain_dir, target)
        file_count += sum(1 for f in target.rglob("*") if f.is_file())

    return file_count


def calc_target_attack_chunks(rate: float, benign_chunks: int) -> int:
    """전체 chunk 중 attack chunk 비율이 rate가 되도록 attack chunk 수를 계산한다."""
    if rate <= 0:
        return 0
    if rate >= 1:
        raise ValueError("rate must be less than 1.0")
    return max(1, math.ceil(benign_chunks * rate / (1 - rate)))


def count_file_chunks(path: Path) -> int:
    """현재 프로젝트 chunking 로직 기준으로 단일 파일이 만드는 chunk 수를 센다."""
    blocks = load_document_blocks(path)
    return len(chunk_blocks(blocks, path))


def count_staged_chunks(stage_dir: Path) -> int:
    """stage_dir 하위 문서들의 chunk 수를 센다."""
    total = 0
    for path in sorted(stage_dir.rglob("*")):
        if not path.is_file() or path.name == "inject_summary.json":
            continue
        total += count_file_chunks(path)
    return total


def sample_attack_files(attack_types: list, n: int, seed: int) -> list:
    """지정 유형 전체 파일 풀에서 n개를 랜덤 샘플링한다."""
    rng = random.Random(seed)
    pool: list = []
    for t in attack_types:
        pool.extend(list_attack_files(t))

    if not pool:
        return []
    if n >= len(pool):
        return pool[:]
    return rng.sample(pool, n)


def inject(
    rate: float,
    attack_types: list,
    stage_dir: Path,
    benign_dir: Path,
    seed: int,
    strategy: str = "blackbox",
    benign_chunks: int = KNOWN_BENIGN_CHUNKS,
) -> dict:
    """
    staging dir을 구성하고 attack 파일을 주입한다.

    Returns:
        dict: 주입 결과 요약 (파일 수, 경로, 설정값 등)
    """
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    # 1. benign 파일 staging
    benign_file_count = stage_benign(stage_dir, benign_dir)
    actual_benign_chunks = count_staged_chunks(stage_dir)
    print(f"[inject] Benign files staged: {benign_file_count}  →  {stage_dir}")
    print(f"[inject] Benign chunks: {actual_benign_chunks}")

    # 2. attack 파일 풀 구성
    all_available = []
    for t in attack_types:
        all_available.extend(list_attack_files(t))
    sampled = sample_attack_files(attack_types, len(all_available), seed) if all_available else []
    if strategy == "blackbox":
        target_attack_chunks = None
        max_copies = len(sampled)
    elif strategy == "rate":
        target_attack_chunks = calc_target_attack_chunks(rate, actual_benign_chunks)
        max_copies = None
    else:
        raise ValueError(f"Unknown injection strategy: {strategy}")

    # 3. attack 파일을 benign 도메인 디렉토리에 직접 주입
    # filename prefix 기반으로 해당 도메인 선택 (없으면 01_보안정책 fallback)
    PREFIX_TO_DOMAIN = {
        "SEC": "01_보안정책",
        "HR": "02_인사총무",
        "MFA": "03_IT운영",
        "IT": "03_IT운영",
        "OPS": "03_IT운영",
        "D1": "04_보안운영",
        "D3": "04_보안운영",
        "LEG": "05_법무컴플라이언스",
        "SVC": "06_제품지원",
        "PRD": "06_제품지원",
        "FIN": "07_재무경비",
    }
    DEFAULT_DOMAIN = "01_보안정책"

    injected_paths = []
    injected_chunk_count = 0
    copy_index = 0
    while sampled and (copy_index < max_copies if max_copies is not None else injected_chunk_count < target_attack_chunks):
        src = sampled[copy_index % len(sampled)]
        prefix = src.stem.split("-")[0].split("_")[0].upper()
        domain = PREFIX_TO_DOMAIN.get(prefix, DEFAULT_DOMAIN)
        dst_dir = stage_dir / domain
        dst_dir.mkdir(parents=True, exist_ok=True)
        if copy_index < len(sampled):
            dst_name = src.name
        else:
            dst_name = f"{src.stem}__mutedrag_dup{copy_index // len(sampled):03d}{src.suffix}"
        dst = dst_dir / dst_name
        shutil.copy2(src, dst)
        injected_paths.append(dst)
        injected_chunk_count += count_file_chunks(dst)
        copy_index += 1

    actual_rate = (
        injected_chunk_count / (actual_benign_chunks + injected_chunk_count)
        if actual_benign_chunks + injected_chunk_count
        else 0.0
    )

    summary = {
        "rate_target": rate,
        "rate_actual": round(actual_rate, 4),
        "strategy": strategy,
        "benign_chunks": actual_benign_chunks,
        "target_attack_chunks": target_attack_chunks,
        "attack_chunks": injected_chunk_count,
        "benign_files": benign_file_count,
        "attack_files": len(injected_paths),
        "unique_attack_files": len(sampled),
        "attack_types": attack_types,
        "stage_dir": str(stage_dir),
        "injected_dir": str(stage_dir) + " (blended into benign domains)",
        "seed": seed,
        "injected_names": [f.name for f in injected_paths],
    }

    if target_attack_chunks is not None:
        print(f"[inject] Target attack chunks: {target_attack_chunks}")
    else:
        print("[inject] Target attack chunks: n/a (blackbox one malicious text per target)")
    print(f"[inject] Attack files injected: {len(injected_paths)} → blended into benign domains")
    for dst in injected_paths:
        print(f"         - {dst.parent.name}/{dst.name}")
    print(f"[inject] Contamination rate (actual chunk-based): {actual_rate:.2%}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Inject attack files into experiment staging corpus."
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=0.01,
        help="Target contamination rate as a fraction (default: 0.01 = 1%%)",
    )
    parser.add_argument(
        "--strategy",
        choices=("blackbox", "rate"),
        default="blackbox",
        help="Injection strategy: blackbox injects each attack file once; rate duplicates to target a chunk ratio.",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=list(PAYLOAD_TYPES.keys()),
        metavar="TYPE",
        help="Attack subdir types to sample from (default: all 4 types). "
             "Example: --types 02_간접_명시형 03_간접_혼합형",
    )
    parser.add_argument(
        "--no-attack",
        action="store_true",
        help="Only stage benign files without injecting attack files.",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=DEFAULT_STAGE_DIR,
        help=f"Staging output directory (default: {DEFAULT_STAGE_DIR})",
    )
    parser.add_argument(
        "--benign-dir",
        type=Path,
        default=BENIGN_BASE,
        help=f"Source benign corpus directory (default: {BENIGN_BASE})",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--benign-chunks",
        type=int,
        default=KNOWN_BENIGN_CHUNKS,
        help=f"Known benign chunk count for rate calculation (default: {KNOWN_BENIGN_CHUNKS})",
    )
    args = parser.parse_args()

    result = inject(
        rate=args.rate,
        attack_types=[] if args.no_attack else args.types,
        stage_dir=args.stage_dir,
        benign_dir=args.benign_dir,
        seed=args.seed,
        strategy=args.strategy,
        benign_chunks=args.benign_chunks,
    )

    summary_path = args.stage_dir / "inject_summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[inject] Summary saved → {summary_path}")


if __name__ == "__main__":
    main()
