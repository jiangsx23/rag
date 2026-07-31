"""Streamlit 入口 - RAG + Agent 双模式对话界面

RAG 模式：    检索 → 生成 → 反思（带来源和改写 query 展示）
Agent 模式：  ReAct 多步调工具 → 展示完整决策树（Thought → Action → Observation）
"""
import json
import requests
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="企业知识库 RAG + Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📚 企业级 RAG + Agent 智能问答系统")
st.caption("🤖 Agent 模式 · 手写 ReAct 循环，可解释每一步决策")

# ============================================
# 初始化 session_state
# ============================================
_DEFAULTS = {
    "session_id": None,
    "messages": [],
    "user_id": "demo_user",
    "session_list_cache": None,
    "mode": "RAG",  # "RAG" | "Agent"
}
for k, v in _DEFAULTS.items():
    st.session_state.setdefault(k, v)


def load_sessions(force: bool = False) -> list:
    """从后端拉会话列表"""
    if not force and st.session_state.session_list_cache is not None:
        return st.session_state.session_list_cache
    try:
        r = requests.get(
            f"{API_BASE}/sessions/{st.session_state.user_id}", timeout=5
        )
        if r.ok:
            st.session_state.session_list_cache = r.json().get("sessions", [])
            return st.session_state.session_list_cache
    except Exception:
        pass
    return []


# ============================================
# 工具函数：渲染 Agent 步骤树
# ============================================

def render_agent_steps(steps: list[dict]):
    """把 Agent 的 Thought/Action/Observation 渲染为可视化的步骤树"""
    for i, step in enumerate(steps, 1):
        thought = step.get("thought", "")
        action = step.get("action", "")
        action_input = step.get("action_input", {})
        observation = step.get("observation", "")
        latency = step.get("latency_ms", 0)

        icon = "✅" if action else "💭"
        with st.container(border=True):
            cols = st.columns([6, 1])
            with cols[0]:
                st.markdown(f"**{icon} Step {i}**")
            with cols[1]:
                st.caption(f"{latency}ms" if latency else "")

            # Thought
            if thought:
                st.markdown(f"💡 **思考**: {thought}")

            # Action
            if action:
                input_str = json.dumps(action_input, ensure_ascii=False)
                st.markdown(f"🔧 **调用**: `{action}({input_str})`")

            # Observation
            if observation:
                st.markdown(f"📊 **结果**: {observation[:300]}")
                if len(observation) > 300:
                    st.caption("...（结果过长已截断）")

    if st.session_state.get("finished_reason"):
        reason = st.session_state.finished_reason
        reason_labels = {
            "final_answer": "✅ 正常完成",
            "max_iter": "⚠️ 达到最大迭代次数",
            "parse_error": "⚠️ 解析失败",
            "tool_error": "⚠️ 工具执行异常",
            "llm_error": "❌ LLM 不可用",
        }
        st.info(f"结束原因: {reason_labels.get(reason, reason)}")


# ============================================
# 侧边栏：模式切换 + 会话管理
# ============================================
with st.sidebar:
    st.header("⚙️ 模式")

    # 模式切换
    mode = st.radio(
        "对话模式",
        options=["RAG", "Agent"],
        index=0 if st.session_state.mode == "RAG" else 1,
        horizontal=True,
        help="RAG: 检索知识库直接回答 | Agent: 多步推理 + 工具调用",
    )
    if mode != st.session_state.mode:
        st.session_state.mode = mode
        st.session_state.messages = []
        st.session_state.session_id = None
        st.rerun()

    mode_help = {
        "RAG": "📖 检索知识库 → 生成答案 → 质量评估",
        "Agent": "🤖 思考 → 调工具 → 观察 → 再思考 → 最终回答",
    }
    st.caption(mode_help[mode])

    st.divider()

    # 会话管理
    st.header("💬 会话管理")

    if st.button("➕ 新建会话", use_container_width=True):
        st.session_state.session_id = None
        st.session_state.messages = []
        st.session_state.session_list_cache = None
        st.rerun()

    st.divider()

    sessions = load_sessions()
    if not sessions:
        st.caption("（暂无历史会话）")
    else:
        for s in sessions[:20]:
            sid = s["session_id"]
            label = f"🗨️ {sid[:8]} · {s['message_count']}条"
            cols = st.columns([5, 1])
            with cols[0]:
                active = sid == st.session_state.session_id
                btn_label = f"{'🟢' if active else '⚪'} {label}"
                if st.button(btn_label, key=f"open_{sid}", use_container_width=True):
                    st.session_state.session_id = sid
                    st.session_state.messages = []
                    st.rerun()
            with cols[1]:
                if st.button("🗑️", key=f"del_{sid}"):
                    try:
                        requests.delete(
                            f"{API_BASE}/sessions/{st.session_state.user_id}/{sid}",
                            timeout=5,
                        )
                        st.session_state.session_list_cache = None
                        if st.session_state.session_id == sid:
                            st.session_state.session_id = None
                            st.session_state.messages = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"删除失败: {e}")

    st.divider()

    with st.expander("🔧 调试信息", expanded=False):
        st.write(f"**模式**: {st.session_state.mode}")
        st.write(f"**user_id**: {st.session_state.user_id}")
        st.write(f"**session_id**: {st.session_state.session_id or '（无）'}")
        st.write(f"**消息条数**: {len(st.session_state.messages)}")

        if st.button("🔄 刷新 /health"):
            try:
                r = requests.get(f"{API_BASE}/health", timeout=5)
                st.json(r.json())
            except Exception as e:
                st.error(str(e))


# ============================================
# 主区：历史消息
# ============================================
for msg in st.session_state.messages:
    role = msg["role"]
    with st.chat_message(role):
        st.write(msg["content"])

        # Agent 消息额外展示步骤树
        if role == "ai" and msg.get("agent_steps"):
            with st.expander("🤖 查看 Agent 决策过程", expanded=True):
                render_agent_steps(msg["agent_steps"])

        # RAG 消息额外展示来源
        if role == "ai" and msg.get("sources"):
            with st.expander(f"📎 来源（{len(msg['sources'])} 条）", expanded=False):
                for i, src in enumerate(msg["sources"], 1):
                    meta = src.get("metadata", {})
                    st.markdown(
                        f"**[{i}]** `{meta.get('source', '?')}` · 页码 `{meta.get('page', '?')}`"
                    )
                    st.caption(src.get("content", "")[:200])

# ============================================
# 输入框
# ============================================
user_input = st.chat_input(
    "请输入问题"
    if st.session_state.mode == "RAG"
    else "请输入问题（Agent 会自动选择工具）"
)

if user_input:
    # 1. 显示用户消息
    with st.chat_message("human"):
        st.write(user_input)

    # 2. 根据模式调不同接口
    is_agent = st.session_state.mode == "Agent"
    api_path = "/agent/chat" if is_agent else "/chat"

    if is_agent:
        # Agent 模式：保持非流式（Agent 暂不支持 SSE）
        with st.spinner("🤔 思考中..."):
            try:
                resp = requests.post(
                    f"{API_BASE}{api_path}",
                    json={
                        "question": user_input,
                        "session_id": st.session_state.session_id,
                        "user_id": st.session_state.user_id,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                result = resp.json()
            except requests.exceptions.ConnectionError:
                st.error("❌ 连不上后端（http://localhost:8000）。请先 `uvicorn app.api:app --reload`")
                st.stop()
            except requests.exceptions.Timeout:
                st.error("⏱️ 后端超时，请重试")
                st.stop()
            except Exception as e:
                st.error(f"请求失败: {e}")
                st.stop()

        # 更新 session_id
        st.session_state.session_id = result.get("session_id") or st.session_state.session_id

        # 渲染 Agent 回复
        answer = result.get("answer", "")
        steps = result.get("steps", [])
        st.session_state.finished_reason = result.get("finished_reason", "")
        with st.chat_message("ai"):
            st.write(answer)

            if steps:
                with st.expander("🤖 查看 Agent 决策过程", expanded=True):
                    render_agent_steps(steps)

            rewritten = result.get("rewritten_query", "")
            if rewritten and rewritten != user_input:
                with st.expander("🔍 Query 改写（debug）", expanded=False):
                    st.code(f"原 query: {user_input}\n改写后: {rewritten}")

            reflection = result.get("reflection")
            if reflection:
                score = reflection.get("score", "?")
                acceptable = reflection.get("is_acceptable", False)
                label = "✅ 通过" if acceptable else "⚠️ 需改进"
                with st.expander(f"🧠 LLM-as-Judge 反思评估（评分 {score}/10 {label}）", expanded=False):
                    st.json(reflection)

        # 保存 Agent 消息
        ai_msg = {"role": "ai", "content": answer, "agent_steps": steps}
        st.session_state.messages.append(ai_msg)

    else:
        # RAG 模式：SSE 流式，首 token 秒到
        body = {
            "question": user_input,
            "session_id": st.session_state.session_id,
            "user_id": st.session_state.user_id,
            "stream": True,
        }
        full_answer = ""
        sources = None
        reflection = None
        rewritten_query = None
        timing = None
        session_id = st.session_state.session_id

        try:
            with requests.post(
                f"{API_BASE}{api_path}",
                json=body,
                stream=True,
                timeout=120,
            ) as r:
                r.raise_for_status()

                with st.chat_message("ai"):
                    answer_placeholder = st.empty()
                    answer_placeholder.markdown("⏳ 思考中...")

                    for line in r.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8")
                        if not line_str.startswith("data: "):
                            continue

                        data = json.loads(line_str[6:])
                        event_type = data.get("type")

                        if event_type == "rewrite":
                            rewritten_query = data.get("query")

                        elif event_type == "sources":
                            sources = data.get("sources")

                        elif event_type == "token":
                            content = data.get("content", "")
                            full_answer += content
                            answer_placeholder.markdown(full_answer + "▌")

                        elif event_type == "done":
                            answer_placeholder.markdown(full_answer)
                            session_id = data.get("session_id", session_id)
                            reflection = data.get("reflection")
                            timing = data.get("_timing")

                        elif event_type == "error":
                            st.error(f"后端错误: {data.get('message')}")
                            st.stop()

                    # 流式完成 → 展示来源、改写、反思
                    if sources:
                        with st.expander(f"📎 来源（{len(sources)} 条）", expanded=False):
                            for i, src in enumerate(sources, 1):
                                meta = src.get("metadata", {})
                                st.markdown(
                                    f"**[{i}]** `{meta.get('source', '?')}` · 页码 `{meta.get('page', '?')}`"
                                )
                                st.caption(src.get("content", "")[:200])

                    if rewritten_query and rewritten_query != user_input:
                        with st.expander("🔍 Query 改写（debug）", expanded=False):
                            st.code(f"原 query: {user_input}\n改写后: {rewritten_query}")

                    if reflection:
                        score = reflection.get("score", "?")
                        acceptable = reflection.get("is_acceptable", False)
                        label = "✅ 通过" if acceptable else "⚠️ 需改进"
                        with st.expander(f"🧠 LLM-as-Judge 反思评估（评分 {score}/10 {label}）", expanded=False):
                            st.json(reflection)

                    if timing:
                        cols = st.columns(5)
                        labels = ["检索", "精排", "生成", "反思", "总计"]
                        keys = ["retrieve_s", "rerank_s", "generate_s", "reflection_s", "total_s"]
                        for col, label, key in zip(cols, labels, keys):
                            val = timing.get(key, 0)
                            col.metric(label, f"{val:.1f}s")

        except requests.exceptions.ConnectionError:
            st.error("❌ 连不上后端（http://localhost:8000）。请先 `uvicorn app.api:app --reload`")
            st.stop()
        except requests.exceptions.Timeout:
            st.error("⏱️ 后端超时，请重试")
            st.stop()
        except Exception as e:
            st.error(f"请求失败: {e}")
            st.stop()

        # 更新 session_id
        st.session_state.session_id = session_id

        # 保存 RAG 消息
        ai_msg = {"role": "ai", "content": full_answer, "sources": sources or []}
        st.session_state.messages.append(ai_msg)

    # 6. 刷新会话列表
    st.session_state.session_list_cache = None
