"""多轮对话 Agent = ReAct + Memory

是 Step 6 的"成品"——把 ReAct 和 Step 5 的 Memory 串起来：
1. 拿到 user_id + session_id → 拉历史
2. Query 改写（"那病假呢？" → "病假几天？"）
3. ReAct Agent 跑改写后的问题（可能多步调工具）
4. 把本轮问答写回 session
"""

from langchain_core.messages import AIMessage, HumanMessage

from app.logger import logger
from core.memory import MemoryManager
from core.observability import observe, span, update_current, update_trace
from core.react_agent import ReActAgent
from core.tools import TOOL_DESCRIPTIONS, TOOLS


class MultiTurnAgent:
    def __init__(self):
        self.react_agent = ReActAgent(
            tools=TOOLS,
            tool_descriptions=TOOL_DESCRIPTIONS,
        )
        self.memory = MemoryManager()
        logger.info("MultiTurnAgent initialized")

    @observe(name="multi-turn-agent")
    def run(
        self,
        question: str,
        session_id: str = None,
        user_id: str = "default",
    ) -> dict:
        # 把整个请求绑到 user/session 上
        update_trace(
            user_id=user_id,
            session_id=session_id,
            input={"question": question},
            tags=["agent", "multi-turn"],
        )

        # 1. 拿 session
        session = self.memory.get_or_create_session(user_id, session_id)
        update_trace(session_id=session.session_id)
        update_current(metadata={"history_len": len(session.messages)})

        # 2. Query 改写
        with span("query-rewrite", input_data={"raw": question}):
            rewritten = self.memory.rewrite_query_with_context(question, session)
        update_current(metadata={"rewritten_query": rewritten})

        # 3. 跑 Agent
        result = self.react_agent.run(rewritten)

        # 3.5 反思评估（仅对 final_answer 路径）
        reflection_data = None
        if result.finished_reason == "final_answer":
            try:
                from core.reflection import ReflectionModule

                reflection = ReflectionModule()
                evaluation = reflection.evaluate(
                    question=question,
                    answer=result.answer,
                    sources=[],  # Agent 没有显式 sources，传空
                )
                reflection_data = {
                    "score": evaluation.score,
                    "is_hallucination": evaluation.is_hallucination,
                    "is_acceptable": evaluation.is_acceptable,
                    "issues": evaluation.issues,
                    "suggestion": evaluation.suggestion,
                }
                update_current(
                    metadata={
                        "reflection_score": evaluation.score,
                        "reflection_acceptable": evaluation.is_acceptable,
                    }
                )
            except Exception as e:
                logger.warning(f"Agent reflection skipped (non-blocking): {e}")

        # 4. 写回 session
        with span("save-session"):
            session.add_message(HumanMessage(content=question))
            session.add_message(AIMessage(content=result.answer))
            self.memory.save(session)

        # 5. 序列化 trace + Langfuse 输出
        steps = [
            {
                "thought": s.thought,
                "action": s.action,
                "action_input": s.action_input,
                "observation": s.observation,
                "latency_ms": s.latency_ms,
            }
            for s in result.steps
        ]
        update_current(
            output={
                "answer": result.answer,
                "finished_reason": result.finished_reason,
                "num_iterations": result.total_iterations,
            },
            metadata={"n_steps": len(steps)},
        )
        update_trace(
            output={"answer": result.answer, "n_steps": len(steps)},
        )

        result_dict = {
            "answer": result.answer,
            "rewritten_query": rewritten,
            "session_id": session.session_id,
            "steps": steps,
            "num_iterations": result.total_iterations,
            "finished_reason": result.finished_reason,
        }
        if reflection_data:
            result_dict["reflection"] = reflection_data
        return result_dict


# ============================================
# 单例：避免每次请求都 new 一个 Agent（要建 LLM 连接）
# ============================================
_agent_singleton: MultiTurnAgent | None = None


def get_agent() -> MultiTurnAgent:
    global _agent_singleton
    if _agent_singleton is None:
        _agent_singleton = MultiTurnAgent()
    return _agent_singleton
