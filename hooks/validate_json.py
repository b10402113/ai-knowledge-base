#!/usr/bin/env python3
"""
JSON 知识条目校验脚本

用法: python validate_json.py <json_file> [json_file2 ...]
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# 必填字段定义：字段名 -> 类型
REQUIRED_FIELDS: Dict[str, type] = {
    "id": str,
    "title": str,
    "url": str,
    "summary": str,
    "tags": list,
}

# 允许的 status 值
VALID_STATUS = {"draft", "review", "published", "archived"}

# 允许的 audience 值
VALID_AUDIENCE = {"beginner", "intermediate", "advanced"}


# ID 格式正则：{source}-{YYYY-MM-DD}-{NNN} 或 {YYYY-MM-DD}-{NNN}
ID_PATTERN = re.compile(r"^(?:[a-z0-9_-]+-)?(\d{4}-\d{2}-\d{2})-(\d{3})$")

# URL 格式正则
URL_PATTERN = re.compile(r"^https?://.+")


class ValidationResult:
    """单个文件的校验结果"""

    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.errors: List[str] = []

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_json_syntax(filepath: Path, result: ValidationResult) -> Optional[Dict[str, Any]]:
    """校验 JSON 语法"""
    try:
        content = filepath.read_text(encoding="utf-8")
        data = json.loads(content)
        return data
    except json.JSONDecodeError as e:
        result.add_error(f"JSON 解析失败: {e}")
        return None


def validate_required_fields(data: Dict[str, Any], result: ValidationResult) -> None:
    """校验必填字段存在性和类型"""
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            result.add_error(f"缺少必填字段: {field}")
        elif not isinstance(data[field], expected_type):
            actual_type = type(data[field]).__name__
            result.add_error(
                f"字段类型错误: {field} 期望 {expected_type.__name__}, 实际 {actual_type}"
            )


def validate_id_format(id_value: str, result: ValidationResult) -> None:
    match = ID_PATTERN.match(id_value)
    if not match:
        result.add_error(
            f"ID 格式错误: '{id_value}'，应为 {{source}}-{{YYYY-MM-DD}}-{{NNN}} 或 {{YYYY-MM-DD}}-{{NNN}} 格式"
        )
        return

    date_str = match.group(1)
    try:
        parts = date_str.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError("日期范围无效")
        if year < 1900 or year > 2100:
            result.add_error(f"ID 日期年份无效: {date_str}")
    except ValueError as e:
        result.add_error(f"ID 日期无效: {date_str} - {e}")


def validate_status(status: str, result: ValidationResult) -> None:
    """校验 status 值"""
    if status not in VALID_STATUS:
        result.add_error(
            f"status 值无效: '{status}'，允许值: {', '.join(sorted(VALID_STATUS))}"
        )


def validate_url(url: str, result: ValidationResult) -> None:
    """校验 URL 格式"""
    if not URL_PATTERN.match(url):
        result.add_error(f"URL 格式错误: '{url}'，应以 http:// 或 https:// 开头")


def validate_summary(summary: str, result: ValidationResult) -> None:
    """校验摘要长度"""
    if len(summary) < 20:
        result.add_error(f"摘要长度不足: {len(summary)} 字，至少需要 20 字")


def validate_tags(tags: List[Any], result: ValidationResult) -> None:
    """校验标签数量和类型"""
    if len(tags) < 1:
        result.add_error("标签数量不足: 至少需要 1 个标签")
    for i, tag in enumerate(tags):
        if not isinstance(tag, str):
            result.add_error(f"标签类型错误: 第 {i + 1} 个标签应为字符串")


def validate_optional_fields(data: Dict[str, Any], result: ValidationResult) -> None:
    """校验可选字段"""
    # 校验 score (如有)
    if "score" in data:
        score = data["score"]
        if not isinstance(score, (int, float)):
            result.add_error(f"score 类型错误: 期望数字，实际 {type(score).__name__}")
        elif not (1 <= score <= 10):
            result.add_error(f"score 范围错误: {score}，应在 1-10 范围内")

    # 校验 audience (如有)
    if "audience" in data:
        audience = data["audience"]
        if not isinstance(audience, str):
            result.add_error(f"audience 类型错误: 期望字符串，实际 {type(audience).__name__}")
        elif audience not in VALID_AUDIENCE:
            result.add_error(
                f"audience 值无效: '{audience}'，允许值: {', '.join(sorted(VALID_AUDIENCE))}"
            )


def validate_file(filepath: Path) -> ValidationResult:
    """校验单个 JSON 文件"""
    result = ValidationResult(filepath)

    # 检查文件存在
    if not filepath.exists():
        result.add_error(f"文件不存在: {filepath}")
        return result

    if not filepath.is_file():
        result.add_error(f"不是文件: {filepath}")
        return result

    # 校验 JSON 语法
    data = validate_json_syntax(filepath, result)
    if data is None:
        return result

    # 确保是字典类型
    if not isinstance(data, dict):
        result.add_error(f"JSON 根元素应为对象，实际为 {type(data).__name__}")
        return result

    # 校验必填字段
    validate_required_fields(data, result)

    # 校验各字段格式
    if "id" in data and isinstance(data["id"], str):
        validate_id_format(data["id"], result)

    if "status" in data and isinstance(data["status"], str):
        validate_status(data["status"], result)

    if "url" in data and isinstance(data["url"], str):
        validate_url(data["url"], result)

    if "summary" in data and isinstance(data["summary"], str):
        validate_summary(data["summary"], result)

    if "tags" in data and isinstance(data["tags"], list):
        validate_tags(data["tags"], result)

    # 校验可选字段
    validate_optional_fields(data, result)

    return result


def expand_patterns(patterns: List[str]) -> List[Path]:
    """展开通配符模式为文件列表"""
    files: List[Path] = []
    for pattern in patterns:
        matched = glob.glob(pattern)
        if matched:
            files.extend(Path(p) for p in matched)
        else:
            # 模式未匹配到任何文件，保留原路径以便报错
            files.append(Path(pattern))
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验知识条目 JSON 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python validate_json.py knowledge.json
  python validate_json.py "knowledge/articles/*.json"
  python validate_json.py file1.json file2.json
        """,
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="JSON 文件路径，支持通配符 (如 *.json)",
    )

    args = parser.parse_args()

    # 展开通配符
    files = expand_patterns(args.files)

    # 校验所有文件
    results = [validate_file(f) for f in files]

    # 汇总统计
    total = len(results)
    passed = sum(1 for r in results if r.is_valid)
    failed = total - passed

    # 输出错误信息
    for result in results:
        if result.errors:
            print(f"\n❌ {result.filepath}:")
            for error in result.errors:
                print(f"   - {error}")

    # 输出汇总
    print(f"\n{'='*50}")
    print(f"校验完成: 总计 {total} 个文件")
    print(f"  ✅ 通过: {passed}")
    print(f"  ❌ 失败: {failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
