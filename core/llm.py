"""共享 LLM 单例 — 复用 ChatDeepSeek 实例，减少连接开销

所有模块统一从这里获取 LLM 实例，避免重复创建 HTTP 连接。
"""
from functools import lru_cache
from langchain_deepseek import ChatDeepSeek

from app.config import settings


@lru_cache(maxsize=1)
def get_default_llm() -> ChatDeepSeek:
    """全局共享的 DeepSeek LLM 实例

    每个 LLM 调用设 30 秒超时，单个调用挂起不会拖垮整个请求。
    """
    return ChatDeepSeek(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        temperature=0,
        timeout=30,
    )
