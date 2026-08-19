"""人机协作守卫（HITL Guard）

高风险操作（如 send_email）在执行前需要人工审批，防止 Agent 误操作。

设计：
- HITLGuard 是全局单例（类变量持有 pending 列表）
- check() 在工具执行前调用，返回 PendingAction
- 高风险操作 → requires_approval=True → 不执行，等审批
- 低风险操作 → requires_approval=False → 直接执行
- 审批通过 approve() / 拒绝 reject() 由 API 端点触发

用法：
    guard = HITLGuard()
    action = guard.check("send_email", {"to": "...", "subject": "..."})
    if action.requires_approval:
        return f"需要审批 (ID: {action.id})"
    # else: 直接执行
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.logger import logger


class ActionRisk(Enum):
    """风险等级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class PendingAction:
    """一条待审批的操作"""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    action_name: str = ""
    action_input: dict = field(default_factory=dict)
    risk_level: ActionRisk = ActionRisk.LOW
    requires_approval: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "pending"  # pending | approved | rejected


class HITLGuard:
    """HITL 守卫 — 全局单例（通过类变量共享 pending 状态）"""

    # 高风险动作列表（匹配 tools.py 中的函数名）
    HIGH_RISK_ACTIONS: set = {"send_email", "publish_content", "delete_data"}

    # 全局待审批列表（类变量，所有实例共享）
    _pending_actions: dict[str, PendingAction] = {}

    def check(self, action_name: str, action_input: dict) -> PendingAction:
        """检查 action 是否需要人工审批

        Returns:
            PendingAction: requires_approval=True 表示需要审批
        """
        risk = ActionRisk.HIGH if action_name in self.HIGH_RISK_ACTIONS else ActionRisk.LOW
        action = PendingAction(
            action_name=action_name,
            action_input=action_input,
            risk_level=risk,
            requires_approval=(risk == ActionRisk.HIGH),
        )
        if action.requires_approval:
            self._pending_actions[action.id] = action
            logger.warning(f"HITL: 高风险操作「{action_name}」等待审批 (ID: {action.id})")
        return action

    def approve(self, action_id: str) -> bool:
        """人工批准操作"""
        action = self._pending_actions.get(action_id)
        if action is None or action.status != "pending":
            return False
        action.status = "approved"
        logger.info(f"HITL: 操作已批准 (ID: {action_id}, action: {action.action_name})")
        return True

    def reject(self, action_id: str) -> bool:
        """人工拒绝操作"""
        action = self._pending_actions.get(action_id)
        if action is None or action.status != "pending":
            return False
        action.status = "rejected"
        logger.info(f"HITL: 操作已拒绝 (ID: {action_id}, action: {action.action_name})")
        return True

    def list_pending(self) -> list[PendingAction]:
        """列出所有待审批操作（按创建时间倒序）"""
        return sorted(
            [a for a in self._pending_actions.values() if a.status == "pending"],
            key=lambda a: a.created_at,
            reverse=True,
        )

    def get_action(self, action_id: str) -> PendingAction | None:
        """获取指定操作"""
        return self._pending_actions.get(action_id)

    def clear_old(self, max_age_hours: float = 24.0) -> int:
        """清理超时未处理的操作"""
        now = datetime.now(timezone.utc)
        to_delete = []
        for aid, action in self._pending_actions.items():
            try:
                created = datetime.fromisoformat(action.created_at)
                if (now - created).total_seconds() >= max_age_hours * 3600:
                    to_delete.append(aid)
            except ValueError:
                to_delete.append(aid)
        for aid in to_delete:
            del self._pending_actions[aid]
        return len(to_delete)


# 全局单例
_hitl_guard: HITLGuard | None = None


def get_hitl_guard() -> HITLGuard:
    """获取 HITL 全局单例"""
    global _hitl_guard
    if _hitl_guard is None:
        _hitl_guard = HITLGuard()
    return _hitl_guard
