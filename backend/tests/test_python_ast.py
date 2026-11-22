"""B-02 acceptance: source strings are data, never imported or executed."""

import difflib
import json
from pathlib import Path

import pytest

from app.analyzers.python_ast import ASTLimits, locate_changed_functions
from app.schemas.models import ChangedFile, ChangedFunction


def make_file(old, new, **kwargs):
    status = kwargs.pop("status", "modified")

    def source_lines(text):
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        return [line + "\n" for line in text.split("\n")[:-1]]

    patch = "".join(
        difflib.unified_diff(
            source_lines(old),
            source_lines(new),
            fromfile="a/demo.py",
            tofile="b/demo.py",
        )
    )
    # difflib does not emit the Git no-newline marker. Most fixtures end in LF.
    return ChangedFile(
        path=kwargs.pop("path", "demo.py"),
        status=status,
        old_content=old,
        new_content=new,
        patch=patch,
        **kwargs,
    )


def codes(result):
    return {d.code for d in result.diagnostics}


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "def f():\n    return 1\n",
            "def f():\n    return 2\n",
            [("f", "old", 1, 2), ("f", "new", 1, 2)],
        ),
        (None, "async def f():\n    return 2\n", [("f", "new", 1, 2)]),
        ("def f():\n    return 1\n", None, [("f", "old", 1, 2)]),
        (
            "class C:\n    def f(self):\n        return 1\n",
            "class C:\n    def f(self):\n        return 2\n",
            [("C.f", "old", 2, 3), ("C.f", "new", 2, 3)],
        ),
        (
            "@decorate(1)\ndef f():\n    pass\n",
            "@decorate(2)\ndef f():\n    pass\n",
            [("f", "old", 1, 3), ("f", "new", 1, 3)],
        ),
        (
            "def outer():\n    def inner():\n        return 1\n    return inner()\n",
            "def outer():\n    def inner():\n        return 2\n    return inner()\n",
            [("outer.inner", "old", 2, 3), ("outer.inner", "new", 2, 3)],
        ),
        (
            "def f():\n    return 1\n",
            "def g():\n    return 1\n",
            [("f", "old", 1, 2), ("g", "new", 1, 2)],
        ),
        ("def f(): return 1\n", "def f(): return 2\n", [("f", "old", 1, 1), ("f", "new", 1, 1)]),
    ],
    ids=[
        "function",
        "async-added",
        "deleted",
        "method",
        "decorator",
        "nested",
        "rename",
        "one-line",
    ],
)
def test_manually_annotated_spans(old, new, expected):
    status = "added" if old is None else "removed" if new is None else "modified"
    file = make_file(old, new, status=status)
    result = locate_changed_functions(file)
    assert result.complete and not result.diagnostics and not result.top_level_locations
    assert [
        (f.qualified_name, f.location.side, f.location.start_line, f.location.end_line)
        for f in result.functions
    ] == expected
    for f in result.functions:
        source = old if f.location.side == "old" else new
        assert f.context == "\n".join(
            source.splitlines()[f.location.start_line - 1 : f.location.end_line]
        )
        assert ChangedFunction.model_validate_json(f.model_dump_json()) == f
    assert locate_changed_functions(file) == result


def test_multiple_hunks_and_same_named_methods():
    old = "class A:\n    def run(self):\n        return 1\n" + "\n" * 15
    old += "class B:\n    def run(self):\n        return 3\n"
    new = old.replace("return 1", "return 2").replace("return 3", "return 4")
    result = locate_changed_functions(make_file(old, new))
    assert result.complete
    assert [f.qualified_name for f in result.functions] == ["A.run", "B.run"] * 2


def test_nested_and_outer_changed_independently():
    old = (
        "def outer(x=1):\n    @decorator\n    async def inner():\n"
        "        return 1\n    return inner\n"
    )
    new = old.replace("x=1", "x=2").replace("return 1", "return 2")
    result = locate_changed_functions(make_file(old, new))
    assert [f.qualified_name for f in result.functions] == ["outer", "outer.inner"] * 2
    assert result.functions[1].location.start_line == 2


def test_multiline_decorator_and_signature():
    old = "@decorate(\n    1,\n)\ndef f(\n    x=1,\n):\n    return x\n"
    new = old.replace("    1,", "    2,").replace("x=1", "x=2")
    result = locate_changed_functions(make_file(old, new))
    assert result.complete
    assert [(f.location.start_line, f.location.end_line) for f in result.functions] == [(1, 7)] * 2


@pytest.mark.parametrize(
    "old,new", [("x=1\n", "x=2\n"), ("class C:\n    x=1\n", "class C:\n    x=2\n")]
)
def test_top_level_changes_are_real_locations(old, new):
    result = locate_changed_functions(make_file(old, new))
    assert result.complete and not result.functions
    assert len(result.top_level_locations) == 2
    assert [p.side for p in result.top_level_locations] == ["old", "new"]


@pytest.mark.parametrize(
    "old_path,new_path,sides",
    [
        ("src/old.py", "src/new.py", ["old", "new"]),
        ("src/old.py", "src/new.txt", ["old"]),
        ("src/old.txt", "src/new.py", ["new"]),
        ("src/old.txt", "src/new.txt", []),
    ],
)
def test_rename_paths_and_python_side_filter(old_path, new_path, sides):
    file = make_file(
        "def f():\n    return 1\n",
        "def f():\n    return 2\n",
        status="renamed",
        path=new_path,
        previous_path=old_path,
    )
    result = locate_changed_functions(file)
    assert result.complete
    assert [f.location.side for f in result.functions] == sides
    assert [f.location.path for f in result.functions] == [
        old_path if side == "old" else new_path for side in sides
    ]


def test_missing_source_does_not_block_other_side():
    file = make_file("def f():\n    return 1\n", "def f():\n    return 2\n")
    file.old_content = None
    result = locate_changed_functions(file)
    assert not result.complete and codes(result) == {"SOURCE_MISSING"}
    assert [f.location.side for f in result.functions] == ["new"]


def test_syntax_error_is_safe_and_other_side_survives():
    file = make_file("def f():\n    return 1\n", "def f(:\n    return 2\n")
    result = locate_changed_functions(file)
    assert not result.complete and codes(result) == {"PYTHON_SYNTAX_ERROR"}
    assert [f.location.side for f in result.functions] == ["old"]
    assert "def f" not in result.diagnostics[0].message


@pytest.mark.parametrize("field", ["old_content", "new_content"])
def test_mismatched_snapshot_is_not_localized(field):
    file = make_file("def f():\n    return 1\n", "def f():\n    return 2\n")
    setattr(file, field, "def unrelated():\n    pass\n")
    result = locate_changed_functions(file)
    assert not result.complete and "DIFF_SOURCE_MISMATCH" in codes(result)
    assert len(result.functions) == 1


def test_empty_file_and_missing_source_are_distinct():
    file = ChangedFile(
        path="x.py",
        status="modified",
        old_content="",
        new_content="",
        patch="@@ -1 +1 @@\n-old\n+new\n",
    )
    result = locate_changed_functions(file)
    assert codes(result) == {"DIFF_SOURCE_MISMATCH"}


@pytest.mark.parametrize(
    "patch,truncated,code",
    [
        (None, False, "PATCH_MISSING"),
        ("garbage", False, "PATCH_UNRECOGNIZED"),
        ("@@ broken", False, "HUNK_HEADER_INVALID"),
        ("", True, "PATCH_TRUNCATED"),
    ],
)
def test_patch_diagnostics_are_preserved(patch, truncated, code):
    result = locate_changed_functions(
        ChangedFile(path="x.py", status="modified", patch=patch, patch_truncated=truncated)
    )
    assert not result.complete and code in codes(result)


def test_partial_patch_retains_trusted_localization():
    file = ChangedFile(
        path="x.py",
        status="modified",
        old_content="def f():\n    return 1\n",
        new_content="def f():\n    return 2\n",
        patch="@@ -2 +2 @@\n-    return 1\n+    return 2\n",
        patch_truncated=True,
    )
    result = locate_changed_functions(file)
    assert not result.complete and len(result.functions) == 2


@pytest.mark.parametrize(
    "limit,code",
    [
        ({"max_source_bytes": 5}, "SOURCE_SIZE_LIMIT"),
        ({"max_patch_bytes": 5}, "PATCH_SIZE_LIMIT"),
        ({"max_ast_nodes": 1}, "AST_RESOURCE_LIMIT"),
        ({"max_context_chars": 5}, "CONTEXT_LIMIT"),
        ({"max_total_context_chars": 5}, "CONTEXT_LIMIT"),
    ],
)
def test_limits_are_explicit_not_silent_truncation(limit, code):
    result = locate_changed_functions(
        make_file("def f():\n    return 1\n", "def f():\n    return 2\n"), limits=ASTLimits(**limit)
    )
    assert not result.complete and code in codes(result) and not result.functions


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_limits_are_rejected(value):
    with pytest.raises(ValueError):
        ASTLimits(max_source_bytes=value)


def test_context_total_budget_is_shared_between_sides():
    result = locate_changed_functions(
        make_file("def f():\n    return 1\n", "def f():\n    return 2\n"),
        limits=ASTLimits(max_total_context_chars=22),
    )
    assert not result.complete and len(result.functions) == 1
    assert result.functions[0].location.side == "old"


def test_crlf_unicode_and_unicode_line_separator_inside_string():
    old = 'def 中文():\r\n    return "一\u2028二"\r\n'
    new = old.replace("一", "三")
    result = locate_changed_functions(make_file(old, new))
    assert result.complete
    assert [f.qualified_name for f in result.functions] == ["中文"] * 2
    assert result.functions[0].context == 'def 中文():\n    return "一\u2028二"'


def test_source_is_never_executed(tmp_path):
    marker = tmp_path / "MUST_NOT_EXIST"
    text = (
        f"open({str(marker)!r}, 'w').write('bad')\n"
        "raise RuntimeError('bad')\ndef f():\n    return 1\n"
    )
    result = locate_changed_functions(make_file(text, text.replace("return 1", "return 2")))
    assert result.complete and len(result.functions) == 2
    assert not marker.exists()


def test_no_newline_marker_has_no_effect_on_ast_ranges():
    file = ChangedFile(
        path="x.py",
        status="modified",
        old_content="def f(): return 1",
        new_content="def f(): return 2",
        patch="@@ -1 +1 @@\n-def f(): return 1\n\\ No newline at end of file\n"
        "+def f(): return 2\n\\ No newline at end of file\n",
    )
    result = locate_changed_functions(file)
    assert result.complete and len(result.functions) == 2


def test_fixture_round_trip_from_shared_pr():
    sample = json.loads((Path(__file__).parent / "fixtures/review_example.json").read_text("utf-8"))
    for item in sample["pull_request"]["files"]:
        result = locate_changed_functions(ChangedFile.model_validate(item))
        assert result == locate_changed_functions(ChangedFile.model_validate(item))


def test_manually_labelled_fixture():
    sample = json.loads((Path(__file__).parent / "fixtures/b02_spans.json").read_text("utf-8"))
    result = locate_changed_functions(make_file(sample["old"], sample["new"]))
    assert result.complete
    for side in ("old", "new"):
        assert [
            {"name": f.qualified_name, "start": f.location.start_line, "end": f.location.end_line}
            for f in result.functions
            if f.location.side == side
        ] == sample["expected"]
        assert [p.start_line for p in result.top_level_locations if p.side == side] == sample[
            "top_level_lines"
        ]


def test_missing_rename_path_cannot_fabricate_old_location():
    file = make_file("def f():\n    return 1\n", "def f():\n    return 2\n", status="renamed")
    result = locate_changed_functions(file)
    assert not result.complete and codes(result) == {"PREVIOUS_PATH_MISSING"}
    assert [f.location.side for f in result.functions] == ["new"]


@pytest.mark.parametrize("status", ["added", "removed"])
def test_nonexistent_side_edits_are_diagnosed(status):
    result = locate_changed_functions(
        make_file("def f():\n    return 1\n", "def f():\n    return 2\n", status=status)
    )
    assert not result.complete and codes(result) == {"DIFF_SIDE_INVALID"}


def test_context_mismatch_also_rejects_side():
    file = make_file("def f():\n    return 1\n", "def f():\n    return 2\n")
    file.old_content = "def different():\n    return 1\n"
    result = locate_changed_functions(file)
    assert not result.complete and "DIFF_SOURCE_MISMATCH" in codes(result)
    assert [f.location.side for f in result.functions] == ["new"]


def test_utf8_byte_limit_not_character_limit():
    old = 'def f():\n    return "中"\n'
    new = old.replace("中", "文")
    result = locate_changed_functions(
        make_file(old, new), limits=ASTLimits(max_source_bytes=len(old))
    )
    assert codes(result) == {"SOURCE_SIZE_LIMIT"}


@pytest.mark.parametrize(
    "field,code", [("patch", "PATCH_ENCODING_INVALID"), ("new_content", "SOURCE_ENCODING_INVALID")]
)
def test_invalid_unicode_is_diagnosed(field, code):
    file = make_file("def f():\n    return 1\n", "def f():\n    return 2\n")
    setattr(file, field, "\ud800")
    result = locate_changed_functions(file)
    assert not result.complete and code in codes(result)


@pytest.mark.parametrize("exception", [RecursionError, MemoryError])
def test_parser_resource_failure_is_diagnosed(monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr("app.analyzers.python_ast.ast.parse", fail)
    result = locate_changed_functions(make_file(None, "def f():\n    pass\n", status="added"))
    assert not result.complete and codes(result) == {"AST_RESOURCE_LIMIT"}


def test_changed_line_outside_source_is_not_a_top_level_edit():
    file = ChangedFile(
        path="x.py", status="added", new_content="x=1\n", patch="@@ -0,0 +99,1 @@\n+x=1\n"
    )
    result = locate_changed_functions(file)
    assert not result.complete and codes(result) == {"DIFF_SOURCE_MISMATCH"}
    assert not result.top_level_locations


def test_python312_grammar_rejects_python313_type_parameter_default():
    file = make_file(None, "def f[T=int]():\n    pass\n", status="added")
    result = locate_changed_functions(file)
    assert not result.complete and codes(result) == {"PYTHON_SYNTAX_ERROR"}


def test_limit_boundary_accepts_exact_context_and_source():
    text = "def f():\n    return 1\n"
    result = locate_changed_functions(
        make_file(None, text, status="added"),
        limits=ASTLimits(
            max_source_bytes=len(text.encode()),
            max_context_chars=len(text) - 1,
            max_total_context_chars=len(text) - 1,
        ),
    )
    assert result.complete and result.functions[0].context == text[:-1]


def test_comments_and_lambdas_do_not_create_fake_functions():
    old = "# def fake():\nf = lambda x: x + 1\n"
    result = locate_changed_functions(make_file(old, old.replace("+ 1", "+ 2")))
    assert result.complete and not result.functions
    assert [p.start_line for p in result.top_level_locations] == [2, 2]


def test_blank_line_inside_function_is_attributed():
    old = "def f():\n    x = 1\n\n    return x\n"
    new = old.replace("\n\n", "\n    # note\n")
    result = locate_changed_functions(make_file(old, new))
    assert result.complete and [f.qualified_name for f in result.functions] == ["f", "f"]


def test_scope_returns_to_outer_then_top_level():
    old = "def outer():\n    def inner():\n        return 1\n    return 2\nx = 3\n"
    new = (
        old.replace("return 1", "return 4")
        .replace("return 2", "return 5")
        .replace("x = 3", "x = 6")
    )
    result = locate_changed_functions(make_file(old, new))
    assert result.complete
    assert [f.qualified_name for f in result.functions] == ["outer", "outer.inner"] * 2
    assert [p.start_line for p in result.top_level_locations] == [5, 5]


def test_python312_generic_function_is_supported():
    result = locate_changed_functions(
        make_file(None, "def identity[T](x: T) -> T:\n    return x\n", status="added")
    )
    assert result.complete and result.functions[0].qualified_name == "identity"


def test_many_disjoint_functions_are_not_missed():
    old = "\n".join(f"def f_{i}():\n    return {i}\n" for i in range(100))
    new = old.replace("return ", "return -")
    result = locate_changed_functions(make_file(old, new))
    assert result.complete
    assert [f.qualified_name for f in result.functions] == [f"f_{i}" for i in range(100)] * 2
