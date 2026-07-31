"""Ragas 评测运行脚本

用法：
    python -m eval.run_eval                     # 全量评测
    python -m eval.run_eval --sample 5          # 只跑前 5 条（快速验证）
    python -m eval.run_eval --output my.csv     # 指定输出路径

流程：
    1. 从 eval/dataset.jsonl 加载 QA 数据集
    2. 对每条 question 调用 pipeline.query()，收集 answer + contexts
    3. 调用 Ragas evaluate 计算 faithfulness / answer_relevancy / context_precision / context_recall
    4. 结果写入 eval/reports/
"""
import json
import sys
import argparse
import csv
from pathlib import Path
from datetime import datetime

# 让脚本能找到项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.logger import logger
from core.pipeline import RAGPipeline


def load_dataset(path: str) -> list[dict]:
    """从 JSONL 加载评测数据集"""
    if not Path(path).exists():
        logger.error(f"数据集文件不存在: {path}")
        sys.exit(1)

    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))

    logger.info(f"Loaded {len(data)} eval samples from {path}")
    return data


def main():
    parser = argparse.ArgumentParser(description="Ragas 评测运行脚本")
    parser.add_argument("--dataset", default="eval/dataset.jsonl", help="评测数据集路径")
    parser.add_argument("--sample", type=int, default=None, help="只跑前 N 条（快速验证）")
    parser.add_argument("--output", default=None, help="输出 CSV 路径（默认自动生成）")
    args = parser.parse_args()

    # 1. 加载数据集
    dataset = load_dataset(args.dataset)
    if args.sample:
        dataset = dataset[:args.sample]
        logger.info(f"Sample mode: running on first {args.sample} questions")

    # 2. 初始化 Pipeline
    logger.info("Initializing RAGPipeline...")
    pipeline = RAGPipeline()

    # 3. 逐条查询
    results = []
    for i, item in enumerate(dataset):
        qid = item.get("id", f"{i+1:03d}")
        question = item["question"]
        logger.info(f"[{i+1}/{len(dataset)}] {qid}: {question[:60]}...")

        try:
            result = pipeline.query(question, top_k=5)
            results.append({
                "question": question,
                "answer": result["answer"],
                "contexts": [s["content"] for s in result["sources"]],
                "ground_truth": item["ground_truth"],
            })
        except Exception as e:
            logger.exception(f"Query failed for {qid}: {e}")
            results.append({
                "question": question,
                "answer": f"[ERROR] {e}",
                "contexts": [],
                "ground_truth": item["ground_truth"],
            })

    # 4. Ragas 评测
    logger.info("Running Ragas evaluation...")
    try:
        from ragas import evaluate
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
        )
        from datasets import Dataset

        ds = Dataset.from_list(results)
        scores = evaluate(
            ds,
            metrics=[
                faithfulness,
                answer_relevancy,
                context_precision,
                context_recall,
            ],
        )

        # 打印结果
        print("\n" + "=" * 60)
        print("📊 Ragas Evaluation Results")
        print("=" * 60)
        for metric, value in scores.items():
            print(f"  {metric:<30} {value:.4f}")
        print("=" * 60)

        # 保存 CSV
        output_path = args.output or f"eval/reports/{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        scores_df = scores.to_pandas()
        scores_df.to_csv(output_path, index=False)

        # 同时写一份 latest.csv
        latest_path = "eval/reports/latest.csv"
        scores_df.to_csv(latest_path, index=False)

        logger.info(f"Results saved to {output_path}")
        logger.info(f"Latest symlink: {latest_path}")

    except Exception as e:
        logger.exception(f"Ragas evaluation failed: {e}")
        # 降级：保存原始结果以便后续分析
        fallback_path = f"eval/reports/raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fallback_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"Raw results saved to {fallback_path} (Ragas skipped)")


if __name__ == "__main__":
    main()
