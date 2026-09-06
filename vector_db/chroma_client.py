"""Chroma 向量库封装：把仓库文本切块入库，供 Agent 做 RAG 检索。"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from app.config import CHROMA_DIR, IGNORED_DIR_PARTS

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".cs", ".php", ".rb", ".swift", ".sh", ".ps1",
    ".sql", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".md", ".rst", ".txt", ".xml",
}

try:  # Chroma 不是硬依赖：未安装或加载失败时自动禁用检索
    import chromadb
    from chromadb.config import Settings

    CHROMA_AVAILABLE = True
except Exception:  # pragma: no cover - 环境差异保护
    chromadb = None
    Settings = None
    CHROMA_AVAILABLE = False


def _chunk_text(text: str, size: int = 1200, overlap: int = 150):
    """按固定窗口切块，带少量重叠以保证语义连续。"""
    text = text.strip()
    if not text:
        return
    step = max(1, size - overlap)
    for start in range(0, len(text), step):
        yield text[start : start + size]


class LocalHashingEmbedding:
    """离线哈希词袋向量器（384 维），避免 Chroma 默认模型下载与外部缓存。

    语义效果弱于 ONNX MiniLM，但足够支撑“给 Agent 提供相关代码片段”的演示，
    且不依赖网络；若后续需要更强检索，可替换为 OpenAI/本地模型嵌入。
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    @staticmethod
    def name() -> str:
        return "local_hashing"

    def __call__(self, input):
        texts = [input] if isinstance(input, str) else list(input)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", (text or "").lower())
            for token in tokens:
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                idx = int(digest[:8], 16) % self.dim
                vector[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([round(v / norm, 6) for v in vector])
        return vectors

    def embed_query(self, input):
        return self(input)

    def get_config(self) -> dict:
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config: dict) -> "LocalHashingEmbedding":
        return LocalHashingEmbedding(dim=int(config.get("dim", 384)))

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self):
        return ["cosine", "l2", "ip"]

    def validate_config_update(self, old_config: dict, new_config: dict) -> None:
        pass

    @staticmethod
    def validate_config(config: dict) -> None:
        pass


class RepoVectorDB:
    """一个仓库对应一个 Chroma collection。"""

    def __init__(self) -> None:
        self._client = None
        if CHROMA_AVAILABLE:
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False),
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def _collection_name(self, repo_name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", repo_name).strip("_") or "repo"
        return name[:63]

    def _collection(self, repo_name: str):
        if not self.available:
            return None
        return self._client.get_or_create_collection(
            name=self._collection_name(repo_name),
            embedding_function=LocalHashingEmbedding(),
        )

    def index_directory(self, root: Path, repo_name: str) -> int:
        """把仓库内文本文件分块写入 collection，返回入库块数。"""
        if not self.available:
            return 0

        name = self._collection_name(repo_name)
        try:
            self._client.delete_collection(name)
        except Exception:
            pass
        collection = self._client.get_or_create_collection(
            name=name,
            embedding_function=LocalHashingEmbedding(),
        )
        root = root.resolve()
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        seq = 0

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith(".devagent"):
                continue
            if any(part.lower() in IGNORED_DIR_PARTS for part in path.relative_to(root).parts[:-1]):
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = path.relative_to(root).as_posix()
            for chunk in _chunk_text(content):
                ids.append(f"{repo_name}:{seq}")
                documents.append(chunk)
                metadatas.append({"path": rel})
                seq += 1

        if ids:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def query(self, repo_name: str, question: str, k: int = 5) -> list[dict]:
        """按问题检索仓库片段，返回 [{path, text, distance}]。"""
        collection = self._collection(repo_name)
        if collection is None or collection.count() == 0:
            return []
        try:
            result = collection.query(query_texts=[question], n_results=min(k, collection.count()))
        except Exception:
            return []

        items: list[dict] = []
        docs = result.get("documents") or [[]]
        metas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]
        for i, doc in enumerate(docs[0] if docs else []):
            meta = (metas[0][i] if metas and metas[0] else {}) or {}
            distance = distances[0][i] if distances and distances[0] else None
            items.append({"path": meta.get("path", ""), "text": doc, "distance": distance})
        return items

    def clear(self, repo_name: str) -> None:
        if not self.available:
            return
        try:
            self._client.delete_collection(self._collection_name(repo_name))
        except Exception:
            pass
