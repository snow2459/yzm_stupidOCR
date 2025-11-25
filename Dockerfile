# syntax=docker/dockerfile:1.7
FROM --platform=$TARGETPLATFORM python:3.9-slim AS base

# 基础优化
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UVICORN_WORKERS=1

WORKDIR /app

# 仅复制 requirements，利用缓存
COPY requirements.txt .

# 安装依赖（仅使用二进制包，避免编译依赖）
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --prefer-binary --no-compile -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制代码
COPY . .

# 容器暴露端口
EXPOSE 6688

# 默认启动命令（可通过 docker run 覆盖）
CMD ["python", "StupidOCR.py"]
