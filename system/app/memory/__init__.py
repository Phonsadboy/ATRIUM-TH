"""Per-department memory: embeddings (RAG), vector retrieval, knowledge graph, compaction."""
from .company_memory import ensure_company_memory_files, load_company_memory, load_owner_profile
from .embeddings import Embedder, get_embedder, resolve_embedder
from .executive_retrieval import executive_retrieval_context, retrieval_context_for_department

__all__ = [
    "Embedder",
    "ensure_company_memory_files",
    "executive_retrieval_context",
    "get_embedder",
    "load_company_memory",
    "load_owner_profile",
    "resolve_embedder",
    "retrieval_context_for_department",
]
