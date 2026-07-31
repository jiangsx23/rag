# ============================================
# Dockerfile for Enterprise RAG + Agent System
# 多阶段构建，最小化最终镜像体积
# ============================================

# ============================================
# Stage 1: Builder（构建依赖）
# ============================================
FROM python:3.10-slim AS builder

# 元数据
LABEL maintainer="your.email@example.com" \
      version="1.0.0" \
      description="Enterprise RAG + Agent System"

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖（编译 wheels 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖到指定目录
RUN pip install --user --no-warn-script-location -r requirements.txt

# ============================================
# Stage 2: Runtime（运行时）
# ============================================
FROM python:3.10-slim AS runtime

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 健康检查
    curl \
    # 中文支持
    fonts-noto-cjk \
    # 时区数据
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 设置时区（可选）
ENV TZ=Asia/Shanghai

# 从 builder 复制已安装的依赖
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH \
    PYTHONPATH=/app

# 创建非 root 用户（安全）
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

# 复制应用代码
COPY --chown=appuser:appuser ./app /app/app
COPY --chown=appuser:appuser ./core /app/core
COPY --chown=appuser:appuser ./scripts /app/scripts
COPY --chown=appuser:appuser ./data /app/data

# 创建必要目录
RUN mkdir -p /app/logs /app/data/raw /app/data/processed && \
    chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 暴露端口
EXPOSE 8000 8501

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令（默认启动 API，可用 docker-compose 覆盖）
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
