"""RAG Pipeline 整体编排 - 混合检索 + Reranker + Langfuse 观测 + 流式生成"""

from typing import Generator as TypingGenerator

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from app.config import settings
from app.logger import logger
from core.embedder import BGEEmbedder, BGELangChainEmbeddings
from core.generator import Generator
from core.observability import observe, span, update_current, update_trace
from core.reranker import BGEReranker
from core.retriever import HybridRetriever


class RAGPipeline:
    """RAG 流水线：混合检索 → Reranker 精排 → 生成答案"""

    def __init__(self):
        # 1. Embedding
        self.embedder = BGEEmbedder()
        embeddings = BGELangChainEmbeddings(self.embedder)

        # 2. 连接 Qdrant
        client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
        )
        self.vector_store = QdrantVectorStore(
            client=client,
            collection_name="knowledge_base",
            embedding=embeddings,
        )

        # 3. 混合检索器（BM25 + 向量）
        self.hybrid_retriever = HybridRetriever(
            vector_store=self.vector_store,
            all_docs=[],  # 暂时为空，用 set_documents 加载
        )

        # 4. Reranker 精排
        self.reranker = BGEReranker()

        # 5. LLM 生成器
        self.generator = Generator()

        # 6. 加载所有 chunks 到 BM25
        self._load_documents_for_bm25()

        logger.info("RAGPipeline (Hybrid + Reranker) initialized")

    def _load_documents_for_bm25(self):
        """从 Qdrant 加载所有文档，构建 BM25 索引"""
        try:
            # 用 scroll API 拿所有点
            client = self.vector_store.client
            collection_name = self.vector_store.collection_name

            all_docs = []
            offset = None
            while True:
                result = client.scroll(
                    collection_name=collection_name,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, next_offset = result

                for point in points:
                    payload = point.payload
                    all_docs.append(
                        Document(
                            page_content=payload.get("page_content", ""),
                            metadata=payload.get("metadata", {}),
                        )
                    )

                if next_offset is None:
                    break
                offset = next_offset

            self.hybrid_retriever.set_documents(all_docs)
            logger.info(f"Loaded {len(all_docs)} docs for BM25 index")

        except Exception as e:
            logger.warning(f"Failed to load docs for BM25: {e}")
            logger.warning("Will fall back to vector-only search")

    @observe(name="rag-query")
    def query(self, question: str, top_k: int = 5) -> dict:
        """主入口：检索 → 精排 → 生成"""
        logger.info(f"Query: {question}")
        update_current(input={"question": question, "top_k": top_k})

        # 1. 混合检索（粗排 20 个）
        with span("hybrid-retrieve", input_data={"query": question}):
            candidates = self.hybrid_retriever.search(question, top_k=20)
        logger.info(f"Hybrid retrieved {len(candidates)} candidates")
        update_current(metadata={"hybrid_count": len(candidates)})

        # 2. Reranker 精排（top_k 个）
        with span("rerank", input_data={"query": question, "n_candidates": len(candidates)}):
            docs = self.reranker.rerank(question, candidates, top_k=top_k)
        logger.info(f"Reranked to top {len(docs)} docs")

        # 3. 构造 context
        context = "\n\n".join(
            [
                f"[{i+1}] {doc.page_content}\n"
                f"来源: {doc.metadata.get('source', 'unknown')}, "
                f"页码: {doc.metadata.get('page', '?')}"
                for i, doc in enumerate(docs)
            ]
        )

        # 4. 生成答案
        with span("generate", input_data={"context_len": len(context)}):
            answer = self.generator.generate(question, context)
        logger.info(f"Generated answer: {answer[:100]}...")

        # 4.5 反思评估：LLM-as-Judge 检查答案质量
        source_texts = [doc.page_content[:500] for doc in docs]
        try:
            from core.reflection import ReflectionModule

            reflection = ReflectionModule()
            answer, evaluation = reflection.evaluate_rag(
                question=question,
                answer=answer,
                sources=source_texts,
                regenerate_func=lambda: self.generator.generate(question, context),
            )
            reflection_data = {
                "score": evaluation.score,
                "is_hallucination": evaluation.is_hallucination,
                "is_acceptable": evaluation.is_acceptable,
                "issues": evaluation.issues,
                "suggestion": evaluation.suggestion,
            }
            update_current(metadata={"reflection_score": evaluation.score})
        except Exception as e:
            logger.warning(f"Reflection skipped (non-blocking): {e}")
            reflection_data = None

        sources = [
            {
                "content": doc.page_content[:200],
                "metadata": {
                    "source": doc.metadata.get("source"),
                    "page": doc.metadata.get("page"),
                },
            }
            for doc in docs
        ]
        update_current(
            output={"answer": answer, "n_sources": len(sources)},
            metadata={"sources": [s["metadata"].get("source") for s in sources]},
        )

        result = {
            "answer": answer,
            "sources": sources,
        }
        if reflection_data:
            result["reflection"] = reflection_data
        return result

    @observe(name="rag-query-stream")
    def query_stream(self, question: str, top_k: int = 5) -> TypingGenerator[dict, None, None]:
        """流式查询：检索 + 逐 token 生成

        按序 yield 的事件（SSE 友好）：
          {"type": "sources", "sources": [...]}
          {"type": "token",   "content": "..."}  × N
          {"type": "done",    "answer": "...", "reflection": {...} | None}
        """
        import time

        from core.reflection import ReflectionModule

        # === 计时器 ===
        t_all = time.perf_counter()

        # 1. 混合检索（同步）
        with span("hybrid-retrieve", input_data={"query": question}):
            candidates = self.hybrid_retriever.search(question, top_k=20)
        t_retrieve = time.perf_counter()

        with span("rerank", input_data={"query": question, "n_candidates": len(candidates)}):
            docs = self.reranker.rerank(question, candidates, top_k=top_k)
        t_rerank = time.perf_counter()

        # 2. 构造 context + sources 元信息
        context = "\n\n".join(
            [
                f"[{i+1}] {doc.page_content}\n"
                f"来源: {doc.metadata.get('source', 'unknown')}, "
                f"页码: {doc.metadata.get('page', '?')}"
                for i, doc in enumerate(docs)
            ]
        )
        sources = [
            {
                "content": doc.page_content[:200],
                "metadata": {
                    "source": doc.metadata.get("source"),
                    "page": doc.metadata.get("page"),
                },
            }
            for doc in docs
        ]
        yield {"type": "sources", "sources": sources}
        t_sources = time.perf_counter()

        # 3. 流式生成
        full_answer_parts = []
        with span("generate-stream", input_data={"context_len": len(context)}):
            try:
                for token in self.generator.generate_stream(question, context):
                    full_answer_parts.append(token)
                    yield {"type": "token", "content": token}
            except Exception as e:
                logger.warning(f"Streaming generation error: {e}")
                yield {"type": "token", "content": f"\n\n[生成中断: {e}]"}
        t_gen_done = time.perf_counter()

        answer = "".join(full_answer_parts)

        # 4. 反思评估（非阻塞，不影响流式）
        reflection_data = None
        if answer:
            try:
                source_texts = [doc.page_content[:500] for doc in docs]
                reflection = ReflectionModule()
                answer, evaluation = reflection.evaluate_rag(
                    question=question,
                    answer=answer,
                    sources=source_texts,
                )
                reflection_data = {
                    "score": evaluation.score,
                    "is_hallucination": evaluation.is_hallucination,
                    "is_acceptable": evaluation.is_acceptable,
                    "issues": evaluation.issues,
                }
            except Exception as e:
                logger.warning(f"Reflection after stream skipped: {e}")
        t_reflection = time.perf_counter()

        _timing = {
            "retrieve_s": round(t_retrieve - t_all, 1),
            "rerank_s": round(t_rerank - t_retrieve, 1),
            "generate_s": round(t_gen_done - t_sources, 1),
            "reflection_s": round(t_reflection - t_gen_done, 1),
            "total_s": round(t_reflection - t_all, 1),
        }
        update_current(output={"answer": answer, "n_sources": len(sources)})
        yield {
            "type": "done",
            "answer": answer,
            "reflection": reflection_data,
            "_timing": _timing,
        }

    @observe(name="rag-query-with-memory")
    def query_with_memory(
        self,
        question: str,
        session_id: str = None,
        user_id: str = "default",
        top_k: int = 5,
    ) -> dict:
        """带记忆的查询 - 处理多轮对话"""
        from langchain_core.messages import AIMessage, HumanMessage

        from core.memory import MemoryManager

        # 把整个请求绑到 user/session 上（Langfuse 就能按用户筛了）
        update_trace(
            user_id=user_id,
            session_id=session_id,
            input={"question": question, "top_k": top_k},
            tags=["rag", "multi-turn"],
        )

        # 1. 初始化记忆管理器
        memory = MemoryManager()

        # 2. 获取 session
        with span("load-session", metadata={"user_id": user_id}):
            session = memory.get_or_create_session(user_id, session_id)

        # 把真实 session_id 回写到 trace
        update_trace(session_id=session.session_id)

        # 3. Query 改写（解决"那它呢？"的指代）
        with span("query-rewrite", input_data={"raw": question}):
            rewritten_query = memory.rewrite_query_with_context(question, session)
        update_current(metadata={"rewritten_query": rewritten_query})

        # 4. 用改写后的 query 检索
        result = self.query(rewritten_query, top_k=top_k)

        # 5. 保存到 session
        with span("save-session", metadata={"message_count": len(session.messages)}):
            session.add_message(HumanMessage(content=question))
            session.add_message(AIMessage(content=result["answer"]))
            memory.save(session)

        update_trace(output={"answer": result["answer"], "n_sources": len(result["sources"])})

        # 6. 返回结果（带 session_id 和改写后的 query）
        return {
            "answer": result["answer"],
            "rewritten_query": rewritten_query,
            "session_id": session.session_id,
            "sources": result["sources"],
        }
