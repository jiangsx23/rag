# 🛠️ 企业级 RAG + Agent 系统 — 操作手册

> 适用版本：v0.4.0
> 最后更新：2026-07-28

---

## 目录

1. [环境准备](#1-环境准备)
2. [启动与停止服务](#2-启动与停止服务)
3. [文档管理](#3-文档管理)
4. [运行测试](#4-运行测试)
5. [评测](#5-评测)
6. [日常维护](#6-日常维护)
7. [故障排查](#7-故障排查)
8. [性能优化](#65-性能优化)
9. [附录：命令速查](#8-附录命令速查)

---

## 1. 环境准备

### 1.1 首次搭建

```bash
# 1. 进入项目目录
cd d:\Documents\rag

# 2. 激活虚拟环境
source venv/Scripts/activate          # Git Bash
# 或 venv\Scripts\activate            # CMD

# 3. 安装依赖（首次或依赖变更后）
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 开发依赖（测试/代码质量）
```

### 1.2 环境变量

复制 `.env.example` 为 `.env`，填入真实 Key：

```bash
cp .env.example .env
```

| 变量 | 必填 | 用途 | 获取地址 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | LLM 调用 | https://platform.deepseek.com |
| `QDRANT_URL` | ✅ | 向量数据库 | https://cloud.qdrant.io |
| `QDRANT_API_KEY` | ✅ | 向量数据库认证 | 同上 |
| `LANGFUSE_PUBLIC_KEY` | ❌ | 可观测性（可选） | https://langfuse.com |
| `LANGFUSE_SECRET_KEY` | ❌ | 可观测性（可选） | 同上 |
| `REDIS_URL` | ❌ | 多轮记忆（可选） | 本地或云服务 |

> ⚠️ **安全提醒**：`.env` 已加入 `.gitignore`，不会提交到 Git。
> 但本地磁盘上存有真实 Key，项目演示完毕后建议去各平台轮换密钥。

### 1.3 验证环境

```bash
python -c "from app.config import settings; print('DeepSeek:', settings.DEEPSEEK_API_KEY[:8]+'...' if settings.DEEPSEEK_API_KEY else '未配置'); print('Qdrant:', settings.QDRANT_URL if settings.QDRANT_URL else '未配置')"
```

### 1.4 GPU 环境（可选但强烈推荐）

如果机器有 NVIDIA GPU，安装 CUDA 版 PyTorch 可加速精排 **42 倍**：

```bash
# 查看 GPU 信息
nvidia-smi

# 安装 CUDA 版 PyTorch（以 CUDA 12.8 为例）
# 下载 torch 2.11.0+cu128 安装包后：
pip install torch-2.11.0+cu128-cp311-cp311-win_amd64.whl --force-reinstall --no-deps
pip install torchvision-0.26.0+cu128-cp311-cp311-win_amd64.whl --force-reinstall --no-deps
pip install torchaudio-2.11.0+cu128-cp311-cp311-win_amd64.whl --force-reinstall --no-deps

# 国内推荐阿里云镜像下载
# https://mirrors.aliyun.com/pytorch-wheels/cu128/
```

验证 GPU 是否可用：

```bash
python -c "import torch; print(f'torch: {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"
```

---

## 2. 启动与停止服务

### 2.1 本地开发模式（推荐）

需要两个终端：

```bash
# 终端 1：启动 API 服务（必须）
cd d:\Documents\rag
source venv/Scripts/activate
uvicorn app.api:app --reload --host 0.0.0.0 --port 8000
```

```bash
# 终端 2：启动 Streamlit UI（可选）
cd d:\Documents\rag
source venv/Scripts/activate
streamlit run app/ui.py --server.port 8501
```

**验证服务是否启动：**

```bash
# API 健康检查
curl http://localhost:8000/health
# 预期响应：
# {"status":"ok","version":"0.3.0","pipeline_ready":false,"langfuse_enabled":false}

# 浏览器访问
# API 文档: http://localhost:8000/docs
# Streamlit UI: http://localhost:8501
```

> `--reload` 模式下，代码修改后自动热重载，不需要手动重启。

### 2.2 Docker 部署模式

```bash
# 启动全部服务（API + Streamlit + Qdrant + Redis）
docker-compose up -d

# 仅启动 API + Qdrant + Redis（不带 Streamlit）
docker-compose up -d api qdrant redis

# 查看日志
docker-compose logs -f api

# 停止服务
docker-compose down

# 停止并删除数据卷（清空 Qdrant 和 Redis 数据）
docker-compose down -v
```

如果只需启动 Redis（用于本地开发，API 在本地跑）：

```bash
# 单独启动 Redis
docker run -d --name rag-redis -p 6379:6379 redis:7-alpine redis-server --appendonly yes
```

### 2.3 停止服务

```bash
# 本地模式
# 按 Ctrl+C 停止 uvicorn 和 streamlit 进程

# Docker 模式
docker-compose down
```

---

## 3. 文档管理

### 3.1 添加新文档

**三步完成：**

```
第 1 步：放入文件         第 2 步：入库            第 3 步：更新 QA（可选）
data/raw/                python scripts/          python eval/
  分类文件夹/                ingest.py              generate_qa_from_docs.py
```

**详细操作：**

```bash
# 第 1 步：把文件放到对应分类文件夹
# data/raw/
# ├── 1-产品与服务详情/          ← 票务、酒店、邮轮、餐饮、商品、设施介绍
# ├── 2-运营流程与标准作业程序/   ← 客诉处理、退款政策、操作流程
# ├── 3-特殊情况与应急预案/      ← 紧急情况处理
# ├── 4-客户关系与支持话术/      ← 客户服务话术
# └── 5-内部知识与工具/          ← 员工培训、操作手册、岗位职责

# 第 2 步：入库到 Qdrant（⚠️ 会清空重建）
cd d:\Documents\rag
source venv/Scripts/activate
TRANSFORMERS_OFFLINE=1 python scripts/ingest.py

# 第 3 步：重启 API 服务
# 按 Ctrl+C 停止 uvicorn，重新启动
uvicorn app.api:app --reload
```

**支持的文档格式：**

| 格式 | 解析方式 | 说明 |
|---|---|---|
| `.pdf` | pdfplumber | 支持表格提取，扫描件不支持 |
| `.docx` | python-docx | Word 文档，含段落和表格 |
| `.doc` | python-docx | 旧版 Word，部分文件兼容 |
| `.pptx` | python-pptx | PowerPoint，提取所有 slide 文字 |
| `.txt` | 直接读取 | 支持 UTF-8/GBK 编码自动检测 |
| `.md` | 同 .txt | Markdown 文件 |
| `.html` | BeautifulSoup | 提取纯文本 |
| `.ppt` | ❌ 不支持 | 旧版二进制 PPT 格式 |
| `.jpeg/.png` | ❌ 不支持 | 图片，需 OCR 方案 |

### 3.2 增量追加（保留原有数据）

修改 `scripts/ingest.py`，把清空逻辑改为检测追加：

```python
# 原代码（清空重建）：
if client.collection_exists(collection_name):
    client.delete_collection(collection_name)  # ← 注释掉这行

# 改为：如果 collection 已存在，跳过创建步骤
if not client.collection_exists(collection_name):
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE),
    )
```

然后只对新文档执行向量化 + 写入。

### 3.3 查看已入库的文档

```bash
python -c "
from app.config import settings
from qdrant_client import QdrantClient
c = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
info = c.get_collection('knowledge_base')
print(f'向量总数: {info.points_count}')
print(f'向量维度: {info.config.params.vectors.size}')
"
```

### 3.4 清空知识库

```bash
python -c "
from app.config import settings
from qdrant_client import QdrantClient
c = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
c.delete_collection('knowledge_base')
print('Qdrant collection 已删除')
"
```

---

## 4. 运行测试

### 4.1 快速测试（推荐）

```bash
cd d:\Documents\rag
source venv/Scripts/activate

# 全量单元测试（约 12 秒）
pytest tests/ -v

# 只看失败项
pytest tests/ --tb=short -q
```

### 4.2 含集成测试

集成测试需要真实 DeepSeek/Qdrant 服务，首次加载较慢（>35s）：

```bash
pytest tests/ -v -m integration

# 或跑全部（含 integration）
pytest tests/ -v
```

### 4.3 测试覆盖率

```bash
pytest tests/ --cov=core --cov=app --cov-report=term-missing
```

### 4.4 仅跑某个模块

```bash
pytest tests/test_retriever.py -v          # 混合检索测试
pytest tests/test_generator.py -v          # LLM 生成测试
pytest tests/test_react_agent.py -v        # ReAct Agent 测试
pytest tests/test_memory.py -v             # 多轮记忆测试
pytest tests/test_reflection.py -v         # 反思评估测试
pytest tests/test_hitl.py -v              # HITL 测试
```

---

## 5. 评测

### 5.1 运行 Ragas 评测

```bash
cd d:\Documents\rag
source venv/Scripts/activate

# 全量评测（基于 eval/dataset.jsonl）
python -m eval.run_eval

# 快速验证（只跑前 5 条）
python -m eval.run_eval --sample 5

# 结果输出到 eval/reports/
```

### 5.2 运行 Meta-evaluation

```bash
python -m eval.meta_evaluation
# 输出 eval/reports/meta_evaluation.json
```

### 5.3 生成 QA 数据集

基于 Qdrant 中已入库的文档重新生成：

```bash
python eval/generate_qa_from_docs.py
# 输出 eval/dataset.jsonl
```

生成虚构 QA（不依赖现有文档，用于快速填充）：

```bash
python eval/generate_qa.py --count 60
# 输出 eval/new_qa.jsonl（需手动审核后合并）
```

---

## 6. 日常维护

### 6.1 查看日志

```bash
# 实时日志（API 启动中的终端输出）
# 日志文件
ls logs/
tail -f logs/error_2026-07-17.log
```

### 6.2 API 端点速查

| 方法 | 路径 | 说明 | 是否需要服务 |
|---|---|---|---|
| GET | `/health` | 健康检查 | ✅ API |
| POST | `/query` | 单轮 RAG 问答 | ✅ API + Qdrant |
| POST | `/chat` | 多轮对话 RAG | ✅ API + Qdrant + Redis（可选） |
| POST | `/agent/chat` | Agent 多轮对话 | ✅ API + Qdrant + DeepSeek |
| GET | `/sessions/{user_id}` | 列出用户会话 | ✅ API + Redis |
| DELETE | `/sessions/{user_id}/{sid}` | 删除会话 | ✅ API + Redis |
| GET | `/agent/actions/pending` | 待审批的高风险操作 | ✅ API |
| POST | `/agent/actions/{id}/approve` | 批准高风险操作 | ✅ API |
| POST | `/agent/actions/{id}/reject` | 拒绝高风险操作 | ✅ API |

### 6.3 健康检查

```bash
# API 服务
curl http://localhost:8000/health

# Qdrant（Docker）
curl http://localhost:6333/health

# Streamlit UI
curl http://localhost:8501/_stcore/health

# Redis（Docker）
docker exec rag-redis redis-cli ping
# 预期响应：PONG

# Redis（本地安装）
redis-cli ping
```

### 6.4 更新依赖

```bash
pip install -r requirements.txt -U
pip install -r requirements-dev.txt -U
```

### 6.5 性能优化

系统各阶段的典型耗时分布（RAG 问答，GPU 加速后）：

| 阶段 | GPU 耗时 | CPU 耗时 | 说明 |
|------|----------|----------|------|
| 混合检索 | ~1.2s | ~1.2s | Qdrant 云服务延迟，本地部署可降至 ~0.1s |
| **精排** | **~0.5s** | **~9.2s** | 🚀 **GPU FP16 推理，~18x 加速** |
| LLM 生成 | ~2.0s | ~2.0s | DeepSeek API 调用 |
| 反思评估 | ~1.5s | ~1.5s | LLM-as-Judge 二次校验 |
| **总计** | **~5.5s** | **~14s** | |

#### v0.4.0 已完成的优化

| 改动 | 效果 |
|------|------|
| 重排器从 MiniCPM (11GB) 换为 **bge-reranker-v2-m3** (2.2GB) | 磁盘 -80%，预热从抛异常到 2s |
| GPU FP16 推理（RTX A1000 Laptop 4GB） | 精排从 9.2s → **0.5s** |
| BGE-M3 嵌入模型走 CPU | 避免与重排器争 4GB 显存（55x 减速 bug） |
| `FlagReranker` → `transformers` 直连 | 消除 MiniCPM 配置不兼容 bug |
| CUDA 上下文预初始化 | 消除 sentence-transformers segfault |
| `HF_HUB_OFFLINE=1` 离线模式 | huggingface.co 不可达时免超时 50s+ |
| PyTorch CPU 版 → CUDA 2.11.0+cu128 (阿里云镜像) | 打通 GPU 推理全链路 |
| `time.time()` → `time.perf_counter()` | 修复 NTP 跳变导致的虚假 30s 耗时 |

**当前评测数据（176 条 QA）：**

| 指标 | 数值 |
|------|------|
| Top-1 命中率（Reranker 首位准确率） | **81.2%** |
| Top-5 命中率（Reranker 前 5 召回率） | **99.4%** |
| 检索覆盖率 | **100%** |

#### 后续优化方向

| 方案 | 说明 | 预估收益 |
|------|------|----------|
| 本地 Qdrant | 从云 Qdrant 切到 docker 本地部署 | retrieve 1.2s → ~0.1s |
| 结果缓存 | 对相同 query 缓存检索 + 精排（TTL 60s） | 高频场景显著 |
| 反思并行 | 生成阶段预初始化 ReflectionModule | 0.3-0.5s |
| 减少候选数 | 粗排 10 → 5 | 精排减半 |

---

## 7. 故障排查

### 7.1 API 启动失败

**症状：** `uvicorn app.api:app --reload` 报错

```
检查清单：
□ 虚拟环境已激活？（source venv/Scripts/activate）
□ 依赖已安装？（pip install -r requirements.txt）
□ .env 文件存在且格式正确？
□ 端口 8000 未被占用？
```

**端口被占用：**
```bash
# 查看谁占用了端口
netstat -ano | findstr :8000
# 找到 PID 后
taskkill /PID <PID> /F
```

### 7.2 Qdrant 连接失败

**症状：** API 启动日志中出现 `Qdrant connection failed`

```
□ QDRANT_URL 和 QDRANT_API_KEY 在 .env 中配置正确？
□ Qdrant 实例在运行？（访问 https://cloud.qdrant.io 查看）
□ 网络能访问外网？
```

### 7.3 HuggingFace 模型加载失败

**症状：** `scripts/ingest.py` 运行时连接 huggingface.co 超时

```
原因：国内网络无法访问 HuggingFace
解决方法：
  1. 设置镜像：export HF_ENDPOINT=https://hf-mirror.com
  2. 或使用离线模式（模型需已缓存）：
     TRANSFORMERS_OFFLINE=1 python scripts/ingest.py
  3. 或使用代理：export https_proxy=http://127.0.0.1:7890
```

### 7.4 检索返回空结果

```
□ Qdrant 中有数据？（python scripts/check_qdrant.py）
□ 文档已入库？
□ 搜索的 query 是否与文档语言一致？
□ Pipeline 的 BM25 索引是否已加载？（重启 API）
```

### 7.5 DeepSeek API 报错

```
□ DEEPSEEK_API_KEY 未过期？
□ API 账户余额充足？
□ 网络能访问 api.deepseek.com？
```

### 7.6 Redis 连接失败

不是致命错误，系统会降级为内存 session（重启后丢失）：

```
□ Redis 服务是否启动？（docker ps | findstr redis）
□ REDIS_URL 配置正确？
```

---

## 8. 附录：命令速查

### 常用命令卡

```bash
# === 开发 ===
source venv/Scripts/activate              # 激活虚拟环境
uvicorn app.api:app --reload              # 启动 API
streamlit run app/ui.py                   # 启动 UI

# === 文档入库 ===
TRANSFORMERS_OFFLINE=1 python scripts/ingest.py  # 入库全部文档

# === 测试 ===
pytest tests/ -v                          # 跑测试
pytest tests/ -m integration              # 跑集成测试
pytest tests/test_xxx.py -v              # 跑单个测试文件

# === 评测 ===
python -m eval.run_eval                   # Ragas 评测
python -m eval.run_eval --sample 5        # 快速评测
python -m eval.meta_evaluation            # 评估器一致性验证
python eval/generate_qa_from_docs.py      # 重新生成 QA 数据集

# === Docker ===
docker-compose up -d                      # 启动全部
docker-compose down                       # 停止

# === 工具 ===
python -c "from app.config import settings; print(settings)"  # 查看配置
```

### 文件结构

```
rag/
├── app/              # API + UI（启动入口）
├── core/             # 核心逻辑（RAG/Agent/反思/工具）
├── eval/             # 评测（QA 数据集 + 评测脚本）
├── tests/            # 单元测试（69 个）
├── scripts/          # 工具脚本（文档入库）
├── docs/             # 文档（操作手册/架构图/知识索引）
├── data/raw/         # 原始文档（按分类放在 5 个文件夹）
├── monitoring/       # Prometheus 监控配置
└── venv/             # Python 虚拟环境
```
