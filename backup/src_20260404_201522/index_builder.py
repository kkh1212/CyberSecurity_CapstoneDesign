import pickle

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from src.chunking import load_documents_from_dir
from src.config import (
    INDEX_DIR,
    RAW_DOCS_DIR,
    get_domain_index_dir,
    get_domain_name,
    get_index_file_paths,
    get_requested_domain,
    list_domain_dirs,
)
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

    texts = [chunk["text"] for chunk in chunks]

    print(f"\n[DOMAIN] {domain_name}")
    print(f"Total chunks: {len(chunks)}")
    print("Generating embeddings...")
    embeddings = np.asarray(embed_texts(texts), dtype="float32")

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    faiss.write_index(index, str(paths["faiss"]))

    tokenized_corpus = [tokenize_text(text) for text in texts]
    bm25 = BM25Okapi(tokenized_corpus)

    with open(paths["chunks"], "wb") as file:
        pickle.dump(chunks, file)

    with open(paths["bm25"], "wb") as file:
        pickle.dump(bm25, file)

    print("Index build complete")
    print(f"FAISS: {paths['faiss']}")
    print(f"Chunks: {paths['chunks']}")
    print(f"BM25: {paths['bm25']}")


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
