from pathlib import Path

import pytest

from app.analyzers.diff import ParsedPatch, parse_patch

FIXTURES = Path(__file__).parent / "fixtures"

ALL_FIXTURES = [
    "simple_add.patch",
    "mixed.patch",
    "multi_hunk.patch",
    "delete_only.patch",
    "no_newline_eof.patch",
    "rename_with_change.patch",
]


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def codes(parsed: ParsedPatch) -> set[str]:
    return {item.code for item in parsed.diagnostics}


@pytest.mark.parametrize(
    ("name", "added", "removed"),
    [
        ("simple_add.patch", [2, 3], []),
        ("mixed.patch", [11], [11]),
        ("multi_hunk.patch", [4, 22], [21]),
        ("delete_only.patch", [], [8, 9]),
        ("no_newline_eof.patch", [2], [2]),
        ("rename_with_change.patch", [3], []),
    ],
)
def test_line_numbers(name: str, added: list[int], removed: list[int]) -> None:
    parsed = parse_patch("demo.py", load(name))
    assert parsed.added_line_numbers == added
    assert parsed.removed_line_numbers == removed


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_counts_match_declared(name: str) -> None:
    parsed = parse_patch("demo.py", load(name))
    assert parsed.hunks
    for hunk in parsed.hunks:
        assert hunk.new_count == sum(1 for line in hunk.lines if line.kind != "removed")
        assert hunk.old_count == sum(1 for line in hunk.lines if line.kind != "added")


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_removed_lines_never_have_new_number(name: str) -> None:
    parsed = parse_patch("demo.py", load(name))
    for hunk in parsed.hunks:
        for line in hunk.lines:
            if line.kind == "removed":
                assert line.new_line_no is None
            elif line.kind == "added":
                assert line.old_line_no is None
            else:
                assert line.new_line_no is not None
                assert line.old_line_no is not None


def test_truncated_reports_diagnostic() -> None:
    parsed = parse_patch("demo.py", load("truncated.patch"))
    assert "HUNK_COUNT_MISMATCH" in codes(parsed)


def test_garbage_does_not_raise() -> None:
    parsed = parse_patch("demo.py", load("garbage.patch"))
    assert parsed.hunks == ()
    assert parsed.added_line_numbers == []


@pytest.mark.parametrize("path", ["demo.py", "src/new_name.py"])
def test_missing_patch(path: str) -> None:
    """patch 缺失：纯重命名、二进制文件、超限省略都会走到这里，不能当成零改动。"""
    parsed = parse_patch(path, None)
    assert parsed.hunks == ()
    assert parsed.path == path
    assert codes(parsed) == {"PATCH_MISSING"}


def test_crlf_is_normalized() -> None:
    patch = "@@ -1,2 +1,3 @@\r\n a = 1\r\n+b = 2\r\n c = 3\r\n"
    parsed = parse_patch("demo.py", patch)
    assert parsed.added_line_numbers == [2]
    assert all("\r" not in line.text for hunk in parsed.hunks for line in hunk.lines)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_parsing_is_deterministic(name: str) -> None:
    """同一输入解析两次，结果必须完全一致；下游依赖这个前提做增量与缓存。"""
    patch = load(name)
    first = parse_patch("demo.py", patch)
    second = parse_patch("demo.py", patch)
    assert first == second
    assert first.added_line_numbers == second.added_line_numbers
    assert first.removed_line_numbers == second.removed_line_numbers
    assert codes(first) == codes(second)
