"""GitHub 仓库工具 — 获取仓库信息、文件内容、Issues

使用 GitHub REST API (v3):
- 无需认证可访问公开仓库（速率限制 60 次/小时）
- 配置 GITHUB_TOKEN 后提升到 5000 次/小时
- 支持获取: 仓库元信息、README、文件内容、Issues、Releases
"""

import os
import base64
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


GITHUB_API = "https://api.github.com"


def _get_headers() -> dict[str, str]:
    """构建 GitHub API 请求头"""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Pi-Agent/1.0",
    }
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _parse_repo_url(url: str) -> tuple[str, str] | None:
    """从 GitHub URL 或 owner/repo 格式解析出 (owner, repo)"""
    url = url.strip()
    # https://github.com/owner/repo
    if "github.com" in url:
        parts = url.rstrip("/").split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    # owner/repo
    if "/" in url and not url.startswith("http"):
        parts = url.split("/")
        if len(parts) == 2:
            return parts[0], parts[1]
    return None


async def _get_repo_info(owner: str, repo: str) -> dict[str, Any]:
    """获取仓库基本信息"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers())
        if resp.status_code == 404:
            return {"error": f"仓库 {owner}/{repo} 不存在"}
        if resp.status_code != 200:
            return {"error": f"GitHub API 返回 {resp.status_code}"}

        data = resp.json()
        return {
            "name": data.get("name", ""),
            "full_name": data.get("full_name", ""),
            "description": data.get("description", ""),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "watchers": data.get("watchers_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "language": data.get("language", ""),
            "license": (data.get("license") or {}).get("name", ""),
            "default_branch": data.get("default_branch", "main"),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "homepage": data.get("homepage", ""),
            "topics": data.get("topics", []),
            "url": data.get("html_url", ""),
        }


async def _get_readme(owner: str, repo: str, max_chars: int = 5000) -> str:
    """获取 README 内容"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/readme"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers())
        if resp.status_code != 200:
            return "(无 README)"

        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64" and content:
            try:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                if len(decoded) > max_chars:
                    decoded = decoded[:max_chars] + "\n\n... (README 已截断)"
                return decoded
            except Exception:
                return "(README 解码失败)"
        return content[:max_chars] if content else "(README 为空)"


async def _get_file_content(owner: str, repo: str, path: str, branch: str = "") -> str:
    """获取文件内容"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    params = {}
    if branch:
        params["ref"] = branch

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers(), params=params)
        if resp.status_code != 200:
            return f"错误: 无法获取文件 {path} (HTTP {resp.status_code})"

        data = resp.json()

        # 如果是目录
        if isinstance(data, list):
            items = []
            for item in data:
                items.append(f"  {item.get('type', '?'):4s}  {item.get('name', '')}")
            return f"目录 {path}:\n" + "\n".join(items)

        # 文件
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return "(文件解码失败)"
        return content


async def _get_issues(owner: str, repo: str, state: str = "open", limit: int = 5) -> list[dict[str, Any]]:
    """获取 Issues"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
    params = {"state": state, "per_page": min(limit, 30), "sort": "created", "direction": "desc"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers(), params=params)
        if resp.status_code != 200:
            return []

        issues = resp.json()
        result = []
        for issue in issues[:limit]:
            # 跳过 PR（GitHub Issues API 也会返回 PR）
            if "pull_request" in issue:
                continue
            result.append({
                "number": issue.get("number", 0),
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "author": (issue.get("user") or {}).get("login", ""),
                "created_at": issue.get("created_at", ""),
                "comments": issue.get("comments", 0),
                "labels": [l.get("name", "") for l in issue.get("labels", [])],
                "url": issue.get("html_url", ""),
            })
        return result


async def _get_releases(owner: str, repo: str, limit: int = 3) -> list[dict[str, Any]]:
    """获取最新 Releases"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases"
    params = {"per_page": min(limit, 10)}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_get_headers(), params=params)
        if resp.status_code != 200:
            return []

        releases = resp.json()
        result = []
        for rel in releases[:limit]:
            result.append({
                "tag": rel.get("tag_name", ""),
                "name": rel.get("name", ""),
                "prerelease": rel.get("prerelease", False),
                "published_at": rel.get("published_at", ""),
                "url": rel.get("html_url", ""),
            })
        return result


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    repo_input = args.get("repo", "").strip()
    action = args.get("action", "info")
    path = args.get("path", "").strip()
    branch = args.get("branch", "").strip()
    max_chars = int(args.get("max_chars", 5000))

    if not repo_input:
        return {"error": "repo 参数不能为空"}

    parsed = _parse_repo_url(repo_input)
    if not parsed:
        return {"error": f"无法解析仓库: {repo_input}。格式: owner/repo 或 https://github.com/owner/repo"}

    owner, repo = parsed

    try:
        if action == "info":
            info = await _get_repo_info(owner, repo)
            readme = await _get_readme(owner, repo, max_chars)
            info["readme"] = readme
            return info

        elif action == "file":
            if not path:
                return {"error": "action=file 时需要 path 参数"}
            content = await _get_file_content(owner, repo, path, branch)
            if len(content) > max_chars:
                content = content[:max_chars] + "\n\n... (文件内容已截断)"
            return {
                "repo": f"{owner}/{repo}",
                "path": path,
                "branch": branch or "default",
                "content": content,
            }

        elif action == "issues":
            state = args.get("state", "open")
            limit = int(args.get("limit", 5))
            issues = await _get_issues(owner, repo, state, limit)
            return {
                "repo": f"{owner}/{repo}",
                "state": state,
                "count": len(issues),
                "issues": issues,
            }

        elif action == "releases":
            limit = int(args.get("limit", 3))
            releases = await _get_releases(owner, repo, limit)
            return {
                "repo": f"{owner}/{repo}",
                "count": len(releases),
                "releases": releases,
            }

        else:
            return {"error": f"未知 action: {action}。支持: info, file, issues, releases"}

    except Exception as e:
        return {"error": f"GitHub API 请求失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="github_repo",
        description="GitHub仓库操作：info(基本信息+README)/file(文件内容)/issues/releases",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["info", "file", "issues", "releases"],
                    "default": "info",
                },
                "path": {"type": "string"},
                "branch": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "default": "open",
                },
                "limit": {"type": "number", "default": 5},
                "max_chars": {"type": "number", "default": 5000},
            },
            "required": ["repo"],
        },
        execute=execute,
        format_input=lambda args: f"GitHub: {args.get('repo', '')} ({args.get('action', 'info')})",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.85,
        keywords=["github", "仓库", "repository", "代码", "issues", "release"],
    ))
