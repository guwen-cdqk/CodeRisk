"""Synthetic GitHub HTTP fixtures; no credentials, network, or executable PR input."""

import asyncio
import base64
import copy
import hashlib
from dataclasses import replace

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ModuleError
from app.integrations.github import (
    DEFAULT_LIMITS,
    GitHubGateway,
    GitHubLimits,
    build_changed_file,
    fetch_changed_files,
    fetch_file_content,
)
from app.schemas.models import PullRequestData

REPO = "demo/repo"
BASE = "b" * 40
HEAD = "a" * 40
OLD = b"print('old')\n"
NEW = b"print('new')\n"
PATCH = "@@ -1 +1 @@\n-print('old')\n+print('new')\n"


def blob(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()


def content(raw):
    return {
        "type": "file",
        "size": len(raw),
        "sha": blob(raw),
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
    }


def file_info(status="modified", path="src/demo.py", **overrides):
    result = {
        "filename": path,
        "status": status,
        "sha": blob(OLD if status == "removed" else NEW),
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "patch": PATCH,
    }
    if status == "renamed":
        result["previous_filename"] = "src/old.py"
    if status == "added":
        result.update(additions=1, deletions=0, changes=1, patch="@@ -0,0 +1 @@\n+print('new')\n")
    if status == "removed":
        result.update(additions=0, deletions=1, changes=1, patch="@@ -1 +0,0 @@\n-print('old')\n")
    result.update(overrides)
    return result


class FakeGitHub:
    def __init__(self, files=None):
        self.files = [file_info()] if files is None else files
        self.pinned = copy.deepcopy(self.files)
        self.metadata = {
            "number": 1,
            "title": "Synthetic PR",
            "changed_files": len(self.files),
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO}},
            "head": {"sha": HEAD, "ref": "feature"},
        }
        self.latest = copy.deepcopy(self.metadata)
        self.comparison = {
            "base_commit": {"sha": BASE},
            "merge_base_commit": {"sha": BASE},
            "files": self.pinned,
        }
        self.requests = []
        self.metadata_calls = 0
        self.source_response = None

    def __call__(self, request):
        assert request.url.host == "api.github.com"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/pulls/1"):
            self.metadata_calls += 1
            return httpx.Response(
                200, json=self.metadata if self.metadata_calls == 1 else self.latest
            )
        if path.endswith("/pulls/1/files"):
            assert request.url.params["per_page"] == "100"
            page = int(request.url.params["page"])
            return httpx.Response(200, json=self.files[(page - 1) * 100 : page * 100])
        if "/compare/" in path:
            assert path.endswith(f"/compare/{BASE}...{HEAD}")
            assert request.url.params["page"] == "1"
            return httpx.Response(200, json=self.comparison)
        if "/contents/" in path:
            assert request.url.params["ref"] in (BASE, HEAD)
            if self.source_response is not None:
                return self.source_response
            return httpx.Response(
                200, json=content(OLD if request.url.params["ref"] == BASE else NEW)
            )
        raise AssertionError(f"Unexpected request: {request.url}")


def fetch(fake, *, repository=REPO, number=1, allowed=frozenset({REPO}), limits=DEFAULT_LIMITS):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(fake), trust_env=False
        ) as client:
            gateway = GitHubGateway(client, allowed_repositories=allowed, limits=limits)
            result = await gateway.fetch_pull_request(repository, number)
            assert not client.is_closed  # injected client belongs to the caller
            return result

    return asyncio.run(run())


@pytest.mark.parametrize("status", ["added", "removed", "modified", "renamed"])
def test_gateway_statuses(status):
    fake = FakeGitHub([file_info(status)])
    pr = fetch(fake)
    assert PullRequestData.model_validate_json(pr.model_dump_json()) == pr
    assert (pr.repository, pr.number, pr.title, pr.base_ref, pr.head_ref) == (
        REPO,
        1,
        "Synthetic PR",
        "main",
        "feature",
    )
    assert (pr.base_sha, pr.head_sha) == (BASE, HEAD)
    assert pr.fetched_at.utcoffset().total_seconds() == 0
    changed = pr.files[0]
    assert changed.old_content == (None if status == "added" else OLD.decode())
    assert changed.new_content == (None if status == "removed" else NEW.decode())
    assert not changed.patch_truncated
    requests = [
        (r.url.path.split("/contents/")[1], r.url.params["ref"])
        for r in fake.requests
        if "/contents/" in r.url.path
    ]
    expected = []
    if status != "added":
        expected.append(("src/old.py" if status == "renamed" else "src/demo.py", BASE))
    if status != "removed":
        expected.append(("src/demo.py", HEAD))
    assert requests == expected


def test_gateway_pagination_and_non_python_pr():
    fake = FakeGitHub([file_info(path=f"docs/{i}.md") for i in range(201)])
    pr = fetch(fake)
    assert len(pr.files) == 201
    assert all(f.new_content is None and f.old_content is None for f in pr.files)
    assert [int(r.url.params["page"]) for r in fake.requests if r.url.path.endswith("/files")] == [
        1,
        2,
        3,
    ]
    assert len(fake.requests) == 6  # metadata twice, pages three times, one pinned compare


def test_empty_pr_is_valid_snapshot():
    fake = FakeGitHub([])
    assert fetch(fake).files == []


@pytest.mark.parametrize(
    "path,previous", [("src/new.txt", "src/old.py"), ("src/new.py", "src/old.txt")]
)
def test_renames_across_python_extension(path, previous):
    fake = FakeGitHub([file_info("renamed", path, previous_filename=previous)])
    changed = fetch(fake).files[0]
    assert changed.old_content == OLD.decode()
    assert changed.new_content == NEW.decode()
    assert changed.previous_path == previous


@pytest.mark.parametrize(
    "patch,truncated",
    [
        (None, False),
        (PATCH, False),
        ("@@ -1 +1 @@\n-print('old')\n", True),
        ("@@ -1,2 +1,2 @@\n-print('old')\n+print('new')\n", True),
    ],
)
def test_gateway_patch_availability(patch, truncated):
    pr = fetch(FakeGitHub([file_info(patch=patch)]))
    assert pr.files[0].patch == patch
    assert pr.files[0].patch_truncated is truncated
    assert pr.files[0].new_content == NEW.decode()


def test_patch_missing_hunk_detected_by_counts():
    changed = build_changed_file(file_info(additions=2, changes=3))
    assert changed.patch_truncated


def test_patch_multiple_hunks_and_no_newline_marker():
    patch = "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n@@ -4 +4 @@\n-old\n+new\n"
    assert not build_changed_file(
        file_info(patch=patch, additions=2, deletions=2, changes=4)
    ).patch_truncated


@pytest.mark.parametrize(
    "repository,number,code",
    [
        ("https://evil.test/demo/repo", 1, "INVALID_REPOSITORY"),
        ("../repo", 1, "INVALID_REPOSITORY"),
        ("demo/..", 1, "INVALID_REPOSITORY"),
        ("demo/repo?x=1", 1, "INVALID_REPOSITORY"),
        ("demo/repo/extra", 1, "INVALID_REPOSITORY"),
        ("demo\\repo", 1, "INVALID_REPOSITORY"),
        ("", 1, "INVALID_REPOSITORY"),
        ("other/repo", 1, "REPOSITORY_NOT_ALLOWED"),
        (REPO, 0, "INVALID_PR_NUMBER"),
        (REPO, -1, "INVALID_PR_NUMBER"),
        (REPO, True, "INVALID_PR_NUMBER"),
        (REPO, "1", "INVALID_PR_NUMBER"),
    ],
)
def test_invalid_input_denied_before_http(repository, number, code):
    fake = FakeGitHub()
    with pytest.raises(ModuleError) as caught:
        fetch(fake, repository=repository, number=number)
    assert caught.value.code == code
    assert fake.requests == []


def test_allowlist_case_insensitive_and_empty_denies():
    assert fetch(FakeGitHub(), repository="Demo/Repo").repository == "Demo/Repo"
    fake = FakeGitHub()
    with pytest.raises(ModuleError, match="Repository is not allowed"):
        fetch(fake, allowed=frozenset())
    assert fake.requests == []


def test_server_allowlist_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("CODERISK_GITHUB_ALLOWED_REPOSITORIES", '["demo/repo"]')
    assert Settings(_env_file=None).github_allowed_repositories == frozenset({REPO})


@pytest.mark.parametrize(
    "status,headers,message,code,public_status,retryable",
    [
        (401, {}, "secret", "GITHUB_FORBIDDEN", 403, False),
        (403, {}, "secret", "GITHUB_FORBIDDEN", 403, False),
        (403, {"x-ratelimit-remaining": "0"}, "secret", "GITHUB_RATE_LIMITED", 429, True),
        (403, {"retry-after": "60"}, "secret", "GITHUB_RATE_LIMITED", 429, True),
        (403, {}, "You have exceeded a secondary rate limit", "GITHUB_RATE_LIMITED", 429, True),
        (404, {}, "secret", "GITHUB_NOT_FOUND", 404, False),
        (429, {}, "secret", "GITHUB_RATE_LIMITED", 429, True),
        (500, {}, "secret", "GITHUB_HTTP_ERROR", 502, True),
        (422, {}, "secret", "GITHUB_HTTP_ERROR", 502, False),
        (302, {"location": "https://evil.test"}, "secret", "GITHUB_HTTP_ERROR", 502, False),
    ],
)
def test_http_errors(status, headers, message, code, public_status, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers=headers, json={"message": message})

    with pytest.raises(ModuleError) as caught:
        fetch(handler)
    error = caught.value
    assert (error.code, error.status_code, error.retryable) == (code, public_status, retryable)
    assert "secret" not in str(error)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (httpx.ReadTimeout, "GITHUB_TIMEOUT", 504),
        (httpx.ConnectTimeout, "GITHUB_TIMEOUT", 504),
        (httpx.ConnectError, "GITHUB_CONNECTION_ERROR", 502),
        (httpx.ReadError, "GITHUB_CONNECTION_ERROR", 502),
    ],
)
def test_network_failures(exception, code, status):
    def handler(request):
        raise exception("secret token and source", request=request)

    with pytest.raises(ModuleError) as caught:
        fetch(handler)
    assert (caught.value.code, caught.value.status_code, caught.value.retryable) == (
        code,
        status,
        True,
    )
    assert "secret" not in str(caught.value)


def test_total_snapshot_timeout():
    async def handler(request):
        await asyncio.sleep(1)
        raise AssertionError("Should be cancelled")

    with pytest.raises(ModuleError) as caught:
        fetch(handler, limits=GitHubLimits(snapshot_timeout=0.01))
    assert caught.value.code == "GITHUB_TIMEOUT"


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda f: f.metadata.update(changed_files=300), "GITHUB_FILE_LIMIT"),
        (lambda f: f.metadata.update(changed_files=2), "GITHUB_INCOMPLETE_FILES"),
        (lambda f: f.files.append(copy.deepcopy(f.files[0])), "GITHUB_INCOMPLETE_FILES"),
        (lambda f: f.pinned[0].update(patch=None), "GITHUB_SNAPSHOT_CHANGED"),
        (lambda f: f.pinned.clear(), "GITHUB_SNAPSHOT_CHANGED"),
        (lambda f: f.pinned[0].update(sha="c" * 40), "GITHUB_SNAPSHOT_CHANGED"),
        (lambda f: f.latest["head"].update(sha="c" * 40), "GITHUB_SNAPSHOT_CHANGED"),
        (lambda f: f.latest["base"].update(sha="c" * 40), "GITHUB_SNAPSHOT_CHANGED"),
        (lambda f: f.latest.update(changed_files=2), "GITHUB_SNAPSHOT_CHANGED"),
        (
            lambda f: f.comparison["merge_base_commit"].update(sha="c" * 40),
            "GITHUB_UNSUPPORTED_BASE",
        ),
        (lambda f: f.comparison["base_commit"].update(sha="c" * 40), "GITHUB_INVALID_RESPONSE"),
    ],
)
def test_snapshot_incompleteness_and_mutation(change, code):
    fake = FakeGitHub()
    change(fake)
    with pytest.raises(ModuleError) as caught:
        fetch(fake)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "info,code",
    [
        (file_info(status="copied"), "GITHUB_UNSUPPORTED_STATUS"),
        (file_info("renamed", previous_filename=None), "GITHUB_MISSING_PREVIOUS_PATH"),
        (file_info(path="../secret.py"), "GITHUB_INVALID_RESPONSE"),
        (file_info(patch="not a diff"), "GITHUB_INVALID_RESPONSE"),
        (file_info(sha="bad"), "GITHUB_INVALID_RESPONSE"),
        (file_info(changes=20), "GITHUB_INVALID_RESPONSE"),
    ],
)
def test_invalid_file_data(info, code):
    with pytest.raises(ModuleError) as caught:
        fetch(FakeGitHub([info]))
    assert caught.value.code == code


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"type": "dir"}, "GITHUB_CONTENT_UNAVAILABLE"),
        ({"type": "symlink"}, "GITHUB_CONTENT_UNAVAILABLE"),
        ({"encoding": "none", "content": ""}, "GITHUB_UNSUPPORTED_ENCODING"),
        ({"encoding": "gzip", "content": ""}, "GITHUB_UNSUPPORTED_ENCODING"),
        ({"encoding": "base64", "content": "!!!!"}, "GITHUB_INVALID_BASE64"),
        ({"encoding": "base64", "content": "abc"}, "GITHUB_INVALID_BASE64"),
        ({"encoding": "base64"}, "GITHUB_CONTENT_UNAVAILABLE"),
        (content(b"\xff"), "GITHUB_UNSUPPORTED_ENCODING"),
        (dict(content(OLD), size=999), "GITHUB_CONTENT_UNAVAILABLE"),
        (dict(content(OLD), size=1_000_001), "GITHUB_FILE_TOO_LARGE"),
        (dict(content(OLD), sha="c" * 40), "GITHUB_CONTENT_UNAVAILABLE"),
    ],
)
def test_content_failures(payload, code):
    fake = FakeGitHub()
    fake.source_response = httpx.Response(200, json=payload)
    with pytest.raises(ModuleError) as caught:
        fetch(fake)
    assert caught.value.code == code


def test_content_404_is_failure_not_empty_source():
    fake = FakeGitHub()
    fake.source_response = httpx.Response(404, json={"message": "secret"})
    with pytest.raises(ModuleError) as caught:
        fetch(fake)
    assert caught.value.code == "GITHUB_NOT_FOUND"


def test_zero_byte_python_file_is_available():
    info = file_info("added", sha=blob(b""), patch=None, additions=0, changes=0)
    fake = FakeGitHub([info])
    fake.source_response = httpx.Response(200, json=content(b""))
    assert fetch(fake).files[0].new_content == ""


@pytest.mark.parametrize(
    "limits,code",
    [
        (GitHubLimits(max_file_bytes=1), "GITHUB_FILE_TOO_LARGE"),
        (GitHubLimits(max_source_bytes=1), "GITHUB_SOURCE_LIMIT"),
        (GitHubLimits(max_response_bytes=1), "GITHUB_RESPONSE_TOO_LARGE"),
    ],
)
def test_size_budgets(limits, code):
    with pytest.raises(ModuleError) as caught:
        fetch(FakeGitHub(), limits=limits)
    assert caught.value.code == code


def test_decoded_size_checked_even_without_declared_size():
    fake = FakeGitHub()
    payload = content(OLD)
    del payload["size"]
    fake.source_response = httpx.Response(200, json=payload)
    with pytest.raises(ModuleError) as caught:
        fetch(fake, limits=GitHubLimits(max_file_bytes=1))
    assert caught.value.code == "GITHUB_FILE_TOO_LARGE"


def test_repeated_pagination_is_bounded():
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        return httpx.Response(200, json=[{"filename": f"{page}-{i}.py"} for i in range(100)])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_changed_files(REPO, 1, client)

    with pytest.raises(ModuleError) as caught:
        asyncio.run(run())
    assert caught.value.code == "GITHUB_INCOMPLETE_FILES"
    assert calls == list(range(1, 31))


@pytest.mark.parametrize("payload", [None, "secret", {}, {"number": 1}, {"number": 2}])
def test_malformed_metadata(payload):
    with pytest.raises(ModuleError) as caught:
        fetch(lambda request: httpx.Response(200, json=payload))
    assert caught.value.code == "GITHUB_INVALID_RESPONSE"


def test_invalid_json_is_safe():
    with pytest.raises(ModuleError) as caught:
        fetch(lambda request: httpx.Response(200, content=b"invalid secret json"))
    assert caught.value.code == "GITHUB_INVALID_RESPONSE"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "options",
    [
        {"max_files": 300},
        {"max_files": 0},
        {"max_files": 1.5},
        {"max_file_bytes": -1},
        {"snapshot_timeout": float("nan")},
        {"request_timeout": float("inf")},
        {"max_source_bytes": True},
    ],
)
def test_invalid_operator_limits(options):
    with pytest.raises(ValueError):
        GitHubLimits(**options)


def test_source_url_reserved_characters_are_encoded():
    path = "src/a?#%.py"

    async def run():
        def handler(request):
            assert dict(request.url.params) == {"ref": HEAD}
            assert request.url.fragment == ""
            assert b"a%3F%23%25.py" in request.url.raw_path
            return httpx.Response(200, json=content(NEW))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_file_content(REPO, path, HEAD, client)

    assert asyncio.run(run()) == NEW.decode()


def test_exact_configured_file_limit_is_allowed():
    fake = FakeGitHub([file_info(path="docs/one.md")])
    assert len(fetch(fake, limits=replace(GitHubLimits(), max_files=1)).files) == 1


@pytest.mark.parametrize("field", ["size", "sha", "type"])
def test_gateway_requires_full_content_metadata(field):
    fake = FakeGitHub()
    payload = content(OLD)
    del payload[field]
    fake.source_response = httpx.Response(200, json=payload)
    with pytest.raises(ModuleError) as caught:
        fetch(fake)
    assert caught.value.code == "GITHUB_INVALID_RESPONSE"


def test_patch_unicode_line_separator_is_source_text():
    patch = "@@ -1 +1 @@\n-old\n+new\u2028still_same_line\n"
    assert not build_changed_file(file_info(patch=patch)).patch_truncated


@pytest.mark.parametrize("stage", ["files", "compare", "contents"])
def test_later_http_failures_abort_snapshot(stage):
    fake = FakeGitHub()

    def handler(request):
        if f"/{stage}/" in request.url.path or request.url.path.endswith(f"/{stage}"):
            return httpx.Response(429, json={"message": "private source and token"})
        return fake(request)

    with pytest.raises(ModuleError) as caught:
        fetch(handler)
    assert caught.value.code == "GITHUB_RATE_LIMITED"
    assert "private" not in str(caught.value)
    assert fake.metadata_calls == 1


def test_mixed_pages_rejected_even_when_metadata_returns_to_original_sha():
    fake = FakeGitHub([file_info(path=f"docs/{i}.md") for i in range(101)])
    fake.files[100] = file_info(path="docs/mixed.md")
    with pytest.raises(ModuleError) as caught:
        fetch(fake)
    assert caught.value.code == "GITHUB_SNAPSHOT_CHANGED"
    assert fake.metadata["head"]["sha"] == fake.latest["head"]["sha"] == HEAD


def test_oversized_response_closes_http_stream():
    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"{" * 20
            raise AssertionError("Must stop reading after limit")

        async def aclose(self):
            self.closed = True

    stream = Stream()
    with pytest.raises(ModuleError) as caught:
        fetch(
            lambda request: httpx.Response(200, stream=stream),
            limits=GitHubLimits(max_response_bytes=10),
        )
    assert caught.value.code == "GITHUB_RESPONSE_TOO_LARGE"
    assert stream.closed


def test_caller_cancellation_is_not_converted_to_module_error():
    async def handler(request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        fetch(handler)
