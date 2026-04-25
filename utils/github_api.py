"""GitHub API utility functions."""

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


@dataclass
class RepoInfo:
    """GitHub repository information."""

    name: str
    full_name: str
    stars: int
    forks: int
    description: Optional[str]
    url: str


def get_repo_info(owner: str, repo: str, token: Optional[str] = None) -> RepoInfo:
    """Fetch basic information for a GitHub repository.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        token: Optional GitHub personal access token for rate limit increase.

    Returns:
        RepoInfo object containing repository details.

    Raises:
        requests.HTTPError: If the API request fails.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info(f"Fetching repository info: {owner}/{repo}")
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return RepoInfo(
        name=data["name"],
        full_name=data["full_name"],
        stars=data["stargazers_count"],
        forks=data["forks_count"],
        description=data.get("description"),
        url=data["html_url"],
    )