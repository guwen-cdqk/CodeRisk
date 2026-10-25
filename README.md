# CodeRisk

基于 Python 代码变更分析的 AI Pull Request 风险审查系统。

**当前是工程骨架：后端和前端可在 Mock 模式运行；真实 GitHub、Diff/AST、AI 调用、任务协调和数据库尚未实现。**

<a id="documentation"></a>

## 文档导航

文档更新：2026-10-25。已确定方向与待讨论的实现方案分开记录，具体细则以决策记录为准。
建议阅读：**需求 → 架构 → 自己的角色任务**，开发时按需查接口、协作流程和待决议事项。

| 文档 | 主要内容 |
|---|---|
| [项目需求](docs/requirements.md) | 做什么、解决什么问题、怎样使用、首版范围与潜在创新 |
| [架构概览](docs/architecture.md) | 系统组成、数据流、现有文件与待实现模块 |
| [数据与接口](docs/interfaces.md) | 公共模型用途、位置约定、模块调用、HTTP 和共同样例 |
| [开发与协作](docs/development.md) | 开始开发、PR、测试联调、五周参考和部署流程 |
| [决策记录](docs/decisions.md) | 已确定方向、待讨论的接口/预算/部署方案 |
| [A：后端与 GitHub](docs/roles/a-backend-github.md) | PR 获取、Webhook、任务协调、存储与评论 |
| [B：代码分析](docs/roles/b-code-analysis.md) | Diff、AST、函数定位、静态规则 |
| [C：AI 审查](docs/roles/c-ai-review.md) | 模型适配、证据校验、质量与架构协调 |
| [D：前端与测试部署](docs/roles/d-frontend-testing.md) | 页面、API、集成/E2E、CI 和部署 |

每条规则只在所属文档维护，角色文档引用公共约定。需求中的“作者/审查者/维护者”是使用角色，A/B/C/D 是开发分工。
拟议方案不代表现有功能，业务代码尚未因此改动。

```text
docs/
├── requirements.md
├── architecture.md
├── interfaces.md
├── development.md
├── decisions.md
└── roles/
    ├── a-backend-github.md
    ├── b-code-analysis.md
    ├── c-ai-review.md
    └── d-frontend-testing.md
```

## 本地启动

要求 Python 3.12+、Node 22.12+、npm。以下 PowerShell 命令在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements-dev.lock
.\.venv\Scripts\python -m pip install --no-deps -e backend
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --reload --no-access-log
```

如果已有 `.env`，跳过复制，保留原有配置。Linux/macOS 将 `.\.venv\Scripts\python` 换成 `.venv/bin/python`。

后端：`http://127.0.0.1:8000`；交互 API：`http://127.0.0.1:8000/docs`。
健康检查 `/health` 应返回 `status=ok` 和 `mode=mock`；不表示外部业务服务已经接入。

另开终端，在仓库根目录执行：

```powershell
cd frontend
npm ci
npm run dev
```

前端：`http://127.0.0.1:5173`。Vite 将 `/api` 请求代理给后端，无须自行放开 CORS。
当前模式不需要任何 API Key。Docker 配置是待实测的开发基线，不是已经验证的生产部署。

## 检查与测试

在仓库根目录执行：

```powershell
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m pytest backend/tests
.\.venv\Scripts\python -m ruff check backend
.\.venv\Scripts\python -m ruff format --check backend
```

在 `frontend/` 执行 `npm run build`，同时检查 TypeScript 和构建。
后端目前有 15 个基础测试；完整浏览器 E2E 和真实业务闭环仍待开发。

## 工程约定

各成员负责自己的单元测试，D 负责集成与 E2E。功能分支通过 PR 合并，至少一名其他成员 Review，CI 通过后合并。
公共模型和接口由 C 协调，变更必须同步通知消费方。
禁止执行待审 PR 中的代码；LLM 输出必须经过结构和证据校验，不能作为正确性保证。
密钥只从环境变量或安全配置提供；个人笔记、环境文件、缓存和打包产物不入库。
