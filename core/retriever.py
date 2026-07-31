"""混合检索 - BM25 + 向量 + RRF 融合"""
import jieba
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from app.logger import logger


class HybridRetriever:
    """BM25 + 向量检索，用 RRF 融合"""

    def __init__(
        self,
        vector_store: QdrantVectorStore,
        all_docs: list[Document] = None,
    ):
        self.vector_store = vector_store
        self.all_docs = all_docs or []
        if self.all_docs:
            self._build_bm25_index()
        else:
            self.bm25 = None

    def _build_bm25_index(self):
        """构建 BM25 索引（用 jieba 分词）"""
        tokenized_corpus = [
            list(jieba.cut(doc.page_content))
            for doc in self.all_docs
        ]
        self.bm25 = BM25Okapi(tokenized_corpus)
        logger.info(f"BM25 index built: {len(self.all_docs)} docs")

    def set_documents(self, docs: list[Document]):
        """从外部设置文档列表（用于构建 BM25 索引）"""
        self.all_docs = docs
        self._build_bm25_index()

    def search(
        self,
        query: str,
        top_k: int = 20,
        vector_weight: float = 0.7,
    ) -> list[Document]:
        """混合检索：BM25 + 向量 + RRF 融合"""
        
        # 1. 向量检索（从 Qdrant）
        vector_results = self.vector_store.similarity_search_with_score(
            query, k=top_k
        )
        vector_docs = [doc for doc, _ in vector_results]
        
        # 2. BM25 检索（从内存索引）
        if self.bm25 is None or not self.all_docs:
            return vector_docs[:top_k]
        
        tokenized_query = list(jieba.cut(query))
        bm25_scores = self.bm25.get_scores(tokenized_query)
        
        # 只保留 BM25 分数 > 0 的文档
        top_bm25_idx = [
            i for i in bm25_scores.argsort()[-top_k:][::-1]
            if bm25_scores[i] > 0
        ]
        bm25_docs = [self.all_docs[i] for i in top_bm25_idx]
        
        # 3. RRF 融合（Reciprocal Rank Fusion）
        K = 60
        rrf_scores = {}
        
        for rank, doc in enumerate(vector_docs):
            # 🆕 用 page_content + source 做唯一 key
            doc_key = self._doc_key(doc)
            rrf_scores[doc_key] = rrf_scores.get(doc_key, 0) + vector_weight / (rank + K)
        
        for rank, doc in enumerate(bm25_docs):
            doc_key = self._doc_key(doc)
            rrf_scores[doc_key] = rrf_scores.get(doc_key, 0) + (1 - vector_weight) / (rank + K)
        
        # 4. 🆕 排序去重（用 dict 保证唯一性）
        # doc_key -> doc 的映射
        doc_map = {}
        for doc in vector_docs + bm25_docs:
            doc_map[self._doc_key(doc)] = doc
        
        sorted_keys = sorted(
            rrf_scores.keys(),
            key=lambda k: rrf_scores[k],
            reverse=True,
        )
        
        return [doc_map[k] for k in sorted_keys[:top_k]]
    
    def _doc_key(self, doc: Document) -> str:
        """生成文档的唯一 key（用于去重）

        优先用 metadata 里的 chunk_id（由 SmartChunker 写入），
        没有则 fallback 到 source|page|content_prefix。
        """
        chunk_id = doc.metadata.get("chunk_id")
        if chunk_id:
            return chunk_id
        source = doc.metadata.get("source", "")
        page = doc.metadata.get("page", "")
        content_start = doc.page_content[:50]
        return f"{source}|{page}|{content_start}"
