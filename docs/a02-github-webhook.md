# A-02 GitHub Webhook 接收

`POST /api/v1/webhooks/github` 对原始请求体做 HMAC-SHA256 验签，验证通过后解析 UTF-8 JSON。
服务端限制实际读取的请求体为 2 MiB，分块传输也计入。签名、事件、delivery 请求头各应只有一个值。
对需审查的事件校验仓库、PR 编号、base/head SHA，并使用与 A-01 相同的服务端仓库允许列表，大小写不敏感。

## 本地配置

| 配置 | 用途 |
| --- | --- |
| `CODERISK_GITHUB_WEBHOOK_SECRET` | 服务端共享密钥。空或未配置时返回 503 |
| `CODERISK_GITHUB_ALLOWED_REPOSITORIES` | JSON 数组，例如 `["demo/repo"]`。默认空数组，拒绝为任何仓库提交审查 |
| `CODERISK_GITHUB_WEBHOOK_DELIVERY_DB` | 本地开发默认使用仓库根目录下的 `webhook-deliveries.sqlite3`。须使用普通文件路径，配置为 `:memory:` 或 SQLite `file:` URI 时拒绝受理 |

Docker 镜像设置 `/var/lib/coderisk/webhook-deliveries.sqlite3`，目录由 `coderisk` 用户写入。
Docker Compose 将命名卷 `coderisk-data` 挂载到该目录。独立运行容器时，将持久卷挂载到同一目录；
如果使用已有卷或宿主机目录，须确保容器内的 `coderisk` 用户有目录写权限。

真实密钥放在未跟踪的根目录 `.env` 或服务端环境变量里。多个进程应使用同一份持久磁盘上的投递库和任务库，
且两者使用不同的 SQLite 文件。部署、备份和清理投递记录时需要保留受理记录与对应任务的一致性。
当前投递表随首次受理创建，无自动清理策略。

## 事件和响应

- `opened`、`reopened`、`synchronize`、`ready_for_review` 在 PR 打开且为非 Draft 时提交。
- `edited` 还要求前后 base 分支名不同。仅修改标题、正文等返回 204。
- Draft、关闭（含合并）、`ping` 和其他无关事件返回 204。关闭事件无需取消在途任务。

| 状态 | 含义 |
| --- | --- |
| 202 | 任务已持久化受理，且 delivery 记录已提交。相同 delivery 和请求体重投也返回 202。响应体为空 |
| 204 | 事件无需受理 |
| 400 / 401 / 413 / 422 | 请求体传输中断、验签失败、请求体超限、请求头或 JSON/字段非法 |
| 403 | 待审查事件的仓库未获允许 |
| 409 | 相同 delivery ID 已用于另一份需审查的请求体 |
| 503 | 缺少密钥、缺少正式协调器、投递库不可用，或协调器受理失败 |

错误使用统一 `ErrorResponse`，协调器的安全 `ModuleError` 直接传播。默认应用没有生产受理适配器，
对通过验证且需审查的事件返回 `WEBHOOK_COORDINATOR_NOT_CONFIGURED`。离线测试注入 SQLite Fake Coordinator。

## 投递与任务的恢复

1. 投递库使用 `BEGIN IMMEDIATE` 先拿写锁。主键是 `delivery_id`，只保存已受理记录、请求体 SHA-256 和任务 ID。
2. 新投递持有写锁，等待协调器提交任务，再提交 delivery。重复投递在锁释放后查询已受理记录。
   投递锁等待 1 秒，等待超时返回可重试的 503。写锁会串行化同一个投递库中的新任务受理，适用于低流量开发场景。
3. 受理异常、写入失败或进程退出时，未提交的 delivery 事务回滚。协调器的取消超时是 70 秒，
   实际耗时还取决于适配器是否及时响应取消。这是服务端保护上限，生产受理还需满足发送方的响应时限。
4. 任务已提交但 delivery 未提交时，重新投递同一事件，协调器应先按完整快照键找到原任务。若 HTTP 响应发送失败，
   已提交的 delivery 会给重投返回 202。两库之间允许重试带来的重复调用，依靠任务唯一键防止重复创建。

两库均需使用持久磁盘和可靠的文件锁。HMAC 覆盖请求体，请求头依赖 HTTPS 传输完整性。
发生失败后需通过可信的重投流程发送原始事件。A-02 没有部署重投服务或后台执行器。

## 接入 A-03

`create_app(settings, webhook_submitter=...)` 是生产装配位置。A-02 的内部适配器接收 `WebhookSnapshot`，
其中包含 `repository, pr_number, base_sha, head_sha`。A-03 在一次任务库事务中以完整快照键原子查找或持久化任务，
只有新建者才能安排该任务执行。找到原任务时直接复用，即使 PR 后来发生变化。没有原任务时，要核对实际快照与事件 SHA 一致；
快照无法取得或已变化时明确失败，不能把另一个快照当成该投递的任务。返回 `ReviewTask` 前，任务须已提交且能够查询。

现有公共 `CreateReviewRequest` 只有仓库和编号，`ReviewTask` 只有 head SHA，旧 `ReviewRepository` 的去重约定也未包括 base SHA。
现阶段路由能检查返回任务的仓库、编号和 head，base 必须由注入的适配器保证。A-03 需与 C 及消费方确认快照绑定及原子受理方式后再装配，
并让正式查询读到该任务库。评论发布前的状态与快照复核属于 A-04。

离线测试使用两个临时 SQLite 文件，包含进程终止、提交前后失败、重投、快照去重和并发。生产查询、真实 GitHub Webhook
和真实 GitHub/AI 请求尚未联调。
