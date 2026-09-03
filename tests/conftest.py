"""conftest.py — 让单元测试不依赖真实 DeepSeek API

背景：core 里多个模块的 __init__ 默认会调 get_default_llm() 构造真实
ChatDeepSeek。CI 上没有 DEEPSEEK_API_KEY（secret 未配置或为空）时，
构造阶段直接抛 pydantic ValidationError，整批单元测试挂掉。

这里用 autouse fixture 在【每个测试】前，把各模块里 get_default_llm 的
引用替换成一个离线 FakeLLM，保证单元测试：
- 不需要 API key（CI 可离线跑）
- 不发起真实 API 调用（快、稳、不烧钱）

注意：这些模块是 `from core.llm import get_default_llm` 引用的原函数，
必须在每个模块里分别替换（光 patch core.llm 不够）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# 通过 `from core.llm import get_default_llm` 引用了原函数的模块
_LLM_CONSUMERS = [
    "core.llm",
    "core.generator",
    "core.react_agent",
    "core.memory",
    "core.reflection",
]


def _fake_invoke(prompt):
    """FakeLLM 的 invoke：只处理 memory 的 query 改写，其余返回空串

    改写逻辑：提取【用户最后问题】，去掉"那/这"指代词前缀。
    这样 "那病假呢？" → "病假呢？"，正好满足 test_memory 的断言。
    """
    if isinstance(prompt, str) and "【用户最后问题】" in prompt:
        question = prompt.split("【用户最后问题】", 1)[1].split("【改写后的问题】", 1)[0].strip()
        for prefix in ("那", "这"):
            if question.startswith(prefix):
                question = question[len(prefix) :]
                break
        return SimpleNamespace(content=question)
    # 摘要等其他 prompt：返回空串，_summarize_history 会兜底
    return SimpleNamespace(content="")


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """每个测试前自动替换 get_default_llm，杜绝真实 ChatDeepSeek 被构造"""
    fake = MagicMock()
    fake.invoke.side_effect = _fake_invoke
    for mod in _LLM_CONSUMERS:
        # 用点分路径形式（"模块.属性"）patch，字符串 target 会按 import path 解析
        monkeypatch.setattr(f"{mod}.get_default_llm", lambda: fake, raising=False)
    return fake
