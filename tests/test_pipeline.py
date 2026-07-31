"""RAGPipeline 集成测试

注意：import core.pipeline 会触发 sentence_transformers/torch 等子模块加载，
首次运行耗时 >35s。标记为 integration，默认不执行。

运行方式：pytest tests/ -m integration
"""
import pytest
from unittest.mock import MagicMock, patch
from contextlib import ExitStack

from langchain_core.documents import Document


def _make_doc(content: str, source: str = "doc.pdf", page: int = 1) -> Document:
    return Document(
        page_content=content,
        metadata={"source": source, "page": page},
    )


@pytest.mark.integration
class TestRAGPipeline:
    """RAGPipeline 编排逻辑测试 — 所有子组件 mock，仅测 orchestration"""

    def _build_pipeline(self):
        stack = ExitStack()
        stack.enter_context(patch("core.pipeline.BGEEmbedder"))
        stack.enter_context(patch("core.pipeline.BGELangChainEmbeddings"))
        stack.enter_context(patch("core.pipeline.QdrantClient"))
        stack.enter_context(patch("core.pipeline.QdrantVectorStore"))
        stack.enter_context(patch("core.pipeline.HybridRetriever"))
        stack.enter_context(patch("core.pipeline.BGEReranker"))
        stack.enter_context(patch("core.pipeline.Generator"))
        # query()/query_with_memory() 内部惰性 import
        stack.enter_context(patch("core.reflection.ReflectionModule"))

        from core.pipeline import RAGPipeline

        p = RAGPipeline()
        p.hybrid_retriever = MagicMock()
        p.reranker = MagicMock()
        p.generator = MagicMock()

        # 默认让 ReflectionModule 返回可接受
        fake_eval = MagicMock()
        fake_eval.score = 8
        fake_eval.is_hallucination = False
        fake_eval.is_acceptable = True
        fake_eval.issues = []
        fake_eval.suggestion = ""
        fake_ref = MagicMock()
        fake_ref.evaluate_rag.return_value = ("答案", fake_eval)
        # 直接替换 _build_pipeline 中设置的 mock_ref.return_value
        # 注意：需要从 stack 获取已创建的 mock
        from unittest.mock import _get_identifier
        # 用另一种方式：直接 patch 返回值
        import core.reflection as _ref
        _ref.ReflectionModule.return_value = fake_ref

        return p

    def test_query_returns_answer_and_sources(self):
        p = self._build_pipeline()
        p.hybrid_retriever.search.return_value = [
            _make_doc("年假 10 个工作日", source="员工手册.pdf", page=5),
        ]
        p.reranker.rerank.return_value = [_make_doc("年假 10 个工作日")]
        p.generator.generate.return_value = "10 个工作日。[来源: 员工手册.pdf]"

        result = p.query("年假几天？", top_k=5)
        assert result["answer"] == "10 个工作日。[来源: 员工手册.pdf]"
        assert len(result["sources"]) >= 1

    def test_query_empty_results(self):
        p = self._build_pipeline()
        p.hybrid_retriever.search.return_value = []
        p.reranker.rerank.return_value = []
        p.generator.generate.return_value = "未找到"

        result = p.query("问题", top_k=5)
        assert result["answer"] == "未找到"

    def test_query_top_k_passthrough(self):
        p = self._build_pipeline()
        p.hybrid_retriever.search.return_value = [_make_doc("内容")]
        p.reranker.rerank.return_value = [_make_doc("内容")]
        p.generator.generate.return_value = "答案"

        p.query("测试", top_k=3)
        assert p.reranker.rerank.call_args[1]["top_k"] == 3
