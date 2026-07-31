"""SmartChunker 单元测试

测试策略：
- 纯逻辑，不依赖任何外部服务
- 用真实的 ParsedElement 数据验证切分行为
- 覆盖：文本递归切分、表格独立保留、chunk_id 生成
"""
from core.chunker import SmartChunker
from core.parser import ParsedElement


def _text_el(text: str, source: str = "doc.pdf", page: int = 1) -> ParsedElement:
    """快速构造文本元素"""
    return ParsedElement(
        text=text,
        element_type="text",
        metadata={"source": source, "page": page},
    )


def _table_el(text: str, source: str = "doc.pdf", page: int = 1) -> ParsedElement:
    """快速构造表格元素"""
    return ParsedElement(
        text=text,
        element_type="table",
        metadata={"source": source, "page": page},
    )


# ============================================
# 1. 短文本不切分
# ============================================

def test_short_text_not_split():
    """短文本（< chunk_size）应保持完整"""
    chunker = SmartChunker(chunk_size=512, overlap=50)
    elements = [_text_el("这是一段短文本。")]
    docs = chunker.chunk(elements)

    assert len(docs) == 1
    assert docs[0].page_content == "这是一段短文本。"
    assert docs[0].metadata["chunk_strategy"] == "recursive"


# ============================================
# 2. 长文本递归切分
# ============================================

def test_long_text_split_into_multiple_chunks():
    """长文本（> chunk_size）应切分为多个 chunk"""
    chunker = SmartChunker(chunk_size=100, overlap=20)
    # 造一段超过 100 字符的文本
    long_text = "测试。" * 50  # 约 150 字符
    elements = [_text_el(long_text)]
    docs = chunker.chunk(elements)

    assert len(docs) >= 2, f"应切分为至少 2 个 chunk，实际 {len(docs)}"
    # 每个 chunk 不超过 chunk_size
    for doc in docs:
        assert len(doc.page_content) <= 120, f"chunk 超长: {len(doc.page_content)}"  # 允许一点 overlap 余量


# ============================================
# 3. 表格独立保留
# ============================================

def test_table_as_standalone_chunk():
    """表格元素应独立成 chunk，不参与文本切分"""
    chunker = SmartChunker(chunk_size=100, overlap=20)
    elements = [
        _text_el("这是表格前的文本。"),
        _table_el("姓名 | 年龄\n张三 | 28\n李四 | 32"),
        _text_el("这是表格后的文本。"),
    ]
    docs = chunker.chunk(elements)

    # 至少 3 个：文本1 + 表格 + 文本2（可能更多因为文本可能被切分）
    assert len(docs) >= 3

    # 表格的 chunk_strategy 应为 standalone
    table_docs = [d for d in docs if d.metadata.get("chunk_strategy") == "standalone"]
    assert len(table_docs) >= 1
    assert "[表格]" in table_docs[0].page_content


# ============================================
# 4. chunk_id 生成
# ============================================

def test_chunk_ids_are_unique():
    """每个 chunk 应有唯一的 chunk_id"""
    chunker = SmartChunker()
    elements = [
        _text_el("第一段文本。", source="doc1.pdf"),
        _text_el("第二段文本。", source="doc2.pdf"),
    ]
    docs = chunker.chunk(elements)

    chunk_ids = [d.metadata["chunk_id"] for d in docs]
    assert len(chunk_ids) == len(set(chunk_ids)), "chunk_id 应全部唯一"


def test_chunk_id_format():
    """chunk_id 应为 source-index 格式"""
    chunker = SmartChunker()
    elements = [_text_el("测试文本。", source="my_report.pdf")]
    docs = chunker.chunk(elements)

    for doc in docs:
        cid = doc.metadata["chunk_id"]
        assert cid.startswith("my_report.pdf-"), f"chunk_id 应以 source 开头: {cid}"
        # source 后面的 -{index} 应为数字
        parts = cid.split("-")
        assert parts[-1].isdigit(), f"chunk_id 末尾应为数字: {cid}"


# ============================================
# 5. 空输入
# ============================================

def test_empty_elements():
    """空元素列表应返回空列表"""
    chunker = SmartChunker()
    docs = chunker.chunk([])
    assert docs == []


# ============================================
# 6. 元数据透传
# ============================================

def test_metadata_preserved():
    """chunk 应保留原始元素的 metadata"""
    chunker = SmartChunker()
    elements = [_text_el("测试文本。", source="guide.pdf", page=5)]
    docs = chunker.chunk(elements)

    assert docs[0].metadata["source"] == "guide.pdf"
    assert docs[0].metadata["page"] == 5
    assert docs[0].metadata["element_type"] == "text"


# ============================================
# 7. 混合元素类型
# ============================================

def test_mixed_text_and_table():
    """混合 text 和 table 元素应正确处理"""
    chunker = SmartChunker(chunk_size=512, overlap=50)
    elements = [
        _text_el("介绍文本。"),
        _table_el("季度 | 收入\nQ1 | 100万"),
        _text_el("总结文本。"),
    ]
    docs = chunker.chunk(elements)

    # text 和 table 都应出现
    types = {d.metadata["element_type"] for d in docs}
    assert "text" in types
    assert "table" in types
