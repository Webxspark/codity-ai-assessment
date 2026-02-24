"""GitHub integration service — fetches commits, diffs, and file contents.

Communicates with the GitHub REST API via httpx to:
- List recent commits (with diffs)
- Get specific commit details and patches
- Read file contents from the repo
- Search code within the repo

Includes rate-limit awareness: checks X-RateLimit-Remaining headers
and backs off automatically when close to exhaustion.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
DIFF_MAX_CHARS = 30_000  # cap diff size to avoid blowing LLM context
RATE_LIMIT_BUFFER = 5  # stop making requests when remaining <= this

logger = logging.getLogger(__name__)


class GitHubRateLimitError(Exception):
    """Raised when GitHub rate limit is exhausted."""

    def __init__(self, reset_at: datetime, remaining: int = 0):
        self.reset_at = reset_at
        self.remaining = remaining
        wait = max(0, int((reset_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()))
        super().__init__(
            f"GitHub rate limit exhausted ({remaining} remaining). "
            f"Resets in {wait}s at {reset_at.isoformat()}Z. "
            f"Use a GitHub token for 5 000 req/hr instead of 60."
        )


class GitHubService:
    """Async GitHub REST API client with rate-limit awareness."""

    def __init__(self, repo: str, token: str | None = None):
        """
        Args:
            repo: "owner/repo" format
            token: GitHub PAT or OAuth token (optional for public repos)
        """
        self.repo = repo
        self._authenticated = bool(token)
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "CodityAI/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers=headers,
            timeout=30.0,
        )

    async def close(self):
        await self.client.aclose()

    # ── Rate-limit helpers ───────────────────────────────────────────

    def _check_rate_limit(self, resp: httpx.Response) -> None:
        """Inspect response headers and raise if we're about to be limited."""
        remaining = resp.headers.get("x-ratelimit-remaining")
        reset_ts = resp.headers.get("x-ratelimit-reset")

        if remaining is not None:
            remaining_int = int(remaining)
            limit = resp.headers.get("x-ratelimit-limit", "?")
            logger.debug(f"GitHub rate limit: {remaining_int}/{limit} remaining")

            if remaining_int <= RATE_LIMIT_BUFFER and reset_ts:
                reset_at = datetime.utcfromtimestamp(int(reset_ts))
                raise GitHubRateLimitError(reset_at, remaining_int)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make a rate-limit-aware request with retry on 403."""
        resp = await self.client.request(method, url, **kwargs)

        # Handle 403 rate limit explicitly
        if resp.status_code == 403:
            reset_ts = resp.headers.get("x-ratelimit-reset")
            remaining = int(resp.headers.get("x-ratelimit-remaining", "0"))
            if reset_ts and remaining == 0:
                reset_at = datetime.utcfromtimestamp(int(reset_ts))
                raise GitHubRateLimitError(reset_at, remaining)
            # Not a rate limit 403 — raise normally
            resp.raise_for_status()

        resp.raise_for_status()
        self._check_rate_limit(resp)
        return resp

    async def get_rate_limit_status(self) -> dict:
        """Check current rate limit status without consuming a request."""
        resp = await self.client.get("/rate_limit")
        data = resp.json()
        core = data.get("resources", {}).get("core", {})
        return {
            "limit": core.get("limit", 0),
            "remaining": core.get("remaining", 0),
            "reset_at": datetime.utcfromtimestamp(core.get("reset", 0)).isoformat() + "Z",
            "authenticated": self._authenticated,
        }

    # ── Commits ──────────────────────────────────────────────────────

    async def get_recent_commits(
        self,
        branch: str = "main",
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Fetch recent commits from the repo.

        Returns a list of compact commit dicts with SHA, message, author,
        date, and list of changed files.
        """
        params: dict[str, Any] = {"sha": branch, "per_page": min(limit, 100)}
        if since:
            params["since"] = since.isoformat() + "Z"

        resp = await self._request("GET", f"/repos/{self.repo}/commits", params=params)
        raw = resp.json()

        commits = []
        for c in raw[:limit]:
            commits.append({
                "sha": c["sha"],
                "message": (c["commit"]["message"] or "")[:500],
                "author": c["commit"]["author"]["name"],
                "author_email": c["commit"]["author"].get("email", ""),
                "date": c["commit"]["author"]["date"],
                "url": c["html_url"],
            })
        return commits

    async def get_commit_detail(self, sha: str) -> dict:
        """Get full commit detail including file-level diffs (patches).

        Returns commit info + list of files with their patches.
        """
        resp = await self._request("GET", f"/repos/{self.repo}/commits/{sha}")
        c = resp.json()

        files = []
        total_diff_size = 0
        for f in c.get("files", []):
            patch = f.get("patch", "")
            # Truncate individual patches to keep total manageable
            if total_diff_size + len(patch) > DIFF_MAX_CHARS:
                patch = patch[: max(0, DIFF_MAX_CHARS - total_diff_size)] + "\n... [truncated]"
            total_diff_size += len(patch)

            files.append({
                "filename": f["filename"],
                "status": f["status"],  # added, modified, removed, renamed
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": patch,
            })

        return {
            "sha": c["sha"],
            "message": (c["commit"]["message"] or "")[:500],
            "author": c["commit"]["author"]["name"],
            "author_email": c["commit"]["author"].get("email", ""),
            "date": c["commit"]["author"]["date"],
            "url": c["html_url"],
            "stats": c.get("stats", {}),
            "files": files,
        }

    # ── File contents ────────────────────────────────────────────────

    async def get_file_content(
        self,
        path: str,
        ref: str | None = None,
    ) -> dict:
        """Fetch file content from the repo.

        Returns decoded content (UTF-8) and metadata.
        """
        import base64

        params = {}
        if ref:
            params["ref"] = ref

        resp = await self._request(
            "GET", f"/repos/{self.repo}/contents/{path}",
            params=params,
        )
        data = resp.json()

        # Handle file too large for contents API
        if data.get("type") != "file":
            return {"error": f"Path is a {data.get('type', 'unknown')}, not a file", "path": path}

        content = ""
        if data.get("encoding") == "base64" and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

        return {
            "path": data["path"],
            "size": data.get("size", 0),
            "sha": data["sha"],
            "content": content[:50_000],  # cap at 50KB
            "url": data.get("html_url", ""),
        }

    async def get_directory_listing(self, path: str = "", ref: str | None = None) -> list[dict]:
        """List files in a directory."""
        params = {}
        if ref:
            params["ref"] = ref

        resp = await self._request(
            "GET", f"/repos/{self.repo}/contents/{path}",
            params=params,
        )
        data = resp.json()

        if not isinstance(data, list):
            return [{"name": data.get("name", path), "type": data.get("type", "file"), "path": data.get("path", path)}]

        return [
            {"name": item["name"], "type": item["type"], "path": item["path"], "size": item.get("size", 0)}
            for item in data
        ]

    # ── Search ───────────────────────────────────────────────────────

    async def search_code(self, query: str, limit: int = 10) -> list[dict]:
        """Search for code within the repo.

        Uses GitHub's code search API.
        """
        search_query = f"{query} repo:{self.repo}"
        resp = await self._request(
            "GET", "/search/code",
            params={"q": search_query, "per_page": min(limit, 30)},
        )
        data = resp.json()

        results = []
        for item in data.get("items", [])[:limit]:
            results.append({
                "path": item["path"],
                "name": item["name"],
                "url": item.get("html_url", ""),
                "score": item.get("score", 0),
            })
        return results

    # ── Repo info ────────────────────────────────────────────────────

    async def get_repo_info(self) -> dict:
        """Get basic repo metadata — validates token access."""
        resp = await self._request("GET", f"/repos/{self.repo}")
        r = resp.json()
        return {
            "full_name": r["full_name"],
            "description": r.get("description", ""),
            "default_branch": r.get("default_branch", "main"),
            "private": r["private"],
            "language": r.get("language", ""),
            "url": r["html_url"],
        }

    async def sync_commits_to_deployments(
        self,
        db_session,
        service_name: str,
        branch: str = "main",
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Fetch recent commits and upsert them as DeploymentLog entries.

        Skips commits that already exist (matched by commit_sha).
        Returns list of newly created deployment dicts.
        """
        from sqlalchemy import select
        from app.models.db_models import DeploymentLog

        if since is None:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)

        commits = await self.get_recent_commits(branch=branch, since=since, limit=limit)

        # Find which SHAs already exist
        existing_shas = set()
        if commits:
            shas = [c["sha"] for c in commits]
            result = await db_session.execute(
                select(DeploymentLog.commit_sha).where(DeploymentLog.commit_sha.in_(shas))
            )
            existing_shas = {r[0] for r in result.all()}

        new_commits = [c for c in commits if c["sha"] not in existing_shas]

        if not new_commits:
            return []

        # Check rate limit before batch-fetching details
        rl = await self.get_rate_limit_status()
        available = rl["remaining"]
        # Each commit detail = 1 API call. Cap to what's safely available.
        max_details = max(0, available - RATE_LIMIT_BUFFER)
        if max_details == 0:
            raise GitHubRateLimitError(
                datetime.fromisoformat(rl["reset_at"].replace("Z", "+00:00")).replace(tzinfo=None),
                available,
            )

        # Limit detail fetches to avoid burning the rate limit
        fetch_count = min(len(new_commits), max_details, 15)  # hard cap at 15 per sync
        if fetch_count < len(new_commits):
            logger.warning(
                f"Capping commit detail fetches to {fetch_count}/{len(new_commits)} "
                f"(rate limit remaining: {available})"
            )

        new_deployments = []
        for i, commit in enumerate(new_commits[:fetch_count]):
            # Small delay between requests to be a good API citizen
            if i > 0:
                await asyncio.sleep(0.3)

            try:
                detail = await self.get_commit_detail(commit["sha"])
            except GitHubRateLimitError:
                logger.warning(f"Rate limit hit after {i} detail fetches, stopping sync")
                break

            deploy = DeploymentLog(
                service_name=service_name,
                timestamp=datetime.fromisoformat(detail["date"].replace("Z", "+00:00")).replace(tzinfo=None),
                commit_sha=detail["sha"],
                commit_message=detail["message"],
                author=detail["author"],
                changed_files=[f["filename"] for f in detail["files"]],
                commit_diff="\n".join(
                    f"--- {f['filename']} ---\n{f['patch']}"
                    for f in detail["files"]
                    if f.get("patch")
                )[:DIFF_MAX_CHARS],
                pr_url=detail["url"],
            )
            db_session.add(deploy)
            new_deployments.append({
                "sha": deploy.commit_sha,
                "message": deploy.commit_message,
                "author": deploy.author,
                "files": deploy.changed_files,
            })

        if new_deployments:
            await db_session.flush()

        return new_deployments
