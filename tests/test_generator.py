"""Generator 单元测试

测试策略：
- 通过构造函数注入 mock LLM，不依赖真实 API
- LangChain 的 pipe (prompt | llm) 在内部会直接 call mock，
  所以需要设置 mock.return_value 而非 mock.invoke.return_value
"""
from unittest.mock import MagicMock

from core.generator import Generator


def _mock_llm(response: str = "测试答案"):
    """构造一个 LangChain pipe 兼容的 LLM mock

    原理：chain.invoke() → prompt | llm → llm(formatted_messages)
    MagicMock 被 LangChain RunnableLambda 包装后走 __call__ 路径，
    所以必须设 return_value.content 而非 invoke.return_value.content。
    """
    llm = MagicMock()
    llm.return_value.content = response  # __call__ 路径
    llm.invoke.return_value.content = response  # 兼容直接 .invoke 路径
    return llm


# ============================================
# 1. 正常生成
# ============================================

def test_generate_returns_llm_response():
    """generate 应返回 LLM 返回的 content"""
    gen = Generator(llm=_mock_llm("10 个工作日。[来源: 员工手册.pdf]"))
    result = gen.generate("年假几天？", "年假 10 个工作日。")
    assert result == "10 个工作日。[来源: 员工手册.pdf]"


def test_generate_passes_question_and_context():
    """generate 应把 question 和 context 传给 LLM"""
    mock_llm = _mock_llm("ok")

    gen = Generator(llm=mock_llm)
    gen.generate("我的问题", "相关上下文")

    # chain.invoke → prompt.invoke → llm(formatted_messages)
    # 验证 mock 被调用过（通过 __call__ 或 invoke）
    assert mock_llm.call_count > 0 or mock_llm.invoke.call_count > 0, "LLM 未被调用"

    # 取出调用参数检查是否包含 question 和 context
    if mock_llm.call_count > 0:
        call_args = str(mock_llm.call_args)
        assert "我的问题" in call_args, f"LLM 未收到问题: {call_args}"
        assert "相关上下文" in call_args, f"LLM 未收到上下文: {call_args}"
    elif mock_llm.invoke.call_count > 0:
        call_args = str(mock_llm.invoke.call_args)
        assert "我的问题" in call_args, f"LLM 未收到问题: {call_args}"
        assert "相关上下文" in call_args, f"LLM 未收到上下文: {call_args}"


# ============================================
# 2. 边界条件
# ============================================

def test_generate_empty_context():
    """空 context 不应崩溃"""
    gen = Generator(llm=_mock_llm("我不知道"))
    result = gen.generate("不存在的问题", "")
    assert result == "我不知道"


def test_generate_empty_question():
    """空 question 不应崩溃"""
    gen = Generator(llm=_mock_llm("请提供问题"))
    result = gen.generate("", "上下文")
    assert result == "请提供问题"


def test_generate_long_context():
    """超长 context 不应崩溃"""
    gen = Generator(llm=_mock_llm("总结完毕"))
    long_context = "测试 " * 10000
    result = gen.generate("总结", long_context)
    assert result == "总结完毕"


# ============================================
# 3. LLM 异常传播
# ============================================

def test_generate_llm_exception_propagates():
    """LLM 抛异常时，异常应原样透传（由调用方决定是否 catch）"""
    bad_llm = MagicMock()
    bad_llm.side_effect = RuntimeError("API timeout")

    import pytest
    with pytest.raises(RuntimeError, match="API timeout"):
        gen = Generator(llm=bad_llm)
        gen.generate("问题", "上下文")


# ============================================
# 4. Prompt 模板结构
# ============================================

def test_system_prompt_contains_context_placeholder():
    """SYSTEM_PROMPT 应包含 {context} 占位符"""
    gen = Generator(llm=_mock_llm("ok"))
    assert "{context}" in gen.SYSTEM_PROMPT


def test_system_prompt_encourages_citation():
    """SYSTEM_PROMPT 应鼓励标注来源"""
    gen = Generator(llm=_mock_llm("ok"))
    assert "来源" in gen.SYSTEM_PROMPT
