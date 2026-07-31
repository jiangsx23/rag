"""反思评估器单元测试

测试策略：
- evaluate() 测试：mock 底层的 evaluator.invoke，验证数据透传和异常降级
- evaluate_rag() 测试：mock evaluate() 方法，验证重试循环逻辑
"""
import pytest
from unittest.mock import MagicMock, patch

from core.reflection import ReflectionModule, AnswerEvaluation


def _eval(**overrides) -> AnswerEvaluation:
    """快速构造 AnswerEvaluation（提供默认值）"""
    defaults = dict(
        score=9, accuracy="high", completeness="high",
        has_citation=True, is_hallucination=False,
        issues=[], suggestion="", is_acceptable=True,
    )
    defaults.update(overrides)
    return AnswerEvaluation(**defaults)


@pytest.fixture
def module():
    """构造一个 evaluator 已被 mock 的 ReflectionModule"""
    mock_llm = MagicMock()
    mock_evaluator = MagicMock()
    mock_llm.with_structured_output.return_value = mock_evaluator
    yield ReflectionModule(llm=mock_llm)


# ============================================
# 1. evaluate() — 链调用透传
# ============================================
def test_evaluate_returns_evaluation(module):
    """evaluator.invoke 返回 AnswerEvaluation 时能正确透传"""
    module.evaluator.invoke.return_value = _eval(score=9)

    result = module.evaluate("Q", "A", ["S"])

    assert isinstance(result, AnswerEvaluation)
    assert result.score == 9
    assert result.is_acceptable is True


def test_evaluate_detects_hallucination(module):
    """幻觉检测透传"""
    module.evaluator.invoke.return_value = _eval(
        score=2, is_hallucination=True, is_acceptable=False,
    )

    result = module.evaluate("Q", "A", ["S"])

    assert result.is_hallucination is True
    assert result.is_acceptable is False
    assert result.score <= 4


def test_evaluate_llm_error_fallback(module):
    """LLM 抛异常 → 返回默认通过评估，不阻塞"""
    module.evaluator.invoke.side_effect = Exception("API timeout")

    result = module.evaluate("Q", "A", ["S"])

    assert result.is_acceptable is True  # 降级：让流程继续
    assert result.score == 7


# ============================================
# 2. evaluate_rag() — 重试逻辑
# ============================================
def test_rag_no_retry_if_acceptable(module):
    """首次评估通过 → 不重试"""
    with patch.object(module, "evaluate", return_value=_eval()) as mock_ev:
        answer, evaluation = module.evaluate_rag(
            "Q", "A", ["S"], regenerate_func=lambda: "B",
        )

    assert mock_ev.call_count == 1
    assert evaluation.is_acceptable is True
    assert answer == "A"


def test_rag_retry_on_low_score(module):
    """首次低分 → 重写 → 第二次通过"""
    calls = iter([
        _eval(score=5, is_acceptable=False),
        _eval(score=9, is_acceptable=True),
    ])

    def regenerate() -> str:
        return "改进后的答案"

    with patch.object(module, "evaluate", side_effect=calls):
        final_answer, evaluation = module.evaluate_rag(
            "Q", "初始答案", ["S"], regenerate_func=regenerate,
        )

    assert evaluation.is_acceptable is True
    assert evaluation.score == 9
    assert final_answer == "改进后的答案"


def test_rag_retry_exhausted(module):
    """重试次数耗尽仍低分 → 返回最后结果"""
    module.max_retries = 2
    calls = iter([
        _eval(score=4, is_acceptable=False),
        _eval(score=5, is_acceptable=False),
        _eval(score=3, is_acceptable=False),
    ])
    regenerate_calls = []

    def regenerate() -> str:
        regenerate_calls.append(1)
        return f"尝试 {len(regenerate_calls)}"

    with patch.object(module, "evaluate", side_effect=calls):
        final_answer, evaluation = module.evaluate_rag(
            "Q", "初始", ["S"], regenerate_func=regenerate,
        )

    assert len(regenerate_calls) == 2
    assert evaluation.is_acceptable is False
    assert evaluation.score <= 5


def test_rag_no_regenerate_func(module):
    """没有传 regenerate_func → 不做重试"""
    with patch.object(
        module, "evaluate", return_value=_eval(score=5, is_acceptable=False),
    ) as mock_ev:
        answer, evaluation = module.evaluate_rag(
            "Q", "A", ["S"], regenerate_func=None,
        )

    assert mock_ev.call_count == 1
    assert answer == "A"


# ============================================
# 3. AnswerEvaluation Pydantic 模型约束
# ============================================
def test_evaluation_model_score_range():
    """score 必须在 1-10 之间"""
    _eval(score=1)
    _eval(score=10)
    with pytest.raises(Exception):
        _eval(score=0)
    with pytest.raises(Exception):
        _eval(score=11)
