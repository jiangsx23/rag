"""FastAPI 入口 - Step 5 收尾加固版

变更要点：
1. Pipeline 改为单例（BGE/Reranker 模型只加载一次）
2. /chat 支持 SSE 流式（Step 6 提前埋点）
3. 新增 /sessions 管理（list / get / delete）
4. Langfuse 全链路观测接入
5. 异常统一处理（避免 500 直接给前端）
6. Step 6: /agent/chat 接口
"""

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.logger import logger
from core.observability import (
    flush as langfuse_flush,
)
from core.observability import (
    is_enabled as langfuse_enabled,
)
from core.observability import (
    observe,
)

# ============================================
# Langfuse 可选接入（key 为空时跳过）
# ============================================
try:
    if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
        from langfuse import Langfuse

        langfuse = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info("✅ Langfuse 已接入")
    else:
        langfuse = None
        logger.info("ℹ️ Langfuse 未配置（key 为空），跳过")
except Exception as e:
    langfuse = None
    logger.warning(f"⚠️ Langfuse 初始化失败: {e}")


# ============================================
# Pipeline 单例：避免每个请求都重新加载 BGE 模型
# ============================================
_pipeline_singleton = None


def get_pipeline():
    """懒加载 + 单例：BGE 模型加载很慢，必须复用"""
    global _pipeline_singleton
    if _pipeline_singleton is None:
        logger.info("🔧 首次加载 RAGPipeline（BGE 模型约 30s）...")
        from core.pipeline import RAGPipeline

        _pipeline_singleton = RAGPipeline()
        logger.info("✅ RAGPipeline 单例已就绪")
    return _pipeline_singleton


# ============================================
# Lifespan：优雅启停
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 FastAPI 启动")
    # 预热 Pipeline（BGE 模型 ~30s），避免第一个用户请求等半分钟
    logger.info("🔧 预热 RAGPipeline（BGE 模型加载约 30s）...")
    try:
        get_pipeline()
        logger.info("✅ Pipeline 预热完成")
    except Exception as e:
        logger.warning(f"⚠️ Pipeline 预热失败（首次请求会延迟加载）: {e}")
    yield
    # 关闭前 flush Langfuse 队列（防止丢 trace）
    langfuse_flush()
    logger.info("👋 FastAPI 关闭")


app = FastAPI(
    title="RAG Knowledge Base",
    version="0.3.0",
    description="企业级 RAG + Agent 智能问答系统",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# 健康检查
# ============================================
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.3.0",
        "pipeline_ready": _pipeline_singleton is not None,
        "langfuse_enabled": langfuse_enabled(),
    }


# ============================================
# 单轮 RAG（保留向后兼容）
# ============================================
class QueryRequest(BaseModel):
    question: str
    top_k: int = 5


@app.post("/query")
async def query(req: QueryRequest):
    """单轮问答接口"""
    try:
        pipeline = get_pipeline()
        result = pipeline.query(req.question, req.top_k)
        return result
    except Exception as e:
        logger.exception("query failed")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# 多轮对话（核心接口）
# ============================================
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    user_id: str = "default"
    top_k: int = 5
    stream: bool = False  # 是否走 SSE


@app.post("/chat")
@observe(name="api-chat")
async def chat(req: ChatRequest):
    """多轮对话接口 - 自动改写 query + 记忆"""
    # === 验证代码是否被更新 ===
    if req.stream:
        return StreamingResponse(
            _chat_sse(req),
            media_type="text/event-stream",
        )
    try:
        pipeline = get_pipeline()
        result = pipeline.query_with_memory(
            req.question,
            req.session_id,
            req.user_id,
            req.top_k,
        )
        return result
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=str(e))


async def _chat_sse(req: ChatRequest) -> AsyncGenerator[str, None]:
    logger.info(f"[SSE_DIAG] _chat_sse entered: question='{req.question[:30]}'")
    """SSE 流式输出：改写 query → 来源 → 逐 token 流式答案 → done

    协议：
      data: {"type": "status",    "message": "..."}\\n\\n
      data: {"type": "rewrite",   "query": "..."}\\n\\n
      data: {"type": "sources",   "sources": [...]}\\n\\n
      data: {"type": "token",     "content": "..."}\\n\\n
      data: {"type": "done",      "session_id": "...", "reflection": ...}\\n\\n
      data: {"type": "error",     "message": "..."}\\n\\n
    """
    from langchain_core.messages import AIMessage, HumanMessage

    from core.memory import MemoryManager

    try:
        # 立即 yield，告知前端正在加载
        yield f"data: {json.dumps({'type': 'status', 'message': '正在加载知识库...'})}\n\n"

        pipeline = get_pipeline()

        # 1. 拿到 session + 改写 query
        yield f"data: {json.dumps({'type': 'status', 'message': '正在改写问题...'})}\n\n"
        memory = MemoryManager()
        session = memory.get_or_create_session(req.user_id, req.session_id)
        rewritten = memory.rewrite_query_with_context(req.question, session)

        yield f"data: {json.dumps({'type': 'rewrite', 'query': rewritten}, ensure_ascii=False)}\n\n"

        # 2. query_stream 逐步 yield sources → token*N → done
        full_answer = ""
        for event in pipeline.query_stream(rewritten, top_k=req.top_k):
            if event["type"] == "done":
                full_answer = event.get("answer", "")
                event.pop("answer", None)  # SSE 协议不传 answer 全文
                event["session_id"] = session.session_id
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 3. 保存到 session
        session.add_message(HumanMessage(content=req.question))
        session.add_message(AIMessage(content=full_answer))
        memory.save(session)

    except Exception as e:
        logger.exception("chat stream failed")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"


# ============================================
# Session 管理
# ============================================
@app.get("/sessions/{user_id}")
async def list_sessions(user_id: str):
    """列出某 user 的所有 session（按最后活跃时间倒序）"""
    try:
        from core.memory import MemoryManager

        memory = MemoryManager()
        pattern = f"session:{user_id}:*"
        keys = memory.redis.keys(pattern)

        sessions = []
        for key in keys:
            data = memory.redis.get(key)
            if not data:
                continue
            d = json.loads(data)
            sessions.append(
                {
                    "session_id": d.get("session_id"),
                    "user_id": d.get("user_id"),
                    "message_count": len(d.get("messages", [])),
                    "last_active": d.get("last_active"),
                    "summary": d.get("summary", "")[:120],
                }
            )
        sessions.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return {"user_id": user_id, "count": len(sessions), "sessions": sessions}
    except Exception as e:
        logger.exception("list sessions failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sessions/{user_id}/{session_id}")
async def delete_session(user_id: str, session_id: str):
    """删除指定 session"""
    try:
        from core.memory import MemoryManager

        memory = MemoryManager()
        deleted = memory.redis.delete(f"session:{user_id}:{session_id}")
        return {"deleted": bool(deleted), "session_id": session_id}
    except Exception as e:
        logger.exception("delete session failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sessions/{user_id}")
async def clear_user_sessions(user_id: str):
    """清空某 user 的所有 session"""
    try:
        from core.memory import MemoryManager

        memory = MemoryManager()
        pattern = f"session:{user_id}:*"
        keys = memory.redis.keys(pattern)
        if not keys:
            return {"deleted": 0}
        deleted = memory.redis.delete(*keys)
        return {"deleted": deleted}
    except Exception as e:
        logger.exception("clear sessions failed")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# 🆕 Step 6: Agent 接口（ReAct + 多轮记忆）
# ============================================
class AgentChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    user_id: str = "default"


@app.post("/agent/chat")
@observe(name="api-agent-chat")
async def agent_chat(req: AgentChatRequest):
    """Agent 多轮对话：自动改写 query + ReAct 多步调工具 + 记忆

    返回比 /chat 多 3 个字段：
      - steps[]:      每步 Thought/Action/Observation（可解释性核心）
      - num_iterations: 跑了多少轮
      - finished_reason: final_answer / max_iter / parse_error / tool_error
    """
    try:
        from core.agent import get_agent

        agent = get_agent()
        result = agent.run(req.question, req.session_id, req.user_id)
        return result
    except Exception as e:
        logger.exception("agent chat failed")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# 🆕 HITL 审批端点
# ============================================
class ActionApproveRequest(BaseModel):
    action_id: str = Field(..., min_length=1, description="待审批的操作 ID")


@app.get("/agent/actions/pending")
async def list_pending_actions():
    """列出所有待人工审批的高风险操作"""
    try:
        from core.hitl import get_hitl_guard

        guard = get_hitl_guard()
        actions = guard.list_pending()
        return {
            "count": len(actions),
            "actions": [
                {
                    "id": a.id,
                    "action_name": a.action_name,
                    "action_input": a.action_input,
                    "created_at": a.created_at,
                    "status": a.status,
                }
                for a in actions
            ],
        }
    except Exception as e:
        logger.exception("list pending actions failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/actions/{action_id}/approve")
async def approve_action(action_id: str):
    """批准高风险操作"""
    try:
        from core.hitl import get_hitl_guard

        guard = get_hitl_guard()
        success = guard.approve(action_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"操作 {action_id} 不存在或已处理",
            )
        action = guard.get_action(action_id)
        return {
            "status": "approved",
            "action_id": action_id,
            "action_name": action.action_name if action else "unknown",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("approve action failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/actions/{action_id}/reject")
async def reject_action(action_id: str):
    """拒绝高风险操作"""
    try:
        from core.hitl import get_hitl_guard

        guard = get_hitl_guard()
        success = guard.reject(action_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"操作 {action_id} 不存在或已处理",
            )
        return {
            "status": "rejected",
            "action_id": action_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("reject action failed")
        raise HTTPException(status_code=500, detail=str(e))
