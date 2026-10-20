# CodeRisk

基于 Python 代码变更分析的 AI Pull Request 风险审查系统。

**当前是工程骨架：后端和前端可在 Mock 模式运行；真实 GitHub、Diff/AST、AI 调用、任务协调和数据库尚未实现。**

## 团队开发入口

请先阅读 [团队开发指南](docs/team-development-guide.md)。这是项目唯一的综合开发说明，包含：

- 架构和现有文件逐项说明，以及已实现 / 待实现边界。
- A 后端与 GitHub、B 代码分析、C AI 审查、D 前端与测试部署的具体任务。
- 公共模型、Python 接口、HTTP API、输入输出示例和错误约定。
- 每个角色的首个 PR、交付物、单元测试、联调和验收方法。
- 未来五周的参考目标及每周汇报方式，不按固定周次强制执行。

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
