# 📚 企业级 RAG + Agent 智能问答系统

> 基于混合检索 + ReAct Agent + LLM-as-Judge 反思的完整 RAG 系统。
> 面试时可逐行讲清原理的**手写版** Agent 实现。

---

## ✨ 特性

| 特性 | 说明 |
|---|---|
| 🔍 **混合检索** | BM25 + 向量检索 + RRF 融合，Recall@10 0.72 → 0.91 |
| ⚡ **Reranker 精排** | BGE-Reranker 对粗排结果重排；**GPU (RTX A1000) 0.2s，42x 加速** |
| 🤖 **手写 ReAct Agent** | 不依赖 LangChain AgentExecutor，80 行核心循环 |
| 💬 **多轮对话** | Redis 记忆 + Query 改写（"那病假呢？" → "病假几天？"） |
| 🧠 **LLM-as-Judge 反思** | 自动评估答案质量，低于阈值触发重写 |
| 🛡️ **HITL 人机协作** | 高风险操作（send_email）需人工审批 |
| 📊 **Langfuse 可观测** | 全链路 trace，Agent 决策路径可解释 |
| 📈 **Ragas 评测** | Faithfulness / Relevancy / Precision / Recall |

---

## 🏗️ 项目结构

```
├── app/                  # FastAPI + Streamlit 入口
│   ├── api.py            # REST API 端点
│   ├── config.py         # pydantic-settings 配置
│   └── logger.py         # loguru 日志
├── core/                 # 核心逻辑
│   ├── pipeline.py       # RAG Pipeline 编排
│   ├── react_agent.py    # 手写 ReAct Agent（核心 80 行循环）
│   ├── agent.py          # MultiTurnAgent（Agent + 记忆）
│   ├── reflection.py     # LLM-as-Judge 反思评估器
│   ├── hitl.py           # HITL 守卫
│   ├── retriever.py      # BM25 + 向量混合检索
│   ├── reranker.py       # BGE Reranker 精排
│   ├── generator.py      # DeepSeek LLM 调用
│   ├── embedder.py       # BGE Embedding
│   ├── chunker.py        # 文档智能切分
│   ├── parser.py         # 文档解析（PDF/DOCX/TXT）
│   ├── memory.py         # Redis 多轮记忆
│   ├── tools.py          # Agent 工具集
│   └── observability.py  # Langfuse 观测封装
├── eval/                 # 评测
│   ├── dataset.jsonl     # QA 评测数据集
│   ├── run_eval.py       # Ragas 评测脚本
│   ├── meta_evaluation.py# LLM-as-Judge 一致性验证
│   └── reports/          # 评测报告输出
├── tests/                # 96 个单元测试
├── scripts/              # 工具脚本
│   └── ingest.py         # 文档入库脚本
└── docs/                 # 文档
    └── react_agent_tool_exception_postmortem.md  # 故障复盘
```

---

## 🚀 快速开始

### 前置条件

- Python 3.10+
- Qdrant 向量数据库（[cloud.qdrant.io](https://cloud.qdrant.io) 免费 1GB）
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com)）
- Redis（可选，用于多轮对话：[redis.io](https://redis.io)）
- **NVIDIA GPU**（可选，精排加速 42 倍；CPU 也能跑）

### 安装

```bash
# 1. 克隆仓库
git clone <your-repo-url> && cd rag

# 2. 虚拟环境
python -m venv venv
source venv/Scripts/activate  # Windows Git Bash
# . venv/bin/activate         # Linux / macOS

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

### 文档入库

```bash
python scripts/ingest.py data/raw
```

### 启动服务

```bash
# 终端 1: API 服务
uvicorn app.api:app --reload

# 终端 2: Streamlit UI（可选）
streamlit run app/ui.py
```

### 运行测试

```bash
pytest tests/ -v
# 96 passed ✅
```

---

## 📡 API 文档

| 端点 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 健康检查 |
| `/query` | POST | 单轮 RAG 问答 |
| `/chat` | POST | 多轮对话（带记忆 + 可选 SSE 流式） |
| `/agent/chat` | POST | Agent 多轮对话（ReAct + 工具调用） |
| `/sessions/{user_id}` | GET | 列出用户 session |
| `/sessions/{user_id}/{session_id}` | DELETE | 删除 session |
| `/agent/actions/pending` | GET | 列出待审批的高风险操作 |
| `/agent/actions/{id}/approve` | POST | 批准高风险操作 |
| `/agent/actions/{id}/reject` | POST | 拒绝高风险操作 |

### 核心请求示例

```bash
# RAG 查询
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "如何购票？"}'

# Agent 多轮对话
curl -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "如何购票？", "user_id": "user01"}'
```

---

## 🧠 架构设计

### 系统总览

```mermaid
graph TB
    subgraph Frontend["🌐 前端层"]
        UI[Streamlit UI] --- API[FastAPI REST]
    end

    subgraph Core["🧩 核心逻辑层"]
        direction TB
        RP[RAGPipeline<br/>检索→生成] --> HR[HybridRetriever<br/>BM25 + 向量 + RRF]
        RP --> RR[BGEReranker<br/>精排重排]
        RP --> GEN[Generator<br/>DeepSeek 调用]
        RP --> REF[ReflectionModule<br/>LLM-as-Judge 反思]
        
        MA[MultiTurnAgent<br/>ReAct + 记忆] --> REA[ReActAgent<br/>手写 80 行循环]
        MA --> MEM[MemoryManager<br/>Redis 多轮记忆]
        REA --> TOOLS[Tool Set<br/>5 个原子工具]
        REA --> HITL[HITLGuard<br/>高风险操作审批]
    end

    subgraph Observability["📊 可观测层"]
        LF[Langfuse<br/>全链路 Trace]
    end

    subgraph External["☁️ 外部服务"]
        QD[(Qdrant<br/>向量数据库)]
        DS[DeepSeek API<br/>LLM 调用]
        RD[(Redis<br/>会话缓存)]
        HF[HuggingFace<br/>BGE 模型]
    end

    API --> RP
    API --> MA
    UI --> API
    HR --> QD
    GEN --> DS
    MEM --> RD
    RP -.-> LF
    MA -.-> LF
    REA --> TOOLS

    style Frontend fill:#e1f5fe,stroke:#01579b
    style Core fill:#f3e5f5,stroke:#7b1fa2
    style Observability fill:#fff3e0,stroke:#e65100
    style External fill:#e8f5e9,stroke:#1b5e20
```

### RAG Pipeline 流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant P as RAGPipeline
    participant M as MemoryManager
    participant HR as HybridRetriever
    participant RR as BGEReranker
    participant G as Generator
    participant R as ReflectionModule

    U->>P: 输入问题
    P->>M: 获取 session + 改写 query
    M-->>P: 改写后 query
    
    P->>HR: 混合检索 (BM25 + 向量)
    HR-->>P: 20 个候选项
    
    P->>RR: BGE Reranker 精排
    RR-->>P: Top 5 结果
    
    P->>G: DeepSeek 生成答案
    G-->>P: 初始答案
    
    P->>R: LLM-as-Judge 反思评估
    alt 评分 ≥ 阈值 (7/10)
        R-->>P: ✅ 通过
    else 评分 < 阈值
        R->>G: 触发重写
        G-->>R: 改进后答案
        R-->>P: ✅ 通过（或重试耗尽）
    end
    
    P->>M: 保存对话历史
    P-->>U: 答案 + 来源 + 反思评分
```

### ReAct Agent 循环

```mermaid
graph TB
    START([用户问题]) --> REWRITE[Query 改写<br/>指代消解]
    REWRITE --> LOOP

    subgraph LOOP["🔄 ReAct 循环（最多 5 轮）"]
        direction TB
        T[💭 Thought<br/>思考下一步] --> A[🔧 Action<br/>选择工具]
        A --> AI[📝 ActionInput<br/>构造参数]
        AI --> HITL{HITL 检查<br/>高风险?}
        
        HITL -->|否| EXEC[⚡ 执行工具]
        HITL -->|是| WAIT[⏳ 等待人工审批]
        WAIT --> OBS
        
        EXEC --> OBS[📊 Observation<br/>工具返回结果]
        OBS --> T
    end

    LOOP -->|FinalAnswer| FINAL([✅ 最终答案])
    LOOP -->|超过 5 轮| TIMEOUT([⚠️ 超时降级])
    LOOP -->|LLM 不可用| ERROR([❌ 异常兜底])

    style LOOP fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style FINAL fill:#e8f5e9,stroke:#2e7d32
    style TIMEOUT fill:#fff3e0,stroke:#e65100
    style ERROR fill:#fce4ec,stroke:#c62828
```

### 工具集

```mermaid
graph LR
    subgraph Tools["🧰 5 个 Agent 工具"]
        KB[search_knowledge_base<br/>知识库检索] -->|调 RAGPipeline| QD[(Qdrant)]
        CAL[python_calculator<br/>数学计算] -->|沙箱 eval| RES[计算结果]
        TIME[get_current_time<br/>当前时间] -->|系统时间| NOW[YYYY-MM-DD HH:MM:SS]
        EMAIL[send_email<br/>⚠️ 需 HITL 审批] -->|高风险| WAIT2[等待人工确认]
        WEB[web_search<br/>互联网搜索] -->|模拟| MOCK[模拟结果]
    end

    style Tools fill:#e3f2fd,stroke:#1565c0
```

---

## 🛠️ 技术栈

| 组件 | 技术选型 |
|---|---|
| **LLM** | DeepSeek-Chat (langchain-deepseek) |
| **Embedding** | BAAI/bge-m3 (SentenceTransformer) |
| **Reranker** | BAAI/bge-reranker-v2-m3 (transformers 直连 + GPU FP16) |
| **向量库** | Qdrant Cloud |
| **记忆** | Redis (+ hiredis) |
| **框架** | FastAPI + Streamlit |
| **可观测** | Langfuse |
| **评测** | Ragas |
| **切分** | LangChain RecursiveCharacterTextSplitter + jieba |
| **解析** | Unstructured |

---

## 📊 评测结果

> 基于 `eval/dataset.jsonl`（176 条 QA，5 个主题分类，已剔除知识库不覆盖的 6 条乐高数据）。

| 指标 | 数值 | 说明 |
|------|------|------|
| **Top-1 命中率** | **81.2%** | Reranker 把正确文档排在第一位的比例（176 条） |
| **Top-5 命中率** | **99.4%** | Reranker 前 5 包含正确文档的比例（176 条） |
| **检索覆盖率** | **100%** | 知识库中均存在对应文档（176 条） |
| Faithfulness | — | ⏳ 需 OpenAI 兼容的评估 LLM |
| Answer Relevancy | — | ⏳ 同上 | |

### Meta-evaluation 一致性

> TODO: 跑完 `python -m eval.meta_evaluation` 后填写

| 指标 | 一致率 |
|---|---|
| Score Agreement (|LLM - Human| ≤ 2) | — |
| Hallucination Match | — |

---

## 📸 演示

> TODO: 上传演示视频和 Langfuse 截图

| 视频 | 内容 | 时长 |
|---|---|---|
| 视频 1 | 基础 RAG 问答 + 来源引用 | 30s |
| 视频 2 | Agent 多步工具调用 | 30s |
| 视频 3 | 多轮对话 + Query 改写 | 30s |
| 视频 4 | HITL 高风险操作拦截 | 30s |

---

## 📄 License

MIT
