"""文档解析测试"""

from pathlib import Path

from core.parser import DocumentParser


def test_parse_text():
    """测试 TXT 解析"""
    # 先造一个测试 txt
    test_file = Path("data/raw/test_parser.txt")
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "这是第一段。\n这是第二段。\n这是第三段。",
        encoding="utf-8",
    )

    parser = DocumentParser()
    elements = parser.parse(str(test_file))
    assert len(elements) > 0
    print(f"✅ 解析到 {len(elements)} 个元素")
