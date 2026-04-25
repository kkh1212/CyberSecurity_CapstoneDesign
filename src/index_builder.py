import pickle

try:
    import faiss
except ModuleNotFoundError:  # pragma: no cover - optional in sparse-only environments
    faiss = None
import numpy as np
from rank_bm25 import BM25Okapi

from src.chunking import load_documents_from_dir
from src.config import (
    INDEX_DIR,
    RAW_DOCS_DIR,
    ENABLE_DENSE,
    get_detector_file_paths,
    get_domain_index_dir,
    get_domain_name,
    get_index_file_paths,
    get_requested_domain,
    list_domain_dirs,
)
from src.detector_pipeline import analyze_chunks_for_ingestion, print_detector_ingestion_summary, write_detector_artifacts
from src.embedder import embed_texts
from src.retrievers import tokenize_text


def build_domain_index(domain_name, domain_dir):
    index_dir = get_domain_index_dir(domain_name)
    paths = get_index_file_paths(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    chunks = load_documents_from_dir(domain_dir)
    if not chunks:
        print(f"[SKIP] No documents found in {domain_dir}")
        return

    print(f"\n[DOMAIN] {domain_name}")
    print(f"Total chunks: {len(chunks)}")
    detector_result = analyze_chunks_for_ingestion(domain_name, chunks)
    sanitized_chunks = detector_result["indexed_chunks"]
    flagged_chunks = detector_result["flagged_chunks"]
    quarantined_chunks = detector_result["quarantined_chunks"]
    summary = detector_result["summary"]
    corpus_stats = detector_result["corpus_stats"]

    write_detector_artifacts(
        index_dir,
        summary,
        corpus_stats,
        flagged_chunks,
        quarantined_chunks,
        get_detector_file_paths,
    )
    print_detector_ingestion_summary(summary)

    if not sanitized_chunks:
        for output_path in paths.values():
            if output_path.exists():
                output_path.unlink()
        with open(paths["chunks"], "wb") as file:
            pickle.dump([], file)
        print("[SKIP] No indexable chunks remain after detector policy.")
        return

    texts = [chunk["text"] for chunk in sanitized_chunks]
    if ENABLE_DENSE:
        if faiss is None:
            raise ModuleNotFoundError(
                "faiss is required when ENABLE_DENSE=true. Install faiss or rerun with ENABLE_DENSE=false."
            )
        print("Generating embeddings...")
        embeddings = np.asarray(embed_texts(texts), dtype="float32")

        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)
        faiss.write_index(index, str(paths["faiss"]))
    elif paths["faiss"].exists():
        paths["faiss"].unlink()

    tokenized_corpus = [tokenize_text(text) for text in texts]
    bm25 = BM25Okapi(tokenized_corpus)

    with open(paths["chunks"], "wb") as file:
        pickle.dump(sanitized_chunks, file)

    with open(paths["bm25"], "wb") as file:
        pickle.dump(bm25, file)

    print("Index build complete")
    if ENABLE_DENSE:
        print(f"FAISS: {paths['faiss']}")
    else:
        print("FAISS: skipped (ENABLE_DENSE=false)")
    print(f"Chunks: {paths['chunks']}")
    print(f"BM25: {paths['bm25']}")
    print(f"Flagged review chunks: {len(flagged_chunks)}")
    print(f"Quarantined chunks: {len(quarantined_chunks)}")


def main():
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    domain_dirs = list_domain_dirs()
    requested_domain = get_requested_domain()

    if not domain_dirs:
        print(f"No domain directories found in {RAW_DOCS_DIR}")
        return

    if requested_domain:
        domain_dirs = [domain_dir for domain_dir in domain_dirs if domain_dir.name == requested_domain]
        if not domain_dirs:
            print(f"Requested domain not found: {requested_domain}")
            return

    for domain_dir in domain_dirs:
        build_domain_index(get_domain_name(domain_dir), domain_dir)


if __name__ == "__main__":
    main()
