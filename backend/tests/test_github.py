import asyncio
import base64

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.github import (
    build_changed_file,
    fetch_changed_files,
    fetch_file_content,
    populate_file_contents,
)
from app.schemas.models import ChangedFile


def test_fetch_changed_files_pagination():
    requested_pages = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)

        if page == 1:
            files = [{"filename": f"file_{i}.py", "status": "modified"} for i in range(100)]

        elif page == 2:
            files = [{"filename": f"file_{i}.py", "status": "modified"} for i in range(100, 200)]
        else:
            files = []

        return httpx.Response(status_code=200, json=files)

    async def run_test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler),
            timeout=10.0,
        ) as client:
            return await fetch_changed_files(
                "demo/repo",
                1,
                client,
            )

    files = asyncio.run(run_test())

    assert len(files) == 200
    assert requested_pages == [1, 2, 3]

    expected_paths = [f"file_{i}.py" for i in range(200)]
    actual_paths = [file["filename"] for file in files]

    assert actual_paths == expected_paths


def test_fetch_changed_files_partial_last_page():
    requested_pages = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        if page == 1:
            files = [{"filename": f"file_{i}.py", "status": "modified"} for i in range(100)]
        elif page == 2:
            files = [{"filename": f"file_{i}.py", "status": "modified"} for i in range(100, 160)]
        else:
            files = []
        return httpx.Response(status_code=200, json=files)

    async def run_test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler),
            timeout=10.0,
        ) as client:
            return await fetch_changed_files(
                "demo/repo",
                1,
                client,
            )

    files = asyncio.run(run_test())

    assert len(files) == 160
    assert requested_pages == [1, 2]
    assert [file["filename"] for file in files] == [f"file_{i}.py" for i in range(160)]


def test_build_changed_file():
    test_files = [
        {"filename": "src/new.py", "status": "added"},
        {"filename": "src/main.py", "status": "modified", "patch": "@@ -1 +1 @@"},
        {"filename": "src/old.py", "status": "removed"},
        {
            "filename": "src/new_name.py",
            "previous_filename": "src/old_name.py",
            "status": "renamed",
        },
    ]
    for file in test_files:
        changed_file = build_changed_file(file)
        assert changed_file.path == file["filename"]
        assert changed_file.previous_path == file.get("previous_filename")
        assert changed_file.status == file["status"]
        assert changed_file.patch == file.get("patch")


def test_build_changed_file_invalid_status():
    test_files = {"filename": "src/new.py", "status": "unknown", "patch": "@@ -1 +1 @@"}
    with pytest.raises(ValidationError):
        build_changed_file(test_files)


def test_fetch_file_content_at_sha():
    source = "print('hello')\n"
    sha = "a" * 40

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert (
            request.url.path == "/repos/guwen-cdqk/CodeRisk/contents/backend/app/analyzers/diff.py"
        )
        assert request.url.params["ref"] == sha

        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        return httpx.Response(
            status_code=200,
            json={
                "encoding": "base64",
                "content": encoded,
            },
        )

    async def run_test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler), timeout=10.0
        ) as client:
            return await fetch_file_content(
                "guwen-cdqk/CodeRisk",
                "backend/app/analyzers/diff.py",
                sha,
                client,
            )

    result = asyncio.run(run_test())

    assert result == source


@pytest.mark.parametrize(
    "status, previous_path, expected_requests",
    [
        ("added", None, [("src/demo.py", "a" * 40)]),
        ("removed", None, [("src/demo.py", "b" * 40)]),
        (
            "modified",
            None,
            [
                ("src/demo.py", "b" * 40),
                ("src/demo.py", "a" * 40),
            ],
        ),
        (
            "renamed",
            "src/old_name.py",
            [
                ("src/old_name.py", "b" * 40),
                ("src/demo.py", "a" * 40),
            ],
        ),
    ],
)
def test_populate_file_contents(status, previous_path, expected_requests):
    changed_file = ChangedFile(
        path="src/demo.py",
        previous_path=previous_path,
        status=status,
    )
    old_source = "print('old')\n"
    new_source = "print('new')\n"
    base_sha = "b" * 40
    head_sha = "a" * 40
    requested_files = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.url.path.startswith("/repos/guwen-cdqk/CodeRisk/contents/")
        path = request.url.path.split("/contents/", 1)[1]
        ref = request.url.params["ref"]
        requested_files.append((path, ref))

        if ref == base_sha:
            source = old_source
        elif ref == head_sha:
            source = new_source
        else:
            raise AssertionError(f"Unexpected ref: {ref}")

        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": encoded},
        )

    async def run_test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler), timeout=10.0
        ) as client:
            return await populate_file_contents(
                "guwen-cdqk/CodeRisk",
                changed_file,
                base_sha,
                head_sha,
                client,
            )

    result = asyncio.run(run_test())
    assert result.old_content == (None if status == "added" else old_source)
    assert result.new_content == (None if status == "removed" else new_source)
    assert requested_files == expected_requests


def test_populate_renamed_file_missing_previous_path():
    changed_file = ChangedFile(path="src/new_name.py", status="renamed")
    requested_files = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        requested_files.append(request)
        raise AssertionError("Missing previous_path must be rejected before any HTTP request")

    async def run_test():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler), timeout=10.0
        ) as client:
            return await populate_file_contents(
                "guwen-cdqk/CodeRisk",
                changed_file,
                "b" * 40,
                "a" * 40,
                client,
            )

    with pytest.raises(ValueError, match="^Renamed file missing previous_path$"):
        asyncio.run(run_test())

    assert requested_files == []
