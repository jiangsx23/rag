"""BGE-M3 Embedding 封装"""
import os
import torch
from loguru import logger
from langchain_core.embeddings import Embeddings

# 强制 HuggingFace 离线模式（huggingface.co 在国内网络不可达时避免超时）
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 注意：必须在 sentence_transformers 导入前初始化 CUDA 上下文，
# 否则 sentence-transformers 的 C++ 扩展在 Windows 上会 segfault。
if torch.cuda.is_available():
    _ = torch.tensor([0]).cuda()

from sentence_transformers import SentenceTransformer


class BGEEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        # Embedding 模型用 CPU 加载，把 GPU 显存留给重排器
        # 两模型同时放 GPU（~3.4GB）在 4GB 卡上会导致 CUDA 内存交换，慢 55 倍
        self.device = "cpu"
        logger.info(f"Loading BGE-M3 on {self.device}...")
        # 直接使用本地缓存路径（huggingface.co 不可用时避免超时重试）
        self.model = SentenceTransformer(
            model_name,
            device=self.device,
            cache_folder=os.path.expanduser("~/.cache/huggingface/hub"),
            local_files_only=True,
        )
        logger.info("BGE-M3 loaded")

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(
            texts,
            batch_size=8,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return embeddings.tolist()

    @property
    def dim(self) -> int:
        return self.model.get_sentence_embedding_dimension()


class BGELangChainEmbeddings(Embeddings):
    """适配 LangChain 的接口"""
    def __init__(self, embedder: BGEEmbedder):
        self.embedder = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embedder.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embedder.embed([text])[0]
