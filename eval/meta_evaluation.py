"""Meta-evaluation: 验证 LLM-as-Judge 评估器与人工标注的一致性

用法：
    python -m eval.meta_evaluation

流程：
    1. 加载人工标注的 20 条样本（覆盖高质量、幻觉、无引用等场景）
    2. 用 ReflectionModule 对每条样本做评估
    3. 对比 LLM 评分 vs 人工评分，计算一致率
    4. 输出报告到 eval/reports/meta_evaluation.json
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.logger import logger
from core.reflection import ReflectionModule, AnswerEvaluation


# 人工标注的评测样本（20 条）
# 注意：这些是示例数据，建议替换为你自己的标注
HUMAN_LABELED = [
    # ---- 高质量答案（应有高评分） ----
    {
        "question": "公司年假几天？",
        "answer": "10 个工作日。[来源: 员工手册.pdf, p.5]",
        "sources": ["年假 10 个工作日。"],
        "human_score": 9,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "如何申请报销？",
        "answer": "通过 OA 系统提交报销申请，附发票和审批单。[来源: 财务制度.pdf]",
        "sources": ["通过OA系统提交报销申请，附发票和审批单"],
        "human_score": 9,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "加班费怎么算？",
        "answer": "工作日加班 1.5 倍，周末 2 倍，法定节假日 3 倍。[来源: 薪酬制度.pdf]",
        "sources": ["工作日加班1.5倍，周末2倍，法定节假日3倍"],
        "human_score": 10,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    # ---- 幻觉答案（应有低评分 + hallucination=True） ----
    {
        "question": "公司年假几天？",
        "answer": "年假 30 天。",
        "sources": ["年假 10 个工作日。"],
        "human_score": 2,
        "human_is_hallucination": True,
        "human_is_acceptable": False,
        "category": "hallucination",
    },
    {
        "question": "加班费怎么算？",
        "answer": "加班费统一按 1.2 倍计算。",
        "sources": ["工作日加班1.5倍，周末2倍，法定节假日3倍"],
        "human_score": 1,
        "human_is_hallucination": True,
        "human_is_acceptable": False,
        "category": "hallucination",
    },
    {
        "question": "如何申请报销？",
        "answer": "直接找财务经理签字即可。",
        "sources": ["通过OA系统提交报销申请，附发票和审批单"],
        "human_score": 2,
        "human_is_hallucination": True,
        "human_is_acceptable": False,
        "category": "hallucination",
    },
    # ---- 无引用答案（质量可接受但缺来源） ----
    {
        "question": "公司年假几天？",
        "answer": "10 个工作日。",
        "sources": ["年假 10 个工作日。"],
        "human_score": 6,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "no-citation",
    },
    {
        "question": "加班费怎么算？",
        "answer": "工作日 1.5 倍，周末 2 倍，节假日 3 倍。",
        "sources": ["工作日加班1.5倍，周末2倍，法定节假日3倍"],
        "human_score": 7,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "no-citation",
    },
    # ---- 不完整答案（有引用但缺关键信息） ----
    {
        "question": "公司数据保留政策？",
        "answer": "客户数据保留 5 年。[来源: 数据安全规范.pdf]",
        "sources": ["客户数据保留5年，员工数据保留至离职后3年，日志数据保留1年"],
        "human_score": 5,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "incomplete",
    },
    {
        "question": "服务器故障应急响应流程？",
        "answer": "通知运维组长。[来源: 技术运维手册.pdf]",
        "sources": ["1. 通知运维组长 2. 判断故障等级 3. 启动预案 4. 每30分钟通报 5. 复盘"],
        "human_score": 4,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "incomplete",
    },
]

# 再加 10 条随机样本凑到 20 条
_EXTRA = [
    {
        "question": "公司培训有哪些？",
        "answer": "新员工入职培训和技术内部分享。[来源: 培训手册.pdf]",
        "sources": ["新员工入职培训、技术内部分享、管理能力提升培训"],
        "human_score": 8,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "如何重置 OA 密码？",
        "answer": "点击登录页的'忘记密码'，通过邮箱重置。[来源: IT服务指南.pdf]",
        "sources": ["登录页点击'忘记密码'，通过绑定的企业邮箱接收重置链接"],
        "human_score": 9,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "年度调薪什么时候？",
        "answer": "每年 4 月。",
        "sources": ["每年4月进行年度调薪，基于上年度绩效评估结果"],
        "human_score": 6,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "incomplete",
    },
    {
        "question": "如何进行代码审查？",
        "answer": "代码审查必须由 2 名高级工程师进行。",
        "sources": ["必须有至少1名高级工程师审查"],
        "human_score": 3,
        "human_is_hallucination": True,
        "human_is_acceptable": False,
        "category": "hallucination",
    },
    {
        "question": "公司技术栈是什么？",
        "answer": "Python/FastAPI 和 React。[来源: 技术架构文档.pdf]",
        "sources": ["后端Python/FastAPI，前端React/TypeScript，数据库PostgreSQL + Redis"],
        "human_score": 8,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "如何申请远程办公？",
        "answer": "通过 OA 提交申请，每次最长连续 3 个工作日。[来源: 员工手册.pdf]",
        "sources": ["通过OA提交远程办公申请，每次最长连续3个工作日"],
        "human_score": 9,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "high-quality",
    },
    {
        "question": "转岗需要什么条件？",
        "answer": "在当前岗位满 1 年即可申请。",
        "sources": ["在当前岗位工作满1年后可申请转岗，需经当前部门和目标部门负责人同意"],
        "human_score": 5,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "incomplete",
    },
    {
        "question": "安全漏洞怎么报告？",
        "answer": "发邮件给安全团队。[来源: 信息安全手册.pdf]",
        "sources": ["通过安全专用邮箱报告，禁止在公开渠道讨论，安全团队承诺48小时内响应"],
        "human_score": 7,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "no-citation",
    },
    {
        "question": "灾备方案是怎样的？",
        "answer": "两地三中心架构，RTO≤4 小时。[来源: 技术架构文档.pdf]",
        "sources": ["两地三中心架构，RTO≤4小时，RPO≤1小时，每季度进行一次灾备演练"],
        "human_score": 7,
        "human_is_hallucination": False,
        "human_is_acceptable": True,
        "category": "no-citation",
    },
    {
        "question": "信息安全红线有哪些？",
        "answer": "禁止共享账号密码、禁止在公网传输未加密数据。",
        "sources": ["禁止将账号密码共享给他人、禁止将敏感数据下载到个人设备、禁止在公网传输未加密数据"],
        "human_score": 6,
        "human_is_hallucination": False,
        "human_is_acceptable": False,
        "category": "incomplete",
    },
]
HUMAN_LABELED.extend(_EXTRA)


def run_meta_evaluation():
    """运行 Meta-evaluation，输出一致性报告"""
    logger.info("Initializing ReflectionModule for meta-evaluation...")
    reflection = ReflectionModule()

    score_matches = 0
    hallucination_matches = 0
    acceptable_matches = 0
    total = len(HUMAN_LABELED)

    print("\n" + "=" * 70)
    print(f"{'Category':<25} {'Human Score':<12} {'LLM Score':<12} {'Result'}")
    print("=" * 70)

    for sample in HUMAN_LABELED:
        result: AnswerEvaluation = reflection.evaluate(
            sample["question"],
            sample["answer"],
            sample["sources"],
        )

        score_match = abs(result.score - sample["human_score"]) <= 2
        hallucination_match = result.is_hallucination == sample["human_is_hallucination"]
        acceptable_match = result.is_acceptable == sample["human_is_acceptable"]

        if score_match:
            score_matches += 1
        if hallucination_match:
            hallucination_matches += 1
        if acceptable_match:
            acceptable_matches += 1

        mark = "✅" if (score_match and hallucination_match and acceptable_match) else "⚠️"
        print(
            f"{sample['category']:<25} "
            f"{sample['human_score']:<12} "
            f"{result.score:<12} "
            f"{mark}"
        )

    print("=" * 70)
    print(f"\n📊 Meta-evaluation Report ({total} samples)")
    print("-" * 40)
    print(f"  Score Agreement (|LLM - Human| ≤ 2):     {score_matches}/{total} = {score_matches/total:.0%}")
    print(f"  Hallucination Match:                     {hallucination_matches}/{total} = {hallucination_matches/total:.0%}")
    print(f"  Acceptable Match:                        {acceptable_matches}/{total} = {acceptable_matches/total:.0%}")
    print("-" * 40)

    # 保存报告
    report = {
        "total_samples": total,
        "score_agreement_rate": score_matches / total,
        "hallucination_agreement_rate": hallucination_matches / total,
        "acceptable_agreement_rate": acceptable_matches / total,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }
    report_path = Path("eval/reports/meta_evaluation.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"Report saved to {report_path}")

    # 返回关键指标供后续使用
    return report


if __name__ == "__main__":
    run_meta_evaluation()
