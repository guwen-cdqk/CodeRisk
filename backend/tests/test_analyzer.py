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
    assert not parsed.diagnostics
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


# --- PR #2 review 回归：非法输入、hunk 计数校验、数字溢出 ----------------------
# 对应 guwen-cdqk 在 PR #2 上的三条 P2 意见。


def test_illegal_nonempty_patch_reports_diagnostic() -> None:
    """① 非空但认不出来的输入，必须能和「确实没有变更」区分开。"""
    parsed = parse_patch("demo.py", "not a diff")
    assert parsed.hunks == ()
    assert parsed.added_line_numbers == []
    assert codes(parsed) == {"PATCH_UNRECOGNIZED"}


def test_legal_header_without_hunk_stays_quiet() -> None:
    """① 的反例：合法文件头但没有 hunk（空改动/纯元数据）不该报错。"""
    patch = "diff --git a/demo.py b/demo.py\n--- a/demo.py\n+++ b/demo.py\n"
    parsed = parse_patch("demo.py", patch)
    assert parsed.hunks == ()
    assert codes(parsed) == set()


def test_malformed_hunk_header_is_diagnosed() -> None:
    """长得像 hunk 头却解析不了，也不能静默跳过。"""
    parsed = parse_patch("demo.py", "@@ 这不是合法的 hunk 头 @@\n")
    assert parsed.hunks == ()
    assert "HUNK_HEADER_INVALID" in codes(parsed)


def test_hunk_overrun_is_diagnosed() -> None:
    """②-1 超出声明数量的行不能被静默丢掉，但已解析的行要保留。"""
    patch = "@@ -1 +1 @@\n-old\n+new\n+extra\n"
    parsed = parse_patch("demo.py", patch)
    assert "HUNK_LINE_OVERFLOW" in codes(parsed)
    assert parsed.removed_line_numbers == [1]
    # 合法的 +new 仍在；+extra 不产生第 2 行
    assert parsed.added_line_numbers == [1]


def test_zero_count_side_rejects_addition() -> None:
    """②-2 new_count=0 的一侧不能再产出新增行。"""
    patch = "@@ -1,1 +1,0 @@\n+unexpected\n-old\n"
    parsed = parse_patch("demo.py", patch)
    assert "HUNK_LINE_OVERFLOW" in codes(parsed)
    assert parsed.added_line_numbers == []
    assert parsed.removed_line_numbers == [1]


def test_zero_start_is_diagnosed() -> None:
    """②-3 非零 count 的起点为 0 属于非法位置，不能返回第 0 行。"""
    patch = "@@ -0,1 +1,1 @@\n-old\n+new\n"
    parsed = parse_patch("demo.py", patch)
    assert "HUNK_HEADER_INVALID" in codes(parsed)
    assert parsed.hunks == ()
    assert parsed.removed_line_numbers == []


def test_new_file_hunk_with_zero_start_is_legal() -> None:
    """②-3 的例外：新增文件用 -0,0，count 为 0 时起点 0 是合法的。"""
    patch = "@@ -0,0 +1,2 @@\n+first\n+second\n"
    parsed = parse_patch("demo.py", patch)
    assert codes(parsed) == set()
    assert parsed.added_line_numbers == [1, 2]


def test_huge_hunk_number_does_not_raise() -> None:
    """③ 超长数字要转成诊断，不能抛 ValueError。"""
    patch = "@@ -" + "9" * 5000 + " +1 @@\n-old\n+new\n"
    parsed = parse_patch("demo.py", patch)
    assert parsed.hunks == ()
    assert "HUNK_HEADER_INVALID" in codes(parsed)


@pytest.mark.parametrize(
    ("patch", "changed"),
    [
        ("diff --git a/demo.py b/demo.py\n--- a/demo.py\n+++ b/demo.py\nnot a diff\n", []),
        ("@@ -1 +1 @@\n-old\n+new\n\n+extra\n", [1]),
        ("@@ -1 +1 @@\n-old\n+new\ninvalid\n", [1]),
    ],
    ids=["header-with-garbage", "extra-change-after-empty-line", "hunk-with-garbage"],
)
def test_unrecognized_content_is_diagnosed_without_losing_valid_hunks(patch, changed) -> None:
    parsed = parse_patch("demo.py", patch)
    assert "PATCH_UNRECOGNIZED" in codes(parsed)
    assert parsed.added_line_numbers == parsed.removed_line_numbers == changed
    assert all(item.path == "demo.py" for item in parsed.diagnostics)
