from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RetrievedDocument:
    page_content: str
    metadata: dict[str, Any]


class DoctrineRetriever:
    """Local lexical retriever over processed doctrine chunks."""

    def __init__(
        self,
        chroma_dir: str = "rag/data/chroma",
        collection: str = "ajp_doctrine_chunks",
        lexicons_dir: str = "rag/data/processed/lexicons",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        self.processed_dir = Path(lexicons_dir).resolve().parent

    def retrieve(
        self,
        query: str,
        k: int = 5,
        filter_meta: dict[str, Any] | None = None,
    ) -> dict[str, list[RetrievedDocument]]:
        query_terms = _tokenize(query)
        source_filter = (filter_meta or {}).get("source_doc")
        scored: list[tuple[int, RetrievedDocument]] = []

        for path in self.processed_dir.glob("*.chunks.jsonl"):
            if source_filter and source_filter not in path.name:
                continue
            for item in _read_jsonl(path):
                text = str(item.get("text") or item.get("page_content") or "")
                if not text:
                    continue
                metadata = dict(item.get("metadata") or {})
                metadata.setdefault("source_doc", _source_doc_from_name(path.name))
                metadata.setdefault("page", item.get("page", ""))
                score = len(query_terms & _tokenize(text))
                if score > 0:
                    metadata["score"] = score
                    scored.append((score, RetrievedDocument(text, metadata)))

        scored.sort(key=lambda row: row[0], reverse=True)
        return {"docs_final": [doc for _, doc in scored[:k]]}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        if len(token) > 2
    }


def _source_doc_from_name(name: str) -> str:
    if "3_1" in name or "3.1" in name:
        return "AJP-3.1"
    if "3_2" in name or "3.2" in name:
        return "AJP-3.2"
    if "3_3" in name or "3.3" in name:
        return "AJP-3.3"
    return name
