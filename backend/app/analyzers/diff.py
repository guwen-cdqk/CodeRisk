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

    while index < len(raw_lines):
        match = HUNK_HEADER.match(raw_lines[index])
        if match is None:
            # 跳过 ---/+++ 文件头、diff --git、index 等不认识的行
            index += 1
            continue

        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_start = int(match.group(3))
        new_count = int(match.group(4) or 1)
        section = match.group(5).strip()
        index += 1

        old_line_no = old_start
        new_line_no = new_start
        old_left = old_count
        new_left = new_count
        body: list[ChangedLine] = []

        while index < len(raw_lines) and (old_left > 0 or new_left > 0):
            raw = raw_lines[index]
            if raw.startswith("\\"):
                # "\ No newline at end of file"，不占用行号
                index += 1
                continue

            if not raw:
                # patch 末尾换行产生的空串，不是真正的代码行
                break

            tag = raw[0]
            text = raw[1:]

            if tag == "+":
                body.append(ChangedLine("added", None, new_line_no, text))
                new_line_no += 1
                new_left -= 1
            elif tag == "-":
                body.append(ChangedLine("removed", old_line_no, None, text))
                old_line_no += 1
                old_left -= 1
            elif tag == " ":
                body.append(ChangedLine("context", old_line_no, new_line_no, text))
                old_line_no += 1
                new_line_no += 1
                old_left -= 1
                new_left -= 1
            else:
                # 遇到下一个 @@ 或者无法识别的内容，停止当前 hunk
                break
            index += 1

        if old_left > 0 or new_left > 0:
            diagnostics.append(
                Diagnostic(
                    code="HUNK_COUNT_MISMATCH",
                    message=f"hunk 声明 {old_count} 旧行 / {new_count} 新行，实际没有解析完",
                    path=path,
                )
            )

        hunks.append(Hunk(old_start, old_count, new_start, new_count, section, tuple(body)))

    return ParsedPatch(path=path, hunks=tuple(hunks), diagnostics=tuple(diagnostics))
