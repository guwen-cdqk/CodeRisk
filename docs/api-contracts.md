# 接口契约 v0.1

源代码真源：`backend/app/schemas/models.py` 与 `backend/app/services/contracts.py`。
本文的未来接口是实施约定，不代表已实现；HTTP OpenAPI 目前只描述实际路由。
Schema 采用 `extra=forbid`，新增字段须通过接口变更 PR，通知 A/B/D 并更新 fixtures。

## 当前已实现与后续边界

| 范围 | 现状 | 责任人 |
|---|---|---|
| 配置、健康检查、错误封装、公共模型、Protocol | 已实现 | 负责人 |
| 任务/报告 GET | 只读 Mock，`is_mock=true` | 负责人提供基线，A 替换 |
| POST /reviews | 校验请求后返回 501，不创建任务 | A 后续实现 |
| Webhook、GitHub 网络请求、持久化、协调器 | 仅契约 | A |
| Diff、AST、规则与上下文 | 仅契约 | B |
| AI 审查 | 仅契约，无网络调用 | 负责人 |
| Dashboard | 最小 Mock 联调页，统计仅占位 | D 扩展 |

## 公共模型及字段归属

| 模型 | 主要字段、校验 | 填充者 |
|---|---|---|
| PullRequestData | repository=`owner/repo`、number>0、title、base/head_ref、40 位小写 commit SHA、files、fetched_at | A |
| ChangedFile | path、previous_path?、status、patch?、patch_truncated、old/new_content? | A |
| SourceLocation | 仓库相对 POSIX path、side=old/new、start_line>=1、end_line>=start_line | B 定位；负责人验证 AI 输出 |
| ChangedFunction | qualified_name、location、context | B |
| AnalysisResult | head_sha、status=complete/partial/failed、functions、findings、diagnostics | B |
| Finding | category、severity=low/medium/high/critical、location、reason、evidence、suggestion、source=static/ai、rule_id? | B 静态发现；负责人 AI 发现 |
| ReviewReport | task_id UUID、head_sha、status=completed/failed、summary、findings、diagnostics、error?、is_mock、generated_at | 负责人生成，A 在上游失败时生成失败报告 |
| ReviewTask | id UUID、repository、pr_number、head_sha、status、publication_status、error?、created_at、is_mock | A |

SHA 将任务绑定到不可变快照，不能用分支名称代替。A 必须在同一 head/base SHA 获取 Diff 与文件全文，防止分支移动后上下文错位。
Diff 是各 `ChangedFile.patch` 的 unified diff；不再重复储存全局 Diff。
`patch=None` 表示 API 未返回/二进制/不可用；不能当作空变更。截断必须显式标记。
old/new_content 可缺失：修改/新增优先获取 head 全文，删除获取 base 全文；按文件大小预算获取，缺失由 B 输出诊断。
不强制将全仓源码装入模型，诊断无需暴露私有源码。

路径统一不含 `a/`、`b/` 前缀，不允许绝对路径、反斜杠、空段、`.`、`..` 或盘符。
行号从 1 开始，范围含两端。new 对应 head，old 对应 base；删除行使用 old，重命名 old 使用 previous_path。
上下文不能确定时输出诊断，禁止伪造 0 行或随意挂到新文件第一行。
首版只发布 GitHub 总体评论；行级评论为 P2，避免复杂 diff position 映射阻碍闭环。
时间对外序列化为 UTC ISO-8601；目前自动生成时间均带时区，模块不得传入本地无时区时间。

## 模块签名

```python
class GitHubGateway(Protocol):  # A
    async def fetch_pull_request(self, repository: str, number: int) -> PullRequestData: ...
    async def publish_report(self, pr: PullRequestData, report: ReviewReport) -> str: ...

class CodeAnalyzer(Protocol):  # B，纯本地同步输入输出，不做网络 I/O
    def analyze(self, pr: PullRequestData) -> AnalysisResult: ...

class AIReviewer(Protocol):  # 负责人
    async def review(self, task_id: UUID, pr: PullRequestData,
                     analysis: AnalysisResult) -> ReviewReport: ...

class ReviewCoordinator(Protocol):  # A
    async def submit(self, request: CreateReviewRequest) -> ReviewTask: ...
    async def run(self, task_id: UUID) -> None: ...
```

分析器不得读取任意本地仓库路径、执行代码、调用 GitHub/LLM 或写库。
AI Reviewer 不获取 PR、不写库、不发布评论；将 B 的静态发现保留，并对重复项去重。
分析器同步 CPU 工作由 A 在协调器里通过 `run_in_threadpool` 调用，避免阻塞 HTTP 事件循环。
报告 head_sha 必须与输入一致，task_id 必须由协调器传入。

## 数据持久化端口（A）

首版计划使用标准库 sqlite3，无需 ORM 或外部数据库；当前没有实际适配器。
完整签名位于 `ReviewRepository`：

```python
async def create_if_absent(self, task: ReviewTask) -> tuple[ReviewTask, bool]: ...
async def get_task(self, task_id: UUID) -> ReviewTask | None: ...
async def list_tasks(self, limit: int, offset: int) -> TaskList: ...
async def save_task(self, task: ReviewTask) -> None: ...
async def save_report(self, report: ReviewReport) -> None: ...
async def get_report(self, task_id: UUID) -> ReviewReport | None: ...
```

`create_if_absent` 必须依赖数据库唯一约束 `(repository, pr_number, head_sha)` 原子去重，不能先查后插。
GitHub repository 标识由 A 统一大小写后存储。`save_report` 以 task_id upsert，保存报告和任务完成态应在同一事务提交；
A 可在实现中增加事务方法并更新 Protocol。列表按 created_at 降序、id 次序稳定排序。
当前 MockReadStore 只提供读取示例，不声称实现这一持久化端口。

## 任务协调与失败

未来流程（A 负责所有调用和保存；不进入 B/AI 内部实现）：

1. HTTP 提交或验签后的 Webhook → coordinator.submit。
2. GitHubGateway 获取 PR 快照 → 原子创建 queued 任务；相同 head 返回已有任务。
3. 单进程后台执行 run → running → B.analyze → AIReviewer.review。
4. 验证报告 task_id/SHA，先持久化报告与 completed，再请求 GitHub 总体评论。
5. 评论以 task/head 隐藏标记 upsert；成功更新 publication_status=published，失败标记 failed。

任务状态：queued → running → completed/failed。报告保存成功后的评论失败不反转审查完成态，前端显示发布失败并允许单独重试。
Webhook delivery_id 用于事件去重，任务唯一键再防止不同 delivery 重复审查同一 head。
MVP 用单进程后台任务，不引入 Celery/Redis。进程重启可能丢后台工作：A 必须在启动时将遗留 queued/running 标记 failed（INTERRUPTED），提供显式重试方案；不能静默悬挂。
相同 head 的手动重试在 A-04 中明确：复用失败任务 ID，只允许 failed→queued，清空旧 error；已完成任务不重复调用 AI。

可预期模块失败抛 `ModuleError(code, safe_message, status_code=502, retryable=False)`。
示例 code：GITHUB_FORBIDDEN、RATE_LIMITED、UPSTREAM_TIMEOUT、ANALYSIS_FAILED、LLM_INVALID_OUTPUT、PERSISTENCE_ERROR。
部分文件无法分析时返回 partial + diagnostics；整体失败返回 failed + diagnostics，协调器不调用 AI。
报告 failed 必须带 ErrorDetail；completed 不允许带 error。上游获取 PR 失败尚未获得 head 时只返回请求错误，不伪造任务 SHA。
重试只处理网络超时/限流，指数退避、上限 2 次、遵守 Retry-After；4xx 权限及无效模型输出不可无限重试。
错误信息脱敏，不向用户回传完整上游响应、栈、密钥或私有源码。

## HTTP API（D 消费，A 实现）

| 方法与路径 | 当前行为 | 后续正式语义 |
|---|---|---|
| GET /health | 200，status/service/mode | 存活检查，不代表外部依赖就绪 |
| GET /api/v1/tasks?limit=20&offset=0 | Mock TaskList | 真实分页；limit 1–100，offset>=0 |
| GET /api/v1/tasks/{uuid} | Mock ReviewTask / 404 | 真实任务；无此任务 404 |
| GET /api/v1/tasks/{uuid}/report | Mock ReviewReport / 404 | 完成/失败报告；任务存在但未生成报告 409 REPORT_NOT_READY |
| POST /api/v1/reviews | 合法输入 501 NOT_IMPLEMENTED | 输入 repository/pr_number，202 ReviewTask；重复输入返回已有任务 |
| POST /api/v1/webhooks/github | 未注册（404） | A：验证原始字节 HMAC-SHA256 后接收 opened/synchronize/reopened；202；不支持事件 204；签名错误 401 |

错误统一为：

```json
{"error":{"code":"TASK_NOT_FOUND","message":"Task not found","retryable":false},"request_id":"uuid"}
```

实际 422 使用统一消息，不回显输入值。响应头 `X-Request-ID` 对应错误体；服务生成 ID，不信任客户端传入 ID。
OpenAPI 中 404/422/500/501/503 已声明 ErrorResponse；未来新增的 401/409/502 等由 A 随真实接口补齐。
当前无身份认证，仅限本机演示。任何公网部署前由 A/D 明确 Dashboard 认证、Webhook 安全、请求/文件/token 大小限制和数据保留策略；不作为 W4 本机 MVP 的新增强制基础设施。
统计首版由 D 通过分页读取记录在客户端展示，不虚构统计端点；大量历史再另议聚合接口。
