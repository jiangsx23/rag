"""Generator 流式生成单元测试

generate_stream() 直接调 self.llm.stream()（不经过 LangChain chain），
mock 的 .stream() 返回值会被直接使用，无 RunnableLambda 包装问题。
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessageChunk

from core.generator import Generator


def _mock_stream(tokens: list[str]):
    """构造 LLM mock，.stream() 返回指定 token 列表"""
    llm = MagicMock()
    llm.stream.return_value = [AIMessageChunk(content=t) for t in tokens]
    return llm


# ============================================
# 1. 正常流式生成
# ============================================


def test_generate_stream_yields_tokens():
    """generate_stream 应逐 token yield"""
    gen = Generator(llm=_mock_stream(["10", " 个", "工作日"]))
    tokens = list(gen.generate_stream("年假几天？", "年假 10 个工作日。"))
    assert tokens == ["10", " 个", "工作日"]


def test_generate_stream_concatenates_to_full():
    """流式 token 拼接后应等于完整答案"""
    gen = Generator(llm=_mock_stream(["答案", "是", "42"]))
    full = "".join(gen.generate_stream("q", "c"))
    assert full == "答案是42"


# ============================================
# 2. 边界条件
# ============================================


def test_generate_stream_empty():
    """LLM 流返回空列表 → yield 空"""
    gen = Generator(llm=_mock_stream([]))
    tokens = list(gen.generate_stream("q", "c"))
    assert tokens == []


def test_generate_stream_skips_empty_chunks():
    """空的 chunk.content 应被跳过"""
    llm = MagicMock()
    llm.stream.return_value = [
        AIMessageChunk(content=""),
        AIMessageChunk(content="实际"),
        AIMessageChunk(content=""),
        AIMessageChunk(content="内容"),
    ]
    gen = Generator(llm=llm)
    tokens = list(gen.generate_stream("q", "c"))
    assert tokens == ["实际", "内容"]


def test_generate_stream_single_chunk():
    """单 chunk 也能正常工作"""
    gen = Generator(llm=_mock_stream(["完整答案"]))
    tokens = list(gen.generate_stream("q", "c"))
    assert tokens == ["完整答案"]


# ============================================
# 3. 异常处理
# ============================================


def test_generate_stream_llm_error():
    """LLM stream 抛异常 → 异常透传"""
    bad_llm = MagicMock()
    bad_llm.stream.side_effect = RuntimeError("stream failed")

    import pytest

    gen = Generator(llm=bad_llm)
    with pytest.raises(RuntimeError, match="stream failed"):
        next(gen.generate_stream("q", "c"))
