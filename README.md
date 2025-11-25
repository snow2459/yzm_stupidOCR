## 验证码识别
油猴脚本为
[my.js](./my.js)
端口6688
## 运行
python 3.9
```shell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
## 环境变量
- `MAX_IMAGE_SIZE`：单张图片最大字节数，默认 5MB。
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`：管理界面账号密码。
- `UVICORN_WORKERS`：Uvicorn 进程数。
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
