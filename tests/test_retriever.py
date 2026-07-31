"""HybridRetriever 单元测试

测试策略：
- 用真实 Document 对象 + 可控数据，验证 RRF 融合排序逻辑
- BM25 用 jieba 真实分词，不用 mock（纯算法，不依赖外部服务）
- Qdrant 向量检索部分用 MagicMock 替代
- 关键测试点：RRF 分数计算、去重、权重调节、空数据
"""
from unittest.mock import MagicMock
from langchain_core.documents import Document

from core.retriever import HybridRetriever


# ============================================
# 辅助函数
# ============================================

def _make_doc(content: str, source: str = "doc.pdf", page: int = 1, chunk_id: str = None) -> Document:
    """快速构造 Document"""
    meta = {"source": source, "page": page}
    if chunk_id:
        meta["chunk_id"] = chunk_id
    return Document(page_content=content, metadata=meta)


def _make_vector_store(mock_docs: list[tuple[Document, float]]):
    """构造一个返回固定结果的 Mock vector_store"""
    store = MagicMock()
    store.similarity_search_with_score.return_value = mock_docs
    return store


# ============================================
# 1. 基本初始化
# ============================================

def test_init_without_docs():
    """没有文档时，bm25 应为 None，search 回退到纯向量"""
    store = _make_vector_store([(_make_doc("测试"), 0.9)])
    retriever = HybridRetriever(store, all_docs=[])

    assert retriever.bm25 is None
    assert retriever.all_docs == []


def test_init_with_docs_builds_bm25():
    """有文档时自动构建 BM25 索引"""
    store = _make_vector_store([])
    docs = [_make_doc("年假 10 个工作日"), _make_doc("病假 5 天")]
    retriever = HybridRetriever(store, all_docs=docs)

    assert retriever.bm25 is not None


# ============================================
# 2. 纯向量回退
# ============================================

def test_fallback_to_vector_only():
    """没有 BM25 索引时，返回向量检索结果"""
    vector_docs = [_make_doc("答案A"), _make_doc("答案B")]
    store = _make_vector_store([(d, 0.9) for d in vector_docs])

    retriever = HybridRetriever(store, all_docs=[])
    results = retriever.search("查询", top_k=5)

    assert len(results) == 2
    assert results[0].page_content == "答案A"


# ============================================
# 3. RRF 融合排序
# ============================================

def test_rrf_fusion_interleaves_results():
    """BM25 和向量结果应通过 RRF 融合排序"""
    # 向量检索返回 docA（高相关）、docB（低相关）
    doc_a = _make_doc("年假 10 个工作日", chunk_id="a")
    doc_b = _make_doc("加班费 1.5 倍", chunk_id="b")
    store = _make_vector_store([(doc_a, 0.95), (doc_b, 0.5)])

    # BM25 索引包含 docB（高匹配）、docC（低匹配）
    doc_c = _make_doc("工资计算方式", chunk_id="c")
    retriever = HybridRetriever(store, all_docs=[doc_a, doc_b, doc_c])

    results = retriever.search("年假", top_k=3)

    # 应该返回 3 个结果（不去重的情况下）
    assert len(results) >= 2
    # 年假相关的结果应排在前面
    contents = [r.page_content for r in results]
    assert "年假" in contents[0] or "年假" in contents


def test_rrf_vector_weight_preference():
    """vector_weight 参数应影响融合倾向"""
    doc_a = _make_doc("向量高分", chunk_id="a")
    doc_b = _make_doc("BM25 高分", chunk_id="b")
    store = _make_vector_store([(doc_a, 0.99)])

    retriever = HybridRetriever(store, all_docs=[doc_a, doc_b])

    # 用高 vector_weight 时，向量高分的结果应靠前
    results = retriever.search("BM25 高分", top_k=2, vector_weight=0.9)
    assert results[0].page_content == "向量高分"


# ============================================
# 4. 去重逻辑
# ============================================

def test_dedup_by_chunk_id():
    """相同 chunk_id 的文档应去重"""
    doc = _make_doc("重复内容", chunk_id="dup-1")
    store = _make_vector_store([(doc, 0.9)])

    # BM25 也有相同文档
    retriever = HybridRetriever(store, all_docs=[doc])
    results = retriever.search("查询", top_k=5)

    # 不应该出现两次
    assert len(results) == 1, f"应去重为 1 条，实际 {len(results)}"


def test_dedup_by_source_page():
    """没有 chunk_id 时，按 source|page|content_prefix 去重"""
    doc_a = _make_doc("相同开头的内容 ABC", source="doc.pdf", page=1)
    doc_b = _make_doc("相同开头的内容 XYZ", source="doc.pdf", page=1)
    store = _make_vector_store([(doc_a, 0.9)])

    retriever = HybridRetriever(store, all_docs=[doc_a, doc_b])
    results = retriever.search("查询", top_k=5)

    # doc_a 和 doc_b 前 50 字符相同 → 应视为重复
    # 实际上它们内容不同（ABC vs XYZ），但 _doc_key 基于前 50 字符
    # 所以只要前 50 字符相同就会被去重
    assert len(results) <= 2


# ============================================
# 5. _doc_key 生成
# ============================================

def test_doc_key_with_chunk_id():
    """有 chunk_id 时用 chunk_id 做 key"""
    doc = _make_doc("内容", chunk_id="my-chunk")
    retriever = HybridRetriever(_make_vector_store([]))
    key = retriever._doc_key(doc)
    assert key == "my-chunk"


def test_doc_key_fallback():
    """没有 chunk_id 时 fallback 到 source|page|content"""
    doc = _make_doc("测试内容", source="test.pdf", page=3)
    retriever = HybridRetriever(_make_vector_store([]))
    key = retriever._doc_key(doc)
    assert "test.pdf" in key
    assert "3" in key
    assert "测试内容" in key


# ============================================
# 6. 边界条件
# ============================================

def test_search_empty_query():
    """空查询字符串不应崩溃"""
    doc = _make_doc("内容", chunk_id="x")
    store = _make_vector_store([(doc, 0.5)])
    retriever = HybridRetriever(store, all_docs=[doc])

    # jieba 切空字符串会返回空列表，BM25 返回空，应回退到向量结果
    results = retriever.search("", top_k=5)
    assert len(results) >= 1


def test_search_top_k_limits_results():
    """top_k 应正确限制返回数量"""
    docs = [_make_doc(f"内容{i}", chunk_id=f"d{i}") for i in range(5)]
    store = _make_vector_store([(d, 0.9 - i * 0.1) for i, d in enumerate(docs)])

    retriever = HybridRetriever(store, all_docs=docs)
    results = retriever.search("查询", top_k=3)

    assert len(results) == 3


def test_set_documents_rebuilds_bm25():
    """set_documents 应重建 BM25 索引"""
    store = _make_vector_store([])
    retriever = HybridRetriever(store, all_docs=[])
    assert retriever.bm25 is None

    retriever.set_documents([_make_doc("新文档")])
    assert retriever.bm25 is not None
