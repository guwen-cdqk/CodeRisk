# A：后端与 GitHub 集成

更新：2026-10-25。
[文档导航](../../README.md#documentation) · [架构](../architecture.md) · [公共接口](../interfaces.md) · [协作流程](../development.md)

## 1. 我负责什么

让系统接上指定 GitHub 仓库，把 B 的分析和 C 的审查组织成任务，保存报告并发布总体评论，向 D 提供 HTTP API。
不实现 B 的 AST/规则，也不编写 C 的模型提示词；通过公共接口调用它们。

## 2. 从哪些文件开始

先读 `backend/app/services/contracts.py`、`schemas/models.py` 和 `api/routes/reviews.py`。
现有 `main.py` 是装配入口，`core/config.py`、`core/errors.py` 提供配置和错误基线。
按需创建 `integrations/github.py`、`api/routes/webhooks.py`、`services/coordinator.py`、`db/sqlite.py` 和对应测试。
这些业务文件尚未实现；不要直接把 MockReadStore 当作正式数据库扩充。

## 3. 当前阶段任务

### A-01 · P0 · PR 快照与允许仓库

实现 GitHubGateway.fetch_pull_request，获取 PR、分页文件、Diff 及所需 Python 全文。服务端校验允许仓库，按固定 SHA 取内容。
输入 repository/number，输出合法 PullRequestData；交付 GitHub 适配器、合成/脱敏 fixture 和离线 HTTP 测试。
验收覆盖新增/删除/重命名、分页、缺失/截断 patch、大小限制、403/404/429 和超时；不能把不可用 Diff 当零改动。

### A-02 · P0 · Webhook 接收

对原始 body 做 HMAC-SHA256 与 constant-time 比较，验证后解析和筛选 PR 事件，持久化 delivery 去重；先接 fake coordinator。
交付事件路由、配置说明和单测。正确事件受理，无关事件忽略，错误/缺失签名或篡改 body 不调用协调器。
重复事件不能重复执行；draft、关闭和 base 更新策略按 [决策记录](../decisions.md)确认。

### A-03 · P0 · 任务协调、存储与查询

实现 ReviewCoordinator/ReviewRepository，注入 gateway、analyzer、reviewer；真实 GET 替换 Mock，POST 接同一 submit/run。
同步分析在线程运行；任务读取固定快照；先保存报告及终态，再发布。B/C 未就绪时使用同接口 fake。
交付 SQLite、协调器、HTTP 和临时数据库测试。验收并发去重、稳定分页、状态转换、404/报告未就绪、事务回滚与依赖失败。
开始前与 C/D 冻结 [Q-05](../decisions.md#q05)中的降级、base/head 和事务端口，不能各自扩展 Schema。

### A-04 · P0 · 评论、重试与中断恢复

实现 publish_report，按 [Q-09](../decisions.md#q09)处理同 PR 汇总、旧结果和网络重试；按 [Q-07](../decisions.md#q07)区分审查与发布重试。
交付发布逻辑、恢复入口及故障测试。发布 403 不丢报告，可能已写入后的超时重试不重复评论，发布重试不调 AI，同任务不并发运行。
单进程重启后将遗留 queued/running 标记 INTERRUPTED，并提供可解释恢复；不承诺自动续跑或可靠队列。

### A-05 · P1 · 稳定性与追踪

完善安全日志、OpenAPI 错误说明、报告元数据和历史查询，配合 D 的故障反馈。
交付故障矩阵与回归；错误可按 request_id 定位，日志不泄漏凭证/原始源码。
基础超时、大小限制、安全错误和有限重试必须随前面的 P0 实现，不全部推迟到此任务。

## 4. 首个 PR

先完成 A-01 的小闭环：给定仓库/编号，经 fake HTTP 返回合法快照并跑通单测。
不同时承担全部 Webhook、数据库和评论；请 B Review 文件内容/路径约定，C Review 公共模型。

## 5. 依赖与协作

向 B 交付固定 SHA 的 patch 与 old/new_content，缺失要说明；向 C 提供 task_id、PR 和分析结果；向 D 提供稳定 DTO/错误结构。
共享文件由 C 协调；部署数据路径和访问保护与 D 对齐。凭证方式、允许仓库、规模/队列和保留策略先查 decisions。

## 6. 自己必须完成的测试

HTTP 离线异常、验签、并发去重、SQLite 事务/重启、调用顺序、分析失败不调 AI、降级与发布失败分别可见。
网络只对可恢复错误有限退避，不无限重试权限错误。数据库失败时不发布假成功。
真实 GitHub 验收使用专用授权仓库，与默认离线测试分开；D 做跨模块测试，不替 A 写这些单测。
