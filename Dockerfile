# syntax=docker/dockerfile:1.7
FROM --platform=$TARGETPLATFORM python:3.9-slim AS base

# 基础优化
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OCR_THREAD_POOL_SIZE=2 \
    UVICORN_WORKERS=1

WORKDIR /app

# 仅复制 requirements，利用缓存
COPY requirements.txt .

# 安装依赖
RUN --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && apt-get purge -y --auto-remove gcc \
    && rm -rf /var/lib/apt/lists/* \
    && find /usr/local/lib/python3.9 -name "__pycache__" -type d -prune -exec rm -rf {} +

# 复制代码
COPY . .

# 容器暴露端口
EXPOSE 6688

# 默认启动命令（可通过 docker run 覆盖）
CMD ["python", "StupidOCR.py"]