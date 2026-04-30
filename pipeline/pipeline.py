"""Four-step knowledge base automation pipeline.

Collects AI-related content from GitHub Search API and RSS feeds,
analyzes each item with LLM, deduplicates and validates, then saves
structured knowledge entries to the article store.

Usage:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from model_client import chat_with_retry, get_provider, tracker

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "AI OR LLM OR agent OR RAG OR MCP OR agentic"
GITHUB_MAX_PAGES = 3
GITHUB_PER_PAGE = 30

RSS_FEEDS: List[Dict[str, str]] = [
    {
        "name": "hackernews",
        "url": "https://hnrss.org/newest?q=AI+LLM+agent&count=30",
    },
    {
        "name": "hackernews-frontpage",
        "url": "https://hnrss.org/frontpage?count=30",
    },
]

RELEVANCE_THRESHOLD = 0.6
HTTP_TIMEOUT = 30.0


def slugify(text: str) -> str:
    """Convert *text* to a URL-friendly slug.

    Args:
        text: The text to slugify.

    Returns:
        A lowercase, hyphen-separated slug.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    """Return today's date as ``YYYY-MM-DD``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_existing_urls() -> Set[str]:
    """Collect all URLs already stored in the article directory.

    Returns:
        A set of URL strings from existing articles.
    """
    urls: Set[str] = set()
    if not ARTICLES_DIR.exists():
        return urls
    for path in ARTICLES_DIR.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "url" in data:
                urls.add(data["url"])
        except (json.JSONDecodeError, OSError):
            logger.debug("Skipping unreadable file: %s", path)
    return urls


def _github_headers() -> Dict[str, str]:
    """Build HTTP headers for GitHub API requests.

    Returns:
        Header dict with optional authorization.
    """
    headers: Dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_rss_xml(xml_text: str, source_name: str) -> List[Dict[str, Any]]:
    """Parse an RSS feed with minimal regex-based extraction.

    Args:
        xml_text: Raw RSS XML content.
        source_name: Name label for the source.

    Returns:
        A list of item dicts with id, title, description, url fields.
    """
    items: List[Dict[str, Any]] = []
    item_blocks = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)

    for block in item_blocks:
        def _extract(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
            return m.group(1).strip() if m else ""

        title = _extract("title")
        link = _extract("link")
        description = _extract("description")

        clean_desc = re.sub(r"<[^>]+>", "", description)
        clean_desc = clean_desc[:500]

        if not link:
            continue

        items.append({
            "id": slugify(title or link),
            "title": title,
            "description": clean_desc,
            "url": link,
            "source": source_name,
            "stars": 0,
            "language": "",
            "readme_excerpt": "",
        })

    return items


def step1_collect(
    sources: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Step 1: Collect raw items from specified sources.

    Args:
        sources: List of source names (``"github"``, ``"rss"``).
        limit: Maximum number of items to return.

    Returns:
        A list of raw item dicts.
    """
    logger.info("Step 1 — Collect (sources=%s, limit=%d)", sources, limit)
    items: List[Dict[str, Any]] = []

    if "github" in sources:
        items.extend(_collect_github(limit))

    if "rss" in sources:
        items.extend(_collect_rss(limit))

    items = items[:limit]
    logger.info("Collected %d items total", len(items))

    raw_path = RAW_DIR / f"github-trending-{today_str()}.json"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_payload = {
        "source": "pipeline",
        "collected_at": now_iso(),
        "count": len(items),
        "items": items,
    }
    raw_path.write_text(
        json.dumps(raw_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Raw data saved to %s", raw_path)

    return items


def _collect_github(limit: int) -> List[Dict[str, Any]]:
    """Collect repositories from GitHub Search API.

    Args:
        limit: Maximum number of items.

    Returns:
        A list of raw item dicts.
    """
    logger.info("Collecting from GitHub Search API …")
    items: List[Dict[str, Any]] = []
    headers = _github_headers()

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        for page in range(1, GITHUB_MAX_PAGES + 1):
            if len(items) >= limit:
                break
            params = {
                "q": GITHUB_SEARCH_QUERY,
                "sort": "stars",
                "order": "desc",
                "per_page": GITHUB_PER_PAGE,
                "page": page,
            }
            try:
                resp = client.get(GITHUB_SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("GitHub API page %d failed: %s", page, exc)
                break

            repo_list = data.get("items", [])
            if not repo_list:
                break

            for repo in repo_list:
                items.append({
                    "id": repo["full_name"],
                    "title": repo["name"],
                    "description": repo.get("description", "") or "",
                    "url": repo["html_url"],
                    "source": "github-search",
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "language": repo.get("language", ""),
                    "topics": repo.get("topics", []),
                    "license": (repo.get("license") or {}).get("spdx_id", ""),
                    "weekly_stars": "",
                    "readme_excerpt": "",
                })

            rate_remaining = resp.headers.get("X-RateLimit-Remaining")
            if rate_remaining and int(rate_remaining) < 5:
                logger.warning("GitHub rate limit low (%s remaining), stopping", rate_remaining)
                break

            if len(data.get("items", [])) < GITHUB_PER_PAGE:
                break

            time.sleep(1)

    logger.info("GitHub: collected %d repositories", len(items))
    return items[:limit]


def _collect_rss(limit: int) -> List[Dict[str, Any]]:
    """Collect items from configured RSS feeds.

    Args:
        limit: Maximum number of items.

    Returns:
        A list of raw item dicts.
    """
    logger.info("Collecting from RSS feeds …")
    items: List[Dict[str, Any]] = []

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        for feed in RSS_FEEDS:
            try:
                resp = client.get(feed["url"])
                resp.raise_for_status()
                parsed = _parse_rss_xml(resp.text, feed["name"])
                items.extend(parsed)
                logger.info("RSS [%s]: %d items", feed["name"], len(parsed))
            except Exception as exc:
                logger.warning("RSS [%s] failed: %s", feed["name"], exc)

    logger.info("RSS: collected %d items total", len(items))
    return items[:limit]


def step2_analyze(
    items: List[Dict[str, Any]],
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Step 2: Analyze each item with LLM for summary, tags, and scoring.

    Args:
        items: Raw items from Step 1.
        dry_run: If True, skip LLM calls and use placeholder data.

    Returns:
        A list of enriched item dicts with LLM analysis fields.
    """
    logger.info("Step 2 — Analyze (%d items, dry_run=%s)", len(items), dry_run)

    system_prompt = (
        "你是一个 AI/LLM 领域的技术分析师。请对以下项目/文章进行专业分析。"
        "你必须严格以 JSON 格式回复（不要加 markdown 代码块），包含以下字段：\n"
        "  - summary: 中文摘要（100-200 字）\n"
        "  - tags: 英文标签列表（3-5 个，小写连字符格式，如 large-language-model）\n"
        "  - relevance_score: 综合相关性评分（0.0-1.0）\n"
        "  - score_breakdown: 分项评分对象，包含 tech_depth, practical_value, "
        "timeliness, community_heat, domain_match（各 0.0-1.0）\n\n"
        "评分标准：\n"
        "  - tech_depth: 技术深度和创新性\n"
        "  - practical_value: 实际应用价值\n"
        "  - timeliness: 时效性和前沿程度\n"
        "  - community_heat: 社区热度和影响力\n"
        "  - domain_match: 与 AI/LLM/Agent 领域的相关性\n\n"
        "只返回 JSON，不要其他文字。"
    )

    analyzed: List[Dict[str, Any]] = []
    total = len(items)

    for idx, item in enumerate(items, 1):
        logger.info("Analyzing %d/%d: %s", idx, total, item.get("title", item.get("id", "?")))

        if dry_run:
            analyzed.append(_dry_run_analysis(item))
            continue

        user_prompt = (
            f"项目名称: {item.get('title', 'N/A')}\n"
            f"描述: {item.get('description', 'N/A')}\n"
            f"Stars: {item.get('stars', 'N/A')}\n"
            f"语言: {item.get('language', 'N/A')}\n"
            f"URL: {item.get('url', 'N/A')}\n"
            f"README 摘要: {item.get('readme_excerpt', 'N/A')}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = chat_with_retry(
                messages,
                temperature=0.3,
                max_tokens=1024,
            )
            analysis = _parse_llm_json(response.content)
            enriched = {**item, **analysis, "analyzed_at": now_iso()}
            analyzed.append(enriched)
        except Exception as exc:
            logger.error("LLM analysis failed for %s: %s", item.get("id", "?"), exc)
            analyzed.append(_dry_run_analysis(item))

    logger.info("Analyzed %d items", len(analyzed))
    return analyzed


def _parse_llm_json(text: str) -> Dict[str, Any]:
    """Extract and parse JSON from LLM response text.

    Args:
        text: Raw LLM response that should contain JSON.

    Returns:
        Parsed dict with summary, tags, relevance_score, score_breakdown.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            raise

    return {
        "summary": data.get("summary", ""),
        "tags": data.get("tags", []),
        "relevance_score": float(data.get("relevance_score", 0.0)),
        "score_breakdown": data.get("score_breakdown", {}),
    }


def _dry_run_analysis(item: Dict[str, Any]) -> Dict[str, Any]:
    """Generate placeholder analysis for dry-run mode.

    Args:
        item: Raw item dict.

    Returns:
        Item with placeholder analysis fields.
    """
    return {
        **item,
        "summary": f"[DRY RUN] {item.get('description', '')[:200]}",
        "tags": ["ai", "dry-run"],
        "relevance_score": 0.7,
        "score_breakdown": {
            "tech_depth": 0.5,
            "practical_value": 0.5,
            "timeliness": 0.5,
            "community_heat": 0.5,
            "domain_match": 0.5,
        },
        "analyzed_at": now_iso(),
    }


def step3_organize(
    items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Step 3: Deduplicate, validate, and standardize items.

    Items with ``relevance_score`` below the threshold are dropped.
    Items whose URL already exists in the article store are also dropped.

    Args:
        items: Enriched items from Step 2.

    Returns:
        A tuple of (accepted_items, filtered_items).
    """
    logger.info("Step 3 — Organize (%d items)", len(items))

    existing_urls = _load_existing_urls()
    accepted: List[Dict[str, Any]] = []
    filtered: List[Dict[str, Any]] = []

    seen_urls: Set[str] = set()
    seq = _next_seq_today()

    for item in items:
        url = item.get("url", "")

        if url in existing_urls:
            reason = "dedup: URL exists in article store"
        elif url in seen_urls:
            reason = "dedup: duplicate URL in batch"
        elif item.get("relevance_score", 0) < RELEVANCE_THRESHOLD:
            reason = f"low relevance: {item.get('relevance_score', 0):.2f}"
        elif not item.get("title"):
            reason = "missing title"
        elif not item.get("url"):
            reason = "missing url"
        else:
            seen_urls.add(url)
            standardized = _standardize(item, seq)
            accepted.append(standardized)
            seq += 1
            continue

        filtered.append({"id": item.get("id", "?"), "reason": reason})
        logger.debug("Filtered out %s: %s", item.get("id", "?"), reason)

    logger.info(
        "Organize result: %d accepted, %d filtered",
        len(accepted),
        len(filtered),
    )
    return accepted, filtered


def _next_seq_today() -> int:
    """Find the next available sequence number for today's articles.

    Returns:
        The next sequence number.
    """
    prefix = today_str()
    max_seq = 0
    if ARTICLES_DIR.exists():
        for path in ARTICLES_DIR.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    entries = data
                elif isinstance(data, dict):
                    entries = [data]
                else:
                    continue
                for entry in entries:
                    aid = entry.get("id", "") if isinstance(entry, dict) else ""
                    if prefix in aid:
                        seq_str = aid.rsplit("-", 1)[-1]
                        max_seq = max(max_seq, int(seq_str))
            except (json.JSONDecodeError, OSError, ValueError, IndexError):
                pass
    return max_seq + 1


def _standardize(item: Dict[str, Any], seq: int) -> Dict[str, Any]:
    """Convert a raw item into the canonical article schema.

    Args:
        item: An enriched item dict.
        seq: Sequence number for the article ID.

    Returns:
        A standardized article dict matching the project schema.
    """
    date_prefix = today_str()
    slug = slugify(item.get("title", item.get("id", "unknown")))
    article_id = f"kb-{date_prefix}-{seq:03d}"

    return {
        "id": article_id,
        "title": item.get("title", ""),
        "source": item.get("source", "unknown"),
        "source_id": item.get("id", ""),
        "url": item.get("url", ""),
        "summary": item.get("summary", ""),
        "tags": item.get("tags", []),
        "relevance_score": item.get("relevance_score", 0.0),
        "score_breakdown": item.get("score_breakdown", {}),
        "collected_at": item.get("collected_at", now_iso()),
        "analyzed_at": item.get("analyzed_at", ""),
        "organized_at": now_iso(),
        "status": "published",
        "stars": item.get("stars", 0),
        "language": item.get("language", ""),
        "file_slug": f"{date_prefix}-{slug}",
    }


def step4_save(
    articles: List[Dict[str, Any]],
    filtered: List[Dict[str, Any]],
    dry_run: bool = False,
) -> None:
    """Step 4: Save articles as individual JSON files and update the index.

    Args:
        articles: Standardized article dicts from Step 3.
        filtered: Items that were filtered out in Step 3.
        dry_run: If True, skip writing files.
    """
    logger.info("Step 4 — Save (%d articles, dry_run=%s)", len(articles), dry_run)

    if dry_run:
        logger.info("Dry run — no files written")
        for art in articles:
            logger.info("  [DRY] %s → %s.json", art["id"], art["file_slug"])
        return

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    for art in articles:
        file_name = f"{art['file_slug']}.json"
        file_path = ARTICLES_DIR / file_name
        art_copy = {k: v for k, v in art.items() if k != "file_slug"}
        file_path.write_text(
            json.dumps(art_copy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved: %s", file_name)

    if filtered:
        filtered_path = RAW_DIR / f"filtered-{today_str()}.json"
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        filtered_path.write_text(
            json.dumps(
                {"date": today_str(), "filtered_count": len(filtered), "items": filtered},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Filtered log: %s", filtered_path)

    _update_index(articles)
    logger.info("Step 4 complete — %d articles saved", len(articles))


def _update_index(new_articles: List[Dict[str, Any]]) -> None:
    """Merge new articles into the master index file.

    Args:
        new_articles: Newly saved article dicts.
    """
    index_path = ARTICLES_DIR / "index.json"
    existing: Dict[str, Any] = {}

    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing_urls = {e.get("url") for e in existing.get("entries", [])}

    for art in new_articles:
        if art["url"] in existing_urls:
            continue
        existing.setdefault("entries", []).append({
            "id": art["id"],
            "title": art["title"],
            "file": f"{art['file_slug']}.json",
            "tags": art.get("tags", []),
            "relevance_score": art.get("relevance_score", 0.0),
            "organized_at": art.get("organized_at", ""),
        })

    existing["last_updated"] = now_iso()
    existing["total_count"] = len(existing["entries"])

    index_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Index updated: %d total entries", existing["total_count"])


def run_pipeline(
    sources: List[str],
    limit: int,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Execute the full four-step pipeline.

    Args:
        sources: List of source names to collect from.
        limit: Maximum number of items to process.
        dry_run: If True, skip LLM calls and file writes.

    Returns:
        The list of final article dicts.
    """
    logger.info("=" * 60)
    logger.info("Pipeline started (sources=%s, limit=%d, dry_run=%s)", sources, limit, dry_run)
    logger.info("=" * 60)

    items = step1_collect(sources, limit)
    if not items:
        logger.warning("No items collected — pipeline terminated")
        provider = os.environ.get("LLM_PROVIDER", "deepseek")
        tracker.report(provider=provider)
        return []

    analyzed = step2_analyze(items, dry_run=dry_run)
    accepted, filtered = step3_organize(analyzed)

    if not accepted:
        logger.warning("No items passed organize — pipeline terminated")
        provider = os.environ.get("LLM_PROVIDER", "deepseek")
        tracker.report(provider=provider)
        return []

    step4_save(accepted, filtered, dry_run=dry_run)

    provider = os.environ.get("LLM_PROVIDER", "deepseek")
    logger.info("=" * 60)
    logger.info("Pipeline complete: %d articles saved", len(accepted))
    logger.info("=" * 60)

    tracker.report(provider=provider)
    return accepted


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        A configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description="AI Knowledge Base — automated collection & analysis pipeline",
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="Comma-separated source list (default: github,rss)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of items to process (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making LLM calls or writing files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sources = [s.strip() for s in args.sources.split(",")]
    run_pipeline(sources=sources, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
