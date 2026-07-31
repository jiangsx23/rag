"""手写 ReAct Agent - 不依赖 LangChain AgentExecutor

为什么手写？
- 面试时能一行一行讲清原理
- 完全可控（解析失败 / 工具超时 / 死循环 都能自定义兜底）
- 便于接 Langfuse / 打日志 / 加反思

ReAct 循环：
    Thought → Action → ActionInput → Observation → ... → FinalAnswer

核心字段：
- finished_reason:
    - "final_answer"  正常完成
    - "max_iter"      达到 max_iterations 强制结束
    - "parse_error"   解析失败多次
    - "tool_error"    工具抛异常（但仍尝试继续）
    - "llm_error"     LLM 不可用
"""
import re
import json
import time
import asyncio
from dataclasses import dataclass, field
from typing import Callable
from core.llm import get_default_llm
from loguru import logger

from app.config import settings
from core.observability import observe, span, update_current, update_trace


# ============================================
# 数据结构
# ============================================
@dataclass
class AgentStep:
    """Agent 的一步：思考 + 行动 + 结果"""
    thought: str
    action: str
    action_input: dict
    observation: str
    latency_ms: int = 0


@dataclass
class AgentResult:
    """Agent 完整运行结果"""
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    total_iterations: int = 0
    finished_reason: str = ""


# ============================================
# ReAct Agent
# ============================================
class ReActAgent:
    REACT_PROMPT = """你是一个可以使用工具的 Agent。请严格按以下格式思考和行动：

Thought: 你对当前问题的思考（中文，1-2 句）
Action: 工具名称（必须从下方列表中精确选择）
ActionInput: {{"参数名": "参数值"}}

系统会把工具执行结果以 Observation: 形式返回给你。
你可以重复 Thought/Action/ActionInput/Observation 多次。
当你认为已有足够信息回答用户问题时，必须输出：

FinalAnswer: 你的最终回答（直接给用户看的自然语言）

【可用工具】
{tool_descriptions}

【对话历史】
{history}

【当前问题】
{question}

请输出你的下一步："""

    def __init__(
        self,
        tools: dict[str, Callable],
        tool_descriptions: str,
        llm=None,
        max_iterations: int = None,
        tool_timeout: float = 30.0,
        hitl_guard: bool = True,
    ):
        self.tools = tools
        self.tool_descriptions = tool_descriptions
        self.llm = llm or get_default_llm()
        self.max_iterations = max_iterations or settings.AGENT_MAX_ITERATIONS
        self.tool_timeout = tool_timeout
        # HITL 守卫（延迟初始化，避免循环 import）
        self._hitl_guard = None
        if hitl_guard:
            from core.hitl import get_hitl_guard
            self._hitl_guard = get_hitl_guard()

    # ---------- 主循环 ----------
    @observe(name="react-agent-run", as_type="generation")
    def run(self, question: str) -> AgentResult:
        """跑一次 ReAct 循环，返回完整 trace"""
        update_current(input={"question": question, "max_iter": self.max_iterations})
        history: list[dict] = []
        steps: list[AgentStep] = []
        finished_reason = "max_iter"
        max_parse_failures = 2  # 连续解析失败超过这个数就放弃

        for iteration in range(self.max_iterations):
            # 1. 构造 prompt
            with span(
                "react-iteration",
                input_data={"iter": iteration, "history_len": len(history)},
                metadata={"tools": list(self.tools.keys())},
            ):
                prompt = self._build_prompt(question, history)

                # 2. 调 LLM
                try:
                    response_text = self.llm.invoke(prompt).content
                except Exception as e:
                    logger.error(f"LLM invoke failed: {e}")
                    finished_reason = "llm_error"
                    update_current(level="ERROR", status_message=str(e))
                    break

                # 3. 解析
                parsed = self._parse_output(response_text)
                if parsed is None:
                    history.append({
                        "thought": "解析失败",
                        "action": "",
                        "action_input": {},
                        "observation": (
                            f"你的输出无法解析，必须严格按以下格式之一输出：\n"
                            f"1) Thought/Action/ActionInput（要再调工具）\n"
                            f"2) FinalAnswer: ...（要给出最终答案）\n\n"
                            f"你刚才的原始输出：{response_text[:300]}"
                        ),
                    })
                    finished_reason = "parse_error"
                    recent_failures = sum(
                        1 for h in history[-2:]
                        if h.get("action") == "" and "解析失败" in h.get("thought", "")
                    )
                    if recent_failures >= max_parse_failures:
                        break
                    continue

            # 4. 命中 FinalAnswer
            if parsed["final_answer"] is not None:
                with span("final-answer", input_data=parsed):
                    pass
                update_current(
                    output={
                        "answer": parsed["final_answer"],
                        "iterations": iteration + 1,
                    }
                )
                return AgentResult(
                    answer=parsed["final_answer"],
                    steps=steps,
                    total_iterations=iteration + 1,
                    finished_reason="final_answer",
                )

            # 5. 执行工具
            start = time.time()
            observation = ""
            try:
                with span(
                    f"tool:{parsed['action']}",
                    input_data=parsed["action_input"],
                    metadata={"thought": parsed["thought"]},
                ):
                    # span() 现在基于 _SpanGuard（__enter__/__exit__ 协议），
                    # 不涉及任何 generator，因此不存在嵌套 generator 异常改写的风险。
                    # 但工具异常仍在 span 内部捕获，保持语义清晰。
                    try:
                        observation = self._execute_tool(
                            parsed["action"], parsed["action_input"]
                        )
                    except Exception as e:
                        logger.warning(f"Tool '{parsed['action']}' failed: {e}")
                        observation = (
                            f"工具执行失败：{type(e).__name__}: {str(e)}"
                        )
                        finished_reason = "tool_error"
            except Exception:
                # span 本身（langfuse SDK）异常时静默吞掉，
                # 不影响 Agent 业务逻辑继续走
                if not observation:
                    observation = "工具执行失败：未知错误"
                    finished_reason = "tool_error"
            latency_ms = int((time.time() - start) * 1000)

            step = AgentStep(
                thought=parsed["thought"],
                action=parsed["action"],
                action_input=parsed["action_input"],
                observation=observation[:500],
                latency_ms=latency_ms,
            )
            steps.append(step)
            history.append({
                "thought": parsed["thought"],
                "action": parsed["action"],
                "action_input": parsed["action_input"],
                "observation": observation,
            })

        # 兜底
        update_current(
            output={"answer": None, "iterations": len(steps), "reason": finished_reason},
            metadata={"n_steps": len(steps), "n_tools": len(self.tools)},
        )
        return AgentResult(
            answer="抱歉，处理超时或无法生成最终答案。请尝试换个问法。",
            steps=steps,
            total_iterations=len(steps),
            finished_reason=finished_reason,
        )

    # ---------- Prompt 构造 ----------
    def _build_prompt(self, question: str, history: list[dict]) -> str:
        if history:
            history_text = "\n\n".join([
                f"Thought: {h['thought']}\n"
                f"Action: {h['action']}\n"
                f"ActionInput: {json.dumps(h['action_input'], ensure_ascii=False)}\n"
                f"Observation: {h['observation']}"
                for h in history
            ])
        else:
            history_text = "（无）"

        return self.REACT_PROMPT.format(
            tool_descriptions=self.tool_descriptions,
            history=history_text,
            question=question,
        )

    # ---------- 解析 LLM 输出 ----------
    def _parse_output(self, text: str):
        """解析三种情况：FinalAnswer / Action+ActionInput / 解析失败"""
        # 1) FinalAnswer
        final_match = re.search(r"FinalAnswer:\s*(.+?)(?:\n\s*$|$)", text, re.DOTALL)
        if final_match:
            return {
                "final_answer": final_match.group(1).strip(),
                "thought": "",
                "action": "",
                "action_input": {},
            }

        # 2) Action + ActionInput
        action_match = re.search(r"Action:\s*([A-Za-z_][A-Za-z0-9_]*)", text)
        # ActionInput 里的 JSON 可能含嵌套，要非贪婪匹配到行尾
        input_match = re.search(r"ActionInput:\s*(\{.*?\})\s*(?:\n|$)", text, re.DOTALL)
        thought_match = re.search(
            r"Thought:\s*(.+?)(?=\n\s*Action:|\n\s*FinalAnswer:|$)",
            text, re.DOTALL,
        )

        if not (action_match and input_match):
            return None

        try:
            action_input = json.loads(input_match.group(1))
        except json.JSONDecodeError:
            # 尝试修补：单引号 → 双引号
            try:
                action_input = json.loads(
                    input_match.group(1).replace("'", '"')
                )
            except Exception:
                return None

        if not isinstance(action_input, dict):
            return None

        return {
            "final_answer": None,
            "thought": thought_match.group(1).strip() if thought_match else "",
            "action": action_match.group(1).strip(),
            "action_input": action_input,
        }

    # ---------- 工具执行 ----------
    def _execute_tool(self, action: str, action_input: dict) -> str:
        """同步调用工具。工具抛任何 Exception 都原样透传，调用方负责写入 observation。

        不引入 threading.Timer / ThreadPoolExecutor：
        - Timer 在主线程之外抛 TimeoutError 时，pytest 的 capture 机制会把异常替换成
          "RuntimeError: generator didn't stop after throw()"，原始消息（"boom"）丢失。
        - ThreadPoolExecutor 对 generator 函数会包一层，导致用户抛的 ValueError
          被改写成 RuntimeError。
        这里只做最朴素的同步调用 + Exception 捕获，超时由外层 (asyncio.wait_for 或调用方) 控制。
        """
        if action not in self.tools:
            available = ", ".join(self.tools.keys())
            return f"错误：工具 '{action}' 不存在。可用工具：{available}"

        tool_fn = self.tools[action]

        # HITL 检查：高风险操作需要人工审批
        if self._hitl_guard is not None:
            pending = self._hitl_guard.check(action, action_input)
            if pending.requires_approval:
                return (
                    f"⚠️ 高风险操作「{pending.action_name}」已暂停执行，"
                    f"等待人工审批 (ID: {pending.id})。"
                    f"请告知用户去审批后再试。"
                )

        # 异步函数：用 asyncio 跑
        if asyncio.iscoroutinefunction(tool_fn):
            return asyncio.run(
                asyncio.wait_for(
                    tool_fn(**action_input), timeout=self.tool_timeout
                )
            )

        # 同步函数：直接调，原始异常类型/消息原样透传
        return tool_fn(**action_input)