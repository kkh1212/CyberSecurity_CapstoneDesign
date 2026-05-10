# Shared helpers for fixed final MutedRAG corpus experiments.

FINAL_CORPUS_DIR="${FINAL_CORPUS_DIR:-${PROJECT_ROOT}/data/exp_corpus/final_strong_v4}"

stage_fixed_condition() {
    local condition="$1" mode="$2" rate="$3" stage_dir="$4" queries_out="$5"
    log "STAGE ${condition} mode=${mode} rate=${rate} corpus=${FINAL_CORPUS_DIR}"
    rm -rf "${stage_dir}"
    mkdir -p "${stage_dir}" "$(dirname "${queries_out}")"

    (cd "${PROJECT_ROOT}" && "${PYTHON_BIN}" - "${FINAL_CORPUS_DIR}" "${stage_dir}" "${queries_out}" "${mode}" "${rate}" "${condition}" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

corpus_dir = Path(sys.argv[1])
stage_dir = Path(sys.argv[2])
queries_out = Path(sys.argv[3])
mode = sys.argv[4]
rate = float(sys.argv[5])
condition = sys.argv[6]

DOMAIN_DIRS = {
    "SEC": "SEC",
    "HR": "HR",
    "IT": "IT",
    "SOC": "SOC",
    "LEG": "LEG",
    "PST": "PST",
    "FIN": "FIN",
    "GEN": "GEN",
}
UNIVERSAL_DOMAIN_ORDER = ["SEC", "HR", "IT", "SOC", "LEG", "PST", "FIN", "GEN"]
CHUNK_SIZE = 500
CHUNK_OVERLAP = 120

def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc).strip()
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="ignore").strip()

def count_chunks(path: Path) -> int:
    text = read_text(path)
    if not text:
        return 0
    count = 0
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if text[start:end].strip():
            count += 1
        if end == len(text):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return count

def choose_attack_ids(query_ids):
    if mode == "normal_only" or rate <= 0:
        return set()
    if mode.startswith("universal"):
        return set()
    if mode == "muted_all" or rate >= 1:
        return set(query_ids)
    if mode != "rate":
        raise SystemExit(f"unsupported mode: {mode}")
    n = max(1, min(len(query_ids), round(len(query_ids) * rate)))
    return set(query_ids[:n])

def choose_universal_domains():
    if not mode.startswith("universal") or rate <= 0:
        return []
    if mode in {"universal_all", "universal_muted_all"} or rate >= 1:
        return list(UNIVERSAL_DOMAIN_ORDER)
    if mode != "universal_rate":
        raise SystemExit(f"unsupported universal mode: {mode}")
    n = max(1, min(len(UNIVERSAL_DOMAIN_ORDER), round(len(UNIVERSAL_DOMAIN_ORDER) * rate)))
    return UNIVERSAL_DOMAIN_ORDER[:n]

def universal_attack_files(domain: str):
    attack_dir = corpus_dir / "attack_universal_txt"
    return sorted(attack_dir.glob(f"UNIV_{domain}_*__mutedrag_strong.txt"))

queries = json.loads((corpus_dir / "queries_final.json").read_text(encoding="utf-8"))
query_ids = [str(q["query_id"]) for q in queries]
attack_ids = choose_attack_ids(query_ids)
universal_domains = set(choose_universal_domains())
staged = []
total_chunks = 0
attack_chunks = 0
staged_universal_domains = set()
universal_attack_documents = 0
padding_documents = 0

for q in queries:
    qid = str(q["query_id"])
    domain = str(q["domain"])
    domain_dir = DOMAIN_DIRS.get(domain, domain)
    dst_dir = stage_dir / domain_dir
    dst_dir.mkdir(parents=True, exist_ok=True)

    benign_src = corpus_dir / "benign_controlled" / q["benign_filename"]
    benign_dst = dst_dir / q["benign_filename"]
    shutil.copy2(benign_src, benign_dst)
    chunks = count_chunks(benign_dst)
    total_chunks += chunks
    staged.append({"query_id": qid, "kind": "benign", "domain": domain, "path": str(benign_dst), "chunks": chunks})

    if qid in attack_ids:
        attack_src = corpus_dir / "attack_strong_txt" / q["attack_filename"]
        attack_dst = dst_dir / q["attack_filename"]
        shutil.copy2(attack_src, attack_dst)
        chunks = count_chunks(attack_dst)
        total_chunks += chunks
        attack_chunks += chunks
        staged.append({"query_id": qid, "kind": "attack", "domain": domain, "path": str(attack_dst), "chunks": chunks})

    if domain in universal_domains and domain not in staged_universal_domains:
        files = universal_attack_files(domain)
        if not files:
            raise SystemExit(f"missing universal attack file for domain: {domain}")
        for attack_src in files:
            attack_dst = dst_dir / attack_src.name
            shutil.copy2(attack_src, attack_dst)
            chunks = count_chunks(attack_dst)
            total_chunks += chunks
            attack_chunks += chunks
            universal_attack_documents += 1
            staged.append({"query_id": "*", "kind": "attack_universal", "domain": domain, "path": str(attack_dst), "chunks": chunks})
        staged_universal_domains.add(domain)

padding_root = corpus_dir / "benign_padding"
if padding_root.exists():
    for padding_src in sorted(padding_root.rglob("*.txt")):
        try:
            relative = padding_src.relative_to(padding_root)
        except ValueError:
            relative = Path(padding_src.name)
        padding_domain = relative.parts[0] if len(relative.parts) > 1 else "GEN"
        domain_dir = DOMAIN_DIRS.get(padding_domain, padding_domain)
        dst_dir = stage_dir / domain_dir
        dst_dir.mkdir(parents=True, exist_ok=True)
        padding_dst = dst_dir / padding_src.name
        shutil.copy2(padding_src, padding_dst)
        chunks = count_chunks(padding_dst)
        total_chunks += chunks
        padding_documents += 1
        staged.append({"query_id": "-", "kind": "benign_padding", "domain": padding_domain, "path": str(padding_dst), "chunks": chunks})

actual_total_chunks = total_chunks
actual_attack_chunks = attack_chunks
try:
    os.environ["RAW_DOCS_DIR"] = str(stage_dir)
    from src.chunking import load_all_documents

    actual_chunks = load_all_documents()
    attack_sources = {
        str(Path(item["path"]).relative_to(stage_dir).as_posix())
        for item in staged
        if str(item.get("kind", "")).startswith("attack")
    }
    actual_total_chunks = len(actual_chunks)
    actual_attack_chunks = sum(1 for chunk in actual_chunks if chunk.get("source") in attack_sources)
except Exception as exc:
    print(f"[WARN] failed to verify chunk counts with src.chunking: {exc}", file=sys.stderr)

payload = {
    "benign": [],
    "attack": [
        {
            "id": i,
            "query_id": q["query_id"],
            "domain": q["domain"],
            "topic": q.get("topic", ""),
            "text": q["question"],
            "benign_filename": q["benign_filename"],
            "attack_filename": q["attack_filename"],
        }
        for i, q in enumerate(queries, start=1)
    ],
}
queries_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
summary = {
    "condition": condition,
    "corpus_dir": str(corpus_dir),
    "stage_dir": str(stage_dir),
    "mode": mode,
    "rate_requested": rate,
    "total_queries": len(queries),
    "benign_documents": len(queries) + padding_documents,
    "benign_padding_documents": padding_documents,
    "attack_documents": len(attack_ids),
    "universal_attack_documents": universal_attack_documents,
    "universal_attack_domains": sorted(staged_universal_domains),
    "total_documents": len(staged),
    "total_chunks": actual_total_chunks,
    "attack_chunks": actual_attack_chunks,
    "attack_chunk_ratio": actual_attack_chunks / actual_total_chunks if actual_total_chunks else 0,
    "legacy_estimated_total_chunks": total_chunks,
    "legacy_estimated_attack_chunks": attack_chunks,
    "attack_query_ids": sorted(attack_ids),
}
(stage_dir / "inject_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
(stage_dir / "stage_manifest.json").write_text(json.dumps(staged, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
    )
}
