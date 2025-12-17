# Repository Guidelines

本仓库提供基于 FastAPI 与 DDDDOCR 的验证码识别与 Token 管理服务，默认监听 6688 端口，包含 Web 管理页与油猴脚本配合使用。

## Project Structure & Module Organization
- `StupidOCR.py`：主服务入口，封装 OCR、Token 管理、限流与管理接口。
- `admin_template.html`：管理后台 HTML 模板，由后端渲染注入。
- `my.js`：油猴脚本入口，前端侧调用接口。
- `requirements.txt`：运行依赖列表；`Dockerfile`：多平台镜像构建脚本。
- `tokens.db`：默认 SQLite 存储，开发期可留在本地，提交前请忽略或清理敏感数据。

## Build, Test, and Development Commands
- Python 3.9+：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 本地运行（单进程）：`python StupidOCR.py`；多进程可设 `UVICORN_WORKERS=2`.
- Docker 构建：`docker buildx build --platform linux/amd64 -t stupidocr:x64 .`；ARM64：`docker buildx build --platform linux/arm64 -t stupidocr:arm64 .`.
- 管理页 `http://127.0.0.1:6688/admin`，登录凭据由 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 环境变量控制。

## Coding Style & Naming Conventions
- 遵循 PEP8，4 空格缩进；函数/变量使用 `snake_case`，常量全大写。
- 代码注释与文档保持中文，接口使用 FastAPI 的声明式路由与 Pydantic 模型。
- 复用公共工具函数（如 Token/限流相关）避免重复逻辑，新增接口保持返回格式与现有 JSON 结构一致。

## Testing Guidelines
- 当前无自动化测试，提交前至少执行关键手工验证：
  - 识别接口：`curl -X POST http://127.0.0.1:6688/api/ocr/image -H "token:<token>" -H "Content-Type: application/json" -d '{"img":"<base64>"}'`
  - 管理流程：登录 /admin，新建/编辑/删除 Token，并验证限流配置是否生效。
- 若补充自动化测试，推荐以 `tests/` 目录组织，并标注需要的样例图片或基准 base64 数据。

## Commit & Pull Request Guidelines
- 提交信息遵循已有历史的 Conventional Commits 风格，如 `feat(admin): ...`、`fix(ocr): ...`。
- PR 描述应包含变更目的、涉及的接口/配置、测试结果（含手工验证步骤）；前端或管理界面改动请附关键截图。
- 避免提交本地生成的 `tokens.db` 及包含敏感凭据的文件，确保环境变量与样例值分离。

## Security & Configuration Tips
- 环境变量：`MAX_IMAGE_SIZE` 控制图片上限，`TOKEN_DB_PATH` 控制 SQLite 路径，部署前根据资源限制合理调整。
- 不要将真实 Token、管理员账号密码硬编码或写入仓库；必要时使用 `.env`/CI Secret 注入。
- 对外暴露时建议置于反向代理后，限制管理接口访问源，并监控限流命中情况。 
