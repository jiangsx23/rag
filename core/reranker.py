"""BGE Reranker 精排"""
import os
import torch
import numpy as np
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from langchain_core.documents import Document
from app.logger import logger


class BGEReranker:
    """用 BGE Reranker 对粗排结果精排

    使用 bge-reranker-v2-m3（encoder-only），自动利用 GPU（FP16）加速。
    GPU 推理约 0.2s（vs CPU 9.2s，42x 加速），VRAM 峰值 ~1.2GB/4GB。
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        # 从 HuggingFace 缓存加载（不联网）
        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        model_dir = os.path.join(
            cache_dir,
            f"models--{model_name.replace('/', '--')}/snapshots",
        )
        snapshots = sorted(os.listdir(model_dir), reverse=True) if os.path.isdir(model_dir) else []
        if not snapshots:
            logger.warning(f"模型 {model_name} 不在缓存中，尝试在线加载")
            model_path = model_name
        else:
            model_path = os.path.join(model_dir, snapshots[0])

        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path)

        # 自动检测 GPU 并切换到 FP16
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda":
            self.model = self.model.cuda().half()
        self.model.eval()

        device_label = f"GPU ({torch.cuda.get_device_name(0)})" if self.device == "cuda" else "CPU"
        logger.info(f"BGE Reranker loaded: {model_name} on {device_label}")

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: int = 5,
    ) -> list[Document]:
        """对 documents 精排，返回 top_k 个"""
        if not documents:
            return []

        # 构造 query-doc 对
        pairs = [f"{query} {doc.page_content}" for doc in documents]

        # Tokenize
        inputs = self.tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        if self.device == "cuda":
            inputs = {k: v.cuda() for k, v in inputs.items()}

        # 推理
        with torch.no_grad():
            outputs = self.model(**inputs)

        # 提取分数（分类模型的 logits）
        scores = outputs.logits.squeeze(-1).cpu().numpy()
        if scores.ndim == 0:
            scores = [float(scores)]
        else:
            scores = scores.tolist()

        # 按分数排序
        ranked = sorted(
            zip(documents, scores),
            key=lambda x: x[1],
            reverse=True,
        )

        return [doc for doc, _ in ranked[:top_k]]
