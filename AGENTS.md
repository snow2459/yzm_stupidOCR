# Repository Guidelines

## 项目结构与模块组织
- `StupidOCR.py` 为核心 FastAPI 服务，封装验证码识别、Token 校验与管理路由，进程入口直接运行此文件即可启动。
- `admin_template.html` 为管理端模板，`my.js` 为油猴脚本示例；`.token_config.json` 运行后生成，存储 Token，已加入 `.gitignore`。
- `requirements.txt` 记录运行依赖；`Dockerfile` 提供多架构构建；根目录无额外子包，便于快速定位。

## 构建、测试与本地开发
- Python 3.9 推荐：`python3 -m venv .venv && source .venv/bin/activate`，再执行 `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`。
- 本地启动：`python StupidOCR.py`（默认 0.0.0.0:6688），开发时可用 `uvicorn StupidOCR:app --reload --port 6688` 便于热重载。
- Docker 多架构：`docker buildx build --platform linux/amd64 -t stupidocr:x64 .`；ARM64 同理。
- 环境变量：`MAX_IMAGE_SIZE`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`、`UVICORN_WORKERS`；修改时同步在 README 说明。

## 代码风格与命名约定
- 遵循 PEP8，4 空格缩进；FastAPI 路由函数使用小写下划线；Pydantic 模型用帕斯卡命名。
- 常量大写蛇形（如 `APP_VERSION`），配置读取集中于文件顶部；注释与文档字符串统一中文，简洁说明意图。
- 首选类型注解与显式错误处理，避免裸 `except`；保持线程池、OCR 实例为模块级单例。

## 测试指南
- 目前无自动化测试，提交前至少做手工验证：1) 管理端登录 `POST /api/admin/login`，确认会话与 Token CRUD 正常；2) 核验 `X-Token` 保护的 OCR 路由。
- 示例请求：`curl -X POST http://127.0.0.1:6688/api/ocr/image -H "Content-Type: application/json" -H "X-Token: <token>" -d '{"img_base64":"<base64>"}'`，确认返回结果合理。
- 注意图片体积不超过 `MAX_IMAGE_SIZE`，滑块接口需同时传 `gapimg_base64` 与 `fullimg_base64`。

## 提交与合并规范
- 历史记录采用类 Conventional Commits（如 `feat(ocr): ...`，`chore(docker): ...`），新增提交沿用此格式，前缀与作用域尽量准确。
- PR 建议包含：变更目的与范围、受影响的接口/配置、手工验证步骤与结果；如改动管理页面或前端脚本，请附截图或交互说明。
- 确保不提交 `.token_config.json`、本地虚拟环境、IDE 目录等敏感或冗余文件。

## 安全与配置提示
- 默认管理员账号 `ADMIN_USERNAME/ADMIN_PASSWORD` 请在部署前覆盖；Token 文件权限保持 600，避免泄露。
- 开放跨域已允许全部来源，若内网部署建议按需收紧；生产环境请置于受控网段或前置网关。
