"""基于 Qdrant 中已入库的迪士尼文档内容生成 QA 数据集

流程：
1. 从 Qdrant 读取所有文档 chunks，按 source 分组
2. 对每组文档内容，用 DeepSeek 生成 QA 对
3. 输出到 eval/dataset.jsonl

用法：
    python eval/generate_qa_from_docs.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek
from qdrant_client import QdrantClient

from app.config import settings
from app.logger import logger

SYSTEM_PROMPT = """你是一个 QA 数据集生成器。请基于提供的文档内容，生成贴合该文档的问答对。

要求：
1. 问题要真实、具体，像用户会问的
2. 答案必须严格基于提供的文档内容，不能编造
3. 每条包含 question / ground_truth / source_doc / difficulty / category 字段
4. 难度（difficulty）：easy（直接可找到）/ medium（需组合信息）/ hard（需推理）
5. 类别（category）：根据内容判断，policy/process/tech/info/guide 等
6. source_doc 使用文档文件名
7. 只输出 JSONL，不要任何额外文字

输出格式（每条一行 JSON）：
{{"question":"...","ground_truth":"...","source_doc":"...","difficulty":"easy|medium|hard","category":"..."}}"""


def load_chunks_from_qdrant():
    """从 Qdrant 读取所有 chunks，按 source 分组"""
    client = QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )
    collection = "knowledge_base"

    docs_by_source = defaultdict(list)
    offset = None
    while True:
        result = client.scroll(
            collection_name=collection,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_offset = result
        for point in points:
            payload = point.payload or {}
            source = payload.get("metadata", {}).get("source", "unknown")
            content = payload.get("page_content", "")
            if content:
                docs_by_source[source].append(content)

        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        f"从 Qdrant 读取了 {sum(len(v) for v in docs_by_source.values())} chunks，"
        f"{len(docs_by_source)} 个文档"
    )
    return docs_by_source


def generate_qa_for_document(llm, source: str, chunks: list[str], count: int = 5) -> list[dict]:
    """为单个文档生成 QA 对"""
    # 合并 chunks（取前 3000 字符作为代表内容）
    full_text = "\n".join(chunks)
    if len(full_text) > 3500:
        full_text = full_text[:3500] + "\n...（文档较长，已截取核心内容）"

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            (
                "human",
                """请基于以下文档内容生成 {count} 条问答对。

文档名称：{source}
文档内容：
{content}

请生成 {count} 条问答对，覆盖 easy/medium/hard 不同难度。""",
            ),
        ]
    )

    messages = prompt.format_messages(
        count=count,
        source=source,
        content=full_text,
    )

    response = llm.invoke(messages)
    text = response.content.strip()

    items = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line in ("```json", "```", "```jsonl"):
            continue
        try:
            item = json.loads(line)
            required = {"question", "ground_truth", "source_doc", "difficulty", "category"}
            if required.issubset(item.keys()):
                item["source_doc"] = source  # 强制使用真实文档名
                items.append(item)
            else:
                logger.warning(f"字段不全，跳过: {item.keys()}")
        except json.JSONDecodeError:
            continue
    return items


def main():
    llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        temperature=0.3,
    )

    # 1. 从 Qdrant 加载
    docs_by_source = load_chunks_from_qdrant()

    # 2. 对每个文档生成 QA
    all_qa = []
    # 按文档大小排序，大的文档生成更多 QA
    doc_sizes = [(src, len(chunks)) for src, chunks in docs_by_source.items()]
    doc_sizes.sort(key=lambda x: -x[1])

    for i, (source, chunk_count) in enumerate(doc_sizes):
        # 根据 chunk 数量决定生成几条
        if chunk_count >= 20:
            count = 8
        elif chunk_count >= 10:
            count = 6
        elif chunk_count >= 5:
            count = 4
        else:
            count = 2

        chunks = docs_by_source[source]
        logger.info(f"[{i+1}/{len(doc_sizes)}] {source} ({chunk_count} chunks, 生成 {count} 条)")

        try:
            qa_items = generate_qa_for_document(llm, source, chunks, count)
            all_qa.extend(qa_items)
            logger.info(f"  → 生成 {len(qa_items)} 条")
        except Exception as e:
            logger.warning(f"  → 生成失败: {e}")

    # 3. 编号并输出
    output_path = Path("eval/dataset.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, item in enumerate(all_qa, 1):
            ordered = {
                "id": f"{i:03d}",
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "source_doc": item["source_doc"],
                "source_page": 1,
                "difficulty": item["difficulty"],
                "category": item["category"],
            }
            f.write(json.dumps(ordered, ensure_ascii=False) + "\n")

    logger.info(f"\n完成！共生成了 {len(all_qa)} 条 QA 对")
    logger.info(f"已保存到 {output_path}")

    # 统计
    from collections import Counter

    cats = Counter(item["category"] for item in all_qa)
    diffs = Counter(item["difficulty"] for item in all_qa)
    logger.info(f"类别分布: {dict(cats)}")
    logger.info(f"难度分布: {dict(diffs)}")


if __name__ == "__main__":
    main()
