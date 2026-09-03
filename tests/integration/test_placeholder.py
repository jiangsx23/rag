"""集成测试占位 — 需要真实 DeepSeek/Qdrant 服务时在此目录添加测试

用 pytest -m integration 标记，只在 main 分支 CI 中运行。
"""

import pytest


@pytest.mark.integration
def test_placeholder():
    """占位测试，确保 ci.yml 的 pytest tests/integration/ 不报错"""
    assert True
