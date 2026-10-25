# 开发与协作流程

更新：2026-10-25。[返回导航](../README.md#documentation)。本文只讲怎样开始、协作和联调。
系统结构见 [架构](architecture.md)，数据与调用见 [接口](interfaces.md)，未定方案见 [决策](decisions.md)，个人任务在 roles/。

## 1. 每个人从哪里开始

先读 [需求说明](requirements.md)和架构概览，再进入自己的 [A](roles/a-backend-github.md)、[B](roles/b-code-analysis.md)、[C](roles/c-ai-review.md)、[D](roles/d-frontend-testing.md)文档。
P0 表示首版闭环必需，P1 表示闭环后的增强。先做每人首个小 PR，不一次实现整份任务。

安装、后端/前端启动与基础检查命令集中在 [根 README](../README.md)，不重复维护。
当前无 Key 即可看 Mock；真实模式仍待实现，关闭 Mock 不会自动接通 GitHub/模型。

## 2. 分支、PR 和公共文件

使用功能分支，例如 feat/A-01-github-fetch、feat/B-01-diff、feat/C-01-fake-reviewer、feat/D-01-dashboard。
一个 PR 完成一个可验收任务，关联 Issue，说明行为、失败路径和实际验证。至少一名其他成员 Review，CI 通过后合并。
main 保持稳定；保护规则由管理员配置，文档不代表远程已经开启保护。

公共模型/Protocol/HTTP 字段或错误语义变更，先通知 C 和消费方，再同步代码、接口文档、fixture 和 TS 类型。
A 牵头装配，B/C 提供实现，D 协助联调；依赖、Docker、CI 变更邀请 D。
不要通过删失败断言、隐式换字段或返回假成功解决集成问题。

## 3. 用共同样例逐步联调

1. A 用 fake analyzer/reviewer 验证任务、存储和 HTTP；B 用合成 PR 测解析；C 用人工分析结果测 fake/provider。
2. D 用当前 Mock GET 开发页面，提交先验证 501/fake 受理，不等待外部服务。
3. B 接入协调器，再接 C；A 替换真实读取来源，D 保持相同 DTO。
4. 用 fake 外部服务和真实内部实现做集成/浏览器验证，默认不需要 Key。
5. 在专用授权仓库分别验收真实手动与自动路径，记录模型、环境和结果。

共同数据样例见 interfaces。真实外部验收与离线测试分开，Mock 页面不等于完成业务闭环。

## 4. 测试职责和完成标准

作者负责自己模块单元测试，D 负责页面、跨模块和浏览器验证。具体用例只在各角色文件维护。
后端跑 pytest、Ruff check、Ruff format --check、pip check；前端跑 npm run build 和新增的页面/E2E 命令。
CI 当前只有后端检查和前端构建，新 E2E 由 D 接入；远程结果以对应提交 Actions 为准。

一条最小链路完成时，应能演示：

- 网页手动提交和合法 PR 事件都能创建可查询任务。
- Python 改动定位、3 条规则、结构化 AI 报告能串联。
- 报告先保存，网页能查，GitHub 总体评论能发布。
- 重复输入、不同快照、过时结果、模型降级和发布失败分别可见。
- 干净环境可复现，单测/构建/集成通过，真实与 fake 演示明确区分。

模型是否真正有用还需人工标注对照；不把流程跑通当准确性保证。

## 5. 五周参考目标和每周对齐

| 参考阶段 | 共同目标 |
|---|---|
| 第 1 周 | 跑通框架、确认输入输出，开始 A-01/B-01/C-01/D-01 |
| 第 2 周 | 模块独立运行，用共同 fixture 和 fake 对接 |
| 第 3 周 | 串通存储、分析、报告和页面，补真实模型适配 |
| 第 4 周 | 争取一个仓库、小型 Python PR 的真实最小闭环 |
| 第 5 周 | 补缺失 P0、修失败路径、完善测试和演示，再选 P1 |

这是目标，不是固定工期；每周按实际结果调整，具体任务可跨周移动。
模型/凭证受阻时可以展示 fake 闭环，但要明确真实 AI 尚未接通。

每周用 20–30 分钟展示：本周交付和 PR/测试结果、阻塞和需要谁协助、下一次可验收目标、接口变化。
C 汇总协调，接口变化和阻塞及时沟通，不等周会。不用“完成 80%”代替可运行产物。

## 6. 配置、数据和安全

当前后端读取 CODERISK_APP_NAME、CODERISK_LOG_LEVEL、CODERISK_MOCK_ENABLED；根 .env 与环境变量生效，环境变量优先，改后重启。
GITHUB_TOKEN、GITHUB_WEBHOOK_SECRET、LLM_API_KEY/BASE_URL/MODEL 只是 .env.example 的预留项，尚未实现读取。
Vite 代理使用启动进程的 CODERISK_API_TARGET，默认本机 8000；不假设后端 .env 自动传给前端。

只提交 .env.example，不提交凭证、私有原始样例、虚拟环境、node_modules、dist 或个人笔记；笔记放忽略的 docs/local/。
不执行、导入或运行 PR 代码/安装脚本/测试。模型输入当不可信数据，日志和 CI 工件脱敏。
运行限制、保留策略和共享访问保护按 decisions 确认；现在不开发公众注册和多租户。

## 7. Docker 与部署基线

在有 Docker 的环境，从根目录 `docker compose up --build`；停止用 `docker compose down`。
现有配置为开发基线，尚未实测；前端是 Vite 开发服务器，不直接作公网生产服务。宿主机映射仅回环地址。
SQLite 实现后由 A/D 配数据卷、健康等待与备份恢复，不随意 down -v 删除数据。

D 完成实测后在本节更新系统/版本、实际命令和地址、凭证变量名、Webhook 可达性、访问保护、数据路径及恢复步骤。
记录实际通过/失败/未验证，不记录凭证值。真实调用需要专用授权环境，不要求每位成员配置 Key。

## 8. 文档怎样维护

根 README 只作导航与快速开始；需求讲产品；架构讲结构；接口讲共同约定；本文件讲协作；角色文件讲个人任务；decisions 讲未定细则和选择理由。
同一条规则只维护一次，其他位置链接引用。重要架构选择在对应 PR 说明理由和影响，讨论结论更新 decisions，再同步相关接口。
文档按主题和角色维护；待讨论的方案经相关成员确认后，再同步代码与对应文档。
