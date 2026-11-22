# B-02：Python 函数定位与上下文

此模块实现文件级 AST 定位，不包含 B-03 静态规则、PR 聚合或 HTTP 装配。基于已合并 B-01，未修改 Diff 解析器及公共 Schema/Protocol。

## 调用

```python
from app.analyzers.python_ast import ASTLimits, locate_changed_functions
from app.schemas.models import ChangedFile

file = ChangedFile(
    path="src/example.py", status="added",
    patch="@@ -0,0 +1,2 @@\n+def run():\n+    return 1\n",
    new_content="def run():\n    return 1\n",
)
result = locate_changed_functions(file, limits=ASTLimits())
assert result.complete
assert result.functions[0].qualified_name == "run"
```

输入只作为文本传给 `ast.parse`；不 import、eval、exec 或运行待审代码，无网络、读文件或写库。

## 输出与归属

内部不可变容器 `FunctionAnalysis` 包含公共 `ChangedFunction`、`Diagnostic` 元组、`top_level_locations` 及 `complete`。内含的公共 Pydantic 对象仍按原模型可变，调用方不得共享后修改；本模块每次创建新对象。

- old/new 分别使用完整 old_content/new_content；只有有变更行的 Python 一侧需要解析，未变更一侧无需全文。
- 新增文件不要求 old_content；删除文件不要求 new_content。合法空字符串不同于 None。
- `.py` 按路径大小写精确筛选；跨扩展名重命名逐侧处理。旧侧使用 previous_path。缺失 previous_path 时诊断并跳过旧侧，保留可用新侧。
- 函数范围含两端、从 1 开始，起点纳入所有装饰器，终点为 AST end_lineno。上下文是完整函数范围，以 LF 连接且无额外末尾换行，不带行号前缀。
- 类方法限定名如 `Service.run`，嵌套函数如 `outer.inner`，嵌套类也按词法作用域连接。名称不保证全局唯一，同名重定义以 side/path/range 区分。
- 每个变更行归属最深层的包含函数；外层有其他变更时可同时返回内外函数，外层上下文本来就包含内层源码。每侧同一函数去重，两侧独立保留。
- 模块顶层、类体赋值、lambda、函数结束后的独立注释无伪造函数，真实变更位置放在 top_level_locations。这是已覆盖的顶层修改，不产生风险发现。
- 注释/空行只有落在 AST 函数跨度内才归属该函数。类头/类装饰器变化不推断其所有方法受影响；此处不做语义传播。
- 输出顺序固定：旧侧后新侧；每侧按起始行、结束行、限定名排序。顶层位置按侧和行号排序。

complete=true 仅表示输入中已知变更的定位覆盖完整，不代表代码正确、安全或 AI 已审查。任何 Diff 诊断、缺失或受限输入使 complete=false，同时保留可用侧与可用函数。B-03 的统一 analyzer 负责 PR 级 complete/partial/failed 和 head_sha；本模块不伪造完整 AnalysisResult。

## 真实性与失败处理

解析前逐行核对 patch 中每一侧的变更行与上下文行是否匹配该侧全文。越界或文本不符则丢弃该侧定位并诊断，另一侧继续。此校验不能证明 GitHub SHA，快照身份仍由 A 保证。

缺失/损坏/截断 patch 原样保留 B-01 的诊断。截断时只定位已知行，不对缺失部分猜测。缺失 patch 无法推断变更行，即使有全文也不对全部函数假定受影响。纯合法元数据且没有 hunk 不做 AST 工作。

语法按 Python 3.12 grammar 解析；支持普通/async 函数、方法、装饰器、嵌套函数及 3.12 语法。Python 语法解析成功不等同于编译期/运行期语义有效。诊断不回显源码或 parser 原始异常。

| 诊断 | 含义 |
|---|---|
| PREVIOUS_PATH_MISSING | 重命名未提供旧路径 |
| DIFF_SIDE_INVALID | 新增/删除状态与变更侧冲突 |
| SOURCE_MISSING | 有变更的侧缺失全文 |
| DIFF_SOURCE_MISMATCH | Diff 行号或文本与全文不符 |
| PYTHON_SYNTAX_ERROR | 全文不能按 Python 3.12 语法解析，包括 NUL |
| SOURCE_ENCODING_INVALID / PATCH_ENCODING_INVALID | 无法 UTF-8 编码的文本 |
| SOURCE_SIZE_LIMIT / PATCH_SIZE_LIMIT | 输入超过本地预算 |
| AST_RESOURCE_LIMIT | 节点数超限或解析资源异常 |
| CONTEXT_LIMIT | 整个函数上下文被省略，不截断冒充完整函数 |

## 本地安全默认值

ASTLimits 为可信装配配置，字段必须为正整数；不是已冻结 Q-03 产品预算。

| 字段 | 默认值 | 范围 |
|---|---:|---|
| max_source_bytes | 204800 | 每侧 UTF-8 全文 |
| max_patch_bytes | 1048576 | 每文件 patch |
| max_ast_nodes | 50000 | 每侧 AST（解析后迭代计数） |
| max_context_chars | 12000 | 单函数完整上下文 |
| max_total_context_chars | 50000 | 本次文件调用两侧累计上下文 |

字节限制在 parse 前检查；不截断源码。AST 节点数限制在 parse 后检查，不能作为进程内解析的硬内存/时间隔离承诺；MemoryError/RecursionError 转诊断。函数收集为迭代遍历，行归属使用排序与堆，避免变更行数乘以函数数量的扫描。PR 总预算与跨文件隔离仍由 B-03/A 协调。

## 验证

`python -m pytest backend/tests/test_python_ast.py -v`：人工标注跨度、b02_spans.json、双侧与多 hunk、装饰器/嵌套/async、跨扩展名重命名、顶层改动、CRLF/Unicode、缺失/损坏输入、无末尾换行、语法失败、输入/AST/上下文预算和不执行源码。所有测试离线、无需 Key。

另运行完整 backend/tests、Ruff check/format。用同一锁文件在 Python 3.12 与 3.13 验证。具体运行结果见交付验收报告；这些测试不代表真实业务闭环已实现。
