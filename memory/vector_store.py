"""
Persistent memory — Finch remembers conversations, clients, deals.

Primary backend: ChromaDB (semantic search) when installed.
Fallback: JSON file store (works on free hosts with 512MB RAM).
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


class _JsonMemoryBackend:
    """Lightweight keyword/recency memory for free-tier hosts."""

    def __init__(self, persist_path: str = "./data/memory/"):
        self.path = Path(persist_path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / "memories.json"
        self._items: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.file.exists():
            try:
                self._items = json.loads(self.file.read_text(encoding="utf-8"))
            except Exception:
                self._items = []

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def remember(self, content: str, metadata: Optional[dict] = None, memory_type: str = "conversation") -> str:
        mem_id = str(uuid.uuid4())
        meta = {"timestamp": _now_iso(), "type": memory_type, **(metadata or {})}
        self._items.append({"id": mem_id, "content": content, "metadata": meta})
        # Cap growth on free disk
        if len(self._items) > 2000:
            self._items = self._items[-2000:]
        self._save()
        return mem_id

    def recall(self, query: str, n_results: int = 10, memory_type: Optional[str] = None) -> List[dict]:
        q = (query or "").lower().split()
        scored = []
        for item in self._items:
            meta = item.get("metadata") or {}
            if memory_type and meta.get("type") != memory_type:
                continue
            text = (item.get("content") or "").lower()
            score = sum(1 for w in q if w and w in text) if q else 0
            # Soft boost for recent items
            scored.append((score, item))
        scored.sort(key=lambda x: (x[0], x[1].get("metadata", {}).get("timestamp", "")), reverse=True)
        out = []
        for score, item in scored[:n_results]:
            out.append({
                "content": item["content"],
                "metadata": item.get("metadata") or {},
                "relevance": float(score),
            })
        return out

    def recent(self, n: int = 20, memory_type: Optional[str] = None) -> List[dict]:
        items = self._items
        if memory_type:
            items = [i for i in items if (i.get("metadata") or {}).get("type") == memory_type]
        items = sorted(items, key=lambda m: (m.get("metadata") or {}).get("timestamp", ""), reverse=True)
        return [{"content": i["content"], "metadata": i.get("metadata") or {}} for i in items[:n]]

    def forget(self, memory_id: str) -> None:
        self._items = [i for i in self._items if i.get("id") != memory_id]
        self._save()

    def count(self) -> int:
        return len(self._items)


class _ChromaMemoryBackend:
    def __init__(self, persist_path: str = "./data/memory/", collection_name: str = "finch_memory"):
        import chromadb
        from chromadb.config import Settings

        self.client = chromadb.PersistentClient(
            path=persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Finch's persistent memory"},
        )

    def remember(self, content: str, metadata: Optional[dict] = None, memory_type: str = "conversation") -> str:
        mem_id = str(uuid.uuid4())
        meta = {"timestamp": _now_iso(), "type": memory_type, **(metadata or {})}
        self.collection.add(ids=[mem_id], documents=[content], metadatas=[meta])
        return mem_id

    def recall(self, query: str, n_results: int = 10, memory_type: Optional[str] = None) -> List[dict]:
        where = {"type": memory_type} if memory_type else None
        results = self.collection.query(query_texts=[query], n_results=n_results, where=where)
        if not results["documents"] or not results["documents"][0]:
            return []
        memories = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            memories.append({
                "content": doc,
                "metadata": meta,
                "relevance": 1 - dist if dist else 1.0,
            })
        return sorted(memories, key=lambda m: m["relevance"], reverse=True)

    def recent(self, n: int = 20, memory_type: Optional[str] = None) -> List[dict]:
        all_data = self.collection.get()
        if not all_data["ids"]:
            return []
        memories = []
        for doc, meta in zip(all_data["documents"], all_data["metadatas"]):
            if memory_type and meta.get("type") != memory_type:
                continue
            memories.append({"content": doc, "metadata": meta})
        memories.sort(key=lambda m: m["metadata"].get("timestamp", ""), reverse=True)
        return memories[:n]

    def forget(self, memory_id: str) -> None:
        self.collection.delete(ids=[memory_id])

    def count(self) -> int:
        return self.collection.count()


class MemoryStore:
    """
    Public API used by the rest of Finch.
    Uses ChromaDB if installed and FINCH_MEMORY_BACKEND != json;
    otherwise falls back to JSON (default on free cloud hosts).
    """

    def __init__(self, persist_path: str = "./data/memory/", collection_name: str = "finch_memory"):
        force_json = os.environ.get("FINCH_MEMORY_BACKEND", "").lower() in ("json", "file", "light")
        backend = None
        if not force_json:
            try:
                backend = _ChromaMemoryBackend(persist_path, collection_name)
                self.backend_name = "chromadb"
            except Exception as e:
                print(f"[memory] ChromaDB unavailable ({e}); using JSON backend")
                backend = None
        if backend is None:
            backend = _JsonMemoryBackend(persist_path)
            self.backend_name = "json"
        self._b = backend

    def remember(self, content, metadata=None, memory_type="conversation"):
        return self._b.remember(content, metadata, memory_type)

    def recall(self, query, n_results=10, memory_type=None):
        return self._b.recall(query, n_results, memory_type)

    def recent(self, n=20, memory_type=None):
        return self._b.recent(n, memory_type)

    def forget(self, memory_id):
        return self._b.forget(memory_id)

    def count(self):
        return self._b.count()

    def summarize_for_context(self, query, n=5):
        memories = self.recall(query, n_results=n)
        if not memories:
            return ""
        lines = []
        for i, mem in enumerate(memories, 1):
            ts = mem["metadata"].get("timestamp", "unknown")
            lines.append(f"{i}. [{ts}] {mem['content']}")
        return "\n".join(lines)
