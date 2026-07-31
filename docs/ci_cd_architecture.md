# CI/CD 架构设计

> 本项目使用 GitHub Actions + Docker + GHCR 实现完整的 CI/CD 流水线。

## 🏗️ 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          GitHub Repository                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐    │
│  │  Developer    │     │  Pull        │     │  Main        │    │
│  │  Branch       │────▶│  Request     │────▶│  Branch      │    │
│  │  (feature/x)  │     │  (review)    │     │  (stable)    │    │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘    │
│         │                    │                    │             │
│         ▼                    ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  GitHub Actions                          │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │                                                           │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │  │
│  │  │   CI    │  │   CD    │  │ Review  │  │ Deps    │    │  │
│  │  │ Pipeline│  │Pipeline │  │  Auto   │  │ Update  │    │  │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘    │  │
│  │       │            │            │            │          │  │
│  └───────┼────────────┼────────────┼────────────┼──────────┘  │
│          │            │            │            │              │
└──────────┼────────────┼────────────┼────────────┼──────────────┘
           │            │            │            │
           ▼            ▼            ▼            ▼
      ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐
      │ Lint + │   │ Docker │   │Auto    │   │Weekly  │
      │ Test + │   │ Build +│   │Comment │   │PR      │
      │ Build  │   │ Deploy │   │Check   │   │Update  │
      └────────┘   └────────┘   └────────┘   └────────┘
           │            │
           ▼            ▼
      ┌────────────────────────────────────┐
      │       GitHub Container Registry     │
      │   (ghcr.io/yourname/rag-agent)     │
      └────────────────┬───────────────────┘
                       │
                       ▼
      ┌────────────────────────────────────┐
      │       Production Deployment        │
      │   Railway / Vercel / 自建 K8s      │
      └────────────────────────────────────┘
```

---

## 📦 工作流清单

本项目配置了 **4 个 GitHub Actions 工作流**：

| # | 工作流 | 文件 | 触发时机 | 主要功能 |
|---|---|---|---|---|
| 1 | **CI** | `ci.yml` | push / PR | Lint + Test + Build |
| 2 | **CD** | `cd.yml` | push to main | Docker 构建 + 部署 |
| 3 | **Code Review** | `code-review.yml` | PR open/update | 自动 review 检查 |
| 4 | **Dependency Update** | `dependency-update.yml` | 每周一 | 自动更新依赖 |

---

## 🔍 工作流 1：CI Pipeline

**触发条件**：任何 push 或 PR 到 `main` / `develop` 分支

### 6 个 Job 并行执行

```
┌──────────────────────────────────────────────────────────┐
│                       CI Pipeline                          │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │   Lint   │  │   Test   │  │ Security │  │  Build   │ │
│  │          │  │          │  │          │  │          │ │
│  │ • Ruff   │  │ • pytest │  │ • Truffle│  │ • wheel  │ │
│  │ • Black  │  │ • cov 70%│  │   Hog    │  │ • sdist  │ │
│  │ • Mypy   │  │ • matrix │  │ • Safety │  │          │ │
│  │          │  │   3.10/  │  │          │  │          │ │
│  │          │  │   3.11/  │  │          │  │          │ │
│  │          │  │   3.12   │  │          │  │          │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│       │             │             │             │        │
│       └─────────────┴─────────────┴─────────────┘        │
│                          │                                │
│                          ▼                                │
│                  ┌──────────────┐                          │
│                  │ CI Status    │                          │
│                  │ Summary      │                          │
│                  └──────────────┘                          │
└──────────────────────────────────────────────────────────┘
```

### 关键特性

| 特性 | 作用 |
|---|---|
| **矩阵测试** | 在 Python 3.10/3.11/3.12 上并行测试 |
| **覆盖率门禁** | `--cov-fail-under=70`，低于 70% 失败 |
| **Codecov 集成** | 自动上传覆盖率报告 |
| **并发取消** | 新 push 自动取消旧 build |
| **集成测试隔离** | 仅 main 分支跑，PR 不消耗 API 额度 |
| **安全扫描** | TruffleHog 检测密钥泄露 + Safety 漏洞扫描 |

### 快速失败机制

```
Job 1 (Lint) ─┐
              ├─ 任何一个失败 → 整个 CI 失败 → 不能 merge
Job 2 (Test) ─┤
              │
Job 3 (Sec) ──┤
              │
Job 4 (Build) ┘
```

---

## 🚀 工作流 2：CD Pipeline

**触发条件**：push 到 `main` 分支（且代码有变更）

### 4 个 Job 顺序执行

```
push to main
     │
     ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Build     │───▶│   Deploy    │───▶│  Health     │
│   (镜像)    │    │  (Railway)  │    │  Check      │
│             │    │  (Vercel)   │    │             │
└─────────────┘    └─────────────┘    └─────────────┘
     │                    │
     ▼                    ▼
┌─────────────┐    ┌─────────────┐
│  GHCR       │    │  Production │
│  Image      │    │  URL        │
│  (latest)   │    │             │
└─────────────┘    └─────────────┘
```

### 多 Tag 策略

同一个 commit 会推送多个 tag 到 GHCR：

| Tag | 用途 |
|---|---|
| `main-abc1234` | 分支 + 短 SHA（精确追溯） |
| `main` | 分支名（最新代码） |
| `20260101-abc1234` | 日期 + 短 SHA（时间维度） |
| `latest` | main 分支专用（默认） |

### 镜像优化

| 优化 | 效果 |
|---|---|
| **多阶段构建** | 最终镜像 ~800MB（vs 完整 ~2GB） |
| **非 root 用户** | 安全性提升 |
| **健康检查** | Docker 自动重启不健康容器 |
| **多平台构建** | linux/amd64 + linux/arm64 |
| **Buildx 缓存** | 二次构建速度提升 5-10 倍 |

### 部署目标

| 服务 | 平台 | 用途 |
|---|---|---|
| **Railway** | 后端 API | https://your-app.up.railway.app |
| **Vercel** | Streamlit 前端 | https://your-app.vercel.app |
| **GHCR** | 镜像仓库 | ghcr.io/yourname/rag-agent |

---

## 🤖 工作流 3：Code Review Automation

**触发条件**：PR 创建/更新

### 自动检查项

1. **PR 规模警告** — 超过 1000 行提示拆分
2. **自动标签** — 根据变更文件自动打标签
3. **PR 描述检查** — 检查必填项（变更说明、测试方法、截图）
4. **规模分级** — XS / S / M / L / XL 标签

### PR 描述模板

提交 PR 时应包含：

```markdown
## 变更说明
- 实现了 X 功能
- 修复了 Y bug

## 测试方法
- 跑 `pytest tests/`
- 手动测试步骤 1, 2, 3

## 截图/录屏
（如果涉及 UI 变更）
```

---

## 📦 工作流 4：Dependency Update

**触发条件**：每周一上午 9:00

自动跑 `pur` 检测过时依赖，如有更新自动创建 PR：

```bash
# 手动触发
gh workflow run dependency-update.yml
```

---

## 🐳 Docker 镜像架构

### 多阶段构建

```dockerfile
# Stage 1: Builder (~1.2GB)
FROM python:3.10-slim AS builder
RUN apt-get install gcc g++  # 编译工具
RUN pip install -r requirements.txt  # 安装依赖

# Stage 2: Runtime (~800MB)
FROM python:3.10-slim AS runtime
COPY --from=builder /root/.local /root/.local  # 只复制 site-packages
USER appuser  # 非 root 安全
HEALTHCHECK ...  # 健康检查
```

### 镜像层优化

| 优化 | 效果 |
|---|---|
| `--no-cache-dir` | 不缓存 pip 包 |
| 合并 `RUN` 命令 | 减少层数 |
| `.dockerignore` | 排除无用文件 |
| 多阶段构建 | 排除编译工具 |

### `.dockerignore`

```gitignore
.git
.github
tests
docs
*.md
.env
__pycache__
*.pyc
.pytest_cache
.coverage
.venv
venv
logs
data/raw/*
!data/raw/.gitkeep
```

---

## 📊 监控体系（可选）

通过 `--profile monitoring` 启动：

```bash
docker-compose --profile monitoring up -d
```

### Prometheus + Grafana

| 组件 | 端口 | 作用 |
|---|---|---|
| **Prometheus** | 9090 | 时序数据库，采集指标 |
| **Grafana** | 3000 | 可视化面板 |
| **AlertManager** | 9093 | 告警通知 |

### 内置告警规则

| 告警 | 触发条件 | 严重程度 |
|---|---|---|
| `HighAPILatency` | P95 延迟 > 5s | warning |
| `HighErrorRate` | 5xx 错误率 > 5% | critical |
| `ServiceDown` | 服务下线 > 2min | critical |
| `QdrantHighMemory` | Qdrant 内存 > 1.5GB | warning |

---

## 🔐 必要的 GitHub Secrets

在仓库 Settings → Secrets → Actions 中配置：

| Secret | 用途 | 必需 |
|---|---|---|
| `GITHUB_TOKEN` | 自动提供 | ✅ 自动 |
| `DEEPSEEK_API_KEY_TEST` | CI 测试用 | ⭐ 测试 |
| `QDRANT_URL_TEST` | 测试 Qdrant | ⭐ 测试 |
| `QDRANT_API_KEY_TEST` | 测试 Qdrant | ⭐ 测试 |
| `LANGFUSE_PUBLIC_KEY_TEST` | 测试观测 | ⭐ 测试 |
| `LANGFUSE_SECRET_KEY_TEST` | 测试观测 | ⭐ 测试 |
| `DEEPSEEK_API_KEY_PROD` | 生产环境 | ⭐⭐ 部署 |
| `QDRANT_URL_PROD` | 生产 Qdrant | ⭐⭐ 部署 |
| `QDRANT_API_KEY_PROD` | 生产 Qdrant | ⭐⭐ 部署 |
| `RAILWAY_TOKEN` | Railway 部署 | ⭐⭐ 部署 |
| `RAILWAY_SERVICE_ID` | Railway 服务 | ⭐⭐ 部署 |
| `VERCEL_TOKEN` | Vercel 部署 | ⭐⭐ 部署 |
| `VERCEL_ORG_ID` | Vercel 组织 | ⭐⭐ 部署 |
| `VERCEL_PROJECT_ID` | Vercel 项目 | ⭐⭐ 部署 |

---

## 🚦 完整流程演练

### 场景：开发者提交新功能

```bash
# 1. 开发者在 feature 分支写代码
git checkout -b feature/add-new-tool
# ... 写代码 ...
git commit -m "feat: add new agent tool"
git push origin feature/add-new-tool

# 2. 在 GitHub 创建 PR
# → 触发 ci.yml + code-review.yml
# → CI 跑：lint + test + security + build
# → 4 个并行 job 跑完后，PR 显示"✅ All checks passed"

# 3. Code Review bot 自动评论：
#    - "✅ PR 描述完整"
#    - 自动打标签：size/M, agent
#    - 自动请求 review

# 4. 团队成员 review 并 approve
# 5. 合并到 main
# → 触发 ci.yml + cd.yml
# → CI 再跑一遍
# → CD 开始：build 镜像 → 推 GHCR → 部署 Railway
# → 健康检查通过 → 新功能上线

# 6. 每周一自动跑 dependency-update.yml
# → 如有新版本 → 自动创建 PR
```

### 时间估算

| 阶段 | 耗时 |
|---|---|
| CI 跑完（lint + test） | 3-5 分钟 |
| CD 构建镜像 | 5-8 分钟 |
| CD 部署 + 健康检查 | 2-3 分钟 |
| **总计** | **10-16 分钟** |

---

## 💰 成本估算

本 CI/CD 体系**完全免费**（针对个人项目）：

| 服务 | 免费额度 |
|---|---|
| **GitHub Actions** | 2000 分钟/月（私有），无限（公开） |
| **GitHub Container Registry** | 无限（公开），500MB（私有） |
| **Railway** | 500 小时/月（$5 免费额度） |
| **Vercel** | 100GB 带宽/月 |
| **Prometheus + Grafana** | 自部署（仅服务器成本） |

---

## 📁 完整文件清单

```
.github/
├── workflows/
│   ├── ci.yml                    # CI Pipeline
│   ├── cd.yml                    # CD Pipeline
│   ├── code-review.yml           # 自动 review
│   └── dependency-update.yml     # 依赖更新
└── ISSUE_TEMPLATE/
    ├── bug_report.md
    └── feature_request.md

monitoring/
├── prometheus.yml                # Prometheus 配置
└── alerts.yml                    # 告警规则

Dockerfile                        # 多阶段构建
docker-compose.yml                # 一键启动
.dockerignore                    # Docker 排除
```

---

## 🎯 简历话术

> 搭建完整的 CI/CD 流水线，基于 GitHub Actions 实现自动化测试、代码质量检查、
> Docker 多阶段构建与多平台镜像推送，并集成 Railway/Vercel 实现自动部署。
> 引入 Prometheus + Grafana 监控体系，覆盖 4 类核心告警规则。

---

## 🚀 快速开始

### 本地跑 CI 检查

```bash
# 跑 lint
ruff check . && black --check .

# 跑测试
pytest tests/ --cov=core --cov=app

# 本地构建镜像
docker build -t rag-agent:dev .

# 本地启动完整服务
docker-compose up -d

# 启动 + 监控
docker-compose --profile monitoring up -d
```

### 第一次部署到生产

```bash
# 1. 在 GitHub 创建仓库并推送代码
git remote add origin https://github.com/yourname/rag-agent-system.git
git push -u origin main

# 2. 配置 Secrets（见上表）
# 3. 在 Railway 创建服务，连接 GitHub 仓库
# 4. 在 Vercel 导入项目，配置环境变量
# 5. 推送任意 commit 到 main，自动触发部署
```

---

**CI/CD 是 Top 1 项目的标配**，面试时能讲清楚"代码怎么从开发到生产"是非常加分的。
