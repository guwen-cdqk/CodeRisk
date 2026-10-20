# CodeRisk

基于代码变更的 Python PR 风险审查系统。
**当前仅为第一阶段工程骨架，默认 Mock 模式；尚未接入 GitHub、真实 Diff/AST、LLM 或持久化数据库。**

## 快速开始

Python 3.12+，Node 22.12+，npm。以下命令在仓库根目录执行；PowerShell 示例：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements-dev.lock
.\.venv\Scripts\python -m pip install --no-deps -e backend
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --reload --no-access-log
```

Linux/macOS 将 `.\.venv\Scripts\python` 换为 `.venv/bin/python`。
`GET http://127.0.0.1:8000/health` 返回 `status=ok, mode=mock`。
打开 `/docs` 查看交互 API，`/openapi.json` 查看契约。
配置从根目录 `.env` 和环境变量读取，环境变量优先；修改配置后重启。

另开终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`。Vite 将 `/api` 转发到本机 8000，无须放开 CORS。
如需其他后端地址，启动前设置 `$env:CODERISK_API_TARGET='http://127.0.0.1:8001'`。
Mock 仅提供一个只读任务，界面始终提示示例用途；不需要任何 API Key。

## 验证

```powershell
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m pytest backend/tests
.\.venv\Scripts\python -m ruff check backend
.\.venv\Scripts\python -m ruff format --check backend
cd frontend
npm ci
npm run build
```

日常开发可 `pip install -e "backend[dev]"`；可复现安装优先使用已提交的 Python 锁定清单和 `package-lock.json`。
升级 Python 依赖由负责人在干净的 Python 3.12 环境更新锁定清单并跑完整 CI。

## 目录与边界

```text
backend/app/
  main.py                应用装配、日志与统一错误
  api/routes/            HTTP 适配，A；不包含分析逻辑
  core/                  配置、公共异常
  schemas/               公共模型，负责人协调变更
  services/              Protocol、只读 Mock；A 后续实现任务协调
  integrations/          A：GitHub 适配器（后续创建）
  analyzers/             B：Diff / AST / 静态规则（后续创建）
  reviewers/             负责人：AI Reviewer（后续创建）
  db/                    A：SQLite 适配器（后续创建）
backend/tests/           各成员提交自己模块的测试
frontend/src/            D：React / TypeScript Dashboard
docs/                   接口契约、开发流程、ADR
.github/                CI、Issue / PR 模板
```

后续目录仅在有实际实现时创建，避免空模块。依赖方向为 HTTP → services → schemas / Protocol；
适配器实现 Protocol，经应用装配注入，禁止 GitHub、分析器与 AI 相互导入内部实现。

## Docker 开发基线

安装 Docker Engine + Compose 后在根目录 `docker compose up --build`。
后端与前端仅映射到宿主机回环地址；前端为开发服务器，**不作为公网生产部署**。
当前不挂数据库卷，因为没有数据库实现。Docker 配置为开发基线，尚未完成容器构建与启动验证。

## 团队入口

- [接口与字段归属](docs/api-contracts.md)
- [开发环境与协作流程](docs/development.md)
- [架构决策](docs/adr/0001-modular-monolith.md)

所有贡献走功能分支与 PR，由至少一名其他成员 Review，CI 通过后合并。

PR 中的文件内容只允许当作数据解析，禁止执行、导入或安装其中代码。
LLM 输出是候选风险提示，必须经过 Schema 与位置校验；不能作为代码正确性保证。
密钥只通过环境变量或安全配置提供；日志、fixtures 与报告不包含凭证。

技术参考：[FastAPI 配置](https://fastapi.tiangolo.com/advanced/settings/)、
[FastAPI 测试](https://fastapi.tiangolo.com/tutorial/testing/)、
[Vite 环境要求](https://vite.dev/guide/)。
