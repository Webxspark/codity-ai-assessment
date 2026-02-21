"""GitHub integration service — fetches commits, diffs, and file contents.

Communicates with the GitHub REST API via httpx to:
- List recent commits (with diffs)
- Get specific commit details and patches
- Read file contents from the repo
- Search code within the repo
"""

from datetime import datetime, timedelta
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
DIFF_MAX_CHARS = 30_000  # cap diff size to avoid blowing LLM context


class GitHubService:
    """Async GitHub REST API client."""

    def __init__(self, repo: str, token: str | None = None):
        """
        Args:
            repo: "owner/repo" format
            token: GitHub PAT or OAuth token (optional for public repos)
        """
        self.repo = repo
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

        resp = await self.client.get(f"/repos/{self.repo}/commits", params=params)
        resp.raise_for_status()
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
        resp = await self.client.get(f"/repos/{self.repo}/commits/{sha}")
        resp.raise_for_status()
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

        resp = await self.client.get(
            f"/repos/{self.repo}/contents/{path}",
            params=params,
        )
        resp.raise_for_status()
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

        resp = await self.client.get(
            f"/repos/{self.repo}/contents/{path}",
            params=params,
        )
        resp.raise_for_status()
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
        resp = await self.client.get(
            "/search/code",
            params={"q": search_query, "per_page": min(limit, 30)},
        )
        resp.raise_for_status()
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
        resp = await self.client.get(f"/repos/{self.repo}")
        resp.raise_for_status()
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
            since = datetime.utcnow() - timedelta(hours=48)

        commits = await self.get_recent_commits(branch=branch, since=since, limit=limit)

        # Find which SHAs already exist
        existing_shas = set()
        if commits:
            shas = [c["sha"] for c in commits]
            result = await db_session.execute(
                select(DeploymentLog.commit_sha).where(DeploymentLog.commit_sha.in_(shas))
            )
            existing_shas = {r[0] for r in result.all()}

        new_deployments = []
        for commit in commits:
            if commit["sha"] in existing_shas:
                continue

            # Fetch full detail with diff
            detail = await self.get_commit_detail(commit["sha"])

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
