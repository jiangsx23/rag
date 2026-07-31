"""配置加载 - 用 pydantic-settings 强类型校验"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # DeepSeek
    DEEPSEEK_API_KEY: str = ""

    # Qdrant
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""

    # Langfuse
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # 模型
    BGE_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    BGE_RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    EMBEDDING_DEVICE: str = "cpu"

    # Pipeline
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 50
    RETRIEVAL_TOP_K: int = 20
    RERANK_TOP_K: int = 5

    # 反思
    REFLECTION_THRESHOLD: int = 7
    REFLECTION_MAX_RETRIES: int = 3

    # Agent
    AGENT_MAX_ITERATIONS: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    SESSION_TTL: int = 3600
    MAX_MESSAGES: int = 20

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
