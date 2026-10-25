# B：代码分析模块

更新：2026-10-25。
[文档导航](../../README.md#documentation) · [架构](../architecture.md) · [公共接口](../interfaces.md) · [协作流程](../development.md)

## 1. 我负责什么

说明代码改动发生在哪里、影响哪些 Python 函数，并提供少量可解释的静态发现和必要上下文。
统一入口是 CodeAnalyzer.analyze：PullRequestData → AnalysisResult。只解析输入文本，不执行 PR 代码，不调用 GitHub/LLM，不写库。

## 2. 从哪些文件开始

先读 `backend/app/schemas/models.py` 中的 ChangedFile、SourceLocation、ChangedFunction、AnalysisResult，以及 `services/contracts.py`。
按需创建 `analyzers/diff.py`、`python_ast.py`、`rules.py`、`analyzer.py` 和 `backend/tests/` 的标注 fixture/单测。
模块内部结构自行组织，不为内部中间结果随意扩充公共 Schema。

## 3. 当前阶段任务

### B-01 · P0 · Unified Diff 解析

将文件级 patch 转成 old/new 变更行及诊断，只处理 Python 文件。交付 diff.py、人工标注 patch 和单测。
验收多 hunk、增删、纯删除、重命名、CRLF、无换行标记、缺失/损坏/截断 patch。删除行不能标成新侧，不把 patch 当完整代码。

### B-02 · P0 · AST 函数定位与上下文

使用 old/new_content 做 ast.parse，把变更行关联到普通/async 函数、类方法、装饰器和嵌套函数，返回限定名、范围和有限上下文。
依赖 B-01 与 A 的文本约定；交付 python_ast.py、函数跨度标注和单测。
验收删除函数、语法错误、无全文、顶层修改和大小限制。嵌套重叠应明确归属策略，例如优先最内层；装饰器归被装饰函数。
顶层代码可在真实位置产生规则发现，不必伪造函数；不能定位或超预算时写诊断。

### B-03 · P0 · 三条规则与统一输出

先做裸 except、可变默认参数、直接 eval/exec；余力再加 shell=True。围绕变更关联范围输出 Finding，保留稳定 rule_id。
依赖 B-01/02；交付 rules.py、analyzer.py、规则说明和回归测试。
每条规则有正例、反例、边界例，每个发现有位置、证据、原因和建议。统一输出正确 head_sha、complete/partial/failed 与 diagnostics。
部分文件坏掉不拖垮整个服务；“无命中”和“未分析成功”分开。别名调用等局限如实说明，不宣称完整语义分析。

### B-04 · P1 · 误报、预算和实验基线

闭环后改进上下文选择、误报、确定性排序和输入规模控制，与 C 建固定人工标注集并提供静态基线。
交付质量回归、基准环境/耗时记录和可复现实验数据；另一成员能复核标签，不只挑成功样例。
基础输入限制随 P0 实现，具体数值在决策记录确认。

## 4. 首个 PR

先做 B-01：使用手写 patch 输出人工可核对的两侧变更行和诊断，不等待真实 GitHub。
请 A 核对输入约定，C 核对后续所需上下文；A 可以先用 fake analyzer 开发协调器。

## 5. 依赖与协作

A 提供同一 SHA 的 Diff/全文，B 提供确定性 AnalysisResult，C 使用函数上下文和静态证据，D 复用固定结果展示。
公共路径/行侧只在 interfaces 维护；大小/上下文预算与 A/C 对齐。分析失败和部分覆盖的语义不能各自解释。

## 6. 自己必须完成的测试

解析与跨度标注、规则正反边界、缺失/损坏输入、partial/failed、序列化及重复运行确定性。
检查字符串和注释不会被误作真实调用；加入已知误报回归。测试无需网络、密钥或真实仓库执行。
D 负责链路集成，B 负责修复和验证自己模块的问题。
