# AI Knowledge Base Agent System

## Project Overview

AI 知识库助手系统：自动从 GitHub Trending 和 Hacker News 采集 AI/LLM/Agent 领域的技术动态，经 AI 分析后结构化存储为 JSON，并支持多渠道分发（Telegram/飞书）。

## Tech Stack

- **Runtime**: Python 3.12
- **Agent Framework**: OpenCode + 国产大模型
- **Workflow**: LangGraph
- **Crawler**: OpenClaw

## Coding Standards

- **Style**: PEP 8
- **Naming**: snake_case
- **Docstring**: Google 风格
- **Logging**: 禁止裸 `print()`，必须使用日志模块

## Project Structure

```
.
├── .opencode/
│   ├── agents/          # Agent 定义
│   └── skills/         # Skill 定义
├── knowledge/
│   ├── raw/            # 原始采集数据
│   └── articles/       # 整理后的文章
└── AGENTS.md
```

## Knowledge Entry JSON Schema

```json
{
  "id": "uuid-string",
  "title": "string",
  "source_url": "string",
  "source": "github_trending | hacker_news",
  "summary": "string (50-200 chars)",
  "tags": ["llm", "agent", "framework"],
  "status": "raw | analyzed | published",
  "created_at": "ISO8601",
  "published_at": "ISO8601 | null"
}
```

## Agent Roles

| Role | Responsibility | Input | Output |
|------|---------------|-------|--------|
| Collector | 采集原始数据 | 源页面 URL | raw/**/*.json |
| Analyst | 分析生成摘要 | raw/**/*.json | articles/**/*.json |
| Organizer | 整理分发 | articles/**/*.json | Telegram/飞书消息 |

## Red Lines

- 禁止提交任何 API Key、Token 至仓库
- 禁止修改已发布内容的 `status` 字段
- 禁止直接使用外部 API 而无降级策略
- 禁止跳过 AI分析环节直接抓取