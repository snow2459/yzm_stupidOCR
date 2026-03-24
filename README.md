## 验证码识别
油猴脚本为
[my.js](./my.js)
端口6688
## 运行
python 3.9
```shell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

小内存机器推荐直接这样启动：
```shell
OCR_CPU_THREADS=2 OCR_MODEL_IDLE_SECONDS=300 UVICORN_WORKERS=1 UVICORN_ACCESS_LOG=0 python StupidOCR.py
```

## 环境变量
- `MAX_IMAGE_SIZE`：单张图片最大字节数，默认 5MB。
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`：管理界面账号密码。
- `OCR_CPU_THREADS`：限制 OCR/ONNX 相关线程数，默认 `2`，双核机器建议保持为 `2`。
- `OCR_MODEL_IDLE_SECONDS`：除通用 `image` 模型外，其他 OCR 模型空闲多久后自动释放，默认 `300` 秒，设为 `0` 表示不自动释放。
- `UVICORN_WORKERS`：Uvicorn 进程数。
- `UVICORN_ACCESS_LOG`：是否开启访问日志，默认 `0`，生产环境建议关闭以减少额外开销。
- `TOKEN_DB_PATH`：Token 的 SQLite 文件路径，默认使用项目目录下的 `tokens.db`。

## Token 管理
- Token 仅存储于 SQLite，不再使用 `.token_config.json`。
- 管理页支持为每个 Token 配置每分钟/每小时限流（留空为不限），并提供一键复制。
## docker打包
x64
```shell
docker buildx build --platform linux/amd64 -t stupidocr:x64 .
```

arm
```shell
docker buildx build --platform linux/arm64 -t stupidocr:arm64 .
```

小机器部署示例：
```shell
docker run -d \
  --name stupidocr \
  -p 6688:6688 \
  -e OCR_CPU_THREADS=2 \
  -e OCR_MODEL_IDLE_SECONDS=300 \
  -e UVICORN_WORKERS=1 \
  -e UVICORN_ACCESS_LOG=0 \
  -e TOKEN_DB_PATH=/data/tokens.db \
  -v /opt/stupidocr-data:/data \
  stupidocr:x64
```

## 优化说明
- 通用 `image` 模型在启动时预热并常驻，避免最常用接口出现空闲后的冷启动。
- 其他 OCR 模型按需加载，空闲后自动释放，适合偶发请求的小型云主机。
- 图片大小在 base64 解码前先做估算校验，避免超大请求瞬间吃掉过多内存。
- Docker 镜像只复制运行必需文件，不再把本地数据库、脚本和说明文件带进镜像。
