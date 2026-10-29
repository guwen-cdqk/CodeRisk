"""Read-only GitHub adapter. Downloaded text is data and is never executed."""

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.core.errors import ModuleError
from app.schemas.models import ChangedFile, PullRequestData, SourceLocation

API_ROOT = "https://api.github.com"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?:.*)")


@dataclass(frozen=True)
class GitHubLimits:
    """Operator-tunable safety defaults, not approved Q-03 product budgets."""

    max_files: int = 299  # compare's 300-file boundary cannot prove completeness
    max_file_bytes: int = 1_000_000
    max_source_bytes: int = 8_000_000
    max_response_bytes: int = 8_000_000
    request_timeout: float = 10.0
    snapshot_timeout: float = 60.0

    def __post_init__(self) -> None:
        for value in vars(self).values():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("GitHub limits must be positive")
        for value in (
            self.max_files,
            self.max_file_bytes,
            self.max_source_bytes,
            self.max_response_bytes,
        ):
            if type(value) is not int:
                raise ValueError("GitHub size and count limits must be integers")
        if self.max_files >= 300:
            raise ValueError("Snapshot validation supports fewer than 300 changed files")


DEFAULT_LIMITS = GitHubLimits()


def _invalid() -> ModuleError:
    return ModuleError("GITHUB_INVALID_RESPONSE", "GitHub returned invalid or incomplete data")


def _validate_repository(repository: str) -> None:
    if (
        not isinstance(repository, str)
        or not REPOSITORY.fullmatch(repository)
        or any(part in (".", "..") for part in repository.split("/"))
    ):
        raise ModuleError(
            "INVALID_REPOSITORY", "Expected repository as owner/name", status_code=422
        )


def _validate_number(number: int) -> None:
    if type(number) is not int or number <= 0:
        raise ModuleError(
            "INVALID_PR_NUMBER", "PR number must be a positive integer", status_code=422
        )


def _http_error(status: int, headers: httpx.Headers, body: bytes) -> ModuleError:
    limited = status == 429 or (
        status == 403 and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers)
    )
    if status == 403 and not limited:
        try:
            message = json.loads(body).get("message", "").lower()
            limited = "rate limit" in message or "secondary rate" in message
        except (ValueError, AttributeError):
            pass
    if limited:
        return ModuleError(
            "GITHUB_RATE_LIMITED", "GitHub rate limit exceeded", status_code=429, retryable=True
        )
    if status in (401, 403):
        return ModuleError("GITHUB_FORBIDDEN", "GitHub access denied", status_code=403)
    if status == 404:
        return ModuleError(
            "GITHUB_NOT_FOUND", "GitHub resource is missing or inaccessible", status_code=404
        )
    return ModuleError("GITHUB_HTTP_ERROR", "GitHub request failed", retryable=status >= 500)


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict | None = None,
    limits: GitHubLimits = DEFAULT_LIMITS,
) -> dict | list:
    """Bound decoded HTTP bytes, disable redirects, and expose only safe errors."""
    try:
        async with client.stream(
            "GET",
            f"{API_ROOT}{path}",
            params=params,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=limits.request_timeout,
            follow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > limits.max_response_bytes:
                    raise ModuleError("GITHUB_RESPONSE_TOO_LARGE", "GitHub response exceeds limit")
            if response.status_code != 200:
                raise _http_error(response.status_code, response.headers, bytes(body))
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                raise _invalid() from None
            if not isinstance(data, (dict, list)):
                raise _invalid()
            return data
    except httpx.TimeoutException:
        raise ModuleError(
            "GITHUB_TIMEOUT", "GitHub request timed out", status_code=504, retryable=True
        ) from None
    except httpx.RequestError:
        raise ModuleError(
            "GITHUB_CONNECTION_ERROR", "Cannot connect to GitHub", retryable=True
        ) from None


def _patch_truncated(file_info: dict) -> bool:
    patch = file_info.get("patch")
    if patch is None:
        return False
    if not isinstance(patch, str):
        raise _invalid()
    expected_old = expected_new = actual_old = actual_new = 0
    additions = deletions = 0
    found_hunk = truncated = False
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    for line in lines:
        match = HUNK.fullmatch(line)
        if match:
            if found_hunk:
                truncated |= actual_old < expected_old or actual_new < expected_new
            expected_old = int(match[1]) if match[1] is not None else 1
            expected_new = int(match[2]) if match[2] is not None else 1
            actual_old = actual_new = 0
            found_hunk = True
        elif found_hunk and line.startswith((" ", "+", "-")):
            actual_old += line[0] in " -"
            actual_new += line[0] in " +"
            additions += line.startswith("+")
            deletions += line.startswith("-")
            if actual_old > expected_old or actual_new > expected_new:
                raise _invalid()
        elif line != "\\ No newline at end of file":
            raise _invalid()
    if found_hunk:
        truncated |= actual_old < expected_old or actual_new < expected_new
    for key, actual in (("additions", additions), ("deletions", deletions)):
        expected = file_info.get(key)
        if expected is not None:
            if type(expected) is not int or expected < actual:
                raise _invalid()
            truncated |= expected > actual
    return truncated


def build_changed_file(file_info: dict) -> ChangedFile:
    return ChangedFile(
        path=file_info["filename"],
        previous_path=file_info.get("previous_filename"),
        status=file_info["status"],
        patch=file_info.get("patch"),
        patch_truncated=_patch_truncated(file_info),
    )


async def fetch_changed_files(
    repository: str,
    number: int,
    client: httpx.AsyncClient,
    *,
    max_files: int = 3000,
    limits: GitHubLimits = DEFAULT_LIMITS,
) -> list[dict]:
    _validate_repository(repository)
    _validate_number(number)
    all_files: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 31):
        files = await _get_json(
            client,
            f"/repos/{repository}/pulls/{number}/files",
            params={"per_page": 100, "page": page},
            limits=limits,
        )
        if not isinstance(files, list) or len(files) > 100:
            raise _invalid()
        for file in files:
            if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
                raise _invalid()
            if file["filename"] in seen:
                raise ModuleError(
                    "GITHUB_INCOMPLETE_FILES", "GitHub returned duplicate file entries"
                )
            seen.add(file["filename"])
        all_files.extend(files)
        if len(all_files) > max_files:
            raise ModuleError("GITHUB_FILE_LIMIT", "PR changed file count exceeds limit")
        if len(files) < 100:
            return all_files
    raise ModuleError("GITHUB_INCOMPLETE_FILES", "GitHub file pagination reached its API limit")


async def fetch_file_content(
    repository: str,
    path: str,
    commit_sha: str,
    client: httpx.AsyncClient,
    *,
    limits: GitHubLimits = DEFAULT_LIMITS,
    expected_blob: str | None = None,
    require_metadata: bool = False,
) -> str:
    _validate_repository(repository)
    SourceLocation.relative_posix_path(path)
    if not SHA.fullmatch(commit_sha):
        raise ValueError("Expected a fixed commit SHA")
    data = await _get_json(
        client,
        f"/repos/{repository}/contents/{quote(path, safe='/')}",
        params={"ref": commit_sha},
        limits=limits,
    )
    if not isinstance(data, dict) or data.get("type", "file") != "file":
        raise ModuleError("GITHUB_CONTENT_UNAVAILABLE", "GitHub file text is unavailable")
    size = data.get("size")
    if size is not None and (type(size) is not int or size < 0):
        raise _invalid()
    if size is not None and size > limits.max_file_bytes:
        raise ModuleError("GITHUB_FILE_TOO_LARGE", "File exceeds full-source size limit")
    if data.get("encoding") != "base64":
        raise ModuleError("GITHUB_UNSUPPORTED_ENCODING", "GitHub file encoding is unsupported")
    encoded = data.get("content")
    if not isinstance(encoded, str):
        raise ModuleError("GITHUB_CONTENT_UNAVAILABLE", "GitHub file text is unavailable")
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, binascii.Error):
        raise ModuleError(
            "GITHUB_INVALID_BASE64", "GitHub file content is invalid Base64"
        ) from None
    if len(raw) > limits.max_file_bytes:
        raise ModuleError("GITHUB_FILE_TOO_LARGE", "File exceeds full-source size limit")
    if require_metadata and (
        data.get("type") != "file"
        or size is None
        or not isinstance(data.get("sha"), str)
        or not SHA.fullmatch(data["sha"])
    ):
        raise _invalid()
    if size is not None and size != len(raw):
        raise ModuleError("GITHUB_CONTENT_UNAVAILABLE", "GitHub file content is incomplete")
    blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
    if (data.get("sha") is not None and data["sha"] != blob) or (
        expected_blob is not None and expected_blob != blob
    ):
        raise ModuleError(
            "GITHUB_CONTENT_UNAVAILABLE", "GitHub file content does not match its blob"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ModuleError("GITHUB_UNSUPPORTED_ENCODING", "Python source must be UTF-8") from None


async def populate_file_contents(
    repository: str,
    changed_file: ChangedFile,
    base_sha: str,
    head_sha: str,
    client: httpx.AsyncClient,
    *,
    limits: GitHubLimits = DEFAULT_LIMITS,
    expected_blob: str | None = None,
    require_metadata: bool = False,
) -> ChangedFile:
    if changed_file.status == "renamed" and changed_file.previous_path is None:
        raise ValueError("Renamed file missing previous_path")
    if changed_file.status != "added":
        old_path = (
            changed_file.previous_path if changed_file.status == "renamed" else changed_file.path
        )
        changed_file.old_content = await fetch_file_content(
            repository,
            old_path,
            base_sha,
            client,
            limits=limits,
            expected_blob=expected_blob if changed_file.status == "removed" else None,
            require_metadata=require_metadata,
        )
    if changed_file.status != "removed":
        changed_file.new_content = await fetch_file_content(
            repository,
            changed_file.path,
            head_sha,
            client,
            limits=limits,
            expected_blob=expected_blob,
            require_metadata=require_metadata,
        )
    return changed_file


def _metadata(data: dict | list, repository: str, number: int) -> tuple[PullRequestData, int]:
    try:
        if not isinstance(data, dict) or data["number"] != number:
            raise _invalid()
        if data["base"]["repo"]["full_name"].casefold() != repository.casefold():
            raise _invalid()
        count = data["changed_files"]
        if type(count) is not int or count < 0:
            raise _invalid()
        return PullRequestData(
            repository=repository,
            number=number,
            title=data["title"],
            base_ref=data["base"]["ref"],
            head_ref=data["head"]["ref"],
            base_sha=data["base"]["sha"],
            head_sha=data["head"]["sha"],
        ), count
    except (KeyError, TypeError, AttributeError, ValidationError):
        raise _invalid() from None


def _file_index(files: list[dict]) -> dict[str, dict]:
    fields = (
        "filename",
        "previous_filename",
        "status",
        "sha",
        "patch",
        "additions",
        "deletions",
        "changes",
    )
    result = {}
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
            raise _invalid()
        if not isinstance(file.get("sha"), str) or not SHA.fullmatch(file["sha"]):
            raise _invalid()
        for key in ("additions", "deletions", "changes"):
            if type(file.get(key)) is not int or file[key] < 0:
                raise _invalid()
        if file["changes"] != file["additions"] + file["deletions"]:
            raise _invalid()
        if file["filename"] in result:
            raise _invalid()
        result[file["filename"]] = {field: file.get(field) for field in fields}
    return result


class GitHubGateway:
    """A-01 fetch port; caller owns the injected AsyncClient lifecycle.

    Allowlist and limits are trusted server configuration, never request data.
    publish_report belongs to A-04 and is intentionally not implemented here.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        allowed_repositories: frozenset[str],
        limits: GitHubLimits = DEFAULT_LIMITS,
    ) -> None:
        for repository in allowed_repositories:
            _validate_repository(repository)
        self._allowed = frozenset(repo.casefold() for repo in allowed_repositories)
        self._client = client
        self._limits = limits

    async def fetch_pull_request(self, repository: str, number: int) -> PullRequestData:
        _validate_repository(repository)
        _validate_number(number)
        if repository.casefold() not in self._allowed:
            raise ModuleError(
                "REPOSITORY_NOT_ALLOWED", "Repository is not allowed", status_code=403
            )
        try:
            async with asyncio.timeout(self._limits.snapshot_timeout):
                return await self._fetch(repository, number)
        except TimeoutError:
            raise ModuleError(
                "GITHUB_TIMEOUT", "GitHub snapshot timed out", status_code=504, retryable=True
            ) from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise _invalid() from None

    async def _fetch(self, repository: str, number: int) -> PullRequestData:
        path = f"/repos/{repository}/pulls/{number}"
        pr, count = _metadata(
            await _get_json(self._client, path, limits=self._limits), repository, number
        )
        if count > self._limits.max_files:
            raise ModuleError("GITHUB_FILE_LIMIT", "PR changed file count exceeds snapshot limit")
        files = await fetch_changed_files(
            repository, number, self._client, max_files=self._limits.max_files, limits=self._limits
        )
        if len(files) != count:
            raise ModuleError(
                "GITHUB_INCOMPLETE_FILES", "PR file list is incomplete", retryable=True
            )
        # PR /files is mutable. An immutable comparison detects mixed pages and ABA changes.
        comparison = await _get_json(
            self._client,
            f"/repos/{repository}/compare/{pr.base_sha}...{pr.head_sha}",
            params={"per_page": 1, "page": 1},
            limits=self._limits,
        )
        if not isinstance(comparison, dict):
            raise _invalid()
        if comparison.get("base_commit", {}).get("sha") != pr.base_sha:
            raise _invalid()
        if comparison.get("merge_base_commit", {}).get("sha") != pr.base_sha:
            raise ModuleError(
                "GITHUB_UNSUPPORTED_BASE", "PR diff origin differs from the declared base commit"
            )
        pinned_files = comparison.get("files")
        if not isinstance(pinned_files, list):
            raise _invalid()
        if _file_index(files) != _file_index(pinned_files):
            raise ModuleError(
                "GITHUB_SNAPSHOT_CHANGED",
                "PR files differ from the fixed SHA comparison",
                retryable=True,
            )
        total_source_bytes = 0
        for info in files:
            if info.get("status") not in ("added", "removed", "modified", "renamed"):
                raise ModuleError("GITHUB_UNSUPPORTED_STATUS", "GitHub file status is unsupported")
            changed = build_changed_file(info)
            if changed.status == "renamed" and changed.previous_path is None:
                raise ModuleError(
                    "GITHUB_MISSING_PREVIOUS_PATH", "Renamed file missing previous_path"
                )
            if changed.path.endswith(".py") or (changed.previous_path or "").endswith(".py"):
                await populate_file_contents(
                    repository,
                    changed,
                    pr.base_sha,
                    pr.head_sha,
                    self._client,
                    limits=self._limits,
                    expected_blob=info["sha"],
                    require_metadata=True,
                )
                total_source_bytes += sum(
                    len(text.encode("utf-8"))
                    for text in (changed.old_content, changed.new_content)
                    if text is not None
                )
                if total_source_bytes > self._limits.max_source_bytes:
                    raise ModuleError("GITHUB_SOURCE_LIMIT", "PR full-source budget exceeded")
            pr.files.append(changed)
        latest, latest_count = _metadata(
            await _get_json(self._client, path, limits=self._limits), repository, number
        )
        if (latest.base_sha, latest.head_sha, latest.base_ref, latest.head_ref, latest_count) != (
            pr.base_sha,
            pr.head_sha,
            pr.base_ref,
            pr.head_ref,
            count,
        ):
            raise ModuleError(
                "GITHUB_SNAPSHOT_CHANGED", "PR changed while fetching", retryable=True
            )
        pr.fetched_at = datetime.now(UTC)
        return pr
