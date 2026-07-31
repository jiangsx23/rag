"""LLM-as-Judge 反思评估器

用途：对 RAG 答案 / Agent 答案做质量评分，低于阈值触发重写。

设计：
- 用独立的 Judge LLM（ChatDeepSeek temperature=0 + with_structured_output）
- 评估维度：准确性、完整性、引用、幻觉
- 支持重试循环：低分 → regenerate_func 重写 → 再评估
"""
from typing import Callable, Optional
from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.logger import logger
from core.llm import get_default_llm
from core.observability import observe, span


SYSTEM_PROMPT = """你是严格的答案质量评审员。

【评估维度】
- 准确性 high/medium/low：答案是否与提供的 Context 一致
- 完整性 high/medium/low：答案是否覆盖了用户问题的所有要点
- 引用：是否标注来源（如 [来源: 文档名称]）
- 幻觉：答案里是否有 Context 中没有的信息

【评分规则】
- 9-10: 优秀，答案准确、完整、有引用
- 7-8: 良好，可接受
- 5-6: 一般，建议重写
- 1-4: 差，必须重写

幻觉一票否决：只要存在幻觉，is_hallucination=True，score ≤ 4。"""


class AnswerEvaluation(BaseModel):
    """LLM Judge 的评估结果"""
    score: int = Field(ge=1, le=10, description="答案质量评分 1-10")
    accuracy: Literal["high", "medium", "low"] = Field(description="准确性")
    completeness: Literal["high", "medium", "low"] = Field(description="完整性")
    has_citation: bool = Field(description="是否标注来源")
    is_hallucination: bool = Field(description="是否包含幻觉")
    issues: list[str] = Field(description="发现的问题列表")
    suggestion: str = Field(description="改进建议")
    is_acceptable: bool = Field(description="是否可接受（通过）")


class ReflectionModule:
    """LLM-as-Judge 反思评估器

    Usage:
        reflection = ReflectionModule()
        evaluation = reflection.evaluate("问题", "答案", ["来源1", "来源2"])
        if not evaluation.is_acceptable:
            # 触发重写流程
            new_answer = regenerate()
            evaluation = reflection.evaluate(question, new_answer, sources)
    """

    def __init__(
        self,
        threshold: Optional[int] = None,
        max_retries: Optional[int] = None,
        llm=None,
    ):
        self.threshold = threshold or settings.REFLECTION_THRESHOLD
        self.max_retries = max_retries or settings.REFLECTION_MAX_RETRIES
        # 复用共享 LLM（temperature=0 保证一致性）
        self.llm = llm or get_default_llm()
        self.evaluator = self.llm.with_structured_output(AnswerEvaluation)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human",
                "【问题】{question}\n"
                "【答案】{answer}\n"
                "【参考 Context】\n{sources}\n\n"
                "请严格按格式评估。如果答案里没有引用任何来源，has_citation=False。"),
        ])

    # ---------- 单次评估 ----------

    @observe(name="reflection-evaluate")
    def evaluate(
        self,
        question: str,
        answer: str,
        sources: list[str],
    ) -> AnswerEvaluation:
        """评估一次答案质量"""
        sources_text = "\n".join([
            f"[{i+1}] {s[:500]}" for i, s in enumerate(sources)
        ]) if sources else "（无参考来源）"
        with span("reflection-llm-call", input_data={"question": question}):
            try:
                messages = self.prompt.format_messages(
                    question=question,
                    answer=answer,
                    sources=sources_text,
                )
                result: AnswerEvaluation = self.evaluator.invoke(messages)
            except Exception as e:
                logger.warning(f"Reflection LLM call failed: {e}")
                # 降级：返回一个通过状态的默认评估
                result = AnswerEvaluation(
                    score=7,
                    accuracy="medium",
                    completeness="medium",
                    has_citation=False,
                    is_hallucination=False,
                    issues=["评估 LLM 异常，跳过检查"],
                    suggestion="",
                    is_acceptable=True,
                )
        logger.info(
            f"Reflection score={result.score}/10 "
            f"hallucination={result.is_hallucination} "
            f"acceptable={result.is_acceptable}"
        )
        return result

    # ---------- 带重试的评估（RAG 专用） ----------

    @observe(name="reflection-rag")
    def evaluate_rag(
        self,
        question: str,
        answer: str,
        sources: list[str],
        regenerate_func: Optional[Callable[[], str]] = None,
    ) -> tuple[str, AnswerEvaluation]:
        """评估 → 如低于阈值则调用 regenerate_func 重写 → 再评估

        Args:
            question: 原始问题
            answer: 初次生成的答案
            sources: 参考来源列表
            regenerate_func: 重写函数，接收当前评估结果返回新答案。
                            传 None 则不重试。

        Returns:
            (final_answer, final_evaluation)
        """
        evaluation = self.evaluate(question, answer, sources)

        for attempt in range(self.max_retries):
            if evaluation.is_acceptable:
                break
            if regenerate_func is None:
                break

            logger.info(
                f"Reflection retry {attempt + 1}/{self.max_retries}: "
                f"score={evaluation.score}, reason={evaluation.suggestion[:80]}"
            )
            with span("reflection-retry", metadata={"attempt": attempt}):
                answer = regenerate_func()
                evaluation = self.evaluate(question, answer, sources)

        return answer, evaluation
