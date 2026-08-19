"""测试 core/tools.py"""

import re
from datetime import datetime

from core.tools import (
    TOOL_DESCRIPTIONS,
    TOOLS,
    get_current_time,
    python_calculator,
    send_email,
    web_search,
)


class TestPythonCalculator:
    def test_basic_arithmetic(self):
        result = python_calculator.func("2 + 2")
        assert "4" in result

    def test_complex_expression(self):
        result = python_calculator.func("100 * 1.13")
        assert "112.999" in result or "113" in result

    def test_list_operations(self):
        result = python_calculator.func("sum([1, 2, 3, 4, 5])")
        assert "15" in result

    def test_power(self):
        result = python_calculator.func("2 ** 10")
        assert "1024" in result

    def test_division(self):
        result = python_calculator.func("(5000 / 21.75) * 3")
        assert "689" in result

    def test_invalid_expression(self):
        result = python_calculator.func("1/0")
        assert "失败" in result or "Error" in result

    def test_sandbox_no_import(self):
        """沙箱应禁止危险调用"""
        result = python_calculator.func("__import__('os').system('ls')")
        assert "失败" in result

    def test_sandbox_no_builtins(self):
        result = python_calculator.func("open('/etc/passwd')")
        assert "失败" in result


class TestGetCurrentTime:
    def test_returns_formatted_time(self):
        result = get_current_time.func()
        # Should match YYYY-MM-DD HH:MM:SS (DayOfWeek)
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(", result)

    def test_contains_weekday(self):
        result = get_current_time.func()
        valid_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(day in result for day in valid_days), f"Weekday not found in: {result}"

    def test_roughly_correct_date(self):
        today = datetime.now().strftime("%Y-%m-%d")
        result = get_current_time.func()
        assert result.startswith(today), f"Expected {today}, got {result}"


class TestSendEmail:
    def test_returns_success_message(self):
        result = send_email.func("hr@company.com", "Test", "Hello")
        assert "模拟" in result or "sent" in result.lower()

    def test_includes_recipient(self):
        result = send_email.func("user@test.com", "Subject", "Body")
        assert "user@test.com" in result

    def test_includes_subject(self):
        result = send_email.func("a@b.com", "Meeting Reminder", "Don't forget!")
        assert "Meeting Reminder" in result

    def test_reports_body_length(self):
        long_body = "A" * 1000
        result = send_email.func("a@b.com", "S", long_body)
        assert "1000" in result


class TestWebSearch:
    def test_returns_mock_result(self):
        result = web_search.func("test query")
        assert "模拟" in result or "Mock" in result

    def test_includes_query(self):
        query = "current weather in Shanghai"
        result = web_search.func(query)
        assert query in result


class TestToolsRegistry:
    def test_tools_dict_has_all_tools(self):
        expected = {
            "search_knowledge_base",
            "python_calculator",
            "get_current_time",
            "send_email",
            "web_search",
        }
        assert set(TOOLS.keys()) == expected

    def test_tools_dict_maps_to_functions(self):
        assert callable(TOOLS["python_calculator"])
        assert callable(TOOLS["get_current_time"])

    def test_tool_descriptions_mention_all_tools(self):
        for name in TOOLS:
            assert name in TOOL_DESCRIPTIONS, f"{name} missing from TOOL_DESCRIPTIONS"
