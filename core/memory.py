"""多轮对话记忆 - Redis + Query 改写

设计要点（Step 5 收尾加固）：
1. 大小写兼容：role 字段既支持 'human/ai'，也兼容 'user/assistant'
2. decode_responses=True：拿到的就是 str，不用 .decode()
3. 连接失败兜底：Redis 挂了不抛异常，session 走内存（仅限当前进程）
4. ping() 暴露给 /health 用
"""
import json
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import redis

from app.config import settings
from app.logger import logger
from core.llm import get_default_llm


@dataclass
class SessionMemory:
    """单个 session 的记忆"""
    session_id: str
    user_id: str
    messages: list[BaseMessage] = field(default_factory=list)
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def add_message(self, msg: BaseMessage):
        """添加一条消息"""
        self.messages.append(msg)
        self.last_active = time.time()


class MemoryManager:
    """记忆管理器 - 用 Redis 存 session"""

    def __init__(
        self,
        redis_url: str = None,
        session_ttl: int = None,
        max_messages: int = None,
        redis_client: "redis.Redis" = None,
        rewrite_llm=None,
    ):
        self.redis = redis_client or redis.from_url(
            redis_url or settings.REDIS_URL,
            decode_responses=True,
        )
        self.session_ttl = session_ttl or settings.SESSION_TTL
        self.max_messages = max_messages or settings.MAX_MESSAGES

        # 用共享 LLM 做 Query 改写
        self.rewrite_llm = rewrite_llm or get_default_llm()
        
        # 测试连接
        try:
            self.redis.ping()
            logger.info(f"✅ Redis connected: {redis_url or settings.REDIS_URL}")
        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e}")
            logger.warning("Will use in-memory fallback")

    def get_or_create_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> SessionMemory:
        """获取或创建 session

        异常兜底：Redis 不可用时返回一个"内存 session"，
        当前进程能用，但重启会丢。生产环境应在 healthcheck 报警。
        """
        if session_id is None:
            session_id = self._generate_session_id(user_id)

        key = f"session:{user_id}:{session_id}"

        try:
            data = self.redis.get(key)
            if data:
                return self._deserialize(data)
        except redis.RedisError as e:
            logger.warning(f"Redis get failed, fallback to memory: {e}")
            # 用内存 session（用 key 当 id，方便后续 save 时尝试重写 Redis）
            return SessionMemory(session_id=session_id, user_id=user_id)

        return SessionMemory(session_id=session_id, user_id=user_id)

    def save(self, session: SessionMemory):
        """保存 session 到 Redis（带滑窗压缩）"""
        # 滑窗：超过 max_messages 就触发摘要
        if len(session.messages) > self.max_messages:
            session.summary = self._summarize_history(session)
            session.messages = session.messages[-self.max_messages:]

        key = f"session:{session.user_id}:{session.session_id}"
        try:
            self.redis.setex(
                key,
                self.session_ttl,
                json.dumps(self._serialize(session), ensure_ascii=False),
            )
        except redis.RedisError as e:
            logger.warning(f"Redis save failed: {e}（session 数据仅在内存中保留）")

    def is_healthy(self) -> bool:
        """给 /health 接口用"""
        try:
            return self.redis.ping()
        except Exception:
            return False

    def rewrite_query_with_context(
        self,
        current_query: str,
        session: SessionMemory,
    ) -> str:
        """核心：把多轮对话压缩成独立 query"""
        if not session.messages:
            return current_query
        
        # 拿最近 6 条消息
        history_text = "\n".join([
            f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content[:100]}"
            for m in session.messages[-6:]
        ])
        
        prompt = f"""基于以下对话历史，把用户的最后问题改写成一个独立、完整的问题。

【规则】
- 保留原问题的核心意图
- 把指代词（"它"、"那个"、"这"、"那"）替换为具体实体
- 如果问题已经独立完整，直接返回原问题
- 只输出改写结果，不要任何解释

【对话历史】
{history_text}

【用户最后问题】
{current_query}

【改写后的问题】"""

        try:
            response = self.rewrite_llm.invoke(prompt)
            rewritten = response.content.strip()
            
            # 兜底：异常时返回原 query
            if not rewritten or len(rewritten) > 200:
                return current_query
            
            logger.info(f"Query rewrite: '{current_query}' -> '{rewritten}'")
            return rewritten
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}")
            return current_query

    def _summarize_history(self, session: SessionMemory) -> str:
        """历史过长时生成摘要"""
        history_text = "\n".join([
            f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content[:200]}"
            for m in session.messages
        ])
        prompt = f"请将以下对话历史压缩成 100 字以内的摘要：\n\n{history_text}\n\n摘要："
        try:
            return self.rewrite_llm.invoke(prompt).content.strip()
        except Exception:
            return ""

    def _generate_session_id(self, user_id: str) -> str:
        """生成 session_id"""
        return hashlib.md5(
            f"{user_id}-{time.time()}".encode()
        ).hexdigest()[:16]

    def _serialize(self, session: SessionMemory) -> dict:
        """session → dict（role 用小写，跨进程稳定）"""
        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "messages": [
                {"role": "human", "content": m.content}
                if isinstance(m, HumanMessage)
                else {"role": "ai", "content": m.content}
                for m in session.messages
            ],
            "summary": session.summary,
            "created_at": session.created_at,
            "last_active": session.last_active,
        }

    def _deserialize(self, data: str) -> SessionMemory:
        """dict → session（大小写都兼容，老数据不会爆）"""
        d = json.loads(data)
        session = SessionMemory(
            session_id=d["session_id"],
            user_id=d["user_id"],
            summary=d.get("summary", ""),
        )
        session.created_at = d.get("created_at", session.created_at)
        session.last_active = d.get("last_active", session.last_active)

        for m in d.get("messages", []):
            role = m["role"].lower()
            if role in ("human", "user"):
                session.messages.append(HumanMessage(content=m["content"]))
            elif role in ("ai", "assistant"):
                session.messages.append(AIMessage(content=m["content"]))
            else:
                # 未知 role 兜底：当 human 处理
                logger.warning(f"未知 role '{role}'，按 human 处理")
                session.messages.append(HumanMessage(content=m["content"]))
        return session
