"""多轮对话记忆测试 - Step 5

覆盖 4 个核心路径：
1. 指代消解（"那病假呢？" → 含"病假"关键词的独立问题）
2. 无历史时直接返回原 query
3. Session 持久化（Redis）
4. 滑窗压缩（>max_messages 触发摘要）

注意：用 fakeredis 替代真实 Redis，测试不依赖外部服务。
"""

import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.memory import MemoryManager, SessionMemory

# ============================================
# Fixture
# ============================================


@pytest.fixture
def memory():
    """每个测试用独立的 fakeredis 实例，互不污染"""
    import fakeredis

    fake_client = fakeredis.FakeStrictRedis(decode_responses=True)
    return MemoryManager(redis_client=fake_client)


@pytest.fixture
def test_user_id():
    """用时间戳生成唯一 user_id，避免测试间状态污染"""
    return f"test_user_{int(time.time() * 1000)}"


# ============================================
# 1. 指代消解（核心面试点）
# ============================================


def test_query_rewriting_solves_coreference(memory, test_user_id):
    """测试指代消解：'那病假呢？' 应改写为含'病假'的问题

    > 为什么这是最重要的测试？
    > 这是多轮对话的核心难点。如果不能解决指代，Agent 在工业场景完全无法用。
    """
    session = memory.get_or_create_session(test_user_id)
    session.add_message(HumanMessage("公司年假几天？"))
    session.add_message(AIMessage("10 个工作日"))

    rewritten = memory.rewrite_query_with_context("那病假呢？", session)

    print("\n[原 query] 那病假呢？")
    print(f"[改写后]   {rewritten}")

    # 关键断言：改写结果必须包含"病假"关键词
    assert "病假" in rewritten, f"改写结果应包含'病假'关键词，实际: {rewritten}"
    # 不应该还是"那病假呢？"这种带指代的形式
    assert not rewritten.startswith("那"), f"改写后不应保留指代词'那'，实际: {rewritten}"


def test_query_rewriting_multiple_references(memory, test_user_id):
    """测试多种指代形式都能正确消解"""
    session = memory.get_or_create_session(test_user_id)
    session.add_message(HumanMessage("公司年假是几天？"))
    session.add_message(AIMessage("员工每年享受 10 个工作日的带薪年假。"))

    test_cases = [
        ("那病假呢？", "病假"),
        ("这个呢？", None),  # "这个"太模糊，可能改写为"年假"或"病假"
        ("加一起能请多少天？", None),  # 可能改写为"年假和病假总和"
    ]

    for query, must_contain in test_cases:
        rewritten = memory.rewrite_query_with_context(query, session)
        print(f"[{query}] -> [{rewritten}]")

        if must_contain:
            assert (
                must_contain in rewritten
            ), f"'{query}' 应改写为含'{must_contain}'的问题，实际: {rewritten}"


# ============================================
# 2. 无历史时直接返回原 query（边界条件）
# ============================================


def test_query_rewriting_no_history(memory, test_user_id):
    """无对话历史时，query 改写应该直接返回原 query

    > 为什么？
    > 第一轮对话没有上下文可参考，强行改写反而会改变原意。
    """
    session = memory.get_or_create_session(test_user_id)
    # session.messages 是空的
    assert len(session.messages) == 0

    original = "公司年假几天？"
    rewritten = memory.rewrite_query_with_context(original, session)

    assert (
        rewritten == original
    ), f"无历史时应直接返回原 query，期望 '{original}'，实际 '{rewritten}'"


# ============================================
# 3. Session 持久化（Redis 核心价值）
# ============================================


def test_session_persistence(memory, test_user_id):
    """测试 session 持久化：保存后能从 Redis 重新加载

    > 为什么这是必测项？
    > 没有持久化，多轮对话就退化成单轮，session_id 形同虚设。
    > Redis 的价值就在于跨进程/重启后状态还在。
    """
    # 1. 创建 session 并写入消息
    session = memory.get_or_create_session(test_user_id)
    session.add_message(HumanMessage("测试问题1"))
    session.add_message(AIMessage("测试回答1"))
    session.add_message(HumanMessage("测试问题2"))
    memory.save(session)

    session_id = session.session_id

    # 2. 模拟"新进程"：重新从 Redis 加载
    loaded = memory.get_or_create_session(test_user_id, session_id)

    # 3. 断言：消息完整恢复
    assert len(loaded.messages) == 3, f"应恢复 3 条消息，实际 {len(loaded.messages)}"
    assert loaded.messages[0].content == "测试问题1"
    assert loaded.messages[1].content == "测试回答1"
    assert loaded.messages[2].content == "测试问题2"
    assert loaded.session_id == session_id

    # 清理测试数据
    memory.redis.delete(f"session:{test_user_id}:{session_id}")


def test_session_isolation(memory):
    """不同 user_id 的 session 应该完全隔离"""
    user_a = f"user_a_{int(time.time() * 1000)}"
    user_b = f"user_b_{int(time.time() * 1000)}"

    session_a = memory.get_or_create_session(user_a)
    session_a.add_message(HumanMessage("A 的问题"))
    memory.save(session_a)

    session_b = memory.get_or_create_session(user_b)
    # user_b 的 session 应该是空的
    assert len(session_b.messages) == 0, "不同 user 的 session 不应共享数据"

    # 清理
    memory.redis.delete(f"session:{user_a}:{session_a.session_id}")


# ============================================
# 4. 滑窗压缩（>max_messages 触发摘要）
# ============================================


def test_sliding_window_compression(memory, test_user_id):
    """测试滑窗：消息数超过 max_messages 时应触发摘要压缩

    > 为什么需要滑窗？
    > LLM 有 token 限制（DeepSeek-Chat 32K），如果不压缩，
    > 长时间对话后 context 会爆掉，调用失败。
    """
    # 用一个很小的 max_messages，方便测试
    memory.max_messages = 5

    session = memory.get_or_create_session(test_user_id)

    # 添加 10 条消息（超过 max_messages=5）
    for i in range(10):
        if i % 2 == 0:
            session.add_message(HumanMessage(f"问题 {i}"))
        else:
            session.add_message(AIMessage(f"回答 {i}"))

    # 保存前手动检查（save 会触发压缩）
    assert len(session.messages) == 10

    memory.save(session)

    # 保存后应该只保留最近 max_messages 条
    assert len(session.messages) == 5, f"滑窗后应保留 5 条，实际 {len(session.messages)}"

    # 摘要应已生成（如果 LLM 可用）
    # 注：如果 DeepSeek 不可用，summary 可能是空字符串（兜底逻辑）
    print(f"摘要: {session.summary}")

    # 重新加载验证
    loaded = memory.get_or_create_session(test_user_id, session.session_id)
    assert len(loaded.messages) == 5, "重新加载后应仍是 5 条"

    # 清理
    memory.redis.delete(f"session:{test_user_id}:{session.session_id}")


# ============================================
# 5. 工具方法测试
# ============================================


def test_session_id_generation(memory):
    """session_id 应基于 user_id + time 生成，保证唯一性"""
    user_id = "test_user_id_gen"

    sid_1 = memory._generate_session_id(user_id)
    time.sleep(0.01)  # 保证 time() 不同
    sid_2 = memory._generate_session_id(user_id)

    assert sid_1 != sid_2, "同一 user_id 多次生成应得到不同 session_id"
    assert len(sid_1) == 16, f"session_id 长度应为 16，实际 {len(sid_1)}"

    # 验证是 md5 hex 格式
    assert all(c in "0123456789abcdef" for c in sid_1), "session_id 应该是 hex 字符"


def test_serialize_deserialize_roundtrip(memory, test_user_id):
    """测试序列化/反序列化不丢失数据"""
    original = SessionMemory(
        session_id="test_sid_123",
        user_id=test_user_id,
    )
    original.add_message(HumanMessage("问题A"))
    original.add_message(AIMessage("回答A"))
    original.summary = "测试摘要"

    # 序列化 → 反序列化
    serialized = memory._serialize(original)
    json_str = __import__("json").dumps(serialized, ensure_ascii=False)
    deserialized = memory._deserialize(json_str)

    assert deserialized.session_id == "test_sid_123"
    assert deserialized.user_id == test_user_id
    assert deserialized.summary == "测试摘要"
    assert len(deserialized.messages) == 2
    assert isinstance(deserialized.messages[0], HumanMessage)
    assert isinstance(deserialized.messages[1], AIMessage)
    assert deserialized.messages[0].content == "问题A"
