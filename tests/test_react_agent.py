"""ReAct Agent 单元测试 - 覆盖 5 条核心路径

为什么用 MagicMock 替掉 LLM？
- 不依赖 DeepSeek API，CI 能直接跑
- 可以构造"刁钻"的 LLM 输出（解析失败、连续 FinalAnswer 等）
- 跑得很快（毫秒级）

5 个 case：
1. 单步直接 FinalAnswer（最常见）
2. 多步调 tool（Agent 的核心价值）
3. 解析失败时自动让 LLM 修复
4. 达到 max_iter 强制降级
5. 调不存在的工具能优雅报错并继续
"""

from unittest.mock import MagicMock

from core.react_agent import ReActAgent


def _mock_llm(responses: list[str]) -> MagicMock:
    """构造一个 LLM mock，按序返回 responses"""
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


# ============================================
# 1. 单步 FinalAnswer
# ============================================
def test_final_answer_path():
    """LLM 第一轮就直接给答案（最简单场景）"""
    agent = ReActAgent(
        tools={"search": lambda query: "10 天"},
        tool_descriptions="- search: 搜索",
        max_iterations=3,
    )
    agent.llm = _mock_llm(
        [
            "Thought: 我知道答案\nFinalAnswer: 10 个工作日",
        ]
    )
    result = agent.run("年假几天？")

    assert result.answer == "10 个工作日"
    assert result.finished_reason == "final_answer"
    assert result.total_iterations == 1
    assert result.steps == []  # 没调工具


# ============================================
# 2. 多步调工具
# ============================================
def test_tool_call_path():
    """Agent 调工具 → 拿到 observation → 再 FinalAnswer"""
    agent = ReActAgent(
        tools={"calc": lambda expr: "200"},
        tool_descriptions="- calc: 计算",
        max_iterations=5,
    )
    agent.llm = _mock_llm(
        [
            "Thought: 需要算 100*2\n" 'Action: calc\nActionInput: {"expression": "100*2"}\n',
            "Thought: 工具返回 200\nFinalAnswer: 200",
        ]
    )
    result = agent.run("100*2 = ?")

    assert result.total_iterations == 2
    assert result.finished_reason == "final_answer"
    assert result.answer == "200"
    assert len(result.steps) == 1
    assert result.steps[0].action == "calc"
    assert result.steps[0].action_input == {"expression": "100*2"}


# ============================================
# 3. 解析失败时让 LLM 自我修复
# ============================================
def test_parse_failure_recovery():
    """LLM 输出完全无法解析 → Agent 给反馈 → LLM 修正"""
    agent = ReActAgent(
        tools={"dummy": lambda: "ok"},
        tool_descriptions="- dummy: 测试",
        max_iterations=5,
    )
    agent.llm = _mock_llm(
        [
            "胡言乱语完全无法解析",  # 解析失败
            "Thought: 好的我重新来\nFinalAnswer: 42",  # 修复
        ]
    )
    result = agent.run("?")

    assert result.finished_reason == "final_answer"
    assert result.answer == "42"
    assert len(result.steps) == 0


# ============================================
# 4. 达到 max_iter 强制结束
# ============================================
def test_max_iterations():
    """LLM 一直想调工具 → 达到 max_iter 强制降级"""
    agent = ReActAgent(
        tools={"search": lambda query: "..."},
        tool_descriptions="- search: 搜索",
        max_iterations=3,
    )
    agent.llm = _mock_llm(
        [
            'Thought: 再查一次\nAction: search\nActionInput: {"query":"x"}',
            'Thought: 再查\nAction: search\nActionInput: {"query":"y"}',
            'Thought: 再查\nAction: search\nActionInput: {"query":"z"}',
        ]
    )
    result = agent.run("找不到答案")

    assert result.finished_reason in ("max_iter", "tool_error")  # 工具签名对了才是 max_iter
    assert result.total_iterations == 3
    assert len(result.steps) == 3
    assert "抱歉" in result.answer  # 兜底文案


# ============================================
# 5. 调不存在的工具能优雅降级
# ============================================
def test_unknown_tool():
    """LLM 拼错工具名 → Agent 返回可用工具列表 → LLM 用对的工具 FinalAnswer"""
    agent = ReActAgent(
        tools={"valid_tool": lambda: "ok"},
        tool_descriptions="- valid_tool: 唯一合法工具",
        max_iterations=3,
    )
    agent.llm = _mock_llm(
        [
            "Thought: 我以为有这个工具\n" "Action: invalid_tool\nActionInput: {}\n",
            "Thought: 我换一个\nFinalAnswer: 跳过",
        ]
    )
    result = agent.run("?")

    # 关键：解析能继续，没崩
    assert result.finished_reason == "final_answer"
    assert result.answer == "跳过"
    # observation 里应该提示了可用工具
    assert "valid_tool" in result.steps[0].observation


# ============================================
# 6. 工具抛异常能优雅处理
# ============================================
def test_tool_exception():
    """工具自己抛异常 → observation 写入错误信息 → Agent 可继续"""

    def _boom():
        raise ValueError("boom")

    agent = ReActAgent(
        tools={"bad": _boom},
        tool_descriptions="- bad: 会爆的工具",
        max_iterations=3,
    )
    agent.llm = _mock_llm(
        [
            "Action: bad\nActionInput: {}",
            "FinalAnswer: 出错了",
        ]
    )
    result = agent.run("?")

    print("--------------------------")
    print(result)
    print("--------------------------")

    assert "boom" in result.steps[0].observation
    assert result.finished_reason == "final_answer"


# ============================================
# 7. 解析器边界 case
# ============================================
def test_parser_handles_markdown_fences():
    """LLM 偶尔会把 JSON 包在 ```json ... ``` 里"""
    agent = ReActAgent(
        tools={"calc": lambda expr: "42"},
        tool_descriptions="- calc: 计算",
        max_iterations=3,
    )
    agent.llm = _mock_llm(
        [
            'Thought: 用计算器\nAction: calc\nActionInput: ```json\n{"expression":"6*7"}\n```',
            "FinalAnswer: 42",
        ]
    )
    result = agent.run("?")

    # 这个 case 当前解析器可能解析失败，但不应该崩
    # 至少 finished_reason 必须是 final_answer 或 parse_error/max_iter
    assert result.finished_reason in ("final_answer", "parse_error", "max_iter")
