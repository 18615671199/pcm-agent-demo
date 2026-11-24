"""Tool 工厂 —— 每个 Agent 可以按需引用这些 Tool"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from .agent_base import Tool
from ..knowledge.kb import get_kb


SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    f = SCHEMAS_DIR / name
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


# ---------- Tool 1: 知识检索 ----------
def make_retrieve_knowledge_tool() -> Tool:
    def fn(query: str = "", top_k: int = 5) -> str:
        if not query:
            return "请提供 query 参数"
        results = get_kb().search(query, top_k=top_k)
        if not results:
            return "知识库中未检索到相关内容"
        parts = []
        for r in results:
            parts.append(f"[{r['source']}] score={r['score']:.2f}\n{r['text']}")
        return "\n\n".join(parts)

    t = Tool(
        name="retrieve_knowledge",
        description="从项目施工方案知识库中检索相关段落。参数 query 为检索关键词（如 '深基坑 降水'），top_k 为返回数量。",
        fn=fn,
    )
    # 覆盖更精确的 schema
    t.schema = lambda: {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或问题"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
        },
    }
    return t


# ---------- Tool 2: 获取表样 JSON ----------
_TABLE_SCHEMA_MAP = {
    "risk_table": "risk_tables.json",
    "jsa_table": "risk_tables.json",
    "emergency_plan": "risk_tables.json",
    "quality_checklist": "quality_checklist.json",
}


def make_get_table_schema_tool() -> Tool:
    def fn(table_name: str = "") -> str:
        if not table_name:
            return f"请提供 table_name。可选值: {list(_TABLE_SCHEMA_MAP.keys())}"
        schema = _load_schema(_TABLE_SCHEMA_MAP.get(table_name, "risk_tables.json"))
        # 根据 table_name 取出对应子 schema（如果文件里有多个）
        inner = schema.get("tables", {}).get(table_name, schema)
        return json.dumps(inner, ensure_ascii=False, indent=2)

    t = Tool(
        name="get_table_schema",
        description="获取输出表样的 JSON schema，按 schema 严格生成。参数 table_name 可选: risk_table / jsa_table / emergency_plan / quality_checklist",
        fn=fn,
    )
    t.schema = lambda: {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "enum": list(_TABLE_SCHEMA_MAP.keys()),
                        "description": "表样名称",
                    },
                },
                "required": ["table_name"],
            },
        },
    }
    return t


# ---------- Tool 3: 格式校验 ----------
def make_validate_output_tool() -> Tool:
    def fn(table_name: str = "", rows_json: str = "") -> str:
        """检查行列数等基础约束"""
        try:
            rows = json.loads(rows_json)
        except Exception as e:
            return f"rows_json 无法解析: {e}"
        if not isinstance(rows, list):
            return "rows 必须是 list"

        schema = _load_schema(_TABLE_SCHEMA_MAP.get(table_name, "risk_tables.json"))
        inner = schema.get("tables", {}).get(table_name, schema)
        cols = inner.get("columns", [])
        min_rows = inner.get("rows_min", 3)

        issues = []
        if len(rows) < min_rows:
            issues.append(f"行数 {len(rows)} < 最小值 {min_rows}")
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                issues.append(f"第 {i} 行不是对象")
                continue
            for c in cols:
                if c not in r and c not in ("序号",):  # 序号允许 LLM 自动补
                    issues.append(f"第 {i} 行缺少字段: {c}")
        if issues:
            return "⚠️ 校验发现问题:\n" + "\n".join(f"- {x}" for x in issues)
        return "✅ 校验通过"

    t = Tool(
        name="validate_output",
        description="对已生成的表数据做基础格式校验（行数、列完整性）。参数 table_name 和 rows_json（JSON 字符串）",
        fn=fn,
    )
    t.schema = lambda: {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "rows_json": {"type": "string", "description": "表的 JSON 字符串"},
                },
                "required": ["table_name", "rows_json"],
            },
        },
    }
    return t


# ---------- Tool 4: 标准对照（质量检查用） ----------
def make_compare_against_standard_tool() -> Tool:
    def fn(check_item: str = "", standard_text: str = "") -> str:
        """把检查项和标准做对比，给出通过/不通过建议"""
        if not check_item:
            return "请提供 check_item"
        # 简单返回模板，LLM 自己做判断
        return (
            f"检查项: {check_item}\n"
            f"对照标准: {standard_text if standard_text else '(已从知识库检索)'}\n"
            f"请根据上述标准判断是否合格，并给出理由。"
        )

    t = Tool(
        name="compare_against_standard",
        description="将某条检查项与规范标准做对照。LLM 可自行调用获取结构化提示。参数 check_item 和可选的 standard_text。",
        fn=fn,
    )
    t.schema = lambda: {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "check_item": {"type": "string"},
                    "standard_text": {"type": "string"},
                },
                "required": ["check_item"],
            },
        },
    }
    return t


def all_tools() -> list[Tool]:
    return [
        make_retrieve_knowledge_tool(),
        make_get_table_schema_tool(),
        make_validate_output_tool(),
        make_compare_against_standard_tool(),
    ]
