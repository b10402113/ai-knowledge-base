#!/usr/bin/env python3
"""
知识条目质量评分脚本 - 5 维度评分系统

用法: python check_quality.py <json_file> [json_file2 ...]
      python check_quality.py "knowledge/articles/*.json"
"""

import argparse
import glob
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


EMPTY_WORDS_ZH = [
    "赋能", "抓手", "闭环", "打通", "全链路",
    "底层逻辑", "颗粒度", "对齐", "拉通", "沉淀",
    "强大的", "革命性的",
]

EMPTY_WORDS_EN = [
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "innovative", "disruptive",
    "next-generation", "world-class", "industry-leading",
    "state-of-the-art", "best-in-class",
]

TECH_KEYWORDS = [
    "ai", "llm", "agent", "gpt", "transformer",
    "neural", "deep-learning", "machine-learning", "nlp",
    "reinforcement-learning", "cuda", "gpu", "fine-tuning",
    "inference", "training", "embedding", "tokenizer",
    "attention", "moe", "rag", "api", "sdk", "framework",
    "kubernetes", "docker", "sre", "devops", "python",
    "rust", "typescript", "open-source", "benchmark",
]

STANDARD_TAGS = {
    "agent-framework", "ai-agent", "llm-inference",
    "llm-from-scratch", "deep-learning", "machine-learning",
    "transformer", "cuda-kernel", "reinforcement-learning",
    "nlp", "education", "tutorial", "open-source",
    "python", "rust", "typescript", "devops", "sre",
    "incident-response", "fp8", "moe", "deepseek",
    "fine-tuning", "rag", "embedding", "tokenizer",
    "benchmark", "inference", "training", "mlops",
    "docker", "kubernetes", "gpu", "serverless",
    "data-pipeline", "automation", "monitoring",
    "observability", "reliability", "scalability",
    "reverse-engineering", "android", "security",
    "voice-synthesis", "context-management", "gemm",
    "cuda", "high-performance", "math",
}


@dataclass
class DimensionScore:
    """单维度评分结果"""
    dimension: str
    score: float
    max_score: float
    detail: str


@dataclass
class QualityReport:
    """单文件质量报告"""
    filepath: Path
    dimensions: List[DimensionScore] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return round(sum(d.score for d in self.dimensions), 1)

    @property
    def grade(self) -> str:
        t = self.total_score
        if t >= 80:
            return "A"
        if t >= 60:
            return "B"
        return "C"


def score_summary(summary: str) -> DimensionScore:
    """摘要质量评分：长度 + 技术关键词"""
    length = len(summary)
    score = 0.0

    if length >= 50:
        score += 15.0
    elif length >= 20:
        score += 8.0
    else:
        score += max(0.0, length / 20.0 * 8.0)

    summary_lower = summary.lower()
    keyword_hits = sum(1 for kw in TECH_KEYWORDS if kw in summary_lower)
    bonus = min(keyword_hits * 2.5, 10.0)
    score += bonus
    score = min(score, 25.0)

    if length >= 50:
        length_desc = "达标"
    elif length >= 20:
        length_desc = "基本达标"
    else:
        length_desc = "不足"
    detail = f"长度 {length} 字（{length_desc}），含 {keyword_hits} 个技术关键词"

    return DimensionScore("摘要质量", round(score, 1), 25.0, detail)


def score_tech_depth(data: Dict) -> DimensionScore:
    """技术深度评分：基于 score/relevance_score/score_breakdown.tech_depth"""
    raw = None
    source = ""

    if "score" in data and isinstance(data["score"], (int, float)):
        raw = data["score"]
        source = "score"
    elif "relevance_score" in data and isinstance(data["relevance_score"], (int, float)):
        raw = data["relevance_score"] * 10
        source = "relevance_score×10"
    elif "score_breakdown" in data and isinstance(data["score_breakdown"], dict):
        td = data["score_breakdown"].get("tech_depth")
        if isinstance(td, (int, float)):
            raw = td * 10
            source = "tech_depth×10"

    if raw is None:
        return DimensionScore("技术深度", 0.0, 25.0, "未找到评分字段")

    raw = max(1, min(10, raw))
    mapped = round(raw / 10.0 * 25.0, 1)

    return DimensionScore("技术深度", mapped, 25.0, f"原始评分 {round(raw, 1)}/10（{source}）")


def score_format(data: Dict) -> DimensionScore:
    """格式规范评分：5 项必要字段各 4 分"""
    checks = [
        ("id", "id" in data and isinstance(data["id"], str) and len(data["id"]) > 0),
        ("title", "title" in data and isinstance(data["title"], str) and len(data["title"]) > 0),
        ("source_url", "source_url" in data and isinstance(data["source_url"], str)),
        ("status", "status" in data and isinstance(data["status"], str)),
        ("timestamp", any(k in data for k in ("collected_at", "created_at", "timestamp"))),
    ]

    passed = sum(1 for _, ok in checks if ok)
    failed_items = [name for name, ok in checks if not ok]
    score = passed * 4.0

    if failed_items:
        detail = f"缺少: {', '.join(failed_items)}"
    else:
        detail = "5/5 项格式检查通过"

    return DimensionScore("格式规范", float(score), 20.0, detail)


def score_tags(tags) -> DimensionScore:
    """标签精度评分：数量合理性 + 标准标签命中率"""
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 0.0, 15.0, "tags 字段缺失或类型错误")

    count = len(tags)

    if 1 <= count <= 3:
        base = 10.0
    elif 4 <= count <= 6:
        base = 7.0
    elif count >= 7:
        base = 4.0
    else:
        base = 0.0

    valid_tags = [t for t in tags if isinstance(t, str) and t in STANDARD_TAGS]
    ratio = len(valid_tags) / max(count, 1)
    bonus = round(ratio * 5.0, 1)
    score = min(base + bonus, 15.0)

    detail = f"{count} 个标签，{len(valid_tags)} 个命中标准标签库"

    return DimensionScore("标签精度", round(score, 1), 15.0, detail)


def score_empty_words(text: str) -> DimensionScore:
    """空洞词检测评分"""
    text_lower = text.lower()

    found_zh = [w for w in EMPTY_WORDS_ZH if w in text]
    found_en = [w for w in EMPTY_WORDS_EN if w in text_lower]
    total_found = len(found_zh) + len(found_en)

    if total_found == 0:
        score = 15.0
        detail = "未检测到空洞词"
    else:
        penalty = total_found * 5.0
        score = max(0.0, 15.0 - penalty)
        all_words = found_zh + found_en
        detail = f"检测到 {total_found} 个空洞词: {', '.join(all_words)}"

    return DimensionScore("空洞词检测", round(score, 1), 15.0, detail)


def evaluate_article(data: Dict, filepath: Path) -> QualityReport:
    """对单篇文章进行 5 维度评分"""
    report = QualityReport(filepath=filepath)

    summary = data.get("summary", "")
    report.dimensions.append(score_summary(summary))
    report.dimensions.append(score_tech_depth(data))
    report.dimensions.append(score_format(data))
    report.dimensions.append(score_tags(data.get("tags", [])))
    report.dimensions.append(score_empty_words(summary))

    return report


def render_bar(score: float, max_score: float, width: int = 20) -> str:
    """渲染可视化进度条"""
    ratio = score / max_score if max_score > 0 else 0
    filled = int(ratio * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def print_report(report: QualityReport) -> None:
    """打印单文件评分报告"""
    grade = report.grade
    icon = {"A": "\U0001f7e2", "B": "\U0001f7e1", "C": "\U0001f534"}.get(grade, "")

    print(f"\n{'─' * 60}")
    print(f"\U0001f4c4 {report.filepath.name}")
    print(f"{'─' * 60}")

    for dim in report.dimensions:
        bar = render_bar(dim.score, dim.max_score)
        print(
            f"  {dim.dimension:<8} {bar} "
            f"{dim.score:>5.1f}/{dim.max_score:.0f}  {dim.detail}"
        )

    print(f"  {'─' * 56}")
    bar = render_bar(report.total_score, 100)
    print(f"  {'总分':<8} {bar} {report.total_score:>5.1f}/100")
    print(f"  {'等级':<8} {icon} {grade}  (A\u226580, B\u226560, C<60)")


def expand_patterns(patterns: List[str]) -> List[Path]:
    """展开通配符模式为文件列表"""
    files: List[Path] = []
    for pattern in patterns:
        matched = glob.glob(pattern)
        if matched:
            files.extend(Path(p) for p in matched)
        else:
            files.append(Path(pattern))
    return sorted(set(files))


def make_error_report(filepath: Path, reason: str) -> QualityReport:
    """生成错误文件的零分报告"""
    report = QualityReport(filepath=filepath)
    report.dimensions = [
        DimensionScore("摘要质量", 0, 25, reason),
        DimensionScore("技术深度", 0, 25, reason),
        DimensionScore("格式规范", 0, 20, reason),
        DimensionScore("标签精度", 0, 15, reason),
        DimensionScore("空洞词检测", 0, 15, reason),
    ]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="知识条目质量评分（5 维度）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python check_quality.py knowledge/articles/2026-04-25-deepgemm.json
  python check_quality.py "knowledge/articles/*.json"
        """,
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="JSON 文件路径，支持通配符 (如 *.json)",
    )

    args = parser.parse_args()
    files = expand_patterns(args.files)

    reports: List[QualityReport] = []

    for filepath in files:
        if not filepath.exists():
            print(f"\u26a0\ufe0f  文件不存在: {filepath}")
            reports.append(make_error_report(filepath, "文件不存在"))
            continue

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"\u26a0\ufe0f  读取失败: {filepath}: {e}")
            reports.append(make_error_report(filepath, f"读取失败: {e}"))
            continue

        if not isinstance(data, dict):
            reports.append(make_error_report(filepath, "JSON 根元素非对象"))
            continue

        reports.append(evaluate_article(data, filepath))

    for report in reports:
        print_report(report)

    total = len(reports)
    grade_counts = {"A": 0, "B": 0, "C": 0}
    for r in reports:
        grade_counts[r.grade] += 1

    print(f"\n{'=' * 60}")
    print(f"评分汇总: 共 {total} 个文件")
    print(
        f"  \U0001f7e2 A 级: {grade_counts['A']}  "
        f"\U0001f7e1 B 级: {grade_counts['B']}  "
        f"\U0001f534 C 级: {grade_counts['C']}"
    )
    print(f"{'=' * 60}")

    return 1 if grade_counts["C"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
