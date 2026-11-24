"""向量知识库 —— FastEmbed 编码 + SQLite 存储"""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from .chunker import chunk_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "backend" / "vector_db" / "kb.sqlite"
SOURCES_DIR = PROJECT_ROOT / "backend" / "knowledge" / "sources"


class KnowledgeBase:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._embedder = None
        self._embedding_dim = 768  # all-MiniLM-L6-v2

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                text TEXT,
                embedding BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
        """)
        self._conn.commit()

    def _get_embedder(self):
        if self._embedder is None:
            from fastembed import TextEmbedding
            # BGE-M3 对中文支持更好，且支持混合检索；如果加载慢就换 minilm
            try:
                self._embedder = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
                self._embedding_dim = 512
            except Exception:
                self._embedder = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
                self._embedding_dim = 384
        return self._embedder

    # ========== 构建 ==========
    def rebuild(self):
        """清空 → 重新切片所有 sources 目录文件 → 编码入库"""
        conn = self._connect()
        conn.execute("DELETE FROM chunks")
        conn.commit()

        for fp in sorted(SOURCES_DIR.iterdir()):
            if fp.suffix.lower() not in (".pdf", ".docx", ".doc", ".txt"):
                continue
            print(f"[KB] 切片 {fp.name} ...")
            chunks = chunk_file(str(fp))
            print(f"[KB]   → {len(chunks)} 块")
            self.add_chunks(fp.name, chunks)

    def add_chunks(self, source: str, chunks: list[str]):
        conn = self._connect()
        embedder = self._get_embedder()
        embeddings = list(embedder.embed(chunks))
        for text, emb in zip(chunks, embeddings):
            blob = emb.astype("float32").tobytes()
            conn.execute(
                "INSERT INTO chunks (source, text, embedding) VALUES (?, ?, ?)",
                (source, text, blob),
            )
        conn.commit()

    # ========== 检索 ==========
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """简单 cosine 相似度检索"""
        conn = self._connect()
        embedder = self._get_embedder()
        q_emb = next(embedder.embed([query])).astype("float32")

        rows = conn.execute("SELECT id, source, text, embedding FROM chunks").fetchall()
        scored: list[tuple[float, dict]] = []
        for rid, source, text, blob in rows:
            import numpy as np
            emb = np.frombuffer(blob, dtype="float32")
            denom = np.linalg.norm(emb) * np.linalg.norm(q_emb)
            sim = float(np.dot(emb, q_emb) / denom) if denom > 0 else 0.0
            scored.append((sim, {"id": rid, "source": source, "text": text, "score": sim}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


# 模块级单例，懒加载
_kb_instance: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance


def ensure_kb_built():
    """启动时调用一次：如果 KB 还是空的就 rebuild"""
    kb = get_kb()
    if kb.count() == 0:
        print("[KB] 知识库为空，开始构建...")
        kb.rebuild()
        print(f"[KB] 构建完成，共 {kb.count()} 块")
    else:
        print(f"[KB] 已存在 {kb.count()} 块")
