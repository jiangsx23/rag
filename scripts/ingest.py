"""完整入库脚本"""
import sys
from pathlib import Path

# 让脚本能找到 app/ 和 core/
sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

from app.config import settings
from app.logger import logger
from core.parser import DocumentParser
from core.chunker import SmartChunker
from core.embedder import BGEEmbedder, BGELangChainEmbeddings


def main(data_dir: str = "data/raw"):
    parser = DocumentParser()
    chunker = SmartChunker()
    embedder = BGEEmbedder()
    embeddings = BGELangChainEmbeddings(embedder)

    # 连接 Qdrant
    client = QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )
    collection_name = "knowledge_base"

    # 删旧建新
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)
        logger.info(f"Deleted old collection: {collection_name}")

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE),
    )
    logger.info(f"Created new collection: {collection_name}")

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
    )

    # 处理所有文档
    all_chunks = []
    for file_path in Path(data_dir).glob("**/*"):
        if file_path.suffix.lower() not in {".pdf", ".docx", ".doc", ".pptx", ".txt", ".md", ".html"}:
            continue
        if file_path.name.startswith("."):  # 跳过 .gitkeep 等
            continue

        elements = parser.parse(str(file_path))
        chunks = chunker.chunk(elements)
        all_chunks.extend(chunks)
        logger.info(f"✅ {file_path.name}: {len(chunks)} chunks")

    if not all_chunks:
        logger.error("❌ 没有找到任何文档，请检查 data/raw/ 目录")
        return

    # 批量写入
    vector_store.add_documents(all_chunks)
    logger.info(f"🎉 Total: {len(all_chunks)} chunks ingested into Qdrant")

    # 验证
    info = client.get_collection(collection_name)
    logger.info(f"📊 Collection vectors: {info.points_count}, dim: {embedder.dim}, distance: COSINE")


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
    main(data_dir)
