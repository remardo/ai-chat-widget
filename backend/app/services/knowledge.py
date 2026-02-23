"""Knowledge base loader with optional vector retrieval."""

from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence


class KnowledgeBase:
    """Load knowledge files and serve query-focused context."""

    def __init__(
        self,
        knowledge_path: str,
        *,
        enable_rag: bool = True,
        top_k: int = 5,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        use_transformer_embeddings: bool = False,
        use_zvec: bool = False,
        fallback_max_chars: int = 12000,
    ):
        self.knowledge_path = knowledge_path
        self.enable_rag = enable_rag
        self.top_k = max(1, top_k)
        self.chunk_size = max(200, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size // 2))
        self.embedding_model = embedding_model
        self.use_transformer_embeddings = use_transformer_embeddings
        self.use_zvec = use_zvec
        self.fallback_max_chars = max(1500, fallback_max_chars)

        self.content = ""
        self._documents: List[Dict[str, str]] = []
        self._collection = None
        self._embed_fn: Optional[Callable[[Sequence[str]], Sequence[Sequence[float]]]] = None
        self._fallback_chunks: List[Dict[str, Any]] = []
        self._index_attempted = False
        self._zvec_checked = False
        self._zvec_supported = False

        self._load()

    @staticmethod
    def _hash_embed(texts: Sequence[str], dim: int = 384) -> Sequence[Sequence[float]]:
        """
        Lightweight deterministic embedding without external models.
        Prevents runtime OOM/model-download failures on small servers.
        """
        vectors: List[List[float]] = []
        for text in texts:
            vec = [0.0] * dim
            for token in re.findall(r"\w+", (text or "").lower()):
                idx = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % dim
                vec[idx] += 1.0

            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors

    def _iter_knowledge_files(self):
        """Yield supported knowledge file paths."""
        for root, _dirs, files in os.walk(self.knowledge_path):
            for filename in files:
                if filename.endswith((".md", ".txt")):
                    yield os.path.join(root, filename)

    def _make_chunks(self, text: str) -> List[str]:
        """Split content into overlapping chunks."""
        clean = text.strip()
        if not clean:
            return []

        chunks: List[str] = []
        start = 0
        n = len(clean)
        while start < n:
            end = min(n, start + self.chunk_size)
            chunk = clean[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            start = max(0, end - self.chunk_overlap)
        return chunks

    def _build_embed_fn(self):
        """Lazily initialize sentence-transformers encoder."""
        if self._embed_fn is not None:
            return

        if not self.use_transformer_embeddings:
            self._embed_fn = self._hash_embed
            return

        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(self.embedding_model)

            def _embed(texts: Sequence[str]):
                vectors = model.encode(list(texts), normalize_embeddings=True)
                return vectors.tolist()
        except Exception as e:
            print(f"Warning: sentence-transformers unavailable, using hash embeddings: {e}")
            self._embed_fn = self._hash_embed
            return

        self._embed_fn = _embed

    def _build_vector_index(self):
        """Build zvec index from chunked documents."""
        self._index_attempted = True
        if not self.enable_rag:
            return
        if not self.use_zvec:
            print("RAG vector index disabled (RAG_USE_ZVEC=false), using text fallback")
            return
        if not self._documents:
            return

        if not self._zvec_checked:
            self._zvec_supported = self._probe_zvec_runtime()
            self._zvec_checked = True
        if not self._zvec_supported:
            print("zvec runtime probe failed, using text fallback")
            return

        try:
            import zvec
        except Exception as e:
            print(f"Warning: zvec unavailable, using text fallback: {e}")
            return

        try:
            self._build_embed_fn()
            assert self._embed_fn is not None

            sample_vec = self._embed_fn(["dimension probe"])[0]
            dimensions = len(sample_vec)

            # In-memory index: enough for startup-time indexing and retrieval.
            self._collection = zvec.create(
                "knowledge",
                dimensions=dimensions,
                embedding_fn=self._embed_fn,
                fields={"content": "str", "source": "str"},
                metric="cosine",
            )

            ids: List[str] = []
            docs: List[str] = []
            fields: List[Dict[str, str]] = []

            for doc in self._documents:
                source = doc["source"]
                chunks = self._make_chunks(doc["content"])
                for idx, chunk in enumerate(chunks):
                    chunk_id = hashlib.sha1(f"{source}:{idx}".encode("utf-8")).hexdigest()
                    ids.append(chunk_id)
                    docs.append(chunk)
                    fields.append({"content": chunk, "source": source})

            if ids:
                self._collection.upsert(ids=ids, docs=docs, fields=fields)
                print(f"zvec index ready: {len(ids)} chunks from {len(self._documents)} files")
        except Exception as e:
            self._collection = None
            print(f"Warning: Failed to initialize zvec index, using fallback: {e}")

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-Zа-яА-Я0-9_-]{2,}", (text or "").lower()) if len(t) > 1]

    def _build_fallback_chunks(self):
        """Prepare chunk cache for lexical fallback retrieval."""
        self._fallback_chunks = []
        for doc in self._documents:
            source = doc["source"]
            chunks = self._make_chunks(doc["content"])
            for idx, chunk in enumerate(chunks):
                self._fallback_chunks.append(
                    {
                        "source": source,
                        "content": chunk,
                        "chunk_index": idx,
                        "tokens": set(self._tokenize(chunk)),
                    }
                )

    def _fallback_context_for_query(self, query: str, k: int) -> str:
        """Token-overlap retrieval used when vector index is unavailable."""
        if not self._fallback_chunks:
            return self.get_content()[: self.fallback_max_chars]

        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            # Prefer diverse documents for generic queries.
            selected = self._fallback_chunks[:k]
        else:
            scored: List[tuple[int, int, Dict[str, Any]]] = []
            for chunk in self._fallback_chunks:
                overlap = len(query_tokens.intersection(chunk["tokens"]))
                if overlap <= 0:
                    continue
                scored.append((overlap, -chunk["chunk_index"], chunk))

            if not scored:
                selected = self._fallback_chunks[:k]
            else:
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                selected = [item[2] for item in scored[:k]]

        snippets: List[str] = []
        for chunk in selected:
            snippets.append(
                f"[source: {chunk['source']}, mode: lexical]\n{chunk['content']}"
            )
        return "\n\n---\n\n".join(snippets)

    def _probe_zvec_runtime(self) -> bool:
        """
        Probe zvec in a separate process to avoid crashing the API process
        on incompatible CPU/instruction-set issues.
        """
        probe_code = (
            "import zvec\n"
            "c=zvec.create('probe', dimensions=8, embedding_fn=lambda xs:[[0.0]*8 for _ in xs], "
            "fields={'content':'str'}, metric='cosine')\n"
            "c.upsert(ids=['1'], docs=['test'], fields=[{'content':'test'}])\n"
            "print('ok')\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", probe_code],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return True
            err = (result.stderr or result.stdout or "").strip()
            print(f"Warning: zvec probe failed (code={result.returncode}): {err}")
            return False
        except Exception as e:
            print(f"Warning: zvec probe exception: {e}")
            return False

    def _load(self):
        """Load all knowledge files and rebuild index."""
        self.content = ""
        self._documents = []
        self._collection = None
        self._fallback_chunks = []
        self._index_attempted = False

        if not os.path.exists(self.knowledge_path):
            print(f"Warning: Knowledge path does not exist: {self.knowledge_path}")
            return

        rendered_documents: List[str] = []

        for file_path in self._iter_knowledge_files():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                rel_path = os.path.relpath(file_path, self.knowledge_path)
                self._documents.append({"source": rel_path, "content": content})
                rendered_documents.append(f"=== {rel_path} ===\n\n{content}")
            except Exception as e:
                print(f"Warning: Failed to load {file_path}: {e}")

        if rendered_documents:
            self.content = "\n\n---\n\n".join(rendered_documents)
            print(f"Loaded {len(rendered_documents)} knowledge documents")
        else:
            print("No knowledge documents found")

        self._build_fallback_chunks()

        # Defer vector index build to first relevant query.

    def get_content(self) -> str:
        """Get full knowledge content."""
        return self.content

    def get_context_for_query(self, query: str, top_k: Optional[int] = None) -> str:
        """Get query-focused context via vector search, with safe fallback."""
        if not query:
            return self.get_content()[: self.fallback_max_chars]

        k = max(1, top_k or self.top_k)

        if self._collection is not None:
            try:
                result = self._collection.query(
                    query,
                    top_k=k,
                    include=["score", "fields"],
                )
                rows = result.to_list()
                if rows:
                    snippets: List[str] = []
                    for item in rows:
                        item_fields = item.fields or {}
                        source = item_fields.get("source", "unknown")
                        content = item_fields.get("content", item.doc or "")
                        score = item.score if item.score is not None else 0.0
                        snippets.append(
                            f"[source: {source}, score: {score:.4f}]\n{content}"
                        )
                    return "\n\n---\n\n".join(snippets)
            except Exception as e:
                print(f"Warning: zvec query failed, using fallback: {e}")

        # Lazy index build to keep API startup fast and healthcheck-friendly.
        if not self._index_attempted and self.enable_rag and self._documents:
            self._build_vector_index()
            if self._collection is not None:
                return self.get_context_for_query(query, top_k=k)

        return self._fallback_context_for_query(query, k)

    def reload(self):
        """Reload knowledge base and rebuild vector index."""
        self._load()
