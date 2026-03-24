# syntax=docker/dockerfile:1.7
FROM --platform=$TARGETPLATFORM python:3.9-slim AS runtime

# 基础优化
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UVICORN_WORKERS=1 \
    UVICORN_ACCESS_LOG=0 \
    OCR_CPU_THREADS=2 \
    OCR_MODEL_IDLE_SECONDS=300

WORKDIR /app

# 仅复制 requirements，利用缓存
COPY requirements.txt ./

# 安装依赖（优先使用二进制包，避免编译依赖）
RUN pip install --no-cache-dir --prefer-binary --no-compile -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 仅复制运行所需文件，避免把本地数据库和杂项文件带进镜像
COPY StupidOCR.py admin_template.html ./
COPY api ./api

# 容器暴露端口
EXPOSE 6688

# 默认启动命令（可通过 docker run 覆盖）
CMD ["python", "StupidOCR.py"]
