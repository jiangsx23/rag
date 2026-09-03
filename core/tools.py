"""Agent 工具集 - 5 个原子能力

设计原则：
- 每个工具职责单一，输入输出明确
- description 必须讲清楚"什么时候该用 / 不该用"（让 LLM 少调错）
- 错误不抛异常，返回字符串（让 Agent 能自我修复）
- 重要工具（search_knowledge_base）复用现有 Pipeline 单例
"""

from datetime import datetime

from langchain_core.tools import tool

from app.logger import logger


# ============================================
# 1. 知识库检索（核心工具）
# ============================================
@tool
def search_knowledge_base(query: str) -> str:
    """在企业知识库中检索信息。

    适用：查询公司制度、政策、流程、文档内容、年假病假报销等内部信息。
    不适用：实时计算（用 calculator）、外部新闻（用 web_search）、当前时间。

    Args:
        query: 检索关键词或完整问题（必须中文）。

    Returns:
        包含"答案"和"来源"的字符串，便于 Agent 拼接。
    """
    try:
        from app.api import get_pipeline

        pipeline = get_pipeline()
        result = pipeline.query(query, top_k=5)
        sources = [s.get("metadata", {}).get("source", "?") for s in result.get("sources", [])]
        return (
            f"答案：{result.get('answer', '（无）')}\n" f"来源：{sources if sources else '（无）'}"
        )
    except Exception as e:
        logger.exception("search_knowledge_base failed")
        return f"检索失败：{type(e).__name__}: {e}"


# ============================================
# 2. 数学计算（沙箱 eval）
# ============================================
@tool
def python_calculator(expression: str) -> str:
    """执行数学/列表计算。

    适用：求和、平均、复利、单位换算等。
    示例：100 * 1.13, sum([1,2,3]), 2**10, (5000 / 21.75) * 3

    Args:
        expression: 合法 Python 表达式（禁止 import / open 等危险调用）。

    Returns:
        计算结果字符串，失败时返回错误信息。
    """
    # 沙箱：禁用内置函数和属性访问
    allowed_names = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "pow": pow,
        "True": True,
        "False": False,
        "None": None,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"计算结果：{result}"
    except Exception as e:
        return f"计算失败：{type(e).__name__}: {e}"


# ============================================
# 3. 当前时间
# ============================================
@tool
def get_current_time() -> str:
    """获取当前时间（YYYY-MM-DD HH:MM:SS）。

    适用：用户问"今天几号"、"现在几点"、"今天星期几"等。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S (%A)")


# ============================================
# 4. 邮件通知（mock）
# ============================================
@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件通知。重要操作会触发二次确认。

    Args:
        to:      收件人邮箱（如 hr@company.com）
        subject: 邮件主题
        body:    邮件正文

    Returns:
        模拟发送结果。真实环境应调用 SMTP / SendGrid / 飞书 webhook。
    """
    logger.info(f"[MOCK EMAIL] to={to}, subject={subject}")
    return f"[模拟发送] 已发给 {to}，主题「{subject}」，" f"正文长度 {len(body)} 字符"


# ============================================
# 5. 互联网搜索（mock）
# ============================================
@tool
def web_search(query: str) -> str:
    """在互联网上搜索实时信息。

    适用：新闻、天气、股票、当前事件、企业外部资料。
    不适用：企业内部制度、流程（用 search_knowledge_base）。
    """
    return (
        f"[模拟] 网络搜索「{query}」的结果：\n"
        f"- 来源1: 相关信息摘要...\n"
        f"- 来源2: 补充资料..."
    )


# ============================================
# 给 Agent 用的描述 + 注册表
# ============================================
TOOL_DESCRIPTIONS = "\n".join(
    [
        '- search_knowledge_base: 在企业知识库中检索。参数 {"query": "问题"}。'
        "适用：公司制度、政策、流程、文档内容；不适用：实时计算、外部信息。",
        '- python_calculator: 执行数学计算。参数 {"expression": "Python 表达式"}。'
        "示例：100*1.13, sum([1,2,3]), 2**10。",
        "- get_current_time: 获取当前时间。无需参数。",
        '- send_email: 发送邮件。参数 {"to": "邮箱", "subject": "主题", "body": "正文"}。',
        '- web_search: 搜索互联网实时信息。参数 {"query": "关键词"}。适用：新闻、天气、当前事件。',
    ]
)

TOOLS = {
    "search_knowledge_base": search_knowledge_base.func,
    "python_calculator": python_calculator.func,
    "get_current_time": get_current_time.func,
    "send_email": send_email.func,
    "web_search": web_search.func,
}
