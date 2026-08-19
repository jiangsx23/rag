"""测试 core/agent.py"""

from unittest.mock import MagicMock

from core.agent import MultiTurnAgent, get_agent


class TestMultiTurnAgent:
    def test_init_creates_react_agent(self):
        """初始化后 react_agent 和 memory 应被创建"""
        agent = MultiTurnAgent()
        assert agent.react_agent is not None
        assert agent.memory is not None

    def test_init_logs_message(self):
        """初始化应打印日志（验证不抛异常）"""
        agent = MultiTurnAgent()
        assert agent.react_agent is not None

    def test_run_with_mocked_deps(self):
        """用 mock 跑 run()，验证流程完整"""
        agent = MultiTurnAgent()

        # Mock memory
        mock_session = MagicMock()
        mock_session.session_id = "test-session-id"
        mock_session.messages = []
        agent.memory.get_or_create_session = MagicMock(return_value=mock_session)
        agent.memory.rewrite_query_with_context = MagicMock(return_value="rewritten query")
        agent.memory.save = MagicMock()

        # Mock react_agent result
        mock_result = MagicMock()
        mock_result.finished_reason = "final_answer"
        mock_result.answer = "test answer"
        mock_result.total_iterations = 1
        mock_result.steps = []
        agent.react_agent.run = MagicMock(return_value=mock_result)

        result = agent.run("test question", session_id="sid", user_id="uid")

        assert result["answer"] == "test answer"
        assert result["rewritten_query"] == "rewritten query"
        assert result["session_id"] == "test-session-id"
        assert result["finished_reason"] == "final_answer"

        # Verify the flow
        agent.memory.get_or_create_session.assert_called_once_with("uid", "sid")
        agent.memory.rewrite_query_with_context.assert_called_once_with(
            "test question", mock_session
        )
        agent.react_agent.run.assert_called_once_with("rewritten query")
        agent.memory.save.assert_called_once()

    def test_run_with_mocked_deps_no_reflection(self):
        """非 final_answer 路径不应触发反思"""
        agent = MultiTurnAgent()

        mock_session = MagicMock()
        mock_session.session_id = "sid"
        mock_session.messages = []
        agent.memory.get_or_create_session = MagicMock(return_value=mock_session)
        agent.memory.rewrite_query_with_context = MagicMock(return_value="q")
        agent.memory.save = MagicMock()

        mock_result = MagicMock()
        mock_result.finished_reason = "max_iterations"
        mock_result.answer = "timeout"
        mock_result.total_iterations = 5
        mock_result.steps = []
        agent.react_agent.run = MagicMock(return_value=mock_result)

        result = agent.run("test")

        assert "reflection" not in result
        assert result["finished_reason"] == "max_iterations"


class TestGetAgent:
    def test_returns_multiturn_agent(self):
        agent = get_agent()
        assert isinstance(agent, MultiTurnAgent)

    def test_singleton_returns_same_instance(self):
        a1 = get_agent()
        a2 = get_agent()
        assert a1 is a2

    def test_singleton_is_initialized(self):
        agent = get_agent()
        assert agent.react_agent is not None
        assert agent.memory is not None
