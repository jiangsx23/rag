"""QA 数据集扩充脚本

从 eval/dataset.jsonl 已有的 20 条出发，用 DeepSeek 批量生成新的 QA 对。
覆盖 3 个 category（policy / process / tech）和 3 个 difficulty（easy / medium / hard）。

用法：
    python eval/generate_qa.py          # 生成新 QA（默认 60 条）
    python eval/generate_qa.py --count 80   # 生成指定数量
    python eval/generate_qa.py --output eval/new_qa.jsonl  # 输出到单独文件

生成后请手动审核：
    1. python eval/generate_qa.py
    2. 打开 eval/new_qa.jsonl 过一遍，改掉不合理的问题或答案
    3. 合并到 eval/dataset.jsonl（或直接替换）
"""
import json
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.logger import logger
from langchain_deepseek import ChatDeepSeek
from langchain_core.prompts import ChatPromptTemplate

# ============================================
# 模板：按 category + difficulty 组合生成
# ============================================

SYSTEM_PROMPT = """你是一个企业知识库 QA 数据集生成器。
请生成贴合中国企业场景的问答对。

要求：
1. 问题要真实、具体，像员工会问的
2. 答案要准确、完整，包含具体数字/流程
3. 参考来源文档名要合理（如"员工手册.pdf""信息安全手册.pdf"等）
4. 每批生成 10 条，JSONL 格式，每条一行
5. 只输出 JSONL，不要任何额外文字

输出格式（每条一行 JSON）：
{{"question":"...","ground_truth":"...","source_doc":"...","source_page":数字,"difficulty":"easy|medium|hard","category":"policy|process|tech"}}"""


def generate_batch(llm: ChatDeepSeek, category: str, difficulty: str, count: int = 10) -> list[dict]:
    """用 LLM 生成一批 QA 对"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """请生成 {count} 条 {difficulty} 难度的 {category} 类问答对。

{category_desc}

要求：
- {difficulty_desc}
- 每条包含 question / ground_truth / source_doc / source_page / difficulty / category 字段
- 纯 JSONL 格式，不要序号、不要多余文字"""),
    ])

    category_desc = {
        "policy": "policy（公司制度/政策类）：年假、病假、加班、调薪、报销标准等",
        "process": "process（流程/操作类）：如何申请、如何审批、出差流程、报销步骤等",
        "tech": "tech（技术/规范类）：技术栈、API规范、数据库迁移、部署流程等",
    }
    difficulty_desc = {
        "easy": "简单：答案在文档中可以直接找到，一问一答，不需要推理",
        "medium": "中等：需要从文档中找信息并做简单组合/对比",
        "hard": "困难：需要综合多个文档的信息，或涉及例外条款/边界情况",
    }

    messages = prompt.format_messages(
        count=count,
        category=category,
        difficulty=difficulty,
        category_desc=category_desc.get(category, ""),
        difficulty_desc=difficulty_desc.get(difficulty, ""),
    )

    response = llm.invoke(messages)
    text = response.content.strip()

    # 解析 JSONL
    items = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去掉可能的 markdown 代码块标记
        if line in ("```json", "```", "```jsonl"):
            continue
        try:
            item = json.loads(line)
            # 校验必填字段
            required = {"question", "ground_truth", "source_doc", "difficulty", "category"}
            if not required.issubset(item.keys()):
                logger.warning(f"字段不全，跳过: {item.keys()}")
                continue
            items.append(item)
        except json.JSONDecodeError:
            logger.warning(f"JSON 解析失败，跳过: {line[:80]}...")
            continue

    return items


def main():
    parser = argparse.ArgumentParser(description="QA 数据集扩充")
    parser.add_argument("--count", type=int, default=60, help="生成多少条（默认 60）")
    parser.add_argument("--output", default="eval/new_qa.jsonl", help="输出文件")
    args = parser.parse_args()

    # 读取已有的，避免重复
    existing_path = Path("eval/dataset.jsonl")
    existing_questions = set()
    if existing_path.exists():
        with open(existing_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        item = json.loads(line)
                        existing_questions.add(item["question"])
                    except json.JSONDecodeError:
                        pass
    logger.info(f"已有 {len(existing_questions)} 条 QA")

    llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        temperature=0.7,
    )

    # 按 category × difficulty 分配
    categories = ["policy", "process", "tech"]
    difficulties = ["easy", "medium", "hard"]
    per_batch = max(1, args.count // (len(categories) * len(difficulties)))

    all_new = []
    for cat in categories:
        for diff in difficulties:
            logger.info(f"生成: {cat}/{diff} × {per_batch}")
            items = generate_batch(llm, cat, diff, per_batch)
            # 去重
            for item in items:
                if item["question"] not in existing_questions:
                    all_new.append(item)
                    existing_questions.add(item["question"])

    # 编号并输出
    start_id = len(existing_questions) - len(all_new) + 1
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, item in enumerate(all_new, start=start_id):
            item["id"] = f"{i:03d}"
            # 确保字段顺序友好
            ordered = {
                "id": item["id"],
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "source_doc": item["source_doc"],
                "source_page": item.get("source_page", 1),
                "difficulty": item["difficulty"],
                "category": item["category"],
            }
            f.write(json.dumps(ordered, ensure_ascii=False) + "\n")

    logger.info(f"新增 {len(all_new)} 条，已保存到 {output_path}")
    logger.info(f"请手动审核 {output_path}，确认无误后合并到 eval/dataset.jsonl")


if __name__ == "__main__":
    main()
