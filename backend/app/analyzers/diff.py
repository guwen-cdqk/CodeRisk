"""把 GitHub 给的文件级 unified diff，解析成"哪几行被改了"。

只做解析，不读文件、不发网络请求、不执行 PR 里的代码。
坏数据只记录 Diagnostic，绝不抛异常。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.schemas.models import Diagnostic

# 匹配 @@ -旧起点,旧行数 +新起点,新行数 @@ 可选的函数名
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")

# 超过这个位数就不可能是合法行号；同时也避开解释器的整数转换位数上限
MAX_HUNK_DIGITS = 9

# 认得的、不承载变更行号的 diff 元数据行。
# 用来区分「合法文件头但没有 hunk」和「根本不是 diff」。
RECOGNIZED_METADATA = (
    "diff --git ",
    "index ",
    "--- ",
    "+++ ",
    "\\",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "copy from ",
    "copy to ",
    "rename from ",
    "rename to ",
    "similarity index ",
    "dissimilarity index ",
    "Binary files ",
    "GIT binary patch",
)

LineKind = Literal["context", "added", "removed"]


@dataclass(frozen=True)
class ChangedLine:
    """diff 里的一行。added 没有旧行号，removed 没有新行号。"""

    kind: LineKind
    old_line_no: int | None
    new_line_no: int | None
    text: str


@dataclass(frozen=True)
class Hunk:
    """一个 @@ ... @@ 块。"""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: tuple[ChangedLine, ...]


@dataclass(frozen=True)
class ParsedPatch:
    """一个文件的解析结果。"""

    path: str
    hunks: tuple[Hunk, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.hunks

    @property
    def added_line_numbers(self) -> list[int]:
        """新增行在【新文件】里的行号，从小到大。"""
        numbers: set[int] = set()
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind == "added" and line.new_line_no is not None:
                    numbers.add(line.new_line_no)
        return sorted(numbers)

    @property
    def removed_line_numbers(self) -> list[int]:
        """删除行在【旧文件】里的行号，从小到大。"""
        numbers: set[int] = set()
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.kind == "removed" and line.old_line_no is not None:
                    numbers.add(line.old_line_no)
        return sorted(numbers)


def _to_int(text: str | None, *, default: int = 1) -> int | None:
    """把 hunk 头里的数字转成 int。

    超长或无法转换时返回 None，绝不抛异常：Python 3.11+ 对整数与字符串之间的
    转换有 4300 位上限，直接 int() 遇到超长数字会抛 ValueError。
    """
    if text is None:
        return default
    if len(text) > MAX_HUNK_DIGITS:
        return None
    try:
        return int(text)
    except ValueError:  # pragma: no cover - 长度已限，理论上到不了
        return None


def _is_metadata(line: str) -> bool:
    return line.startswith(RECOGNIZED_METADATA)


def parse_patch(path: str, patch: str | None, *, truncated: bool = False) -> ParsedPatch:
    """解析一个文件的 diff。"""
    if patch is None:
        return ParsedPatch(
            path=path,
            diagnostics=(
                Diagnostic(code="PATCH_MISSING", message="该文件没有提供 diff", path=path),
            ),
        )

    diagnostics: list[Diagnostic] = []
    if truncated:
        diagnostics.append(
            Diagnostic(code="PATCH_TRUNCATED", message="diff 被截断，结果可能不完整", path=path)
        )

    raw_lines = patch.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hunks: list[Hunk] = []
    index = 0
    # 是否见过 diff 的合法结构：认得的元数据行、合法的 hunk 头，
    # 或以 @@ 开头但畸形的 hunk 头。
    structure_seen = False
    # 完全认不出来、又不是空行的行数
    stray_lines = 0

    while index < len(raw_lines):
        line = raw_lines[index]
        match = HUNK_HEADER.match(line)

        if match is None:
            if not line.strip():
                index += 1
            elif line.startswith("@@"):
                # 长得像 hunk 头却匹配不上，不能静默跳过
                structure_seen = True
                diagnostics.append(
                    Diagnostic(
                        code="HUNK_HEADER_INVALID",
                        message=f"hunk 头无法解析：{line[:40]!r}",
                        path=path,
                    )
                )
                index += 1
            elif _is_metadata(line):
                # ---/+++ 文件头、diff --git、index 等，没有 hunk 也可能是合法的
                structure_seen = True
                index += 1
            else:
                stray_lines += 1
                index += 1
            continue

        structure_seen = True
        old_start = _to_int(match.group(1))
        old_count = _to_int(match.group(2))
        new_start = _to_int(match.group(3))
        new_count = _to_int(match.group(4))

        if old_start is None or old_count is None or new_start is None or new_count is None:
            diagnostics.append(
                Diagnostic(
                    code="HUNK_HEADER_INVALID",
                    message=f"hunk 头的数字超出可解析范围（最多 {MAX_HUNK_DIGITS} 位）",
                    path=path,
                )
            )
            index += 1
            continue

        # 非零 count 的起点必须 >= 1；新增文件那种 -0,0 因为 count 为 0 不受影响
        if (old_count > 0 and old_start < 1) or (new_count > 0 and new_start < 1):
            diagnostics.append(
                Diagnostic(
                    code="HUNK_HEADER_INVALID",
                    message=f"hunk 起点必须 >= 1，实际旧侧 {old_start} / 新侧 {new_start}",
                    path=path,
                )
            )
            index += 1
            continue

        section = match.group(5).strip()
        index += 1

        old_line_no = old_start
        new_line_no = new_start
        old_left = old_count
        new_left = new_count
        overflow_old = 0
        overflow_new = 0
        body: list[ChangedLine] = []

        while index < len(raw_lines):
            raw = raw_lines[index]
            if raw.startswith("\\"):
                # "\ No newline at end of file"，不占用行号
                index += 1
                continue

            if not raw:
                # patch 末尾换行产生的空串，不是真正的代码行
                break

            tag = raw[0]
            if tag not in ("+", "-", " "):
                # 遇到下一个 @@ 或者无法识别的内容，停止当前 hunk
                break

            if raw.startswith(("--- ", "+++ ")) and old_left <= 0 and new_left <= 0:
                # 额度已用完，且这一行看起来是下一个文件的文件头，不属于本 hunk
                break

            text = raw[1:]

            # 消费每一行之前先检查该侧剩余额度；超出就记溢出并跳过，
            # 不能把额度减成负数继续产出越界行号。
            if tag == "+":
                if new_left <= 0:
                    overflow_new += 1
                    index += 1
                    continue
                body.append(ChangedLine("added", None, new_line_no, text))
                new_line_no += 1
                new_left -= 1
            elif tag == "-":
                if old_left <= 0:
                    overflow_old += 1
                    index += 1
                    continue
                body.append(ChangedLine("removed", old_line_no, None, text))
                old_line_no += 1
                old_left -= 1
            else:
                if old_left <= 0 or new_left <= 0:
                    # 上下文行同时占两侧，任一侧越界都算溢出
                    if old_left <= 0:
                        overflow_old += 1
                    else:
                        overflow_new += 1
                    index += 1
                    continue
                body.append(ChangedLine("context", old_line_no, new_line_no, text))
                old_line_no += 1
                new_line_no += 1
                old_left -= 1
                new_left -= 1
            index += 1

        if overflow_old or overflow_new:
            # 诊断 + 保留已解析的行：下游不该因为一行坏数据丢掉真实变更
            diagnostics.append(
                Diagnostic(
                    code="HUNK_LINE_OVERFLOW",
                    message=(
                        f"hunk 实际内容超出声明（旧侧多 {overflow_old} 行、"
                        f"新侧多 {overflow_new} 行），已保留可解析部分"
                    ),
                    path=path,
                )
            )

        if old_left > 0 or new_left > 0:
            diagnostics.append(
                Diagnostic(
                    code="HUNK_COUNT_MISMATCH",
                    message=f"hunk 声明 {old_count} 旧行 / {new_count} 新行，实际没有解析完",
                    path=path,
                )
            )

        hunks.append(Hunk(old_start, old_count, new_start, new_count, section, tuple(body)))

    if not hunks and stray_lines and not structure_seen:
        # 没有任何可识别结构，却又有非空内容：不是"没有变更"，是根本没解析出来
        diagnostics.append(
            Diagnostic(
                code="PATCH_UNRECOGNIZED",
                message=f"输入不是可识别的 unified diff（{stray_lines} 行无法识别）",
                path=path,
            )
        )

    return ParsedPatch(path=path, hunks=tuple(hunks), diagnostics=tuple(diagnostics))
