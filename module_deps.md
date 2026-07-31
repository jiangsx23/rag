# 模块依赖图

```mermaid
graph LR
  subgraph app["app"]
    api["api"]
    config["config"]
    logger["logger"]
  end

  subgraph core["core"]
    agent["agent"]
    chunker["chunker"]
    embedder["embedder"]
    generator["generator"]
    hitl["hitl"]
    memory["memory"]
    observability["observability"]
    pipeline["pipeline"]
    react_agent["react_agent"]
    reflection["reflection"]
    reranker["reranker"]
    retriever["retriever"]
    tools["tools"]
    parser["parser"]
  end

  api --> config
  api --> logger
  api --> agent
  api --> hitl
  api --> memory
  api --> observability
  api --> pipeline

  agent --> logger
  agent --> memory
  agent --> observability
  agent --> react_agent
  agent --> reflection
  agent --> tools

  generator --> config
  generator --> chunker

  hitl --> logger

  memory --> config
  memory --> logger

  observability --> config
  observability --> logger

  pipeline --> config
  pipeline --> logger
  pipeline --> embedder
  pipeline --> generator
  pipeline --> memory
  pipeline --> observability
  pipeline --> reflection
  pipeline --> reranker
  pipeline --> retriever
  pipeline --> parser

  react_agent --> config
  react_agent --> hitl
  react_agent --> observability

  reflection --> config
  reflection --> logger
  reflection --> observability

  reranker --> logger

  retriever --> logger

  tools --> api
  tools --> logger

  style app fill:#e1f5fe,stroke:#0288d1
  style core fill:#f3e5f5,stroke:#7b1fa2
```

## 图例

| 颜色 | 包 |
|------|-----|
| 🟦 浅蓝 | `app` — 应用层（API、配置、日志） |
| 🟪 浅紫 | `core` — 核心 RAG + Agent 逻辑 |

## 关键观察

- **`pipeline`** 是 RAG 管线的核心编排者，几乎依赖所有 core 子模块
- **`agent`** 是 Agent 入口，聚合 `react_agent`、`reflection`、`tools`
- **`api`** 作为 FastAPI 入口，依赖 core 层多个模块
- `config` 和 `logger` 是公共基础设施，大量模块依赖它们
- **`tools` → `api`** 的箭头表示工具回调注册了 API 路由（双向耦合）
