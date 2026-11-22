"""B-02: locate changed Python functions without executing source text."""

from __future__ import annotations

import ast
import heapq
from dataclasses import dataclass
from typing import Literal

from app.analyzers.diff import ParsedPatch, parse_patch
from app.schemas.models import ChangedFile, ChangedFunction, Diagnostic, SourceLocation

Side = Literal["old", "new"]


@dataclass(frozen=True)
class ASTLimits:
    """Local safety defaults, not a frozen Q-03 product budget."""

    max_source_bytes: int = 200 * 1024
    max_patch_bytes: int = 1024 * 1024
    max_ast_nodes: int = 50_000
    max_context_chars: int = 12_000
    max_total_context_chars: int = 50_000

    def __post_init__(self) -> None:
        for value in vars(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError("AST limits must be positive integers")


@dataclass(frozen=True)
class FunctionAnalysis:
    """Internal per-file result; does not extend the shared public Schema.

    complete describes localization coverage, not correctness or safety of code.
    Top-level edits are covered, but have no function; see top_level_locations.
    """

    functions: tuple[ChangedFunction, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    top_level_locations: tuple[SourceLocation, ...] = ()
    complete: bool = True


@dataclass(frozen=True)
class _Function:
    name: str
    start: int
    end: int
    depth: int


def _functions(tree: ast.AST, limit: int) -> list[_Function]:
    """Iterative lexical traversal avoids recursion in our own visitor."""
    result = []
    stack = [(tree, (), 0)]
    count = 0
    while stack:
        node, scope, depth = stack.pop()
        count += 1
        if count > limit:
            raise OverflowError
        nested_scope = scope
        nested_depth = depth
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested_scope = (*scope, node.name)
            nested_depth += 1
            if not isinstance(node, ast.ClassDef):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                result.append(
                    _Function(".".join(nested_scope), start, node.end_lineno or node.lineno, depth)
                )
        stack.extend(
            (child, nested_scope, nested_depth)
            for child in reversed(list(ast.iter_child_nodes(node)))
        )
    return result


def _text_lines(content: str) -> list[str]:
    # Match Python universal newlines, not str.splitlines()'s Unicode separators.
    return content.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _fits(text: str, limit: int) -> bool:
    # Avoid allocating the UTF-8 encoding of an obviously oversized string.
    return len(text) <= limit and len(text.encode("utf-8")) <= limit


def locate_changed_functions(
    file: ChangedFile, *, limits: ASTLimits | None = None
) -> FunctionAnalysis:
    """Parse a file's diff and both full-text sides, returning bounded contexts.

    Old locations use previous_path on renames. Each changed line belongs to the
    deepest containing function; decorators belong to their decorated function.
    No networking, filesystem reads, imports of PR code, eval or exec occur here.
    """
    limits = limits or ASTLimits()
    old_path = file.previous_path or file.path
    relevant = {
        "old": file.status != "added" and old_path.endswith(".py"),
        "new": file.status != "removed" and file.path.endswith(".py"),
    }
    if not any(relevant.values()):
        return FunctionAnalysis()

    diagnostics: list[Diagnostic] = []
    functions: list[ChangedFunction] = []
    top_level: list[SourceLocation] = []
    complete = True
    remaining = limits.max_total_context_chars

    def diagnose(code: str, message: str, path: str) -> None:
        nonlocal complete
        complete = False
        diagnostics.append(Diagnostic(code=code, message=message, path=path))

    if file.status == "renamed" and file.previous_path is None:
        diagnose("PREVIOUS_PATH_MISSING", "Renamed file has no old path", file.path)
        relevant["old"] = False

    try:
        patch_fits = file.patch is None or _fits(file.patch, limits.max_patch_bytes)
    except UnicodeEncodeError:
        diagnose("PATCH_ENCODING_INVALID", "Diff contains invalid UTF-8 text", file.path)
        return FunctionAnalysis(diagnostics=tuple(diagnostics), complete=False)
    if not patch_fits:
        diagnose("PATCH_SIZE_LIMIT", "Diff exceeds the local byte limit", file.path)
        return FunctionAnalysis(diagnostics=tuple(diagnostics), complete=False)

    parsed: ParsedPatch = parse_patch(file.path, file.patch, truncated=file.patch_truncated)
    diagnostics.extend(parsed.diagnostics)
    complete = complete and not parsed.diagnostics

    # Unexpected edits to the non-existent side are not silently accepted.
    if file.status == "added" and parsed.removed_line_numbers:
        diagnose("DIFF_SIDE_INVALID", "Added file contains old-side edits", old_path)
    if file.status == "removed" and parsed.added_line_numbers:
        diagnose("DIFF_SIDE_INVALID", "Removed file contains new-side edits", file.path)

    for side in ("old", "new"):
        if not relevant[side]:
            continue
        path = old_path if side == "old" else file.path
        changed = parsed.removed_line_numbers if side == "old" else parsed.added_line_numbers
        if not changed:
            continue
        content = file.old_content if side == "old" else file.new_content
        if content is None:
            diagnose("SOURCE_MISSING", f"{side}: full source is unavailable", path)
            continue
        try:
            fits = _fits(content, limits.max_source_bytes)
        except UnicodeEncodeError:
            diagnose("SOURCE_ENCODING_INVALID", f"{side}: source is not valid UTF-8", path)
            continue
        if not fits:
            diagnose("SOURCE_SIZE_LIMIT", f"{side}: full source exceeds the byte limit", path)
            continue
        lines = _text_lines(content)
        # A trailing newline does not create an additional source line.
        if lines and lines[-1] == "":
            lines.pop()
        mismatch = False
        for hunk in parsed.hunks:
            for line in hunk.lines:
                number = line.old_line_no if side == "old" else line.new_line_no
                if number is not None and (
                    number < 1 or number > len(lines) or lines[number - 1] != line.text
                ):
                    mismatch = True
        if mismatch:
            diagnose("DIFF_SOURCE_MISMATCH", f"{side}: diff does not match full source", path)
            continue
        try:
            tree = ast.parse(content, feature_version=(3, 12))
            spans = _functions(tree, limits.max_ast_nodes)
        except (SyntaxError, ValueError):
            diagnose("PYTHON_SYNTAX_ERROR", f"{side}: source cannot be parsed as Python 3.12", path)
            continue
        except (RecursionError, MemoryError, OverflowError):
            diagnose("AST_RESOURCE_LIMIT", f"{side}: AST exceeds local resource limits", path)
            continue
        selected: set[_Function] = set()
        ordered = sorted(enumerate(spans), key=lambda item: item[1].start)
        cursor = 0
        active: list[tuple[int, int, int, int, _Function]] = []
        for number in changed:
            while cursor < len(ordered) and ordered[cursor][1].start <= number:
                identity, span = ordered[cursor]
                heapq.heappush(active, (-span.depth, -span.start, span.end, identity, span))
                cursor += 1
            while active and active[0][2] < number:
                heapq.heappop(active)
            if active:
                selected.add(active[0][4])
            else:
                top_level.append(
                    SourceLocation(path=path, side=side, start_line=number, end_line=number)
                )
        for function in sorted(selected, key=lambda f: (f.start, f.end, f.name)):
            context = "\n".join(lines[function.start - 1 : function.end])
            if len(context) > limits.max_context_chars or len(context) > remaining:
                diagnose("CONTEXT_LIMIT", f"{side}: function context omitted by local budget", path)
                continue
            remaining -= len(context)
            functions.append(
                ChangedFunction(
                    qualified_name=function.name,
                    location=SourceLocation(
                        path=path, side=side, start_line=function.start, end_line=function.end
                    ),
                    context=context,
                )
            )
    return FunctionAnalysis(tuple(functions), tuple(diagnostics), tuple(top_level), complete)
