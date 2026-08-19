"""HITL 守卫单元测试 - 测试高风险操作拦截与审批生命周期

测试策略：
- 纯逻辑测试，不依赖外部服务
- 每个测试用独立 HITLGuard 实例
- 覆盖：LOW 放行、HIGH 拦截、approve/reject、list_pending
"""

from core.hitl import ActionRisk, HITLGuard


def _fresh_guard() -> HITLGuard:
    """创建一个干净的 HITLGuard（清空类变量）"""
    g = HITLGuard()
    g._pending_actions.clear()
    return g


# ============================================
# 1. 低风险操作不拦截
# ============================================
def test_low_risk_passes():
    """低风险操作（search）→ requires_approval=False"""
    guard = _fresh_guard()

    action = guard.check("search_knowledge_base", {"query": "年假"})

    assert action.requires_approval is False
    assert action.risk_level == ActionRisk.LOW
    # 不应该进入 pending 列表
    assert len(guard.list_pending()) == 0


# ============================================
# 2. 高风险操作拦截
# ============================================
def test_high_risk_intercepted():
    """高风险操作（send_email）→ requires_approval=True，进入 pending"""
    guard = _fresh_guard()

    action = guard.check(
        "send_email",
        {
            "to": "user@example.com",
            "subject": "测试",
            "body": "内容",
        },
    )

    assert action.requires_approval is True
    assert action.risk_level == ActionRisk.HIGH
    assert action.status == "pending"
    # 应该在 pending 列表里
    pending = guard.list_pending()
    assert len(pending) == 1
    assert pending[0].id == action.id


# ============================================
# 3. 高风险操作含其他动作
# ============================================
def test_high_risk_set():
    """HIGH_RISK_ACTIONS 包含 send_email"""
    guard = _fresh_guard()
    # 验证几个预期的高风险动作
    assert "send_email" in guard.HIGH_RISK_ACTIONS
    # 普通动作不在高风险集里
    assert "search_knowledge_base" not in guard.HIGH_RISK_ACTIONS
    assert "get_current_time" not in guard.HIGH_RISK_ACTIONS


# ============================================
# 4. approve 生命周期
# ============================================
def test_approve_flow():
    """正常审批通过：pending → approved"""
    guard = _fresh_guard()
    action = guard.check("send_email", {"to": "a@b.com", "subject": "hi"})

    assert guard.approve(action.id) is True

    # 状态更新
    assert action.status == "approved"
    # 不再出现在 pending 列表
    assert len(guard.list_pending()) == 0


# ============================================
# 5. reject 生命周期
# ============================================
def test_reject_flow():
    """正常审批拒绝：pending → rejected"""
    guard = _fresh_guard()
    action = guard.check("send_email", {"to": "a@b.com", "subject": "hi"})

    assert guard.reject(action.id) is True

    assert action.status == "rejected"
    assert len(guard.list_pending()) == 0


# ============================================
# 6. 重复操作不生效
# ============================================
def test_double_approve_fails():
    """重复批准返回 False"""
    guard = _fresh_guard()
    action = guard.check("send_email", {})

    assert guard.approve(action.id) is True  # 第一次成功
    assert guard.approve(action.id) is False  # 第二次失败（已不是 pending）


# ============================================
# 7. 不存在的 action_id
# ============================================
def test_unknown_action():
    """不存在的 action_id → approve/reject 返回 False"""
    guard = _fresh_guard()

    assert guard.approve("non_existent") is False
    assert guard.reject("non_existent") is False


# ============================================
# 8. list_pending 排序
# ============================================
def test_list_pending_order():
    """list_pending 列出所有待审批"""
    guard = _fresh_guard()

    a1 = guard.check("send_email", {"action": "first"})
    a2 = guard.check("send_email", {"action": "second"})

    pending = guard.list_pending()
    assert len(pending) == 2
    # 两条都在列表里（顺序不保证，因为时间戳精度可能相同）
    ids = {p.id for p in pending}
    assert a1.id in ids
    assert a2.id in ids


# ============================================
# 9. get_action 查询
# ============================================
def test_get_action():
    """通过 action_id 查询 PendingAction"""
    guard = _fresh_guard()
    action = guard.check("send_email", {"to": "a@b.com"})

    found = guard.get_action(action.id)
    assert found is not None
    assert found.id == action.id
    assert found.action_name == "send_email"

    assert guard.get_action("nobody") is None


# ============================================
# 10. clear_old 清理超时操作
# ============================================
def test_clear_old():
    """清理超时操作"""
    guard = _fresh_guard()
    guard.check("send_email", {})

    # 用极短的过期时间（0 小时）
    assert len(guard._pending_actions) == 1
    cleared = guard.clear_old(max_age_hours=0)
    assert cleared >= 1
    assert len(guard._pending_actions) == 0
