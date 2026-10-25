# 数据与接口约定

更新：2026-10-25。本文解释接口用途和共同约定，字段与签名以 [models.py](../backend/app/schemas/models.py) 和 [contracts.py](../backend/app/services/contracts.py) 为准。
修改公共接口由 C 协调，A/B/C/D 中受影响的消费方共同评审，同步文档、fixture 和 TS 类型。

## 1. 各模块传什么数据

| 对象 | 含义 | 谁填写，谁使用 |
|---|---|---|
| PullRequestData / ChangedFile | 仓库、PR、base/head、文件 Diff 和必要全文 | A → B/C |
| AnalysisResult / ChangedFunction | 受影响函数、上下文、静态发现和分析限制 | B → A/C |
| Finding / SourceLocation | 问题类型、严重程度、位置、证据、原因、建议、来源 | B/C → 报告/A/D |
| ReviewTask | 一次工作的标识、快照、状态与发布状态 | A → D |
| ReviewReport | 一次工作的最终结果，包含发现、诊断或错误 | C；上游失败由 A 组装 → A/D |
| Diagnostic | 处理限制，例如缺少全文或语法错误，不等于代码风险 | B/C → A/D |
| CreateReviewRequest / TaskList | 提交输入 / 分页查询结果 | D → A / A → D |
| ErrorDetail / ErrorResponse | 安全错误内容 / 带 request_id 的 HTTP 错误 | 模块/A → 调用方/D |

任务在排队时也存在；报告生成前不能伪造空成功结果。`findings=[]` 不代表代码正确或安全。
模型使用 extra=forbid，新增字段必须更新公共模型，不能任意往响应里塞数据。

## 2. 位置、快照与状态的公共规则

1. PR 数据绑定固定 base/head SHA（40 位小写十六进制），不能混用新文件内容与旧 Diff。
2. 路径采用仓库相对 POSIX 格式，例如 `src/payment.py`；去掉 a/b 前缀，禁止绝对路径、盘符、反斜杠、空段或 `..`。
3. 行号从 1 开始，含两端，end_line 不小于 start_line。new 对应 head，old 对应 base；删除行使用 old，重命名旧侧使用 previous_path。
4. patch 是 unified diff，不是完整 Python 文件；None 表示不可用，截断必须说明。没有全文时 B 返回诊断，不假装完整分析。
5. 时间由实现者使用带时区 UTC；当前模型并未全面拒绝外部传入无时区时间，需要保持此约定。
6. 静态与 AI 发现分别标 source=static/ai。C 保留有效静态发现并去重，不能让模型随意抹掉证据。

当前 Task 状态为 queued/running/completed/failed；Report 为 completed/failed，failed 必须有 error，completed 禁止有 error。
发布状态独立为 not_requested/pending/published/failed。评论失败不反转已经保存的审查结果。

“AI 失败保留静态结果并明显降级”已确认，但当前 Schema 不能直接返回 degraded。
拟议状态、任务/报告新增 base_sha、去重及事务接口升级见 [Q-05](decisions.md#q05)；不要把草案当成现有接口。

## 3. Python 模块调用

下面是当前 Protocol 的方法摘要，没有真实实现。详细参数和返回类型直接查 [契约代码](../backend/app/services/contracts.py)。

| Protocol / 方法 | 输入 → 输出 | 实现者与边界 |
|---|---|---|
| GitHubGateway.fetch_pull_request | repository、number → PullRequestData | A，按固定 SHA 获取数据 |
| GitHubGateway.publish_report | PR、报告 → 评论 URL | A，保存完成后发布；评论定位细则待确认 |
| CodeAnalyzer.analyze | PullRequestData → AnalysisResult | B，同步本地解析，不网络请求、不执行代码、不写库 |
| AIReviewer.review | task_id、PR、分析结果 → ReviewReport | C，异步模型 I/O，不获取 PR/创建任务/存库/发布 |
| ReviewCoordinator.submit | CreateReviewRequest → ReviewTask | A，取得快照、去重并安排执行 |
| ReviewCoordinator.run | task_id → 无返回值，保存状态和结果 | A，加载固定快照，调用 B/C |
| ReviewRepository | create_if_absent、get_task、list_tasks、save_task、save_report、get_report | A；当前接口尚不足以明确表达快照保存与原子完成，见 Q-05 |

B 的同步分析由 A 放在线程执行；async 本身不会自动把 CPU 工作变快。装配层可以导入具体实现，其他模块只依赖公共类型和约定。
可预期失败使用 ModuleError，错误 message 必须脱敏。个别文件受限用 partial+diagnostics，整体无法分析用 failed；A 不在整体分析失败后继续调模型。

## 4. HTTP：当前行为与待实现目标

| 路径 | 当前实际行为 | 目标 |
|---|---|---|
| GET /health | 200，status/service/mode | 存活检查，不代表外部依赖就绪 |
| GET /api/v1/tasks?limit=20&offset=0 | 固定 Mock TaskList | 真实分页，limit 1–100、offset≥0，稳定排序 |
| GET /api/v1/tasks/{task_id} | 示例任务或 404 | 真实 ReviewTask；无任务 404 |
| GET /api/v1/tasks/{task_id}/report | 示例报告或 404 | 真实报告；有任务未生成报告时 409 REPORT_NOT_READY |
| POST /api/v1/reviews | 合法请求返回 501 | 输入 repository/pr_number，202 ReviewTask；重复返回已有任务 |
| POST /api/v1/webhooks/github | 未注册，404 | 验签接收；支持事件 202，无关事件 204，错误签名 401 |
| POST /api/v1/tasks/{task_id}/retry | 未注册，404 | 拟议接口，见 [Q-07](decisions.md#q07) |

OpenAPI 虽声明 POST /reviews 的成功模型为 202，目前函数仍返回 501。关闭 Mock 后读取为 503；A 还需替换 store_for 和依赖装配。
目标还包含服务端允许仓库检查；它尚未实现。前端不传令牌或任意抓取 URL。

错误统一为：

```json
{"error":{"code":"TASK_NOT_FOUND","message":"Task not found","retryable":false},"request_id":"00000000-0000-4000-8000-000000000001"}
```

X-Request-ID 响应头与错误体相同，由服务端生成。当前 404/422/500/501/503 已声明此模型，未来 401/403/409/502 等需 A 补齐。
TypeScript 当前是展示字段子集，D 需补 error/diagnostics 和已评审的新状态；不能凭页面需要擅自改字段名。

## 5. 共同样例：没有其他模块也能检查数据格式

以下可在安装后端后运行，只创建合成数据，没有执行待审代码或调用真实服务。
成员按此思路共享 fixture；B 补人工定位，C 补模型响应，D 复用页面数据。不要各维护一套含义不同的同一 PR。
示例对应当前模型；Q-05 接口升级获批后再同步修改。

```python
from uuid import UUID
from app.schemas.models import PullRequestData, AnalysisResult, ReviewReport

pr = PullRequestData(
    repository="demo/coderisk", number=1, title="Example change",
    base_ref="main", head_ref="feature/demo",
    base_sha="b" * 40, head_sha="a" * 40,
    files=[{
        "path": "src/parser.py", "status": "modified",
        "patch": "@@ -1,2 +1,2 @@\n def parse(text):\n-    return text\n+    return eval(text)\n",
        "old_content": "def parse(text):\n    return text\n",
        "new_content": "def parse(text):\n    return eval(text)\n",
    }],
)
analysis = AnalysisResult(
    head_sha=pr.head_sha, status="complete",
    findings=[{
        "category": "unsafe_dynamic_execution", "severity": "high",
        "location": {"path": "src/parser.py", "side": "new", "start_line": 2, "end_line": 2},
        "reason": "Untrusted input may execute code.", "evidence": "return eval(text)",
        "suggestion": "Use a parser for the expected input format.",
        "source": "static", "rule_id": "PY-EVAL-001",
    }],
)
report = ReviewReport(
    task_id=UUID("00000000-0000-4000-8000-000000000001"),
    head_sha=pr.head_sha, status="completed",
    summary="MOCK: synthetic data; no analyzer or LLM called.",
    findings=analysis.findings, is_mock=True,
)
assert ReviewReport.model_validate_json(report.model_dump_json()) == report
print(report.model_dump_json(indent=2))
```
