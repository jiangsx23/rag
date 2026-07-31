"""Langfuse 全链路观测 - 统一封装

设计目标：
1. Langfuse 不可用时优雅降级（依然能跑，只是没 trace）
2. 统一接口，业务代码不用关心 SDK 版本差异
3. 提供给 FastAPI 中间件用的高阶函数

要观测的核心 Span：
- /chat 主链：retrieve → rerank → generate → save_memory
- /agent/chat 主链：query_rewrite → react_loop → tool_calls

为什么不用 @observe 装饰业务函数？
- 我们要按 session_id/user_id 把整个请求串成一个 trace
- 装饰器不会自动串父子关系，得手工 langfuse_context.update_current_trace
"""
from __future__ import annotations
import time
import functools
from typing import Optional, Any, Callable

from app.config import settings
from app.logger import logger


# ============================================
# 单例 + 降级判断
# ============================================
_LANGFUSE_ENABLED = bool(
    settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY
)
_langfuse_client = None


def get_langfuse():
    """懒加载 Langfuse client（启动时还没接 key 时不创建连接）"""
    global _langfuse_client
    if not _LANGFUSE_ENABLED:
        return None
    if _langfuse_client is None:
        try:
            from langfuse import Langfuse
            _langfuse_client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
            )
            logger.info("Langfuse client initialized")
        except Exception as e:
            logger.warning(f"Langfuse init failed, disabled: {e}")
            return None
    return _langfuse_client


def is_enabled() -> bool:
    """是否真的把 trace 发到 Langfuse"""
    return _LANGFUSE_ENABLED and get_langfuse() is not None


# ============================================
# 降级版 observe 装饰器
# ============================================
def observe(name: Optional[str] = None, **kwargs):
    """Langfuse 不可用时退化成 identity 装饰器

    用法：
        @observe(name="rag-query", as_type="generation")
        def query(self, question): ...
    """
    try:
        from langfuse.decorators import observe as _observe
        return _observe(name=name, **kwargs)
    except Exception:
        # Langfuse 没装或 key 没配 → 直接透传
        def passthrough(func):
            @functools.wraps(func)
            def wrapper(*args, **kw):
                return func(*args, **kw)
            return wrapper
        return passthrough


# ============================================
# 手动 Span 控制（给 ReAct 多步循环用）
# ============================================
class _SpanGuard:
    """Span 上下文管理器（不依赖 @contextmanager / generator）

    为什么不用 @contextmanager？
    - @contextmanager 把 with 块变成 generator，with 块里抛异常时
      异常会沿 yield 传播。如果再有嵌套 generator（比如 langfuse 的
      update_current_observation），CPython 会抛
      "RuntimeError: generator didn't stop after throw()"，把原始异常吞掉。
    - 这里用 __enter__/__exit__ 协议实现，根本不涉及 generator。

    用法（与原 span() 完全兼容）：
        with span("foo", input_data={...}) as s:
            ... 业务代码 ...
            s.set_metadata({"k": "v"})  # 可选：补充 metadata
    """

    def __init__(self, name: str, input_data: Any = None, metadata: Optional[dict] = None):
        self.name = name
        self.input_data = input_data
        self.metadata = dict(metadata or {})
        self.start: Optional[float] = None
        self._langfuse_ctx = None
        self._ended = False

    def __enter__(self) -> "_SpanGuard":
        self.start = time.time()
        if not is_enabled():
            return self
        try:
            from langfuse.decorators import langfuse_context
            self._langfuse_ctx = langfuse_context
            self._langfuse_ctx.update_current_observation(
                name=self.name,
                input=self.input_data,
                metadata=self.metadata,
            )
        except Exception as e:
            logger.debug(f"langfuse span enter error (ignored): {e}")
            self._langfuse_ctx = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """返回 True 表示吞掉异常，False 让异常继续传播。"""
        if self._ended:
            return False
        self._ended = True
        duration_ms = int((time.time() - (self.start or time.time())) * 1000)
        self.metadata["duration_ms"] = duration_ms
        if exc_type is not None:
            # 把异常本身也记到 metadata 里，方便 trace 里看到
            self.metadata["error"] = type(exc_val).__name__
            self.metadata["error_message"] = str(exc_val)[:200]
        if self._langfuse_ctx is not None:
            try:
                self._langfuse_ctx.update_current_observation(metadata=self.metadata)
            except Exception as e:
                logger.debug(f"langfuse span exit error (ignored): {e}")
        # ⚠️ 关键：不返回 True，让异常原样往外抛。
        # 与 @contextmanager 实现的根本区别就在这里——
        # 不存在 generator，所以异常路径不会被嵌套 generator 改写。
        return False

    # ---- 用例：业务代码在 span 内部补充 metadata ----
    def set_metadata(self, extra: dict) -> None:
        self.metadata.update(extra)


class _NoopSpanGuard:
    """Langfuse 不可用时的占位，方法都不做事"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def set_metadata(self, extra: dict) -> None:
        pass


def span(name: str, input_data: Any = None, metadata: dict | None = None) -> _SpanGuard | _NoopSpanGuard:
    """手工开一个 span，离开 with 块自动 end。

    基于 _SpanGuard（__enter__/__exit__ 协议），不涉及任何 generator，
    因此不会触发嵌套 generator 异常改写问题。

    - Langfuse 不可用时返回 _NoopSpanGuard，业务代码无需 try/except
    """
    if not is_enabled():
        return _NoopSpanGuard()
    return _SpanGuard(name=name, input_data=input_data, metadata=metadata)


def update_trace(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list] = None,
    input: Any = None,
    output: Any = None,
) -> None:
    """更新当前 trace 的 user_id / session_id / metadata

    必须在 @observe 装饰的函数里调用才有效
    """
    if not is_enabled():
        return
    try:
        from langfuse.decorators import langfuse_context
        kwargs = {}
        if user_id is not None:
            kwargs["user_id"] = user_id
        if session_id is not None:
            kwargs["session_id"] = session_id
        if metadata is not None:
            kwargs["metadata"] = metadata
        if tags is not None:
            kwargs["tags"] = tags
        if input is not None:
            kwargs["input"] = input
        if output is not None:
            kwargs["output"] = output
        if kwargs:
            langfuse_context.update_current_trace(**kwargs)
    except Exception as e:
        logger.debug(f"langfuse update_trace error (ignored): {e}")


def update_current(
    input: Any = None,
    output: Any = None,
    metadata: Optional[dict] = None,
    level: Optional[str] = None,
    status_message: Optional[str] = None,
) -> None:
    """更新当前 span（最常用）"""
    if not is_enabled():
        return
    try:
        from langfuse.decorators import langfuse_context
        kwargs = {}
        if input is not None:
            kwargs["input"] = input
        if output is not None:
            kwargs["output"] = output
        if metadata is not None:
            kwargs["metadata"] = metadata
        if level is not None:
            kwargs["level"] = level
        if status_message is not None:
            kwargs["status_message"] = status_message
        if kwargs:
            langfuse_context.update_current_observation(**kwargs)
    except Exception as e:
        logger.debug(f"langfuse update_current error (ignored): {e}")


def score_current(name: str, value: float, comment: Optional[str] = None) -> None:
    """给当前 trace 打分（评测用）"""
    if not is_enabled():
        return
    try:
        from langfuse.decorators import langfuse_context
        langfuse_context.score_current_trace(
            name=name, value=value, comment=comment
        )
    except Exception as e:
        logger.debug(f"langfuse score error (ignored): {e}")


def flush() -> None:
    """进程结束前刷一下队列（防止丢 trace）"""
    if not is_enabled():
        return
    try:
        from langfuse.decorators import langfuse_context
        langfuse_context.flush()
    except Exception as e:
        logger.debug(f"langfuse flush error: {e}")