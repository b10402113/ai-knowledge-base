#!/usr/bin/env python3
"""MCP Server for AI Knowledge Base - JSON-RPC 2.0 over stdio."""

import json
import os
import sys
from collections import Counter

ARTICLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge", "articles")

_articles_cache = {}


def _load_articles():
    global _articles_cache
    if _articles_cache:
        return _articles_cache
    if not os.path.isdir(ARTICLES_DIR):
        return {}
    for fname in os.listdir(ARTICLES_DIR):
        if fname == "index.json" or not fname.endswith(".json"):
            continue
        fpath = os.path.join(ARTICLES_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else [data]
            for article in items:
                if not isinstance(article, dict):
                    continue
                aid = article.get("id")
                if aid:
                    _articles_cache[aid] = article
        except (json.JSONDecodeError, OSError):
            continue
    return _articles_cache


def _reload_articles():
    global _articles_cache
    _articles_cache = {}
    return _load_articles()


def search_articles(keyword, limit=5):
    articles = _load_articles()
    kw = keyword.lower()
    results = []
    for article in articles.values():
        title = article.get("title", "").lower()
        summary = article.get("summary", "").lower()
        tags = " ".join(article.get("tags", [])).lower()
        if kw in title or kw in summary or kw in tags:
            results.append(article)
    results.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)
    return results[:limit]


def get_article(article_id):
    articles = _load_articles()
    return articles.get(article_id)


def knowledge_stats():
    articles = _load_articles()
    total = len(articles)
    sources = Counter(a.get("source", "unknown") for a in articles.values())
    tag_counter = Counter()
    for a in articles.values():
        for tag in a.get("tags", []):
            tag_counter[tag] += 1
    top_tags = tag_counter.most_common(10)
    return {
        "total_articles": total,
        "source_distribution": dict(sources),
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
    }


TOOLS = [
    {
        "name": "search_articles",
        "description": "按关键词搜索知识库文章，匹配标题、摘要和标签",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认 5",
                    "default": 5,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": "按文章 ID 获取完整内容",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {"type": "string", "description": "文章 ID"},
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": "返回知识库统计信息（文章总数、来源分布、热门标签）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

SERVER_INFO = {
    "name": "ai-knowledge-base",
    "version": "1.0.0",
}

CAPABILITIES = {"tools": {}}


def _make_response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _call_tool(name, arguments):
    if name == "search_articles":
        keyword = arguments.get("keyword", "")
        limit = arguments.get("limit", 5)
        results = search_articles(keyword, limit)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(results, ensure_ascii=False, indent=2),
                }
            ]
        }
    elif name == "get_article":
        article_id = arguments.get("article_id", "")
        article = get_article(article_id)
        if article is None:
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"error": f"未找到文章: {article_id}"}, ensure_ascii=False)}
                ],
                "isError": True,
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(article, ensure_ascii=False, indent=2),
                }
            ]
        }
    elif name == "knowledge_stats":
        stats = knowledge_stats()
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(stats, ensure_ascii=False, indent=2),
                }
            ]
        }
    else:
        return None


def handle_request(request):
    request_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "initialize":
        return _make_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _make_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        _reload_articles()
        result = _call_tool(tool_name, arguments)
        if result is None:
            return _make_error(request_id, -32601, f"未知工具: {tool_name}")
        return _make_response(request_id, result)

    if method == "ping":
        return _make_response(request_id, {})

    return _make_error(request_id, -32601, f"未知方法: {method}")


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _make_error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
