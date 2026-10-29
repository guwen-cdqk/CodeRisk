# A-01：GitHub PR 快照适配器

范围：仅实现 `GitHubGateway.fetch_pull_request(repository, number)`，不装配 HTTP 提交入口，
不实现 Webhook、数据库、协调器或评论。公共 Schema、Protocol 和已有 HTTP 错误处理保持原样。

## 使用与配置

`app.integrations.github.GitHubGateway` 接收由调用方管理生命周期的 `httpx.AsyncClient`。
认证头由服务端装配层设置，禁止从提交审查的请求中接收 Token、允许列表或 API URL。
构造后不能通过 `fetch_pull_request` 修改允许列表或预算。空允许列表拒绝全部请求。

```python
from app.core.config import Settings
from app.integrations.github import GitHubGateway, GitHubLimits

# 在持有 AsyncClient 的服务端装配层使用；本示例不发送请求。
def make_gateway(client):
    settings = Settings()
    return GitHubGateway(
        client,
        allowed_repositories=settings.github_allowed_repositories,
        limits=GitHubLimits(),
    )
```

环境变量 `CODERISK_GITHUB_ALLOWED_REPOSITORIES` 是 JSON 数组，例如 `["demo/repo"]`。
默认 `[]`；`.env.example` 不包含真实授权仓库或凭证。仓库按 owner/name 校验并忽略大小写匹配。
API 主机固定为 `https://api.github.com`，不读取响应中的下载 URL，不跟随重定向。
文件路径经过公共路径验证并编码 URL 保留字符。

`GitHubLimits` 是构造时注入的可信配置，当前安全默认值如下，不是已经批准的 Q-03 产品预算：

| 配置 | 默认值 | 行为 |
|---|---:|---|
| max_files | 299 | 所有变更文件的上限，不能设到 300 或以上 |
| max_file_bytes | 1,000,000 | 每侧 UTF-8 源码字节数，不截断全文 |
| max_source_bytes | 8,000,000 | 所有已读取 old/new 全文的总字节数 |
| max_response_bytes | 8,000,000 | 每次 HTTP 响应解压后的字节数，流式限额 |
| request_timeout | 10 秒 | httpx 网络阶段超时 |
| snapshot_timeout | 60 秒 | 包括所有请求的异步总时间预算 |

文件数另受 compare API 的硬边界约束。其他值可由装配层覆盖；队列、模型上下文和分析线程预算不在 A-01 内。
没有自动重试；`retryable=True` 供后续调用方决定是否重试。适配器不会关闭注入的客户端，
但会在成功、超限、超时等退出路径关闭本次响应流，并保留调用方取消信号。

## 快照如何证明一致

1. 先读取 PR 元数据，验证仓库、编号、base/head SHA 和文件总数。
2. 按每页 100 个文件读取 PR 文件列表，拒绝重复路径、数量不符和超限。
3. 请求固定 SHA 的 `compare/{base_sha}...{head_sha}` 第一页。
   对照完整的路径、旧路径、状态、blob SHA、增删统计和 patch，不依赖首尾 SHA 相等就宣称一致。
   顺序可以不同，内容必须逐项相同；因此页间混入其他快照或 SHA 来回变化也不能蒙混通过。
4. 要求 compare 的 base_commit 和 merge_base_commit 都等于声明的 base_sha。
5. Python 文件按固定 SHA 取全文；跨 `.py` 扩展名重命名也读取两侧。
   网关要求文件类型、长度和 blob 元数据，校验解码长度、Git blob 哈希以及文件列表中对应侧的 blob。
6. 最后再读取 PR 元数据，确认 base/head、分支名与文件总数没有变化，返回合法 `PullRequestData`。
   `fetched_at` 是最后检查完成时的 UTC 时间。返回后 PR 仍可能继续变化；返回数据始终绑定已验证的 SHA。

GitHub PR Diff 是三点比较，旧侧通常来自 merge-base，而不一定是 PR 元数据中的最新 base。
当前公共约定要求 old_content 对应 base_sha，A-01 不擅自重定义字段。
因此 merge-base 不等于 base 的合法 PR 会明确失败为 `GITHUB_UNSUPPORTED_BASE`。
这项支持范围需 B/C Review；扩大支持前应共同确定 Diff 旧侧与 base_sha 的含义。

compare 最多返回 300 个变更文件，无法靠返回恰好 300 项证明没有截断。
本实现保守拒绝 300 个或更多文件；PR 文件分页辅助函数另有 30 页/3000 项硬停止条件。
跨仓库 PR 的 head SHA 若在目标仓库不可读取，也会明确失败，不改抓任意 fork URL。

官方依据（2026-10-29 核对）：

- [PR 三点 Diff 与 merge-base](https://docs.github.com/en/pull-requests/reference/branches#three-dot-and-two-dot-git-diff-comparisons)
- [PR files 分页与 3000 文件上限](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files)
- [固定引用 compare、第一页文件列表与 300 文件上限](https://docs.github.com/en/rest/commits/commits#compare-two-commits)
- [Contents API 的 ref、类型、大小及编码边界](https://docs.github.com/en/rest/repos/contents#get-repository-content)
- [主限流、次级限流的响应信号](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#rate-limit-errors)

## 不完整输入与错误策略

缺失 patch 保留 `None`，不等同于零改动，也不默认标截断。
根据 unified diff 的 hunk 计数不足、或 GitHub additions/deletions 大于实际 patch 增删数，
设置 `patch_truncated=True`；这不是对所有服务端截断形式的检测保证。
保留可用部分及完整全文，交由 B 根据既有字段产生覆盖诊断，不伪造完整 Diff。
无法合法解释的 patch 直接失败。

added 的 old_content、removed 的 new_content 为 `None`，表示这一侧按状态不存在，不会发请求。
实际请求返回 404 时整体失败，不能填 `None` 或空字符串；合法空文件返回 `""`。
没有 Python 文件的 PR 仍返回文件元数据与 Diff，全文为 None；是否可分析由 B 判断。

| 条件 | ModuleError code | status_code / retryable |
|---|---|---|
| 输入仓库或编号非法 | INVALID_REPOSITORY / INVALID_PR_NUMBER | 422 / false |
| 仓库不在服务端允许列表 | REPOSITORY_NOT_ALLOWED | 403 / false |
| GitHub 401 / 权限 403 | GITHUB_FORBIDDEN | 403 / false |
| 403 限流信号 / 429 | GITHUB_RATE_LIMITED | 429 / true |
| GitHub 404 | GITHUB_NOT_FOUND | 404 / false |
| 请求或整体超时 | GITHUB_TIMEOUT | 504 / true |
| 连接、读取等请求异常 | GITHUB_CONNECTION_ERROR | 502 / true |
| 其他 HTTP 错误，包括重定向 | GITHUB_HTTP_ERROR | 502 / 仅 5xx 为 true |
| base 与 Diff 起点不同 | GITHUB_UNSUPPORTED_BASE | 502 / false |
| 文件列表与固定 SHA 不符 / 最终元数据变化 | GITHUB_SNAPSHOT_CHANGED | 502 / true |
| 数量不符或重复、达到分页硬上限 | GITHUB_INCOMPLETE_FILES | 502 / 数量不符可重试 |
| 文件数量、单文件、源码总量、响应总量超限 | GITHUB_FILE_LIMIT / GITHUB_FILE_TOO_LARGE / GITHUB_SOURCE_LIMIT / GITHUB_RESPONSE_TOO_LARGE | 502 / false |
| 非法或缺失必需 API 字段 | GITHUB_INVALID_RESPONSE | 502 / false |
| 不支持的状态 / renamed 缺旧路径 | GITHUB_UNSUPPORTED_STATUS / GITHUB_MISSING_PREVIOUS_PATH | 502 / false |
| 文件类型、长度或哈希不符 / 内容缺失 | GITHUB_CONTENT_UNAVAILABLE | 502 / false |
| 非 Base64 或非 UTF-8 | GITHUB_UNSUPPORTED_ENCODING | 502 / false |
| Base64 损坏 | GITHUB_INVALID_BASE64 | 502 / false |

所有消息为固定安全文本，不回显上游 body、认证信息或源码。无法安全取得必需全文时整体失败，
不通过截断源码假称可做完整 AST 分析。此策略保守，未来若引入逐文件失败诊断须与 B/C 协商。
学习阶段辅助函数仍保留其直接调用的 ValidationError / 缺旧路径 ValueError 行为；
对外 fetch_pull_request 将可预期失败统一为 ModuleError。

## 测试与交付边界

`test_github.py` 保留原有 10 个测试实例；`test_github_gateway.py` 使用合成 MockTransport fixture，
覆盖端到端快照、失败路径与资源边界；不依赖 GitHub Token 或真实网络。
依赖锁文件用 uv 基于现有版本约束重新解析，httpx 转为运行依赖后全部版本保持不变。
干净环境按锁文件安装后，还需构建本地 `./backend` 并执行 pip check、测试与安装包导入检查。

真实授权仓库验收未执行；Q-01 身份/权限和 Q-03 预算仍待团队确认。
合并前请 B Review 路径、Diff 起点与缺失标记，C Review 公共模型兼容性，D Review 依赖与 CI。
本模块没有实现 publish_report，因此仅交付 Protocol 的 A-01 fetch 部分；不提前伪造 A-04 行为。

2026-10-29，Windows / Python 3.12.15 本次实际验证：

- 接手基线 25 项通过；最终主环境与干净环境均为 128 passed，新增 103 个测试实例。
- 仅有项目既存的 Starlette/httpx 弃用警告，没有新增失败或被跳过的用例。
- `ruff check backend` 通过，`ruff format --check backend` 在清理构建产物后为 13 files already formatted。
- 主环境与干净环境的 `pip check` 均为 No broken requirements found。
- 干净环境按锁文件安装，再用 `uv pip install --no-deps ./backend` 构建本地包；
  隔离导入检查确认模块来自已安装的包，且安装元数据将 httpx 声明为运行依赖。
- 本次所改文档的本地文件链接与 `git diff --check` 通过。

以上为离线与安装验证结果，不代表 GitHub 真实权限、Docker、远程 CI 或跨模块闭环已经验收。
