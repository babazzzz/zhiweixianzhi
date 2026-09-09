"""
Task 6 final backend.

Loads the real CSV files into SQLite, serves the shadcn-style frontend,
provides device/log/test APIs, stores prediction/work-order records, and
proxies Coze workflow analysis through a local endpoint.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape as html_escape
from html import unescape as html_unescape

try:
    import requests
except Exception:  # requests is optional; local analysis still works.
    requests = None

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(BASE_DIR, "devices.db")
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
REPORT_TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, "maintenance_report_template.md")
WF_ANALYZE_YAML_PATH = os.path.join(PROJECT_DIR, "Workflow-WF_analyze-draft-新", "workflow", "WF_analyze-draft.yaml")

SUMMARY_CSV = os.path.join(DATA_DIR, "MACHINE_SUMMARY_DATA._2025.csv")
LOG_CSV = os.path.join(DATA_DIR, "MACHINE_LOG_DATA._2025.csv")
LINE_CSV = os.path.join(DATA_DIR, "PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_2025.csv")
TEST_CSV = os.path.join(DATA_DIR, "PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_TESTS_2025.csv")

COZE_BASE_URL = os.environ.get("COZE_BASE_URL", "https://api.coze.cn")
COZE_ANALYZE_WORKFLOW_ID = os.environ.get("COZE_ANALYZE_WORKFLOW_ID", "7640415534379728905")
COZE_WF3_WORKFLOW_ID = os.environ.get("COZE_WF3_WORKFLOW_ID", "7640555138759082027")
COZE_CHARTS_WORKFLOW_ID = os.environ.get("COZE_CHARTS_WORKFLOW_ID", "7648909318586646537")
COZE_ANALYZE_TOKEN = os.environ.get(
    "COZE_ANALYZE_TOKEN",
    os.environ.get(
        "COZE_TOKEN",
        "sat_Nzm9vmVpawCJxqOOd3bXeXUyvLi3FRUjatqEPeI6TNtxJYWCZZcGlWExQN7o6JHo",
    ),
)
COZE_WF3_TOKEN = os.environ.get(
    "COZE_WF3_TOKEN",
    "sat_Nzm9vmVpawCJxqOOd3bXeXUyvLi3FRUjatqEPeI6TNtxJYWCZZcGlWExQN7o6JHo",
)
COZE_CHARTS_TOKEN = os.environ.get(
    "COZE_CHARTS_TOKEN",
    "sat_Nzm9vmVpawCJxqOOd3bXeXUyvLi3FRUjatqEPeI6TNtxJYWCZZcGlWExQN7o6JHo",
)
COZE_REPORT_BOT_ID = os.environ.get("COZE_REPORT_BOT_ID", "7633767240911388714")
COZE_CHAT_BOT_ID = os.environ.get("COZE_CHAT_BOT_ID", COZE_REPORT_BOT_ID)
COZE_REPORT_TOKEN = os.environ.get(
    "COZE_REPORT_TOKEN",
    "pat_65r3DwB4trqEtW5vY7dFTM0BExneqRdBt5HyVf7YJeaGn0zlBTWKYA71EwQ9M6xd",
)
COZE_CHAT_TOKEN = os.environ.get("COZE_CHAT_TOKEN", COZE_REPORT_TOKEN)
COZE_REPORT_BASE_URL = os.environ.get("COZE_REPORT_BASE_URL", COZE_BASE_URL)
COZE_REPORT_USER_ID = os.environ.get("COZE_REPORT_USER_ID", "task6-report-export")
COZE_CHAT_USER_ID = os.environ.get("COZE_CHAT_USER_ID", "task6-assistant-chat")
COZE_REPORT_EXPORT_TIMEOUT = float(os.environ.get("COZE_REPORT_EXPORT_TIMEOUT", "8"))
COZE_CASE_KNOWLEDGE_ID = os.environ.get("COZE_CASE_KNOWLEDGE_ID", "7638914627228778502")
COZE_CASE_WORKFLOW_ID = os.environ.get("COZE_CASE_WORKFLOW_ID", "7641119456227704870")
COZE_CASE_SYNC_TOKEN = os.environ.get(
    "COZE_CASE_SYNC_TOKEN",
    os.environ.get("COZE_CASE_TOKEN", COZE_REPORT_TOKEN or COZE_ANALYZE_TOKEN),
)
COZE_CASE_TOKEN = os.environ.get("COZE_CASE_TOKEN", "")
CASE_LIBRARY_TXT = os.environ.get(
    "CASE_LIBRARY_TXT",
    os.path.join(os.path.dirname(BASE_DIR), "案例知识库.txt"),
)

WORKFLOWS = {
    "analyze": {
        "name": "WF_analyze",
        "workflow_id": COZE_ANALYZE_WORKFLOW_ID,
        "token": COZE_ANALYZE_TOKEN,
        "inputs": ["equipment_id", "equipment_type", "input_mode", "query_type", "raw_data", "merged_data"],
        "outputs": ["analysis_result", "risk_level", "predicted_rul_hours", "merged_data", "anomaly_events", "heatmap_data", "result"],
    },
    "workorder": {
        "name": "WF3",
        "workflow_id": COZE_WF3_WORKFLOW_ID,
        "token": COZE_WF3_TOKEN,
        "inputs": ["analysis_result", "equipment_id", "knowledge", "anomaly_events", "heatmap_data"],
        "outputs": ["cost_analysis", "work_order"],
    },
    "charts": {
        "name": "generate_visualization_charts2",
        "workflow_id": COZE_CHARTS_WORKFLOW_ID,
        "token": COZE_CHARTS_TOKEN,
        "inputs": [
            "equipment_id", "trend_result", "heatmap_data", "anomaly_events",
            "anomaly_result", "future_trend", "future_risk_level",
            "predicted_rul_hours", "failure_probability", "window_analysis",
            "visualization_data", "pre_failure_time", "risk_probability", "result",
        ],
        "outputs": ["trend_image", "perdicted_image", "hot_image", "wrong_image", "risk_image", "reason_image"],
    },
    "case_import": {
        "name": "case_library_import",
        "workflow_id": COZE_CASE_WORKFLOW_ID,
        "token": COZE_CASE_SYNC_TOKEN,
        "inputs": ["anli_file", "file_id", "file_name", "knowledge_id", "dataset_id"],
        "outputs": ["case_id", "status", "message"],
    },
}

# Backward compatible names used by older routes/readme.
COZE_WORKFLOW_ID = COZE_ANALYZE_WORKFLOW_ID
COZE_TOKEN = COZE_ANALYZE_TOKEN


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def normalize_date(value):
    if not value:
        return ""
    value = str(value).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return value


def normalize_timestamp(value):
    if not value:
        return ""
    value = str(value).strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return value


def percentile(values, pct):
    values = sorted([parse_float(v) for v in values if v not in (None, "", "null")])
    if not values:
        return 0.0
    if pct <= 0:
        return float(values[0])
    if pct >= 100:
        return float(values[-1])
    idx = (len(values) - 1) * pct / 100.0
    low = int(idx)
    high = min(low + 1, len(values) - 1)
    frac = idx - low
    return round(values[low] * (1 - frac) + values[high] * frac, 4)


def risk_level_from_score(score):
    score = parse_float(score)
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def normalize_risk_level(level, score=None):
    if level is None and score is not None:
        return risk_level_from_score(score)
    raw = str(level or "").strip().lower()
    mapping = {
        "正常": "low",
        "低": "low",
        "低风险": "low",
        "low": "low",
        "中": "medium",
        "中风险": "medium",
        "medium": "medium",
        "预警": "medium",
        "高": "high",
        "高风险": "high",
        "high": "high",
        "高危": "critical",
        "严重": "critical",
        "critical": "critical",
    }
    return mapping.get(raw, risk_level_from_score(score or 0))


def _risk_label_cn(level):
    return {
        "low": "低风险",
        "medium": "中风险",
        "high": "高风险",
        "critical": "高危",
    }.get(level, level or "低风险")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            return [text]
    return [value]


def _extract_json_blocks(text):
    blocks = []
    for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text or ""):
        try:
            blocks.append(json.loads(block))
        except Exception:
            continue
    stripped = (text or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            blocks.append(json.loads(stripped))
        except Exception:
            pass
    return blocks


def _extract_http_urls(value):
    urls = []
    if isinstance(value, str) and value.startswith("http"):
        urls.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(_extract_http_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_extract_http_urls(item))
    return urls


def extract_device_id_from_text(text):
    if not text:
        return None
    match = re.search(r"\b(CNC)[_-]?(\d{3})\b", text, re.IGNORECASE)
    if not match:
        return None
    return "%s_%s" % (match.group(1).upper(), match.group(2))


def _safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _ensure_mapping(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
        if isinstance(parsed, list):
            value = parsed
        else:
            return {}
    if isinstance(value, list):
        merged = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(item)
                continue
            if isinstance(item, str):
                try:
                    parsed = json.loads(item)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    merged.update(parsed)
        return merged
    return {}


def _knowledge_items(value):
    items = []
    for item in _as_list(value):
        if isinstance(item, dict):
            output = item.get("output") or item.get("text") or item.get("content") or item.get("suggestion")
            if output not in (None, "", [], {}):
                row = dict(item)
                row["output"] = str(output)
                items.append(row)
        elif item not in (None, "", [], {}):
            items.append({"output": str(item)})
    return items


def _extract_payload_text(payload):
    texts = []
    if payload is None:
        return texts
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            texts.append(text)
        return texts
    if isinstance(payload, list):
        for item in payload:
            texts.extend(_extract_payload_text(item))
        return texts
    if isinstance(payload, dict):
        for key in ("content", "answer", "text", "message", "delta", "reasoning_content"):
            value = payload.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    texts.append(text)
            elif isinstance(value, (dict, list)):
                texts.extend(_extract_payload_text(value))
        for key in ("data", "message", "messages", "result", "response", "choices", "output"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                texts.extend(_extract_payload_text(value))
        return texts
    return texts


def _try_parse_json_text(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_payload_text_by_keys(payload, keys):
    texts = []
    if payload is None:
        return texts
    if isinstance(payload, list):
        for item in payload:
            texts.extend(_extract_payload_text_by_keys(item, keys))
        return texts
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str):
                text = value.strip()
                if text:
                    texts.append(text)
            elif isinstance(value, (dict, list)):
                texts.extend(_extract_payload_text_by_keys(value, keys))
        return texts
    return texts


def _text_quality_score(text):
    text = str(text or "")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    mojibake = len(re.findall(r"[ÃÂ]|[\u00c0-\u00ff]{2,}|[�□]", text))
    return cjk * 4 - mojibake * 3


def repair_mojibake_text(text):
    text = str(text or "")
    if not text:
        return ""
    candidates = [text]
    for enc in ("latin1", "cp1252"):
        try:
            candidates.append(text.encode(enc).decode("utf-8"))
        except Exception:
            pass
    return max(candidates, key=_text_quality_score)


def _extract_coze_answer_text(payload):
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        return "".join(_extract_coze_answer_text(item) for item in payload).strip()
    if not isinstance(payload, dict):
        return ""
    msg_type = (payload.get("type") or "").strip().lower()
    role = (payload.get("role") or "").strip().lower()
    content_type = (payload.get("content_type") or "").strip().lower()
    if msg_type in ("question", "function_call", "tool_output", "tool_response", "follow_up", "verbose"):
        return ""
    if msg_type and msg_type not in ("answer", "assistant"):
        return ""
    if role and role not in ("assistant", "bot"):
        return ""
    content = payload.get("content")
    if isinstance(content, str):
        parsed = _try_parse_json_text(content)
        if parsed is not None:
            keyed_text = _extract_payload_text_by_keys(parsed, ("report_markdown", "report", "answer", "content", "text"))
            return ("\n".join(keyed_text).strip() if keyed_text else "".join(_extract_payload_text(parsed)).strip())
        if content_type and content_type not in ("", "text", "markdown", "object_string", "card"):
            return ""
        return content.strip()
    if isinstance(content, (dict, list)):
        text = _extract_payload_text(content)
        if text:
            return "\n".join(text).strip()
    nested = []
    for key in ("message", "messages", "data", "result", "response", "output"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            text = _extract_coze_answer_text(value)
            if text:
                nested.append(text)
    return "".join(nested).strip()


def classify_coze_error(text):
    raw = str(text or "")
    low = raw.lower()
    if "authentication is invalid" in low or "\"code\":4100" in low or "code=4100" in low or "401" in low:
        return "Coze 鉴权失败：token 无效、过期或未授权"
    if "token you entered is incorrect" in low or "\"code\":4101" in low or "code=4101" in low:
        return "Coze 鉴权失败：token 填写错误或已失效"
    if "forbidden" in low or "permission" in low:
        return "Coze 权限不足：当前 token 不能访问该机器人或工作流"
    if "read timed out" in low or "timed out" in low:
        return "Coze 请求超时"
    if "not found" in low or "\"code\":404" in low:
        return "Coze 资源不存在：机器人 ID 或工作流 ID 可能不对"
    if "timeout" in low:
        return "Coze 请求超时"
    return raw or "Coze 调用失败"


def _coze_raise_api_error(payload):
    if not isinstance(payload, dict):
        return
    code = payload.get("code")
    if code in (None, "", 0, "0"):
        return
    msg = payload.get("msg") or payload.get("message") or payload.get("error") or json.dumps(payload, ensure_ascii=False)[:500]
    raise RuntimeError("Coze API error code=%s: %s" % (code, classify_coze_error(msg)))


def _table_latest(sql, args=()):
    return row(sql + " ORDER BY created_at DESC LIMIT 1", args)


def build_report_context(device_id=None, hydrate=False, include_charts=False):
    focus_id = (device_id or "").strip()
    if not focus_id:
        pred = _table_latest("SELECT * FROM predictions")
        if pred and pred.get("device_id"):
            focus_id = pred["device_id"]
        else:
            device = row("SELECT * FROM devices ORDER BY id LIMIT 1")
            focus_id = device["id"] if device else ""
    device = load_device(focus_id) if focus_id else None
    prediction = _table_latest("SELECT * FROM predictions WHERE device_id=?", (focus_id,)) if focus_id else _table_latest("SELECT * FROM predictions")
    work_orders = rows(
        "SELECT * FROM work_orders%s ORDER BY created_at DESC LIMIT 12" % (" WHERE device_id=?" if focus_id else ""),
        (focus_id,) if focus_id else (),
    )
    open_orders = [w for w in work_orders if (w.get("status") or "pending") != "completed"]
    prediction_json = _safe_json_loads((prediction or {}).get("analysis_json"), {})
    if isinstance(prediction_json, list):
        prediction_json = _ensure_mapping(prediction_json) or {"items": prediction_json}
    elif isinstance(prediction_json, dict):
        prediction_json = normalize_analysis(prediction_json)
    charts_summary = {
        "has_chart_payload": any(
            prediction_json.get(key)
            for key in ("trend_result", "future_trend", "visualization_data", "heatmap_data", "anomaly_events")
        ),
        "predicted_rul_hours": (prediction or {}).get("predicted_rul_hours"),
        "risk_level": (prediction or {}).get("risk_level") or prediction_json.get("risk_level"),
        "anomaly_events": len(_as_list(prediction_json.get("anomaly_events"))),
        "heatmap_points": len(_as_list(prediction_json.get("heatmap_data"))),
        "image_urls": _extract_http_urls(prediction_json),
        "source": (prediction or {}).get("source"),
    }
    totals = rows("SELECT status, COUNT(*) AS count FROM work_orders GROUP BY status")
    order_counts = {item["status"]: item["count"] for item in totals}
    snapshot = load_latest_workflow_snapshot(focus_id)
    if hydrate and focus_id and not snapshot:
        try:
            snapshot = refresh_workflow_snapshot(focus_id, include_charts=include_charts)
        except Exception:
            snapshot = None
    if snapshot and isinstance(snapshot.get("analysis"), dict):
        prediction_json = normalize_analysis(snapshot.get("analysis") or prediction_json)
    latest_workorder = work_orders[0] if work_orders else {}
    workflow_bundle = snapshot or {
        "analysis": prediction_json or (prediction or {}),
        "workorder": latest_workorder,
        "charts": {
            "source": charts_summary.get("source"),
            "image_urls": charts_summary.get("image_urls", []),
            "has_chart_payload": charts_summary.get("has_chart_payload"),
        },
    }
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "focus_device_id": focus_id or "",
        "device": device,
        "prediction": prediction,
        "prediction_json": prediction_json,
        "recent_work_orders": work_orders,
        "open_work_orders": open_orders,
        "order_counts": order_counts,
        "dashboard": {
            "device_count": row("SELECT COUNT(*) AS count FROM devices").get("count", 0),
            "prediction_count": row("SELECT COUNT(*) AS count FROM predictions").get("count", 0),
            "work_order_count": row("SELECT COUNT(*) AS count FROM work_orders").get("count", 0),
        },
        "charts_summary": charts_summary,
        "latest_logs": load_logs(focus_id, 12) if focus_id else [],
        "latest_tests": load_tests(focus_id, 12) if focus_id else [],
        "workflow_bundle": workflow_bundle,
    }


def _trim_text(value, limit=320):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def normalize_coze_markdown(text):
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    text = text.replace("\\n", "\n").replace("\\t", " ")
    def _repair_table_separator(match):
        header = match.group(1)
        cells = len(header.strip().strip("|").split("|"))
        return header + "| " + " | ".join(["---"] * max(2, cells)) + " |\n"
    text = re.sub(r"(\|[^\n]+\|\s*\n)((?:[|\s:\-]*-+[|\s:\-]*\n)+)(?=\s*\|)", _repair_table_separator, text)
    text = re.sub(r"\|{2,}", "\n", text)
    text = re.sub(r"([。:：；;，,、])(?=\|)", r"\1\n\n", text)
    text = re.sub(r"(?i)(report生成时间[:：])", r"\n\n\1", text)
    text = re.sub(r"(报告生成时间[:：])", r"\n\n\1", text)
    text = re.sub(r"(^|\n)(#{1,6})(?!#)(?=\S)", r"\1\2 ", text)
    text = re.sub(r"(?<![#\n])(#{1,6})(?!#)(?=\S)", r"\n\n\1 ", text)
    text = re.sub(r"\n(#{1,6}\s+)", r"\n\n\1", text)
    text = re.sub(r"(?m)^(#{1,6}\s*[^|\n]+)\|", r"\1\n\n|", text)
    text = re.sub(r"(?m)^(#{1,6}\s+[^\n]+)\n(?=\S)", r"\1\n\n", text)
    text = re.sub(r"([^\n])\n(?=\s*\|[^|\n]+\|\s*\n\s*\|?\s*:?-{3,}:?)", r"\1\n\n", text)
    text = re.sub(r"---(?=#{1,6})", r"\n\n---\n\n", text)
    text = re.sub(r"(?m)^\s*---+\s*$", "---", text)
    text = re.sub(r"(?m)^\s*-\s*$", "", text)
    text = re.sub(r"([^\n\-|*])(?=(?:\d+\.|[-*])\s+)", r"\1\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def standardize_report_markdown(text):
    raw = "" if text is None else str(text)
    warnings = []
    text = repair_mojibake_text(html_unescape(raw)).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\xa0", " ")

    fenced_json = re.fullmatch(r"\s*```(?:json|markdown|md)?\s*([\s\S]*?)\s*```\s*", text, re.I)
    if fenced_json:
        text = fenced_json.group(1).strip()
        warnings.append("已移除外层代码块包装")

    parsed = None
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
    if parsed is not None:
        extracted = _extract_coze_answer_text(parsed) or "".join(_extract_payload_text(parsed)).strip()
        if extracted:
            text = extracted
            warnings.append("已提取JSON中的报告正文")

    if "\t" in text:
        lines = text.split("\n")
        converted = []
        changed = False
        index = 0
        while index < len(lines):
            line = lines[index]
            if "\t" not in line:
                converted.append(line)
                index += 1
                continue
            group = []
            while index < len(lines) and "\t" in lines[index]:
                group.append([cell.strip() for cell in lines[index].split("\t")])
                index += 1
            if len(group) >= 2:
                width = max(len(row) for row in group)
                rows = [row + [""] * (width - len(row)) for row in group]
                converted.append("| " + " | ".join(rows[0]) + " |")
                converted.append("| " + " | ".join(["---"] * width) + " |")
                converted.extend("| " + " | ".join(row) + " |" for row in rows[1:])
                changed = True
            else:
                converted.extend(" | ".join(row) for row in group)
        text = "\n".join(converted)
        if changed:
            warnings.append("已修复制表符表格")
        else:
            warnings.append("已清理制表符")

    before = text
    text = sanitize_coze_response_text(text)
    if text != before:
        warnings.append("已执行Markdown格式标准化")

    table_fixed = False
    blocks = re.split(r"(\n{2,})", text)
    for i, block in enumerate(blocks):
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        if not all("|" in line for line in lines[:2]):
            continue
        has_separator = any(re.match(r"^\|?\s*:?-{3,}:?(\s*\|\s*:?-{3,}:?)+\s*\|?$", line.strip()) for line in lines[:3])
        if has_separator:
            continue
        first_cols = len(lines[0].strip().strip("|").split("|"))
        second_cols = len(lines[1].strip().strip("|").split("|"))
        if first_cols >= 2 and second_cols == first_cols:
            lines.insert(1, "| " + " | ".join(["---"] * first_cols) + " |")
            blocks[i] = "\n".join(lines)
            table_fixed = True
    if table_fixed:
        text = "".join(blocks)
        warnings.append("已补全Markdown表格分隔行")

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        text = "## 维护报告\n\nCoze 智能体未返回有效正文。"
        warnings.append("Coze返回为空，已生成占位说明")
    seen = set()
    unique_warnings = []
    for item in warnings:
        if item and item not in seen:
            unique_warnings.append(item)
            seen.add(item)
    return text, unique_warnings


def sanitize_coze_response_text(text):
    if text is None:
        return ""
    text = repair_mojibake_text(html_unescape(str(text))).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"(?is)```json\s*[\s\S]*?```", "", text)
    text = re.sub(r"(?is)<pre>\s*<code[^>]*>\s*json?\s*[\s\S]*?</code>\s*</pre>", "", text)
    text = re.sub(r"(?is)<pre[^>]*>[\s\S]*?</pre>", "", text)
    text = re.sub(r"(?is)<code[^>]*>\s*json?\s*[\s\S]*?</code>", "", text)
    text = re.sub(r"(?im)^\s*#{1,6}\s*JSON\s*数据块.*$", "", text)
    text = re.sub(r"(?im)^\s*内容由AI生成，无法确保真实准确，仅供参考。?\s*$", "", text)
    text = re.sub(r"(?im)^\s*-{3,}\s*$", "", text)
    heading_match = re.search(r"(?m)^(#{1,6}\s+.+|[一二三四五六七八九十]+、.+)$", text)
    if heading_match:
        text = text[heading_match.start():]
    return normalize_coze_markdown(text).strip()


def markdown_to_html(md_text):
    text = sanitize_coze_response_text(md_text)
    if not text:
        return "<div></div>"
    code_blocks = []

    def _store_code(match):
        code_blocks.append("<pre><code>%s</code></pre>" % html_escape(match.group(2).rstrip()))
        return "\n@@CODE%d@@\n" % (len(code_blocks) - 1)

    text = re.sub(r"```(\w+)?\s*([\s\S]*?)```", _store_code, text)

    def inline(value):
        out = html_escape(value)
        image_url = r"(https?://[^)]+|data:image/[A-Za-z0-9.+-]+;base64,[^) \n]+)"
        out = re.sub(r"!\[([^\]]*)\]\(%s\)" % image_url, r'<img alt="\1" src="\2" loading="lazy" referrerpolicy="no-referrer">', out)
        out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noreferrer noopener">\1</a>', out)
        out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
        out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
        return out.replace("\n", "<br>")

    def is_table_separator(line):
        return bool(re.match(r"^\|?\s*:?-{3,}:?(\s*\|\s*:?-{3,}:?)+\s*\|?$", line.strip()))

    def is_table_line(line):
        return "|" in line and len(line.strip().strip("|").split("|")) >= 2

    def next_non_blank(lines, start):
        for idx in range(start, len(lines)):
            if lines[idx].strip():
                return idx
        return -1

    def starts_table(lines, index):
        if not is_table_line(lines[index]):
            return False
        next_idx = next_non_blank(lines, index + 1)
        return next_idx >= 0 and (is_table_separator(lines[next_idx]) or is_table_line(lines[next_idx]))

    def render_table(table_lines):
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in table_lines
            if not is_table_separator(line)
        ]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        header = rows[0]
        body_rows = rows[1:]
        thead = "<thead><tr>%s</tr></thead>" % "".join("<th>%s</th>" % inline(cell) for cell in header)
        tbody = "<tbody>%s</tbody>" % "".join(
            "<tr>%s</tr>" % "".join("<td>%s</td>" % inline(cell) for cell in row)
            for row in body_rows
        )
        return "<table>%s%s</table>" % (thead, tbody)

    def is_block_start(lines, index):
        line = lines[index].strip()
        return (
            bool(re.match(r"^#{1,6}\s+", line))
            or bool(re.match(r"^[一二三四五六七八九十]+、", line))
            or bool(re.match(r"^>\s?", line))
            or bool(re.match(r"^[-*]\s+", line))
            or bool(re.match(r"^\d+\.\s+", line))
            or bool(re.match(r"^@@CODE\d+@@$", line))
            or starts_table(lines, index)
        )

    blocks = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        code_match = re.fullmatch(r"@@CODE(\d+)@@", line)
        if code_match:
            blocks.append(code_blocks[int(code_match.group(1))])
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = min(6, len(heading.group(1)))
            blocks.append("<h%d>%s</h%d>" % (level, inline(heading.group(2)), level))
            index += 1
            continue
        if re.match(r"^[一二三四五六七八九十]+、", line):
            blocks.append("<h2>%s</h2>" % inline(line))
            index += 1
            continue
        if starts_table(lines, index):
            table_lines = []
            while index < len(lines) and lines[index].strip() and (is_table_line(lines[index]) or is_table_separator(lines[index])):
                table_lines.append(lines[index].strip())
                index += 1
            blocks.append(render_table(table_lines))
            continue
        if re.match(r"^>\s?", line):
            group = []
            while index < len(lines) and re.match(r"^>\s?", lines[index].strip()):
                group.append(re.sub(r"^>\s?", "", lines[index].strip()))
                index += 1
            blocks.append("<blockquote>%s</blockquote>" % inline("\n".join(group)))
            continue
        if re.match(r"^[-*]\s+", line):
            group = []
            while index < len(lines) and re.match(r"^[-*]\s+", lines[index].strip()):
                group.append(re.sub(r"^[-*]\s+", "", lines[index].strip()))
                index += 1
            blocks.append("<ul>%s</ul>" % "".join("<li>%s</li>" % inline(item) for item in group))
            continue
        if re.match(r"^\d+\.\s+", line):
            group = []
            while index < len(lines) and re.match(r"^\d+\.\s+", lines[index].strip()):
                group.append(re.sub(r"^\d+\.\s+", "", lines[index].strip()))
                index += 1
            blocks.append("<ol>%s</ol>" % "".join("<li>%s</li>" % inline(item) for item in group))
            continue
        group = []
        while index < len(lines) and lines[index].strip() and not is_block_start(lines, index):
            group.append(lines[index])
            index += 1
        if group:
            blocks.append("<p>%s</p>" % inline("\n".join(group)))
    return '<div class="md">%s</div>' % "".join(blocks)


def _compact_items(items, fields, limit=5):
    compact = []
    for item in (items or [])[:limit]:
        if not isinstance(item, dict):
            continue
        row = {}
        for field in fields:
            value = item.get(field)
            if value not in (None, "", [], {}):
                row[field] = value
        if row:
            compact.append(row)
    return compact


def _compact_metric_stats(stats):
    if not isinstance(stats, dict):
        return stats
    keep = ("mean", "std", "min", "max", "p5", "p25", "median", "p75", "p95", "cv", "iqr")
    return {key: stats.get(key) for key in keep if stats.get(key) not in (None, "", [], {})}


def _compact_raw_overview(raw_overview):
    if not isinstance(raw_overview, dict):
        return {}
    compact = {}
    for metric in ("temperature", "current", "voltage", "speed", "power"):
        value = raw_overview.get(metric)
        if isinstance(value, dict):
            compact[metric] = _compact_metric_stats(value)
        elif value not in (None, "", [], {}):
            compact[metric] = value
    for key in ("trends", "baselines", "safe_ranges", "degradation", "data_counts"):
        if raw_overview.get(key) not in (None, "", [], {}):
            compact[key] = raw_overview.get(key)
    return compact


def _compact_workflow_analysis(analysis):
    analysis = analysis if isinstance(analysis, dict) else {}
    result = analysis.get("analysis_result") if isinstance(analysis.get("analysis_result"), dict) else analysis.get("result")
    if not isinstance(result, dict):
        result = {}
    summary = {
        "device_id": analysis.get("device_id"),
        "risk_level": analysis.get("risk_level"),
        "health_score": analysis.get("health_score"),
        "predicted_rul_hours": analysis.get("predicted_rul_hours"),
        "fault_name": analysis.get("fault_name"),
        "confidence": analysis.get("confidence"),
        "source": analysis.get("source"),
        "workflow_error": analysis.get("workflow_error"),
        "report_text": _trim_text(normalize_coze_markdown(analysis.get("report_text")), 1800),
        "reasons": [item.get("text") for item in _compact_items([{"text": x} for x in _as_list(analysis.get("reasons"))], ["text"], limit=8)],
    }
    for key in ("risk_score", "risk_method", "future_risk_level", "failure_probability", "risk_probability", "pre_failure_time"):
        value = analysis.get(key) if analysis.get(key) not in (None, "", [], {}) else result.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    for key in ("device_health_score", "anomaly_score", "fused_score", "qei_score"):
        value = result.get(key) if result.get(key) not in (None, "", [], {}) else analysis.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    for key in (
        "future_risk_prediction",
        "future_probability",
        "diagnosis_engine",
        "rul_analysis",
        "anomaly_summary",
        "degradation_analysis",
        "dual_model_engine",
        "cost_analysis",
    ):
        value = result.get(key) if isinstance(result.get(key), dict) else analysis.get(key)
        if isinstance(value, dict):
            summary[key] = value
    raw_overview = result.get("raw_overview") if isinstance(result.get("raw_overview"), dict) else analysis.get("raw_overview")
    if isinstance(raw_overview, dict):
        summary["raw_overview"] = _compact_raw_overview(raw_overview)
    heatmap_data = result.get("heatmap_data") if result.get("heatmap_data") not in (None, "", [], {}) else analysis.get("heatmap_data")
    if heatmap_data not in (None, "", [], {}):
        summary["heatmap_data"] = _as_list(heatmap_data)[:12] if isinstance(heatmap_data, list) else heatmap_data
    predicted_fault = result.get("predicted_fault") if isinstance(result.get("predicted_fault"), dict) else analysis.get("predicted_fault")
    if isinstance(predicted_fault, dict):
        summary["predicted_fault"] = predicted_fault
    return summary


def _compact_workflow_workorder(workorder):
    workorder = workorder if isinstance(workorder, dict) else {}
    return {
        "id": workorder.get("id"),
        "work_order_id": workorder.get("work_order_id"),
        "equipment_id": workorder.get("equipment_id") or workorder.get("device_id"),
        "title": workorder.get("title"),
        "description": _trim_text(workorder.get("description"), 600),
        "priority": workorder.get("priority") or workorder.get("priority_label"),
        "status": workorder.get("status"),
        "failure_type": workorder.get("failure_type") or workorder.get("fault_name"),
        "estimated_cost": workorder.get("estimated_cost"),
        "op_risk": workorder.get("op_risk"),
        "suggested_window": workorder.get("suggested_window"),
        "steps": _as_list(workorder.get("steps"))[:8],
        "source": workorder.get("source"),
        "cost_analysis": workorder.get("cost_analysis") if isinstance(workorder.get("cost_analysis"), dict) else None,
    }


def _compact_workflow_charts(charts):
    charts = charts if isinstance(charts, dict) else {}
    images = charts.get("images") if isinstance(charts.get("images"), list) else []
    return {
        "source": charts.get("source"),
        "image_urls": [item.get("url") for item in images if isinstance(item, dict) and item.get("url")][:6],
        "workflow_error": charts.get("workflow_error"),
        "report_text": _trim_text(normalize_coze_markdown(charts.get("report_text")), 1200),
        "raw_keys": list((charts.get("raw") or {}).keys())[:12] if isinstance(charts.get("raw"), dict) else [],
    }


def _compact_report_scope_for_coze(scope):
    scope = scope if isinstance(scope, dict) else {}
    work_order = scope.get("work_order") if isinstance(scope.get("work_order"), dict) else {}
    return {
        "core": {
            "device_id": scope.get("device_id") or scope.get("equipment_id"),
            "risk_level": scope.get("risk_level"),
            "health_score": scope.get("health_score"),
            "predicted_rul_hours": scope.get("predicted_rul_hours"),
            "pre_failure_time": scope.get("pre_failure_time"),
            "full_chain_support": scope.get("full_chain_support"),
            "confidence_pct": scope.get("confidence_pct"),
        },
        "raw_overview": _compact_raw_overview(scope.get("raw_overview")),
        "predicted_fault": scope.get("predicted_fault"),
        "top3_faults": _as_list(scope.get("top3_faults"))[:5],
        "rul_analysis": scope.get("rul_analysis"),
        "anomaly_summary": scope.get("anomaly_summary"),
        "future_probability": scope.get("future_probability"),
        "degradation_analysis": scope.get("degradation_analysis"),
        "diagnosis_engine": scope.get("diagnosis_engine"),
        "cost_analysis": scope.get("cost_analysis"),
        "work_order": {
            "id": work_order.get("id"),
            "work_order_id": work_order.get("work_order_id"),
            "device_id": work_order.get("device_id") or work_order.get("equipment_id"),
            "title": work_order.get("title"),
            "priority": work_order.get("priority") or work_order.get("priority_label"),
            "status": work_order.get("status"),
            "failure_type": work_order.get("failure_type") or work_order.get("fault_name"),
            "estimated_cost": work_order.get("estimated_cost"),
            "suggested_window": work_order.get("suggested_window"),
            "op_risk": work_order.get("op_risk"),
            "steps": _as_list(work_order.get("step_items") or work_order.get("steps"))[:8],
        },
        "work_order_steps_text": scope.get("work_order_steps"),
        "evidence_lines": scope.get("evidence_lines"),
        "quality_risk_text": scope.get("quality_risk_text"),
        "history_cases_text": scope.get("history_cases_text"),
    }


def build_coze_prompt_context(context):
    device = context.get("device") or {}
    prediction_row = context.get("prediction") or {}
    workflow_bundle = context.get("workflow_bundle") or {}
    analysis = _safe_json_loads(workflow_bundle.get("analysis"), {})
    workorder = _safe_json_loads(workflow_bundle.get("workorder"), {})
    charts = _safe_json_loads(workflow_bundle.get("charts"), {})
    if not isinstance(analysis, dict):
        analysis = {}
    if not isinstance(workorder, dict):
        workorder = {}
    if not isinstance(charts, dict):
        charts = {}
    latest_logs = _compact_items(context.get("latest_logs", []), ["timestamp", "failure_type", "current", "temperature", "voltage", "speed"], limit=5)
    latest_tests = _compact_items(context.get("latest_tests", []), ["date", "line", "machine", "parameter", "measmt_value", "lwr_spec_limit", "upr_spec_limit", "wrkstn_nm"], limit=5)
    recent_orders = _compact_items(context.get("recent_work_orders", []), ["id", "device_id", "title", "priority", "status", "failure_type", "estimated_cost", "created_at"], limit=5)
    open_orders = _compact_items(context.get("open_work_orders", []), ["id", "device_id", "title", "priority", "status", "failure_type", "estimated_cost", "created_at"], limit=5)
    report_scope = _prepare_report_scope(context, [])
    compact_context = {
        "generated_at": context.get("generated_at"),
        "focus_device_id": context.get("focus_device_id"),
        "dashboard": context.get("dashboard"),
        "device": {
            "id": device.get("id"),
            "type": device.get("type"),
            "line": device.get("line"),
            "temperature": device.get("temperature"),
            "amperage": device.get("amperage"),
            "voltage": device.get("voltage"),
            "rotor_speed": device.get("rotor_speed"),
            "risk_score": device.get("risk_score"),
            "risk_level": device.get("risk_level"),
            "status": device.get("status"),
            "latest_log_time": device.get("latest_log_time"),
        },
        "prediction_row": {
            "risk_level": prediction_row.get("risk_level"),
            "health_score": prediction_row.get("health_score"),
            "predicted_rul_hours": prediction_row.get("predicted_rul_hours"),
            "fault_name": prediction_row.get("fault_name"),
            "source": prediction_row.get("source"),
            "created_at": prediction_row.get("created_at"),
        },
        "workflow_analysis": _compact_workflow_analysis(analysis),
        "workflow_workorder": _compact_workflow_workorder(workorder),
        "workflow_charts": _compact_workflow_charts(charts),
        "report_ready_data": _compact_report_scope_for_coze(report_scope),
        "charts_summary": context.get("charts_summary"),
        "open_work_orders": open_orders,
        "recent_work_orders": recent_orders,
        "latest_logs": latest_logs,
        "latest_tests": latest_tests,
    }
    summary_lines = [
        "device_id: %s" % (device.get("id") or context.get("focus_device_id") or "not_provided"),
        "device_type: %s" % (device.get("type") or "not_provided"),
        "line: %s" % (device.get("line") or "not_provided"),
        "risk_level: %s" % (report_scope.get("risk_level") or analysis.get("risk_level") or prediction_row.get("risk_level") or device.get("risk_level") or "not_provided"),
        "health_score: %s" % (report_scope.get("health_score") or analysis.get("health_score") or prediction_row.get("health_score") or device.get("risk_score") or "not_provided"),
        "rul_hours: %s" % (report_scope.get("predicted_rul_hours") or analysis.get("predicted_rul_hours") or prediction_row.get("predicted_rul_hours") or "not_provided"),
        "workorder_title: %s" % ((report_scope.get("work_order") or {}).get("title") or workorder.get("title") or workorder.get("description") or "not_provided"),
        "plan_cost: %s" % _report_value(_nested_get(report_scope, "cost_analysis.plan_cost")),
        "emergency_cost: %s" % _report_value(_nested_get(report_scope, "cost_analysis.emerg_cost")),
        "downtime_loss: %s" % _report_value(_nested_get(report_scope, "cost_analysis.down_loss") or _nested_get(report_scope, "cost_analysis.shut_loss")),
        "roi_savings: %s" % _report_value(_nested_get(report_scope, "cost_analysis.roi_savings")),
        "chart_count: %s" % len(compact_context["workflow_charts"].get("image_urls") or []),
    ]
    return compact_context, "\n".join(summary_lines)


def build_report_prompt(context):
    compact_context, summary_text = build_coze_prompt_context(context)
    payload = {
        "instructions": [
            "Answer in Chinese.",
            "Use only the provided data. Do not invent alarms, work orders, timestamps or chart contents.",
            "Return a complete Markdown report with headings, tables and bullet lists.",
            "Put every heading on its own line and leave a blank line between sections.",
            "Write tables in standard Markdown table format with one row per line; do not compress rows into one line.",
            "Do not use pipe characters as paragraph separators.",
            "Keep the Coze bot report style; do not compress the answer into one short paragraph.",
            "Mention explicitly if an upstream result is local fallback.",
            "Return prose, not raw JSON.",
            "Required sections: device overview, latest prediction, work order, chart summary, risk evidence, conclusion and advice.",
        ],
        "context": compact_context,
    }
    return (
        "You are the report agent of an intelligent predictive maintenance system. "
        "Answer in Chinese and generate a formal, complete Markdown maintenance report. "
        "Integrate the current device data, WF_analyze output, WF3 work-order output and chart workflow output. "
        "The data has already been prepared by the backend; do not say data is missing unless a field is truly empty. "
        "Do not compress the report into a few short lines and do not output raw JSON.\n\n"
        + summary_text
        + "\n\nStructured context:\n"
        + json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    )


def build_local_report(context, source_label="local_fallback", error=None):
    device = context.get("device") or {}
    prediction = context.get("prediction") or {}
    orders = context.get("recent_work_orders") or []
    health_score = prediction.get("health_score")
    if health_score is None:
        health_score = device.get("risk_score", "未提供")
    rul_hours = prediction.get("predicted_rul_hours")
    if rul_hours is None:
        rul_hours = "未提供"
    lines = [
        "## 本地兜底报告",
        "",
        f"- 生成时间: {context.get('generated_at')}",
        f"- 报告来源: {source_label}",
    ]
    if error:
        lines.append(f"- 失败原因: {error}")
    lines.extend([
        "",
        "## 设备概况",
        f"- 设备ID: {device.get('id') or context.get('focus_device_id') or '未提供'}",
        f"- 类型: {device.get('type') or '未提供'}",
        f"- 产线: {device.get('line') or '未提供'}",
        f"- 风险等级: {prediction.get('risk_level') or device.get('risk_level') or '未提供'}",
        f"- 健康评分: {health_score}",
        f"- 预测RUL: {rul_hours}",
        "",
        "## 维护工单",
    ])
    if orders:
        for item in orders[:8]:
            lines.append(
                f"- #{item.get('id')} {item.get('device_id')} | {item.get('priority')} | {item.get('status')} | {item.get('title')}"
            )
    else:
        lines.append("- 暂无工单")
    lines.extend([
        "",
        "## 图表摘要",
        f"- 风险等级: {context.get('charts_summary', {}).get('risk_level') or '未提供'}",
        f"- RUL: {context.get('charts_summary', {}).get('predicted_rul_hours') or '未提供'}",
        f"- 异常事件数: {context.get('charts_summary', {}).get('anomaly_events', 0)}",
        f"- 热力图点数: {context.get('charts_summary', {}).get('heatmap_points', 0)}",
        "",
        "## 结论与建议",
        "- 以上内容基于本地数据库与最新可用预测结果生成。",
    ])
    return "\n".join(lines)


REPORT_CHART_LABELS = ["趋势", "寿命", "热力", "异常", "风险", "归因"]


FIELD_DEFINITIONS = [
    {
        "path": "raw_overview",
        "label": "原始运行概览",
        "source_node": "EDA/原始数据处理",
        "meaning": "汇总温度、电流、电压、转速、功率等短时序运行状态。",
        "usage": "用于健康基线、趋势图、运行状态表和报告第一章。",
        "basis": "WF_analyze 从设备日志抽取约30个采样点并计算描述统计、分位数和趋势。",
    },
    {
        "path": "raw_overview.temperature",
        "label": "温度统计",
        "source_node": "EDA完整统计",
        "meaning": "温度均值、P5/P95、波动、偏度、峰度和变异系数。",
        "usage": "用于判断热稳定性、温升趋势、异常阈值和趋势图基线带。",
        "basis": "YAML中 EDA 节点计算 mean/std/p5/p95/skewness/kurtosis/cv。",
    },
    {
        "path": "raw_overview.current",
        "label": "电流统计",
        "source_node": "EDA完整统计",
        "meaning": "电流负载状态及波动范围。",
        "usage": "用于判断负载耦合、功率、风险概率和异常散点。",
        "basis": "YAML中使用电流均值、CV和跨设备基线参与健康评分。",
    },
    {
        "path": "raw_overview.trends",
        "label": "参数趋势",
        "source_node": "趋势演化节点",
        "meaning": "温度、电流、电压、转速等指标的上升、下降或稳定状态。",
        "usage": "用于解释趋势图、退化方向和风险是否升级。",
        "basis": "WF_analyze 滑动窗口与趋势演化逻辑输出。",
    },
    {
        "path": "anomaly_summary",
        "label": "异常检测摘要",
        "source_node": "异常检测节点",
        "meaning": "综合异常评分、异常事件数、连续异常次数和异常特征。",
        "usage": "用于异常分析图、报告第二章和维护优先级判断。",
        "basis": "YAML异常检测节点融合 Z-score、IQR、CUSUM、规格阈值和连续异常。",
    },
    {
        "path": "heatmap_data",
        "label": "参数相关热力数据",
        "source_node": "EDA相关性分析",
        "meaning": "关键传感器对的 Pearson/Spearman 相关系数。",
        "usage": "用于异常热力图和参数耦合解释。",
        "basis": "YAML相关性分析计算温度-电流、电压-电流、转速稳定性等关系。",
    },
    {
        "path": "dual_model_engine",
        "label": "跨设备统计学习诊断",
        "source_node": "双模型融合节点",
        "meaning": "融合评分、基线偏差、模型一致性和历史案例匹配。",
        "usage": "用于预测故障类型、置信度和报告第三章。",
        "basis": "YAML聚合器收集偏差分析、相似度匹配和故障指纹结果。",
    },
    {
        "path": "predicted_fault",
        "label": "预测故障",
        "source_node": "双模型融合节点",
        "meaning": "最可能故障类型、故障编码、置信度和 Top3 候选。",
        "usage": "用于故障预测卡片、报告第三章和工单故障类型。",
        "basis": "WF_analyze 根据跨设备统计中心和历史故障模式输出。",
    },
    {
        "path": "rul_analysis",
        "label": "RUL剩余寿命",
        "source_node": "RUL节点",
        "meaning": "预测剩余寿命、维护紧迫度、风险级别和预测故障时间。",
        "usage": "用于RUL仪表盘、报告第四章和维护计划。",
        "basis": "YAML中短序列退化估计结合指数退化/威布尔思想输出。",
    },
    {
        "path": "degradation_analysis",
        "label": "退化趋势分析",
        "source_node": "趋势演化节点",
        "meaning": "退化方向、未来温度/电流、退化阶段和稳定性。",
        "usage": "用于趋势预测、RUL解释和维护窗口判断。",
        "basis": "WF_analyze 使用滑动窗口和未来趋势节点生成。",
    },
    {
        "path": "future_probability",
        "label": "未来风险概率",
        "source_node": "风险升级节点",
        "meaning": "未来故障概率、健康评分、风险升级路径和未来风险等级。",
        "usage": "用于风险评估图和报告第四章。",
        "basis": "YAML风险升级节点输出 risk_probability 与 risk_evolution_path。",
    },
    {
        "path": "diagnosis_engine",
        "label": "诊断证据链",
        "source_node": "诊断证据链节点",
        "meaning": "证据、推理、诊断置信度和维护原因。",
        "usage": "用于报告第五章和维护建议解释。",
        "basis": "YAML聚合器收集 evidence、risk_reason、maintenance_reason。",
    },
    {
        "path": "cost_analysis",
        "label": "经营成本分析",
        "source_node": "WF3工单/成本节点",
        "meaning": "计划维修、紧急维修、停机损失、ROI和日产值。",
        "usage": "用于经营成本表、维护报告第六章和工单优先级。",
        "basis": "WF3工作流或最新工单返回的成本字段。",
    },
    {
        "path": "work_order",
        "label": "维修工单",
        "source_node": "WF3工单节点",
        "meaning": "工单编号、优先级、故障类型、预计成本和执行步骤。",
        "usage": "用于维护闭环、报告第六章和工单列表。",
        "basis": "最新 workflow_snapshots.workorder_json 或 work_orders 表记录。",
    },
]


def _nested_get(obj, path, default=None):
    if obj is None or not path:
        return default
    current = obj
    aliases = {
        "温度": "temperature",
        "电流": "current",
        "电压": "voltage",
        "转速": "speed",
        "寿命": "rul",
    }
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part in current:
                current = current.get(part)
            elif aliases.get(part) in current:
                current = current.get(aliases[part])
            else:
                return default
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def _report_value(value):
    if value in (None, "", [], {}):
        return "未提供"
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        if not value:
            return "未提供"
        parts = []
        for item in value[:8]:
            if isinstance(item, dict):
                parts.append(
                    str(
                        item.get("fault_name")
                        or item.get("label")
                        or item.get("name")
                        or item.get("summary")
                        or item.get("abnormal_reason")
                        or json.dumps(item, ensure_ascii=False, default=str)
                    )
                )
            else:
                parts.append(str(item))
        return "、".join(parts)
    if isinstance(value, dict):
        if "score" in value:
            return _report_value(value.get("score"))
        if "tier" in value:
            return _report_value(value.get("tier"))
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _format_list_for_report(value):
    items = _as_list(value)
    if not items:
        return "未提供"
    parts = []
    for item in items[:10]:
        if isinstance(item, dict):
            if item.get("fault_name"):
                prob = item.get("probability")
                parts.append("%s%s" % (item.get("fault_name"), (" %.1f%%" % (parse_float(prob, 0) * 100)) if prob is not None and parse_float(prob, 0) <= 1 else ((" %s%%" % prob) if prob is not None else "")))
            elif item.get("metric") or item.get("indicator"):
                parts.append("%s：%s" % (item.get("metric") or item.get("indicator"), item.get("reason") or item.get("inference") or item.get("value") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        else:
            parts.append(str(item))
    return "、".join([p for p in parts if p]) or "未提供"


def _sanitize_chart_images(chart_images):
    clean = []
    for item in _as_list(chart_images):
        if isinstance(item, dict):
            item = item.get("data_url") or item.get("url") or item.get("src")
        text = str(item or "").strip()
        if re.match(r"^data:image/(png|jpeg|jpg|webp);base64,[A-Za-z0-9+/=\s]+$", text, re.I):
            clean.append(re.sub(r"\s+", "", text))
        elif _looks_like_chart_url(text):
            clean.append(text)
        if len(clean) >= 6:
            break
    return clean


def _normalise_work_order(workorder, fallback_row=None):
    result = dict(workorder or {})
    row_data = dict(fallback_row or {})
    for key in ("id", "device_id", "title", "priority", "status", "estimated_cost", "created_at", "steps"):
        if result.get(key) in (None, "", [], {}) and row_data.get(key) not in (None, "", [], {}):
            result[key] = row_data.get(key)
    parsed_steps = _safe_json_loads(result.get("steps"), None)
    if isinstance(parsed_steps, dict):
        raw = parsed_steps.get("raw") if isinstance(parsed_steps.get("raw"), dict) else {}
        if isinstance(raw, dict):
            for key in (
                "equipment_id",
                "failure_type",
                "op_risk",
                "priority",
                "priority_label",
                "status",
                "suggested_window",
                "work_order_id",
                "estimated_cost",
                "description",
            ):
                if result.get(key) in (None, "", [], {}) and raw.get(key) not in (None, "", [], {}):
                    result[key] = raw.get(key)
            if result.get("cost_analysis") in (None, "", [], {}) and isinstance(raw.get("cost_analysis"), dict):
                result["cost_analysis"] = raw.get("cost_analysis")
        if result.get("step_items") in (None, "", [], {}) and parsed_steps.get("steps") not in (None, "", [], {}):
            result["step_items"] = parsed_steps.get("steps")
    if result.get("work_order_id") in (None, "", [], {}):
        result["work_order_id"] = result.get("id") or row_data.get("id") or "未生成"
    if result.get("priority_label") in (None, "", [], {}):
        result["priority_label"] = result.get("priority") or "未提供"
    if result.get("failure_type") in (None, "", [], {}):
        result["failure_type"] = result.get("fault_name") or result.get("title") or "未提供"
    if result.get("estimated_cost") in (None, "", [], {}):
        result["estimated_cost"] = row_data.get("estimated_cost") or "未提供"
    return result


def _work_order_steps_text(workorder):
    steps = workorder.get("step_items") or workorder.get("steps") if isinstance(workorder, dict) else []
    if isinstance(steps, str):
        parsed = _safe_json_loads(steps, None)
        if isinstance(parsed, dict):
            steps = parsed.get("steps") or parsed.get("actions") or parsed.get("items") or []
        elif isinstance(parsed, list):
            steps = parsed
        else:
            steps = [line.strip() for line in steps.splitlines() if line.strip()]
    items = _as_list(steps)
    if not items:
        return "- 暂无工单步骤"
    output = []
    for index, item in enumerate(items[:10], 1):
        if isinstance(item, dict):
            text = item.get("action") or item.get("step") or item.get("description") or json.dumps(item, ensure_ascii=False, default=str)
        else:
            text = str(item)
        output.append("%d. %s" % (index, text))
    return "\n".join(output)


def _safe_daily_value(device):
    if not isinstance(device, dict):
        return 0
    return parse_float(device.get("units_per_day"), 0) * parse_float(device.get("unit_cost"), 0)


def _prepare_report_scope(context, chart_images=None):
    workflow_bundle = context.get("workflow_bundle") or {}
    analysis = workflow_bundle.get("analysis") if isinstance(workflow_bundle.get("analysis"), dict) else context.get("prediction_json") or {}
    analysis = normalize_analysis(analysis if isinstance(analysis, dict) else {})
    prediction = context.get("prediction") or {}
    device = context.get("device") or {}
    work_orders = context.get("recent_work_orders") or []
    workorder = workflow_bundle.get("workorder") if isinstance(workflow_bundle.get("workorder"), dict) else {}
    workorder = _normalise_work_order(workorder, work_orders[0] if work_orders else {})
    cost_analysis = {}
    if isinstance(workorder.get("cost_analysis"), dict):
        cost_analysis.update(workorder.get("cost_analysis"))
    if isinstance(analysis.get("cost_analysis"), dict):
        cost_analysis = {**analysis.get("cost_analysis"), **cost_analysis}
    if "daily_out" not in cost_analysis and device.get("units_per_day") is not None:
        cost_analysis["daily_out"] = device.get("units_per_day")
    if "unit_cost" not in cost_analysis and device.get("unit_cost") is not None:
        cost_analysis["unit_cost"] = device.get("unit_cost")
    if "daily_revenue" not in cost_analysis:
        daily = _safe_daily_value(device)
        if daily:
            cost_analysis["daily_revenue"] = round(daily, 2)
    if "plan_cost" not in cost_analysis and workorder.get("estimated_cost") not in (None, "", [], {}):
        cost_analysis["plan_cost"] = workorder.get("estimated_cost")
    if "roi_savings" not in cost_analysis and cost_analysis.get("emerg_cost") not in (None, "", [], {}) and cost_analysis.get("plan_cost") not in (None, "", [], {}):
        cost_analysis["roi_savings"] = round(parse_float(cost_analysis.get("emerg_cost"), 0) - parse_float(cost_analysis.get("plan_cost"), 0), 2)

    raw_overview = analysis.get("raw_overview") if isinstance(analysis.get("raw_overview"), dict) else {}
    if not raw_overview:
        report = analysis.get("analysis_report") if isinstance(analysis.get("analysis_report"), dict) else {}
        baseline = analysis.get("baseline_analysis") if isinstance(analysis.get("baseline_analysis"), dict) else {}
        raw_overview = {
            "temperature": {"mean": report.get("avg_temperature"), "std": report.get("temp_std"), **(baseline.get("temperature") or {})},
            "current": {"mean": report.get("avg_current"), "std": report.get("current_std"), **(baseline.get("current") or {})},
            "voltage": {"mean": report.get("avg_voltage"), "std": report.get("voltage_std"), **(baseline.get("voltage") or {})},
            "speed": {"mean": report.get("avg_speed"), "std": report.get("speed_std"), **(baseline.get("speed") or {})},
            "power": {"mean": report.get("avg_power")},
            "trends": analysis.get("trend_analysis") or {},
            "data_counts": {"total_records": report.get("log_count"), "features_count": report.get("param_record_count")},
        }
    raw_overview = dict(raw_overview)
    raw_overview.setdefault("safe_ranges", {})
    if not raw_overview["safe_ranges"].get("temperature"):
        temp = raw_overview.get("temperature") if isinstance(raw_overview.get("temperature"), dict) else {}
        raw_overview["safe_ranges"]["temperature"] = "%s-%s℃" % (_report_value(temp.get("p5")), _report_value(temp.get("p95")))
    if not raw_overview["safe_ranges"].get("current"):
        curr = raw_overview.get("current") if isinstance(raw_overview.get("current"), dict) else {}
        raw_overview["safe_ranges"]["current"] = "%s-%sA" % (_report_value(curr.get("p5")), _report_value(curr.get("p95")))
    raw_overview.setdefault("degradation", {})
    degradation = raw_overview["degradation"] if isinstance(raw_overview["degradation"], dict) else {}
    degr_analysis = analysis.get("degradation_analysis") if isinstance(analysis.get("degradation_analysis"), dict) else {}
    degr_features = degr_analysis.get("degradation_features") if isinstance(degr_analysis.get("degradation_features"), dict) else {}
    for key, value in degr_features.items():
        degradation.setdefault(key, value)
    if "degradation_stage" not in degradation and degr_analysis.get("degradation_stage"):
        degradation["degradation_stage"] = degr_analysis.get("degradation_stage")
    if isinstance(degradation.get("equipment_maturity"), dict):
        maturity = degradation.get("equipment_maturity")
        degradation["age_tier"] = degradation.get("age_tier") or maturity.get("tier")
        degradation["equipment_maturity"] = maturity.get("score")
    raw_overview["degradation"] = degradation

    scope = dict(analysis)
    scope["equipment_id"] = analysis.get("equipment_id") or analysis.get("device_id") or context.get("focus_device_id")
    scope["device_id"] = scope["equipment_id"]
    scope["risk_level"] = analysis.get("risk_level_cn") or _risk_label_cn(analysis.get("risk_level"))
    scope["health_score"] = _report_value(analysis.get("health_score") or prediction.get("health_score") or device.get("risk_score"))
    scope["raw_overview"] = raw_overview
    scope["cost_analysis"] = cost_analysis
    scope["work_order"] = workorder
    scope["full_chain_support"] = "支持" if _nested_get(analysis, "cross_source_analysis.full_chain_available", False) or _nested_get(analysis, "cross_source_analysis.quality_equipment_linkage.applicable", False) else "仅日志评估"
    predicted_fault = analysis.get("predicted_fault") if isinstance(analysis.get("predicted_fault"), dict) else {}
    if not predicted_fault:
        predicted_fault = {"fault_name": analysis.get("fault_name"), "confidence": analysis.get("confidence")}
    scope["predicted_fault"] = predicted_fault
    confidence = parse_float(predicted_fault.get("confidence", analysis.get("confidence")), 0)
    scope["confidence_pct"] = int(round(confidence * 100 if confidence <= 1 else confidence))
    scope["top3_faults"] = predicted_fault.get("top3_faults") or analysis.get("top3_faults") or []
    scope["top3_faults_text"] = _format_list_for_report(scope["top3_faults"])
    scope["history_cases_text"] = _format_list_for_report(_nested_get(analysis, "dual_model_engine.dual_model_result.cross_device_reference_cases") or _nested_get(analysis, "dual_model_result.cross_device_reference_cases") or [])
    evidence = _nested_get(analysis, "diagnosis_engine.evidence", [])
    if _as_list(evidence):
        lines = []
        for index, item in enumerate(_as_list(evidence), 1):
            if isinstance(item, dict):
                metric = item.get("metric") or item.get("indicator") or item.get("feature") or "证据"
                observed = item.get("observed") or item.get("value") or item.get("observation") or item.get("score") or "未提供"
                reason = item.get("reason") or item.get("inference") or item.get("conclusion") or item.get("logic") or "工作流证据项"
                lines.append("> %d %s：%s → %s" % (index, metric, observed, reason))
            else:
                lines.append("> %d %s" % (index, item))
        scope["evidence_lines"] = "\n".join(lines)
    else:
        scope["evidence_lines"] = "- 暂无单条证据项；当前结论基于统计基线、异常摘要和工作流总评分。"
    quality_impact = _nested_get(analysis, "dual_model_engine.dual_model_result.quality_impact") or _nested_get(analysis, "dual_model_result.quality_impact") or {}
    if isinstance(quality_impact, dict) and quality_impact.get("quality_sensitive"):
        scope["quality_risk_text"] = _report_value(quality_impact)
    else:
        scope["quality_risk_text"] = "未识别到明确质量敏感风险，仍建议结合测试记录复核。"
    scope["work_order_steps"] = _work_order_steps_text(workorder)
    scope["chart_images"] = _sanitize_chart_images(chart_images or [])
    return scope


def _build_chart_section(chart_images):
    images = _sanitize_chart_images(chart_images)
    lines = []
    for index, label in enumerate(REPORT_CHART_LABELS):
        if index < len(images) and images[index]:
            lines.append("![%s](%s)" % (label, images[index]))
        else:
            lines.append("- %s图表暂缺：请在前端生成图表后导出 PDF。" % label)
    return "\n\n".join(lines)


def render_template_report(context, chart_images=None):
    scope = _prepare_report_scope(context, chart_images)
    scope["chart_section"] = _build_chart_section(scope.get("chart_images") or [])
    try:
        with open(REPORT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            template = f.read()
    except Exception:
        template = build_local_report(context, source_label="wf_analyze_template_missing")

    def replace_list(match):
        return _format_list_for_report(_nested_get(scope, match.group(1), []))

    def replace_value(match):
        return _report_value(_nested_get(scope, match.group(1), scope.get(match.group(1))))

    text = re.sub(r"\{list:([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\}", replace_list, template)
    text = re.sub(r"\{([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\}", replace_value, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _workflow_yaml_digest(limit=18):
    if not os.path.exists(WF_ANALYZE_YAML_PATH):
        return {"path": WF_ANALYZE_YAML_PATH, "available": False, "nodes": []}
    nodes = []
    current = None
    try:
        with open(WF_ANALYZE_YAML_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                title_match = re.match(r"\s*title:\s*(.+?)\s*$", line)
                if title_match:
                    current = {"title": title_match.group(1).strip().strip('"'), "description": ""}
                    nodes.append(current)
                    continue
                desc_match = re.match(r"\s*description:\s*(.+?)\s*$", line)
                if desc_match and current is not None and not current.get("description"):
                    current["description"] = desc_match.group(1).strip().strip('"')
                if len(nodes) >= limit:
                    break
    except Exception:
        return {"path": WF_ANALYZE_YAML_PATH, "available": False, "nodes": []}
    return {"path": WF_ANALYZE_YAML_PATH, "available": True, "nodes": nodes}


def build_maintenance_explain(device_id=None):
    context = build_report_context(device_id, hydrate=False, include_charts=False)
    scope = _prepare_report_scope(context, [])
    analysis = context.get("workflow_bundle", {}).get("analysis") or context.get("prediction_json") or {}
    workorder = scope.get("work_order") or {}
    source = analysis.get("source") or (context.get("prediction") or {}).get("source") or "unknown"
    fields = []
    for item in FIELD_DEFINITIONS:
        path = item["path"]
        value = _nested_get(scope, path)
        if value is None and path == "cost_analysis":
            value = scope.get("cost_analysis")
        if value is None and path == "work_order":
            value = workorder
        fields.append({
            **item,
            "value": _report_value(value),
            "raw_available": value not in (None, "", [], {}),
        })
    return {
        "device_id": scope.get("equipment_id"),
        "generated_at": context.get("generated_at"),
        "source": source,
        "is_local_fallback": "local" in str(source).lower() or bool(analysis.get("workflow_error")),
        "snapshot": {
            "id": (context.get("workflow_bundle") or {}).get("id"),
            "created_at": (context.get("workflow_bundle") or {}).get("created_at"),
            "query_type": (context.get("workflow_bundle") or {}).get("query_type"),
        },
        "summary": {
            "risk_level": scope.get("risk_level"),
            "health_score": scope.get("health_score"),
            "predicted_rul_hours": _report_value(scope.get("predicted_rul_hours")),
            "fault_name": _report_value(_nested_get(scope, "predicted_fault.fault_name") or scope.get("fault_name")),
            "anomaly_events": len(_as_list(scope.get("anomaly_events"))),
            "full_chain_support": scope.get("full_chain_support"),
        },
        "fields": fields,
        "workflow_yaml": _workflow_yaml_digest(),
    }


def build_chat_prompt(message, device_id=None, context=None):
    context = context or build_report_context(device_id, hydrate=False, include_charts=False)
    compact_context, summary_text = build_coze_prompt_context(context)
    return (
        "You are the AI maintenance assistant of Zhiwei Xianzhi. Answer in Chinese. "
        "Output only the final answer, not reasoning, not a preamble, and not any debug text. "
        "Start directly with Markdown headings. Use short clean sections and tables. "
        "Do not repeat the context or the user question. Do not output raw JSON, HTML tags, code blocks, or JSON数据块. "
        "Use only the provided real data. If something is missing, say not_provided.\n\n"
        "Structured context:\n"
        + json.dumps(compact_context, ensure_ascii=False, default=str, separators=(",", ":"))
        + "\n\nUser question: "
        + message
    )


def stream_sse_events(resp):
    event_name = None
    data_lines = []
    if hasattr(resp, "iter_lines"):
        raw_iter = resp.iter_lines(decode_unicode=False)
    else:
        raw_iter = resp
    for raw in raw_iter:
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        line = raw.rstrip("\r\n")
        if not line:
            if event_name or data_lines:
                yield event_name, "\n".join(data_lines).strip()
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if event_name or data_lines:
        yield event_name, "\n".join(data_lines).strip()


def _coze_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload.get("data")
    return payload if isinstance(payload, dict) else {}


def _coze_collect_chat_ids(payload):
    found = {"chat_id": "", "conversation_id": ""}

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        if not found["conversation_id"] and value.get("conversation_id"):
            found["conversation_id"] = str(value.get("conversation_id"))
        if not found["chat_id"]:
            chat_id = value.get("chat_id")
            if not chat_id and any(key in value for key in ("status", "bot_id", "last_error")):
                chat_id = value.get("id")
            if chat_id:
                found["chat_id"] = str(chat_id)
        for item in value.values():
            if isinstance(item, (dict, list)):
                walk(item)

    walk(payload)
    return found


def _coze_get_json(path, headers, params, timeout=30):
    url = "%s%s" % (COZE_REPORT_BASE_URL, path)
    if requests is not None:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if not resp.ok:
            raise RuntimeError("Coze report bot error %s: %s" % (resp.status_code, classify_coze_error(resp.text[:500])))
        payload = resp.json()
        _coze_raise_api_error(payload)
        return payload
    req_url = "%s?%s" % (url, urllib.parse.urlencode(params))
    req = urllib.request.Request(req_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
            _coze_raise_api_error(payload)
            return payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("Coze report bot error %s: %s" % (exc.code, classify_coze_error(detail)))


def _coze_wait_chat_completed(headers, conversation_id, chat_id, timeout=30):
    if not conversation_id or not chat_id:
        return None
    deadline = time.time() + max(3, min(float(timeout or 30), 30.0))
    last_payload = None
    while time.time() < deadline:
        payload = _coze_get_json(
            "/v3/chat/retrieve",
            headers,
            {"conversation_id": conversation_id, "chat_id": chat_id},
            timeout=min(10, max(3, int(deadline - time.time()) + 1)),
        )
        last_payload = payload
        data = _coze_data(payload)
        status = str(data.get("status") or "").lower()
        if status in ("completed", "failed", "requires_action", "canceled"):
            return payload
        time.sleep(1)
    return last_payload


def _coze_fetch_answer_from_messages(headers, conversation_id, chat_id, timeout=30):
    if not conversation_id or not chat_id:
        return ""
    payload = _coze_get_json(
        "/v3/chat/message/list",
        headers,
        {"conversation_id": conversation_id, "chat_id": chat_id},
        timeout=timeout,
    )
    data = payload.get("data") if isinstance(payload, dict) else payload
    answers = []
    for message in _as_list(data):
        text = _extract_coze_answer_text(message)
        if text:
            answers.append(text)
    if answers:
        return "\n\n".join(answers).strip()
    return _extract_coze_answer_text(payload).strip()


def coze_bot_chat(prompt, bot_id, token, user_id, conversation_id=None, timeout=180, stream_first=True):
    if not token:
        raise RuntimeError("Coze token is not configured")
    headers = {
        "Authorization": "Bearer %s" % token,
        "token": token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {
        "bot_id": bot_id,
        "user_id": user_id,
        "stream": True,
        "auto_save_history": False,
        "additional_messages": [
            {
                "role": "user",
                "content_type": "text",
                "content": prompt,
            }
        ],
    }
    params = {}
    if conversation_id:
        params["conversation_id"] = conversation_id
    url = "%s/v3/chat" % COZE_REPORT_BASE_URL
    resp = None
    deadline = time.time() + max(5.0, float(timeout or 180))

    def _remaining_timeout():
        return max(1.0, deadline - time.time())

    def _request_report(stream_mode, request_timeout=None):
        effective_timeout = max(1.0, float(request_timeout or _remaining_timeout()))
        local_resp = None
        if requests is not None:
            req_body = dict(body)
            req_body["stream"] = stream_mode
            local_resp = requests.post(
                url,
                headers=headers,
                params=params,
                json=req_body,
                stream=stream_mode,
                timeout=effective_timeout,
            )
            if not local_resp.ok:
                raise RuntimeError("Coze report bot error %s: %s" % (local_resp.status_code, classify_coze_error(local_resp.text[:500])))
            return local_resp
        if params:
            req_url = "%s?%s" % (url, urllib.parse.urlencode(params))
        else:
            req_url = url
        req_body = dict(body)
        req_body["stream"] = stream_mode
        req = urllib.request.Request(
            req_url,
            data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=effective_timeout)

    answer_chunks = []
    completed_answer = ""
    chat_id = ""
    response_conversation_id = conversation_id or ""
    event_types = []
    if stream_first:
        try:
            resp = _request_report(True, _remaining_timeout())
            for event_name, data_text in stream_sse_events(resp):
                if time.time() >= deadline:
                    raise RuntimeError("Coze 请求超时")
                if not event_name and not data_text:
                    continue
                if (data_text or "").strip() in ("[DONE]", "DONE"):
                    break
                lower = (event_name or "").lower()
                if lower:
                    event_types.append(lower)
                payload = None
                if data_text:
                    try:
                        payload = json.loads(data_text)
                    except Exception:
                        payload = data_text
                ids = _coze_collect_chat_ids(payload)
                chat_id = chat_id or ids.get("chat_id") or ""
                response_conversation_id = response_conversation_id or ids.get("conversation_id") or ""
                if lower in ("conversation.message.delta", "conversation.message.completed"):
                    content_text = _extract_coze_answer_text(payload)
                    if content_text:
                        if lower.endswith(".delta"):
                            answer_chunks.append(content_text)
                        else:
                            completed_answer = content_text
                elif lower in ("conversation.chat.created", "conversation.chat.in_progress", "conversation.chat.completed"):
                    continue
                elif not lower:
                    fallback_text = _extract_coze_answer_text(payload) or "".join(_extract_payload_text(payload)).strip()
                    if fallback_text:
                        answer_chunks.append(fallback_text)
                elif lower in ("conversation.chat.failed", "error"):
                    try:
                        payload = json.loads(data_text) if data_text else {}
                    except Exception:
                        payload = {"message": data_text}
                    raise RuntimeError(classify_coze_error(payload.get("message") or payload.get("msg") or payload.get("error") or data_text))
                elif lower == "done":
                    break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError("Coze report bot error %s: %s" % (exc.code, classify_coze_error(detail)))
        finally:
            if resp is not None and hasattr(resp, "close"):
                resp.close()
    report_text = completed_answer.strip() or "".join(answer_chunks).strip()
    if not report_text and response_conversation_id and chat_id:
        try:
            retrieve_payload = _coze_wait_chat_completed(headers, response_conversation_id, chat_id, timeout=min(_remaining_timeout(), 30))
            retrieve_data = _coze_data(retrieve_payload)
            if str(retrieve_data.get("status") or "").lower() == "failed":
                last_error = retrieve_data.get("last_error") or {}
                raise RuntimeError(classify_coze_error(last_error.get("msg") or last_error.get("message") or last_error.get("error") or "Coze chat failed"))
            report_text = _coze_fetch_answer_from_messages(headers, response_conversation_id, chat_id, timeout=min(_remaining_timeout(), 30)).strip()
        except Exception:
            report_text = ""
    if not report_text and _remaining_timeout() > 5:
        resp2 = None
        try:
            resp2 = _request_report(False, _remaining_timeout())
            if requests is not None:
                payload = resp2.json()
            else:
                payload = json.loads(resp2.read().decode("utf-8", "replace"))
            _coze_raise_api_error(payload)
            ids = _coze_collect_chat_ids(payload)
            chat_id = chat_id or ids.get("chat_id") or ""
            response_conversation_id = response_conversation_id or ids.get("conversation_id") or ""
            report_text = _extract_coze_answer_text(payload).strip() or "".join(_extract_payload_text_by_keys(payload, ("report_markdown", "report", "answer", "content", "text"))).strip()
            if not report_text and response_conversation_id and chat_id:
                retrieve_payload = _coze_wait_chat_completed(headers, response_conversation_id, chat_id, timeout=min(_remaining_timeout(), 30))
                retrieve_data = _coze_data(retrieve_payload)
                if str(retrieve_data.get("status") or "").lower() == "failed":
                    last_error = retrieve_data.get("last_error") or {}
                    raise RuntimeError(classify_coze_error(last_error.get("msg") or last_error.get("message") or last_error.get("error") or "Coze chat failed"))
                report_text = _coze_fetch_answer_from_messages(headers, response_conversation_id, chat_id, timeout=min(_remaining_timeout(), 30)).strip()
        except Exception:
            report_text = ""
        finally:
            if resp2 is not None and hasattr(resp2, "close"):
                resp2.close()
    if not report_text:
        hint = ""
        if response_conversation_id or chat_id or event_types:
            hint = " (conversation_id=%s, chat_id=%s, events=%s)" % (
                response_conversation_id or "-",
                chat_id or "-",
                ",".join(sorted(set(event_types))) or "-",
            )
        raise RuntimeError("Coze report bot returned no text; check bot output node, token access, or API response format%s" % hint)
    return report_text


def coze_bot_chat_stream(prompt, bot_id, token, user_id, conversation_id=None):
    if not token:
        raise RuntimeError("Coze token is not configured")
    headers = {
        "Authorization": "Bearer %s" % token,
        "token": token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {
        "bot_id": bot_id,
        "user_id": user_id,
        "stream": True,
        "auto_save_history": False,
        "additional_messages": [
            {
                "role": "user",
                "content_type": "text",
                "content": prompt,
            }
        ],
    }
    params = {}
    if conversation_id:
        params["conversation_id"] = conversation_id
    url = "%s/v3/chat" % COZE_REPORT_BASE_URL
    resp = None

    def _request():
        if requests is not None:
            req_body = dict(body)
            return requests.post(
                url,
                headers=headers,
                params=params,
                json=req_body,
                stream=True,
                timeout=180,
            )
        if params:
            req_url = "%s?%s" % (url, urllib.parse.urlencode(params))
        else:
            req_url = url
        req = urllib.request.Request(
            req_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=180)

    try:
        resp = _request()
        if requests is not None and not resp.ok:
            raise RuntimeError("Coze chat bot error %s: %s" % (resp.status_code, classify_coze_error(resp.text[:500])))
        raw_text = ""
        completed_text = ""
        sent_text = ""
        for event_name, data_text in stream_sse_events(resp):
            if not event_name and not data_text:
                continue
            if (data_text or "").strip() in ("[DONE]", "DONE"):
                break
            lower = (event_name or "").lower()
            payload = None
            if data_text:
                try:
                    payload = json.loads(data_text)
                except Exception:
                    payload = data_text
            if lower in ("conversation.message.delta", "conversation.message.completed"):
                piece = _extract_coze_answer_text(payload)
                if piece:
                    if lower.endswith(".delta"):
                        raw_text += piece
                        clean = sanitize_coze_response_text(raw_text)
                        if clean:
                            if clean.startswith(sent_text):
                                delta = clean[len(sent_text):]
                            else:
                                delta = clean
                            if delta:
                                sent_text = clean
                                yield {"event": "delta", "text": delta, "raw_text": clean}
                    else:
                        completed_text = piece
            elif lower in ("conversation.chat.failed", "error"):
                try:
                    payload = json.loads(data_text) if data_text else {}
                except Exception:
                    payload = {"message": data_text}
                raise RuntimeError(classify_coze_error(payload.get("message") or payload.get("msg") or payload.get("error") or data_text))
            elif lower == "done":
                break
        final_text = sanitize_coze_response_text(raw_text or completed_text)
        if completed_text:
            final_clean = sanitize_coze_response_text(completed_text)
            if len(final_clean) > len(final_text):
                final_text = final_clean
        if final_text and final_text != sent_text:
            yield {"event": "replace", "text": final_text, "raw_text": final_text}
        if not final_text:
            raise RuntimeError("Coze report bot returned no text; check bot output node, token access, or API response format")
        yield {"event": "completed", "text": final_text, "raw_text": final_text}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("Coze chat bot error %s: %s" % (exc.code, classify_coze_error(detail)))
    finally:
        if resp is not None and hasattr(resp, "close"):
            resp.close()


def coze_report_chat(prompt, conversation_id=None, timeout=180, tokens=None):
    tokens = tokens or [COZE_REPORT_TOKEN, COZE_CHAT_TOKEN, COZE_ANALYZE_TOKEN, COZE_WF3_TOKEN]
    unique_tokens = []
    seen = set()
    for token in tokens:
        token = (token or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
    last_error = None
    deadline = time.time() + max(8.0, float(timeout or 180))
    for token in unique_tokens:
        remaining = deadline - time.time()
        if remaining <= 3:
            break
        try:
            return coze_bot_chat(
                prompt,
                COZE_REPORT_BOT_ID,
                token,
                COZE_REPORT_USER_ID,
                conversation_id,
                timeout=max(5.0, min(float(timeout or 180), remaining)),
                stream_first=False,
            )
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            remaining = deadline - time.time()
            if "code=4101" in error_text or remaining <= 8:
                continue
            try:
                return coze_bot_chat(
                    prompt,
                    COZE_REPORT_BOT_ID,
                    token,
                    COZE_REPORT_USER_ID,
                    conversation_id,
                    timeout=max(5.0, min(float(timeout or 180), remaining)),
                    stream_first=True,
                )
            except Exception as stream_exc:
                last_error = stream_exc
    if last_error:
        raise last_error
    raise RuntimeError("Coze report bot token is not configured")


def coze_assistant_chat(prompt, conversation_id=None, timeout=180):
    return coze_bot_chat(prompt, COZE_CHAT_BOT_ID, COZE_CHAT_TOKEN, COZE_CHAT_USER_ID, conversation_id, timeout=timeout)


RISK_BASELINES = {}


def compute_risk_baselines(devices):
    return {
        "temperature": {
            "p75": percentile([d.get("temperature") for d in devices], 75),
            "p90": percentile([d.get("temperature") for d in devices], 90),
            "p95": percentile([d.get("temperature") for d in devices], 95),
        },
        "amperage": {
            "p75": percentile([d.get("amperage") for d in devices], 75),
            "p90": percentile([d.get("amperage") for d in devices], 90),
            "p95": percentile([d.get("amperage") for d in devices], 95),
        },
        "voltage": {
            "p05": percentile([d.get("voltage") for d in devices], 5),
            "p95": percentile([d.get("voltage") for d in devices], 95),
        },
        "rotor_speed": {
            "p05": percentile([d.get("rotor_speed") for d in devices], 5),
            "p95": percentile([d.get("rotor_speed") for d in devices], 95),
        },
    }


def get_risk_baselines():
    global RISK_BASELINES
    if not RISK_BASELINES:
        try:
            RISK_BASELINES = compute_risk_baselines(rows("SELECT * FROM devices ORDER BY id"))
        except Exception:
            RISK_BASELINES = {}
    return RISK_BASELINES


def risk_breakdown(device):
    base = get_risk_baselines()
    score = 0
    factors = []

    failure_type = str(device.get("failure_type") or "0").strip()
    if failure_type not in ("", "0", "None", "none"):
        score += 60
        factors.append("原始数据 failure_type=%s，说明该设备已有故障标记" % failure_type)

    temp = parse_float(device.get("temperature"))
    amp = parse_float(device.get("amperage"))
    volt = parse_float(device.get("voltage"))
    speed = parse_float(device.get("rotor_speed"))

    temp_base = base.get("temperature", {})
    if temp_base and temp >= temp_base.get("p95", 9999):
        score += 18
        factors.append("温度 %.2f >= 全设备 P95 %.2f" % (temp, temp_base.get("p95", 0)))
    elif temp_base and temp >= temp_base.get("p90", 9999):
        score += 12
        factors.append("温度 %.2f >= 全设备 P90 %.2f" % (temp, temp_base.get("p90", 0)))
    elif temp_base and temp >= temp_base.get("p75", 9999):
        score += 6
        factors.append("温度 %.2f >= 全设备 P75 %.2f" % (temp, temp_base.get("p75", 0)))

    amp_base = base.get("amperage", {})
    if amp_base and amp >= amp_base.get("p95", 9999):
        score += 14
        factors.append("电流 %.2f >= 全设备 P95 %.2f" % (amp, amp_base.get("p95", 0)))
    elif amp_base and amp >= amp_base.get("p90", 9999):
        score += 9
        factors.append("电流 %.2f >= 全设备 P90 %.2f" % (amp, amp_base.get("p90", 0)))
    elif amp_base and amp >= amp_base.get("p75", 9999):
        score += 5
        factors.append("电流 %.2f >= 全设备 P75 %.2f" % (amp, amp_base.get("p75", 0)))

    volt_base = base.get("voltage", {})
    if volt_base and (volt <= volt_base.get("p05", -9999) or volt >= volt_base.get("p95", 9999)):
        score += 6
        factors.append("电压 %.2f 超出全设备 P05-P95 区间 %.2f-%.2f" % (volt, volt_base.get("p05", 0), volt_base.get("p95", 0)))

    speed_base = base.get("rotor_speed", {})
    if speed_base and (speed <= speed_base.get("p05", -9999) or speed >= speed_base.get("p95", 9999)):
        score += 5
        factors.append("转速 %.2f 超出全设备 P05-P95 区间 %.2f-%.2f" % (speed, speed_base.get("p05", 0), speed_base.get("p95", 0)))

    score = max(0, min(100, int(round(score))))
    if not factors:
        factors.append("最新温度、电流、电压、转速未触发全设备分位数异常规则")
    return {
        "score": score,
        "level": risk_level_from_score(score),
        "factors": factors,
        "method": "真实CSV最新状态 + 全设备分位数基线",
        "baseline": base,
    }


def calc_risk(device):
    return risk_breakdown(device)["score"]


def status_of(device):
    if str(device.get("failure_type") or "0").strip() not in ("", "0", "None", "none"):
        return "crit"
    return "warn" if calc_risk(device) >= 35 else "ok"


def load_sources():
    summary_rows = read_csv(SUMMARY_CSV)
    log_rows = read_csv(LOG_CSV)
    line_rows = read_csv(LINE_CSV)
    test_rows = read_csv(TEST_CSV)

    line_counter = defaultdict(Counter)
    for row in line_rows:
        machine = (row.get("machine") or "").strip()
        line = (row.get("line") or "").strip()
        if machine and line:
            line_counter[machine][line] += 1

    latest_log = {}
    logs = []
    for row in log_rows:
        equipment_id = (row.get("equipment_id") or "").strip()
        if not equipment_id:
            continue
        item = {
            "timestamp": normalize_timestamp(row.get("timestamp")),
            "equipment_id": equipment_id,
            "failure_type": str(row.get("failure_type", "0")).strip(),
            "current": parse_float(row.get("current")),
            "temperature": parse_float(row.get("temperature")),
            "voltage": parse_float(row.get("voltage")),
            "speed": parse_float(row.get("speed")),
        }
        logs.append(item)
        if equipment_id not in latest_log or item["timestamp"] > latest_log[equipment_id]["timestamp"]:
            latest_log[equipment_id] = item

    tests = []
    for row in test_rows:
        tests.append({
            "date": normalize_date(row.get("DATE")),
            "line": (row.get("LINE") or "").strip(),
            "machine": (row.get("MACHINE") or "").strip(),
            "serial_no": (row.get("SERIAL NO") or "").strip(),
            "parent_circuit_no": (row.get("PARENT_CIRCUIT_NO") or "").strip(),
            "parent_case_no": (row.get("PARENT_CASE_NO") or "").strip(),
            "parameter": (row.get("PARAMETER") or "").strip(),
            "measmt_value": parse_float(row.get("MEASMT_VALUE")),
            "lwr_spec_limit": parse_float(row.get("LWR_SPEC_LIMIT")),
            "upr_spec_limit": parse_float(row.get("UPR_SPEC_LIMIT")),
            "wrkstn_nm": (row.get("WRKSTN_NM") or "").strip(),
            "unit_of_meas": (row.get("UNIT_OF_MEAS") or "").strip(),
        })

    test_counter = Counter(t["machine"] for t in tests if t["machine"])
    devices = []
    for row in summary_rows:
        device_id = (row.get("Equipment.Id") or "").strip()
        if not device_id:
            continue
        log = latest_log.get(device_id, {})
        line = line_counter[device_id].most_common(1)[0][0] if line_counter.get(device_id) else ""
        devices.append({
            "id": device_id,
            "type": (row.get("Equipment.Type") or "").strip(),
            "manufacture_month": parse_int(row.get("Month.of.Manufacture")),
            "last_service": normalize_date(row.get("Last Service Date")),
            "next_service": normalize_date(row.get("Next Service Date")),
            "last_repair": normalize_date(row.get("Last Repair Date")),
            "units_per_day": parse_int(row.get("Units Produced Per day")),
            "unit_cost": parse_float(row.get("Unit Cost of Production")),
            "line": line,
            "failure_type": log.get("failure_type", "0"),
            "temperature": parse_float(log.get("temperature")),
            "amperage": parse_float(log.get("current")),
            "voltage": parse_float(log.get("voltage")),
            "rotor_speed": parse_float(log.get("speed")),
            "latest_log_time": log.get("timestamp", ""),
            "test_count": test_counter.get(device_id, 0),
        })
    return devices, logs, tests


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    devices, logs, tests = load_sources()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS devices;
        DROP TABLE IF EXISTS device_logs;
        DROP TABLE IF EXISTS machine_tests;

        CREATE TABLE devices (
            id TEXT PRIMARY KEY,
            type TEXT,
            manufacture_month INTEGER,
            last_service TEXT,
            next_service TEXT,
            last_repair TEXT,
            units_per_day INTEGER,
            unit_cost REAL,
            line TEXT,
            failure_type TEXT,
            temperature REAL,
            amperage REAL,
            voltage REAL,
            rotor_speed REAL,
            latest_log_time TEXT,
            test_count INTEGER
        );

        CREATE TABLE device_logs (
            timestamp TEXT,
            equipment_id TEXT,
            failure_type TEXT,
            current REAL,
            temperature REAL,
            voltage REAL,
            speed REAL
        );

        CREATE TABLE machine_tests (
            date TEXT,
            line TEXT,
            machine TEXT,
            serial_no TEXT,
            parent_circuit_no TEXT,
            parent_case_no TEXT,
            parameter TEXT,
            measmt_value REAL,
            lwr_spec_limit REAL,
            upr_spec_limit REAL,
            wrkstn_nm TEXT,
            unit_of_meas TEXT
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            risk_level TEXT,
            health_score REAL,
            predicted_rul_hours REAL,
            fault_name TEXT,
            confidence REAL,
            source TEXT,
            report_text TEXT,
            analysis_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            prediction_id INTEGER,
            title TEXT,
            priority TEXT,
            failure_type TEXT,
            estimated_cost REAL,
            steps TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS workflow_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            query_type TEXT,
            analysis_json TEXT,
            workorder_json TEXT,
            charts_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS fault_cases (
            id TEXT PRIMARY KEY,
            title TEXT,
            equipment_id TEXT,
            fault_type TEXT,
            case_date TEXT,
            summary TEXT,
            content TEXT,
            source TEXT,
            file_name TEXT,
            workflow_id TEXT,
            sync_status TEXT,
            sync_json TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        );
    """)
    cur.executemany("""
        INSERT INTO devices VALUES
        (:id,:type,:manufacture_month,:last_service,:next_service,:last_repair,:units_per_day,:unit_cost,:line,:failure_type,:temperature,:amperage,:voltage,:rotor_speed,:latest_log_time,:test_count)
    """, devices)
    cur.executemany("""
        INSERT INTO device_logs VALUES
        (:timestamp,:equipment_id,:failure_type,:current,:temperature,:voltage,:speed)
    """, logs)
    cur.executemany("""
        INSERT INTO machine_tests VALUES
        (:date,:line,:machine,:serial_no,:parent_circuit_no,:parent_case_no,:parameter,:measmt_value,:lwr_spec_limit,:upr_spec_limit,:wrkstn_nm,:unit_of_meas)
    """, tests)
    conn.commit()
    conn.close()
    ensure_case_seeded_from_txt()
    global RISK_BASELINES
    RISK_BASELINES = compute_risk_baselines(devices)
    print("[DB] loaded %s devices, %s logs, %s tests" % (len(devices), len(logs), len(tests)))


def rows(sql, args=()):
    conn = get_db()
    data = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return data


def row(sql, args=()):
    conn = get_db()
    data = conn.execute(sql, args).fetchone()
    conn.close()
    return dict(data) if data else None


def load_device(device_id):
    item = row("SELECT * FROM devices WHERE id=?", (device_id,))
    if not item:
        return None
    risk = risk_breakdown(item)
    item["risk_score"] = risk["score"]
    item["risk_level"] = risk["level"]
    item["risk_factors"] = risk["factors"]
    item["risk_method"] = risk["method"]
    item["status"] = status_of(item)
    return item


def load_logs(equipment_id=None, limit=50):
    if equipment_id:
        return rows(
            "SELECT * FROM device_logs WHERE equipment_id=? ORDER BY timestamp DESC LIMIT ?",
            (equipment_id, limit),
        )
    return rows("SELECT * FROM device_logs ORDER BY timestamp DESC LIMIT ?", (limit,))


def load_tests(machine=None, limit=50):
    if machine:
        return rows(
            "SELECT * FROM machine_tests WHERE machine=? ORDER BY date DESC LIMIT ?",
            (machine, limit),
        )
    return rows("SELECT * FROM machine_tests ORDER BY date DESC LIMIT ?", (limit,))


def flatten_workflow_payload(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    value = json.loads(text)
                except Exception:
                    pass
        result[key] = value
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    analysis_result = result.get("analysis_result") if isinstance(result.get("analysis_result"), dict) else {}
    dual = result.get("dual_model_result") if isinstance(result.get("dual_model_result"), dict) else {}
    work_order = result.get("work_order") if isinstance(result.get("work_order"), dict) else {}
    cost_analysis = result.get("cost_analysis") if isinstance(result.get("cost_analysis"), dict) else {}
    visualization_data = result.get("visualization_data") if isinstance(result.get("visualization_data"), dict) else {}
    merged = {}
    for obj in (content, nested, analysis_result, dual, cost_analysis, work_order, visualization_data):
        merged.update(obj)
    if isinstance(content.get("analysis_result"), list):
        for item in content.get("analysis_result"):
            if not isinstance(item, str):
                continue
            try:
                parsed = json.loads(item)
            except Exception:
                continue
            if isinstance(parsed, dict):
                merged.update(flatten_workflow_payload(parsed))
    if isinstance(content.get("result"), dict):
        merged.update(flatten_workflow_payload(content.get("result")))
    if isinstance(content.get("content"), str) and content.get("content").strip().startswith("{"):
        try:
            merged.update(flatten_workflow_payload(json.loads(content.get("content"))))
        except Exception:
            pass
    merged.update(result)
    return merged


def normalize_analysis(data):
    analysis = dict(data or {})
    content = analysis.get("content") if isinstance(analysis.get("content"), dict) else {}
    result = analysis.get("result") if isinstance(analysis.get("result"), dict) else {}
    result_obj = _ensure_mapping(analysis.get("result") or {})
    analysis_obj = _ensure_mapping(analysis.get("analysis_result") or {})
    merged = {}
    for obj in (
        content,
        result,
        analysis_obj if isinstance(analysis_obj, dict) else {},
        analysis.get("dual_model_result") if isinstance(analysis.get("dual_model_result"), dict) else {},
    ):
        merged.update(obj)
    if merged:
        analysis.update(merged)

    result_risk = result.get("risk_level") if isinstance(result, dict) else None
    if analysis.get("risk_level") is not None:
        analysis["risk_level"] = normalize_risk_level(analysis.get("risk_level"), analysis.get("risk_score") or analysis.get("fused_score"))
    elif result_risk is not None:
        analysis["risk_level"] = normalize_risk_level(result_risk, analysis.get("risk_score") or analysis.get("fused_score"))
    else:
        analysis["risk_level"] = normalize_risk_level(None, analysis.get("risk_score") or analysis.get("fused_score"))

    score = analysis.get("risk_score")
    if score is None:
        score = analysis.get("fused_score")
    if score is None and isinstance(result, dict):
        score = result.get("risk_score") or result.get("fused_score")
    if score is None and isinstance(content, dict):
        score = content.get("risk_score") or content.get("fused_score")
    analysis["risk_score"] = parse_float(score, 0)
    analysis["health_score"] = parse_float(analysis.get("health_score", analysis.get("device_health_score", 100)), 100)
    analysis["predicted_rul_hours"] = parse_float(analysis.get("predicted_rul_hours", analysis.get("rul_hours", 720)), 720)
    analysis["failure_probability"] = parse_float(analysis.get("failure_probability", analysis.get("risk_probability", analysis["risk_score"] / 100.0)), analysis["risk_score"] / 100.0)
    analysis["risk_probability"] = parse_float(analysis.get("risk_probability", analysis["failure_probability"]), analysis["failure_probability"])

    if not analysis.get("fault_name"):
        analysis["fault_name"] = (
            content.get("fault_name")
            or analysis.get("predicted_fault_name")
            or analysis.get("failure_type")
            or analysis.get("fault_type")
            or "未发现明确故障"
        )

    if result_obj:
        analysis["analysis_result"] = result_obj
    elif analysis_obj:
        analysis["analysis_result"] = analysis_obj
    elif not analysis.get("analysis_result"):
        analysis["analysis_result"] = {
            "risk_level": analysis.get("risk_level"),
            "risk_score": analysis.get("risk_score"),
            "device_health_score": analysis.get("health_score"),
        }

    if result_obj:
        analysis["result"] = result_obj
    elif not analysis.get("result"):
        analysis["result"] = analysis["analysis_result"]
    if not analysis.get("trend_result"):
        analysis["trend_result"] = analysis.get("future_trend") or {}
    if not analysis.get("future_trend"):
        analysis["future_trend"] = analysis.get("trend_result") or {}
    if not analysis.get("window_analysis"):
        analysis["window_analysis"] = {
            "source": analysis.get("source", "local_csv_baseline"),
            "sample_count": len(_as_list(analysis.get("visualization_data"))),
        }
    if not analysis.get("visualization_data"):
        analysis["visualization_data"] = analysis.get("merged_data") or analysis.get("raw_data", {}).get("merged_data", [])
    if not analysis.get("heatmap_data"):
        analysis["heatmap_data"] = []
    if not analysis.get("anomaly_events"):
        analysis["anomaly_events"] = []
    if not analysis.get("pre_failure_time"):
        analysis["pre_failure_time"] = analysis.get("predicted_pre_failure_time") or analysis.get("pre_failure_time")
    if result_obj:
        analysis["analysis_result"] = result_obj
        analysis["result"] = result_obj
    else:
        analysis["analysis_result"] = _ensure_mapping(analysis.get("analysis_result") or analysis.get("result") or {})
        analysis["result"] = analysis["analysis_result"]
    analysis["trend_result"] = _ensure_mapping(analysis.get("trend_result") or analysis.get("future_trend") or {})
    analysis["future_trend"] = _ensure_mapping(analysis.get("future_trend") or analysis.get("trend_result") or {})
    analysis["window_analysis"] = _ensure_mapping(analysis.get("window_analysis") or {})
    if not analysis.get("source"):
        analysis["source"] = "coze_workflow" if analysis.get("report_text") else "local_csv_baseline"
    analysis["risk_level_cn"] = _risk_label_cn(analysis["risk_level"])
    return analysis


def build_raw_data(device_id):
    device = load_device(device_id)
    if not device:
        return None
    logs = list(reversed(load_logs(device_id, 120)))
    tests = load_tests(device_id, 80)
    merged_data = []
    for item in logs:
        merged_data.append({
            "equipment_id": item["equipment_id"],
            "timestamp": item["timestamp"],
            "failure_type": item["failure_type"],
            "current": item["current"],
            "temperature": item["temperature"],
            "voltage": item["voltage"],
            "speed": item["speed"],
            "power": round(parse_float(item["current"]) * parse_float(item["voltage"]), 4),
        })
    return {"device": device, "logs": logs, "tests": tests, "merged_data": merged_data}


def local_analysis(device_id):
    payload = build_raw_data(device_id)
    if not payload:
        return None
    device = payload["device"]
    risk = risk_breakdown(device)
    score = risk["score"]
    health = max(0, 100 - score)
    level = risk["level"]
    temp = parse_float(device.get("temperature"))
    amp = parse_float(device.get("amperage"))
    failure_type = device.get("failure_type") or "0"
    if failure_type != "0":
        fault = failure_type
    elif "温度" in " ".join(risk["factors"]):
        fault = "温度偏高风险"
    elif "电流" in " ".join(risk["factors"]):
        fault = "电流偏高风险"
    else:
        fault = "未发现明确故障"
    rul = max(24, int(900 - score * 8))
    confidence = min(0.96, 0.55 + score / 200.0)
    reasons = risk["factors"]
    anomaly_events = []
    for item in payload["merged_data"]:
        if parse_float(item.get("temperature")) >= get_risk_baselines().get("temperature", {}).get("p90", 9999):
            anomaly_events.append({
                "equipment_id": device_id,
                "timestamp": item.get("timestamp"),
                "reason": ["temperature_high"],
                "temperature": item.get("temperature"),
                "current": item.get("current"),
            })
    heatmap_data = [
        {"x": "temperature", "y": "current", "value": round(temp * amp, 4)},
        {"x": "voltage", "y": "current", "value": round(parse_float(device.get("voltage")) * amp, 4)},
    ]
    result = {
        "device_health_score": health,
        "risk_score": score,
        "risk_level": level,
        "suggestions": reasons,
        "data_source": "local_csv_baseline",
    }
    return {
        "device_id": device_id,
        "risk_level": level,
        "health_score": health,
        "predicted_rul_hours": rul,
        "fault_name": fault,
        "confidence": round(confidence, 3),
        "source": "local_csv_baseline",
        "risk_score": score,
        "risk_method": risk["method"],
        "reasons": reasons,
        "analysis_result": result,
        "result": result,
        "anomaly_events": anomaly_events,
        "heatmap_data": heatmap_data,
        "trend_result": {"temperature_trend": "unknown", "current_trend": "unknown"},
        "future_trend": {"risk": level},
        "future_risk_level": level,
        "failure_probability": round(score / 100.0, 4),
        "risk_probability": round(score / 100.0, 4),
        "window_analysis": {"source": "local_csv_baseline", "sample_count": len(payload["merged_data"])},
        "visualization_data": payload["merged_data"][-60:],
        "pre_failure_time": None if level in ("low", "medium") else "需由 WF_analyze 确认",
        "raw_data": payload,
        "report_text": (
            "设备 %s 的本地看板风险为 %s，健康评分 %s/100，估算 RUL %sh。"
            "该结果只基于真实 CSV 最新状态和全设备分位数基线，用于页面预筛；正式预测以 WF_analyze 返回为准。"
            "依据：%s。"
        ) % (device_id, level, health, rul, "；".join(reasons)),
    }


def parse_workflow_text(text, fallback):
    analysis = dict(fallback)
    analysis.pop("raw_data", None)
    for data in _extract_json_blocks(text):
        if isinstance(data, dict):
            analysis.update(flatten_workflow_payload(data))
        elif isinstance(data, list) and data:
            for item in data:
                if isinstance(item, str):
                    try:
                        item = json.loads(item)
                    except Exception:
                        continue
                if isinstance(item, dict):
                    analysis.update(flatten_workflow_payload(item))
    analysis["report_text"] = text or fallback.get("report_text", "")
    analysis["source"] = "coze_wf_analyze" if text else fallback.get("source", "local_csv_baseline")
    return normalize_analysis(analysis)


def save_prediction(analysis):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO predictions
        (device_id, risk_level, health_score, predicted_rul_hours, fault_name, confidence, source, report_text, analysis_json)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        analysis.get("device_id"),
        analysis.get("risk_level", "low"),
        analysis.get("health_score", 100),
        analysis.get("predicted_rul_hours", 720),
        analysis.get("fault_name", "Normal"),
        analysis.get("confidence", 0),
        analysis.get("source", "local_estimate"),
        (analysis.get("report_text") or "")[:8000],
        json.dumps(analysis, ensure_ascii=False, default=str),
    ))
    pred_id = cur.lastrowid
    conn.commit()
    conn.close()
    analysis["prediction_id"] = pred_id
    return analysis


def save_workflow_snapshot(device_id, query_type, analysis=None, workorder=None, charts=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO workflow_snapshots
        (device_id, query_type, analysis_json, workorder_json, charts_json)
        VALUES (?,?,?,?,?)
    """, (
        device_id,
        query_type or "fault_analysis",
        json.dumps(analysis or {}, ensure_ascii=False, default=str),
        json.dumps(workorder or {}, ensure_ascii=False, default=str),
        json.dumps(charts or {}, ensure_ascii=False, default=str),
    ))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def load_latest_workflow_snapshot(device_id):
    if not device_id:
        return None
    snap = _table_latest("SELECT * FROM workflow_snapshots WHERE device_id=?", (device_id,))
    if not snap:
        return None
    return {
        "id": snap.get("id"),
        "device_id": snap.get("device_id"),
        "query_type": snap.get("query_type"),
        "created_at": snap.get("created_at"),
        "analysis": _safe_json_loads(snap.get("analysis_json"), {}),
        "workorder": _safe_json_loads(snap.get("workorder_json"), {}),
        "charts": _safe_json_loads(snap.get("charts_json"), {}),
    }


def sse(obj):
    return "data: %s\n\n" % json.dumps(obj, ensure_ascii=False, default=str)


def coze_workflow_stream(workflow_key, parameters):
    wf = WORKFLOWS[workflow_key]
    headers = {
        "Authorization": "Bearer %s" % wf["token"],
        "token": wf["token"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"workflow_id": wf["workflow_id"], "parameters": parameters}
    url = "%s/v1/workflow/stream_run" % COZE_BASE_URL
    resp = None
    try:
        if requests is not None:
            resp = requests.post(
                url,
                headers=headers,
                json=body,
                stream=True,
                timeout=180,
            )
            if not resp.ok:
                raise RuntimeError("Coze workflow error %s: %s" % (resp.status_code, classify_coze_error(resp.text[:500])))
            iterator = resp.iter_lines(decode_unicode=True)
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=180)
            iterator = resp
        for chunk in iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            chunk = chunk.rstrip("\r\n")
            if chunk:
                yield chunk
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("Coze workflow error %s: %s" % (exc.code, classify_coze_error(detail)))
    finally:
        if resp is not None and hasattr(resp, "close"):
            resp.close()


def coze_workflow_events(workflow_key, parameters):
    wf = WORKFLOWS[workflow_key]
    headers = {
        "Authorization": "Bearer %s" % wf["token"],
        "token": wf["token"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"workflow_id": wf["workflow_id"], "parameters": parameters}
    url = "%s/v1/workflow/stream_run" % COZE_BASE_URL
    resp = None
    try:
        if requests is not None:
            resp = requests.post(
                url,
                headers=headers,
                json=body,
                stream=True,
                timeout=180,
            )
            if not resp.ok:
                raise RuntimeError("Coze workflow error %s: %s" % (resp.status_code, classify_coze_error(resp.text[:500])))
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=180)
        for event_name, data_text in stream_sse_events(resp):
            yield event_name, data_text
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("Coze workflow error %s: %s" % (exc.code, classify_coze_error(detail)))
    finally:
        if resp is not None and hasattr(resp, "close"):
            resp.close()


def parse_workflow_event_data(data_text):
    if not data_text:
        return None, ""
    if (data_text or "").strip() in ("[DONE]", "DONE"):
        return None, ""
    payload = data_text
    if isinstance(data_text, str):
        try:
            payload = json.loads(data_text)
        except Exception:
            blocks = _extract_json_blocks(data_text)
            if blocks:
                payload = blocks[-1]
    if isinstance(payload, dict):
        payload = flatten_workflow_payload(payload)
    elif isinstance(payload, list):
        merged = {}
        for item in payload:
            if isinstance(item, dict):
                merged.update(flatten_workflow_payload(item))
        if merged:
            payload = merged
    if isinstance(payload, dict):
        summary = []
        for key in ("message", "report_text", "summary", "status", "note"):
            value = payload.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text and not text.startswith("{") and not text.startswith("["):
                    summary.append(text)
        content = payload.get("content")
        if isinstance(content, str):
            text = content.strip()
            if text and not text.startswith("{") and not text.startswith("["):
                summary.append(text)
        text = normalize_coze_markdown("\n".join(summary).strip())
        return payload, text
    if isinstance(payload, str):
        return None, normalize_coze_markdown(payload.strip())
    return None, ""


def build_workflow_params(device_id, query_type="fault_analysis"):
    payload = build_raw_data(device_id)
    if not payload:
        return None
    device = payload["device"]
    return {
        "equipment_id": device_id,
        "equipment_type": device.get("type", ""),
        "input_mode": "database",
        "query_type": query_type,
        "raw_data": {
            "device": device,
            "logs": payload["logs"][-60:],
            "tests": payload["tests"][-40:],
            "merged_data": payload["merged_data"][-120:],
            "risk_snapshot": {
                "risk_score": device.get("risk_score"),
                "risk_level": device.get("risk_level"),
                "risk_factors": device.get("risk_factors", []),
                "risk_method": device.get("risk_method"),
            },
        },
        "merged_data": payload["merged_data"],
    }


def build_workorder_inputs(analysis):
    analysis_result = analysis.get("result") if isinstance(analysis.get("result"), dict) else analysis.get("analysis_result")
    return {
        "analysis_result": _ensure_mapping(analysis_result or analysis),
        "equipment_id": analysis.get("device_id"),
        "knowledge": _knowledge_items(analysis.get("knowledge") or analysis.get("reasons") or analysis.get("suggestions") or []),
        "anomaly_events": analysis.get("anomaly_events") or [],
        "heatmap_data": analysis.get("heatmap_data") or [],
    }


def merge_analysis_context(device_id, analysis=None):
    merged = {}
    if device_id:
        base = local_analysis(device_id) or {}
        merged.update(base)
    if isinstance(analysis, dict):
        merged.update(analysis)
    if device_id and not merged.get("device_id"):
        merged["device_id"] = device_id
    if device_id:
        base = local_analysis(device_id) or {}
        for key in (
            "analysis_result",
            "result",
            "anomaly_events",
            "heatmap_data",
            "trend_result",
            "future_trend",
            "future_risk_level",
            "predicted_rul_hours",
            "failure_probability",
            "risk_probability",
            "window_analysis",
            "visualization_data",
            "pre_failure_time",
            "risk_level",
            "health_score",
            "fault_name",
            "confidence",
            "source",
            "risk_score",
            "risk_method",
            "reasons",
        ):
            current = merged.get(key)
            if current in (None, "", [], {}):
                if key in base:
                    merged[key] = base.get(key)
    return merged


def build_chart_inputs(analysis):
    analysis_result = analysis.get("result") if isinstance(analysis.get("result"), dict) else analysis.get("analysis_result")
    visualization_data = analysis.get("visualization_data") or []
    if isinstance(visualization_data, list):
        visualization_data = {
            "equipment_id": analysis.get("device_id"),
            "series": visualization_data[-60:],
            "latest_points": visualization_data[-20:],
            "summary": {
                "count": len(visualization_data),
                "source": analysis.get("source") or analysis.get("data_source") or "unknown",
            },
        }
    elif not isinstance(visualization_data, dict):
        visualization_data = {
            "equipment_id": analysis.get("device_id"),
            "value": visualization_data,
            "summary": {"source": analysis.get("source") or analysis.get("data_source") or "unknown"},
        }
    return {
        "equipment_id": analysis.get("device_id"),
        "trend_result": _ensure_mapping(analysis.get("trend_result") or analysis.get("future_trend") or {}),
        "heatmap_data": analysis.get("heatmap_data") or [],
        "anomaly_events": analysis.get("anomaly_events") or [],
        "anomaly_result": _ensure_mapping(analysis_result or {}),
        "future_trend": _ensure_mapping(analysis.get("future_trend") or {}),
        "future_risk_level": analysis.get("future_risk_level") or analysis.get("risk_level") or "low",
        "predicted_rul_hours": parse_float(analysis.get("predicted_rul_hours"), 720),
        "failure_probability": parse_float(analysis.get("failure_probability"), 0.05),
        "window_analysis": _ensure_mapping(analysis.get("window_analysis") or {}),
        "visualization_data": visualization_data,
        "pre_failure_time": analysis.get("pre_failure_time") or "暂无",
        "risk_probability": parse_float(analysis.get("risk_probability", analysis.get("failure_probability")), 0.05),
        "result": _ensure_mapping(analysis.get("result") or analysis_result or {}),
    }


def collect_workflow_bundle(device_id, query_type="fault_analysis", include_charts=False):
    fallback = local_analysis(device_id) or {}
    analyze_payload = build_workflow_params(device_id, query_type)
    if not analyze_payload:
        return None
    analysis = dict(fallback)
    analysis_messages = []
    analysis_error = None
    try:
        for event_name, data_text in coze_workflow_events("analyze", analyze_payload):
            parsed, message = parse_workflow_event_data(data_text)
            if isinstance(parsed, dict):
                analysis.update(parsed)
            if message:
                analysis_messages.append(message)
            if (event_name or "").lower() == "done":
                break
        analysis["report_text"] = "\n".join(analysis_messages).strip() or fallback.get("report_text", "")
        analysis["workflow_source"] = "coze_wf_analyze"
        analysis["source"] = "coze_wf_analyze"
        analysis = normalize_analysis(analysis)
    except Exception as exc:
        analysis_error = str(exc)
        analysis = dict(fallback)
        analysis["workflow_error"] = analysis_error
        analysis = normalize_analysis(analysis)

    workorder_payload = {}
    workorder_error = None
    try:
        wf_inputs = build_workorder_inputs(analysis)
        for event_name, data_text in coze_workflow_events("workorder", wf_inputs):
            parsed, message = parse_workflow_event_data(data_text)
            if isinstance(parsed, dict):
                workorder_payload.update(parsed)
            if (event_name or "").lower() == "done":
                break
    except Exception as exc:
        workorder_error = str(exc)
    workorder_result = parse_workorder_text(json.dumps(workorder_payload, ensure_ascii=False, default=str), analysis)
    if workorder_error:
        workorder_result["workflow_error"] = workorder_error

    charts_result = {}
    charts_error = None
    if include_charts:
        try:
            wf_inputs = build_chart_inputs(analysis)
            charts_payload = {}
            for event_name, data_text in coze_workflow_events("charts", wf_inputs):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    charts_payload.update(parsed)
                if (event_name or "").lower() == "done":
                    break
            charts_result = parse_charts_text(json.dumps(charts_payload, ensure_ascii=False, default=str))
        except Exception as exc:
            charts_error = str(exc)
            charts_result = parse_charts_text("")
            charts_result["workflow_error"] = charts_error

    bundle = {
        "device_id": device_id,
        "query_type": query_type,
        "analysis": analysis,
        "workorder": workorder_result,
        "charts": charts_result,
    }
    if analysis_error:
        bundle["analysis_error"] = analysis_error
    if workorder_error:
        bundle["workorder_error"] = workorder_error
    if charts_error:
        bundle["charts_error"] = charts_error
    return bundle


def refresh_workflow_snapshot(device_id, include_charts=False, query_type="fault_analysis"):
    return collect_workflow_bundle(device_id, query_type=query_type, include_charts=include_charts)



def parse_workorder_text(text, fallback_analysis):
    parsed = {}
    for data in _extract_json_blocks(text):
        parsed.update(flatten_workflow_payload(data))
    work_order = parsed.get("work_order") if isinstance(parsed.get("work_order"), dict) else parsed
    cost = parsed.get("cost_analysis") if isinstance(parsed.get("cost_analysis"), dict) else {}
    if not work_order:
        level = normalize_risk_level(fallback_analysis.get("risk_level"), fallback_analysis.get("risk_score"))
        priority = "高" if level in ("high", "critical") else "中" if level == "medium" else "低"
        work_order = {
            "equipment_id": fallback_analysis.get("device_id"),
            "failure_type": fallback_analysis.get("fault_name", "未发现明确故障"),
            "priority": priority,
            "priority_label": {"高": "P1-紧急", "中": "P2-计划", "低": "P3-常规"}.get(priority, "P2-计划"),
            "estimated_cost": 0,
            "steps": ["复核 WF_analyze 输出", "核查设备最新运行日志", "安排人工确认维护动作"],
            "description": "WF3 未返回结构化工单，已按分析结果生成待复核工单。",
        }
    work_order["source"] = "coze_wf3" if text else "local_csv_baseline"
    if cost:
        work_order["cost_analysis"] = cost
    return work_order


def save_workorder_payload(work_order, prediction_id=None):
    device_id = work_order.get("equipment_id") or work_order.get("device_id")
    priority = work_order.get("priority_label") or work_order.get("priority") or "medium"
    title = work_order.get("title") or work_order.get("description") or "%s 维护工单" % device_id
    failure_type = work_order.get("failure_type") or work_order.get("fault_name") or "待复核"
    steps = work_order.get("steps") or []
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO work_orders (device_id, prediction_id, title, priority, failure_type, estimated_cost, steps)
        VALUES (?,?,?,?,?,?,?)
    """, (
        device_id,
        prediction_id,
        title,
        priority,
        failure_type,
        parse_float(work_order.get("estimated_cost"), 0),
        json.dumps({
            "source": work_order.get("source", "coze_wf3"),
            "steps": steps,
            "raw": work_order,
        }, ensure_ascii=False, default=str),
    ))
    wid = cur.lastrowid
    conn.commit()
    conn.close()
    work_order["id"] = wid
    return work_order


def _looks_like_chart_url(url):
    value = str(url or "").strip().rstrip(").,，。]")
    if not re.match(r"^https?://", value, re.I):
        return False
    return bool(
        re.search(r"(s\.coze\.cn|work_flow\?|image|img|cdn|lf\d?-static|byteimg|coze)", value, re.I)
        or re.search(r"\.(png|jpe?g|gif|webp|svg)(\?|#|$)", value, re.I)
    )


def _collect_chart_urls(value, key_hint="url", urls=None, seen=None):
    if urls is None:
        urls = []
    if seen is None:
        seen = set()
    if value in (None, "", [], {}):
        return urls
    if isinstance(value, str):
        text = value.strip()
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                return _collect_chart_urls(json.loads(text), key_hint, urls, seen)
            except Exception:
                pass
        candidates = re.findall(r"https?://[^\s<>)\"']+", text)
        if text.startswith("http") and re.fullmatch(r"https?://[^\s<>)\"']+", text):
            candidates.insert(0, text)
        for url in candidates:
            clean = str(url).strip().rstrip(").,，。]")
            if clean not in seen and _looks_like_chart_url(clean):
                seen.add(clean)
                urls.append({"key": key_hint or "url", "url": clean})
        return urls
    if isinstance(value, dict):
        for key in (
            "trend_image", "perdicted_image", "predicted_image", "hot_image",
            "wrong_image", "risk_image", "reason_image",
        ):
            if key in value:
                _collect_chart_urls(value.get(key), key, urls, seen)
        for key, item in value.items():
            if key in (
                "trend_image", "perdicted_image", "predicted_image", "hot_image",
                "wrong_image", "risk_image", "reason_image",
            ):
                continue
            hint = key if re.search(r"(image|img|url|data|output|content|result)", str(key), re.I) else key_hint
            _collect_chart_urls(item, hint, urls, seen)
        return urls
    if isinstance(value, list):
        for item in value:
            _collect_chart_urls(item, key_hint, urls, seen)
    return urls


def parse_charts_text(text):
    parsed = {}
    blocks = _extract_json_blocks(text)
    for data in blocks:
        parsed.update(flatten_workflow_payload(data))
    if not parsed:
        try:
            loaded = json.loads(text) if text else {}
            if isinstance(loaded, dict):
                parsed.update(flatten_workflow_payload(loaded))
            elif isinstance(loaded, list):
                for item in loaded:
                    if isinstance(item, dict):
                        parsed.update(flatten_workflow_payload(item))
        except Exception:
            pass
    urls = _collect_chart_urls(parsed)
    # 只有从结构化对象拿不到足够URL时，才去扫原始文本；
    # 扫文本时只取"独立行"里的URL，避免把段落文字误判为URL
    if len(urls) < 6 and text:
        seen = {item["url"] for item in urls}
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^[>\-\*\s]*((https?://[^\s<>)\"']{12,512}))\s*$", line)
            if m:
                u = m.group(1).rstrip(").,\u3002\u3001\u300b]")
                if u not in seen and _looks_like_chart_url(u):
                    seen.add(u)
                    urls.append({"key": "url", "url": u})
    return {"source": "coze_charts" if text else "none", "images": urls[:6], "raw": parsed, "report_text": text or ""}


def _read_text_guess(path):
    if not os.path.exists(path):
        return ""
    raw = None
    with open(path, "rb") as f:
        raw = f.read()
    return _decode_text_guess(raw)


def _decode_text_guess(raw):
    if raw is None:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _case_id_from_text(text):
    return "case_" + hashlib.sha1(str(text or "").encode("utf-8", "ignore")).hexdigest()[:12]


def _first_line(text):
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def normalize_case_item(item, source="coze_knowledge", fallback_index=0):
    raw = item if isinstance(item, dict) else {"content": str(item or "")}
    content = (
        raw.get("content")
        or raw.get("text")
        or raw.get("document_content")
        or raw.get("markdown")
        or raw.get("summary")
        or raw.get("snippet")
        or ""
    )
    title = (
        raw.get("title")
        or raw.get("name")
        or raw.get("document_name")
        or raw.get("file_name")
        or raw.get("filename")
        or _first_line(content)
        or "故障案例 %03d" % (fallback_index + 1)
    )
    title = re.sub(r"\s+", " ", str(title)).strip()
    text_for_parse = "%s\n%s" % (title, content)
    marker = re.search(r"【\s*案例\s*0*(\d+)\s*】\s*([^\n]+)", text_for_parse)
    if marker:
        title = "案例%s %s" % (marker.group(1).zfill(3), marker.group(2).strip())
    equipment = (
        raw.get("equipment_id")
        or raw.get("device_id")
        or raw.get("machine_id")
        or (re.search(r"CNC[_-]\d{3}", text_for_parse, re.I).group(0).upper().replace("-", "_") if re.search(r"CNC[_-]\d{3}", text_for_parse, re.I) else "")
    )
    date_match = re.search(r"(20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?)", text_for_parse)
    fault_match = re.search(r"(?:故障类型|故障类别|异常类型)\s*[:：]\s*([^\n]+)", text_for_parse)
    summary_lines = []
    for line in str(content or "").splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line == title:
            continue
        if re.search(r"^(设备编号|故障日期|故障类型|详细描述|参数|根因|维修)", line):
            continue
        summary_lines.append(line)
        if len("".join(summary_lines)) > 110:
            break
    summary = " ".join(summary_lines).strip()
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."
    case_id = raw.get("id") or raw.get("document_id") or raw.get("doc_id") or raw.get("file_id") or _case_id_from_text(title + content)
    return {
        "id": str(case_id),
        "title": title,
        "equipment_id": equipment or "未标注",
        "fault_type": (fault_match.group(1).strip() if fault_match else raw.get("fault_type") or "未标注"),
        "case_date": (date_match.group(1).strip() if date_match else raw.get("case_date") or raw.get("updated_at") or raw.get("update_time") or ""),
        "summary": summary or "该案例来自故障案例库，点击查看完整内容。",
        "content": normalize_coze_markdown(content or title),
        "source": source,
        "raw": raw,
    }


def case_row_to_item(row_data):
    item = dict(row_data or {})
    raw = _safe_json_loads(item.get("raw_json"), {})
    item["raw"] = raw if isinstance(raw, dict) else {}
    item.pop("raw_json", None)
    item.pop("sync_json", None)
    return item


def save_case_to_db(item, file_name="", sync=None):
    if not item:
        raise ValueError("case item required")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sync = sync or {}
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO fault_cases
        (id, title, equipment_id, fault_type, case_date, summary, content, source, file_name, workflow_id, sync_status, sync_json, raw_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            equipment_id=excluded.equipment_id,
            fault_type=excluded.fault_type,
            case_date=excluded.case_date,
            summary=excluded.summary,
            content=excluded.content,
            source=excluded.source,
            file_name=excluded.file_name,
            workflow_id=excluded.workflow_id,
            sync_status=excluded.sync_status,
            sync_json=excluded.sync_json,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
    """, (
        item.get("id"),
        item.get("title"),
        item.get("equipment_id"),
        item.get("fault_type"),
        item.get("case_date"),
        item.get("summary"),
        item.get("content"),
        item.get("source") or "case_database",
        file_name or raw.get("file_name") or "",
        sync.get("workflow_id") or "",
        sync.get("status") or "local",
        json.dumps(sync, ensure_ascii=False, default=str),
        json.dumps(raw, ensure_ascii=False, default=str),
        now,
        now,
    ))
    conn.commit()
    saved = cur.execute("SELECT * FROM fault_cases WHERE id=?", (item.get("id"),)).fetchone()
    conn.close()
    return case_row_to_item(saved)


def ensure_case_seeded_from_txt():
    try:
        existing = row("SELECT COUNT(*) AS count FROM fault_cases").get("count", 0)
    except Exception:
        existing = 0
    if existing:
        return existing
    count = 0
    for item in load_local_cases():
        item["source"] = "case_database"
        save_case_to_db(item, file_name="案例知识库.txt", sync={"status": "seeded_from_txt"})
        count += 1
    return count


def load_local_cases():
    text = _read_text_guess(CASE_LIBRARY_TXT)
    if not text:
        return []
    sections = [part.strip() for part in re.split(r"\n\s*=+\s*\n", text) if part.strip()]
    case_sections = [part for part in sections if re.search(r"【\s*案例\s*\d+\s*】", part)]
    if not case_sections:
        case_sections = [part.strip() for part in re.split(r"(?=【\s*案例\s*\d+\s*】)", text) if re.search(r"【\s*案例\s*\d+\s*】", part)]
    return [normalize_case_item({"content": part}, "local_case_library", idx) for idx, part in enumerate(case_sections)]


def _next_local_case_number(existing_text):
    nums = []
    for match in re.finditer(r"【\s*案例\s*0*(\d+)\s*】", existing_text or ""):
        try:
            nums.append(int(match.group(1)))
        except Exception:
            pass
    return (max(nums) + 1) if nums else (len(load_local_cases()) + 1)


def _prepare_local_case_text(file_bytes, filename):
    content = _decode_text_guess(file_bytes).replace("\x00", "").strip()
    if not content:
        raise ValueError("empty txt content")
    if re.search(r"【\s*案例\s*\d+\s*】", content):
        return content
    existing = _read_text_guess(CASE_LIBRARY_TXT)
    case_no = _next_local_case_number(existing)
    title = _first_line(content) or os.path.splitext(os.path.basename(filename or ""))[0] or "未命名故障案例"
    title = re.sub(r"\s+", " ", title).strip()[:60]
    return "【案例%s】%s\n\n%s" % (str(case_no).zfill(3), title, content)


def append_local_case_file(file_bytes, filename):
    content = _prepare_local_case_text(file_bytes, filename)
    os.makedirs(os.path.dirname(CASE_LIBRARY_TXT), exist_ok=True)
    exists = os.path.exists(CASE_LIBRARY_TXT) and os.path.getsize(CASE_LIBRARY_TXT) > 0
    with open(CASE_LIBRARY_TXT, "ab") as f:
        if exists:
            f.write("\n\n============================================================================\n\n".encode("utf-8"))
        f.write(content.encode("utf-8"))
        f.write("\n".encode("utf-8"))
    item = normalize_case_item({"content": content, "file_name": filename}, "local_case_library", max(len(load_local_cases()) - 1, 0))
    return item


def _coze_json_request(method, url, token, payload=None, timeout=60):
    headers = {
        "Authorization": "Bearer %s" % token,
        "token": token,
        "Accept": "application/json",
        "Agw-Js-Conv": "str",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        if requests is not None:
            resp = requests.request(method, url, headers=headers, json=payload if payload is not None else None, timeout=timeout)
            if not resp.ok:
                raise RuntimeError("Coze API error %s: %s" % (resp.status_code, classify_coze_error(resp.text[:500])))
            return resp.json() if resp.text else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("Coze API error %s: %s" % (exc.code, classify_coze_error(detail)))


def _payload_ok(payload):
    if not isinstance(payload, dict):
        return True
    code = payload.get("code")
    if code in (None, 0, "0"):
        return True
    msg = payload.get("msg") or payload.get("message") or payload.get("error") or payload
    raise RuntimeError(classify_coze_error(json.dumps(msg, ensure_ascii=False, default=str)))


def _extract_case_documents(payload):
    docs = []

    def walk(value):
        if isinstance(value, list):
            if value and all(isinstance(x, dict) for x in value):
                likely = [
                    x for x in value
                    if any(k in x for k in ("document_id", "doc_id", "id", "name", "title", "content", "snippet", "document_name"))
                ]
                if likely:
                    docs.extend(likely)
                    return
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key in ("documents", "document_infos", "items", "list", "records", "data"):
                if key in value:
                    walk(value.get(key))

    walk(payload)
    seen = set()
    out = []
    for item in docs:
        doc_id = str(item.get("document_id") or item.get("doc_id") or item.get("id") or item.get("name") or item)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(item)
    return out


def coze_list_case_documents(limit=100):
    if not COZE_CASE_TOKEN:
        raise RuntimeError("Coze case token is not configured")
    base = COZE_BASE_URL.rstrip("/")
    dataset_id = COZE_CASE_KNOWLEDGE_ID
    candidates = [
        ("POST", "%s/open_api/knowledge/document/list" % base, {"dataset_id": parse_int(dataset_id), "page": 1, "size": limit}),
    ]
    errors = []
    for method, url, body in candidates:
        try:
            payload = _coze_json_request(method, url, COZE_CASE_TOKEN, body, timeout=8)
            _payload_ok(payload)
            docs = _extract_case_documents(payload)
            if docs:
                return [normalize_case_item(doc, "coze_knowledge", idx) for idx, doc in enumerate(docs)], payload
            if isinstance(payload, dict) and payload.get("data") in ([], {}, None):
                return [], payload
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors[-2:]) or "Coze knowledge documents unavailable")


def coze_upload_case_file(file_bytes, filename, token=None):
    token = token or COZE_CASE_SYNC_TOKEN
    if not token:
        raise RuntimeError("Coze case sync token is not configured")
    boundary = "----task6case%s" % int(time.time() * 1000)
    name = os.path.basename(filename or "case.txt")
    body = []
    body.append(("--%s\r\n" % boundary).encode("utf-8"))
    body.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % name).encode("utf-8"))
    body.append(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
    body.append(file_bytes)
    body.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    data = b"".join(body)
    headers = {
        "Authorization": "Bearer %s" % token,
        "token": token,
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Accept": "application/json",
    }
    errors = []
    for path in ("/v1/files/upload", "/v1/files"):
        url = COZE_BASE_URL.rstrip("/") + path
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            _payload_ok(payload)
            file_id = _find_file_id(payload)
            if not file_id:
                raise RuntimeError("Coze 文件上传成功但未返回 file_id")
            return file_id, payload
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            errors.append("HTTP %s: %s" % (exc.code, classify_coze_error(detail)))
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Coze 文件上传失败：%s" % ("; ".join(errors[-2:]) or "unknown"))


def _find_file_id(payload):
    if isinstance(payload, dict):
        for key in ("file_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _find_file_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_file_id(item)
            if found:
                return found
    return ""


app = Flask(__name__)
CORS(app)


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "code": 0,
        "status": "ok",
        "workflows": {
            key: {
                "name": wf["name"],
                "workflow_id": wf["workflow_id"],
                "configured": bool(wf["token"]),
                "inputs": wf["inputs"],
                "outputs": wf["outputs"],
            }
            for key, wf in WORKFLOWS.items()
        },
    })


@app.route("/api/workflows")
def workflow_contracts():
    return jsonify({"code": 0, "data": {
        key: {
            "name": wf["name"],
            "workflow_id": wf["workflow_id"],
            "inputs": wf["inputs"],
            "outputs": wf["outputs"],
        }
        for key, wf in WORKFLOWS.items()
    }})


@app.route("/api/risk-method")
def risk_method():
    return jsonify({"code": 0, "data": {
        "dashboard_risk": {
            "name": "看板预筛风险",
            "purpose": "只用于页面排序和快速筛选，不等同于最终预测结论",
            "source": ["MACHINE_SUMMARY_DATA._2025.csv", "MACHINE_LOG_DATA._2025.csv"],
            "rules": [
                "failure_type 非 0/空：+60，表示原始数据已有故障标记",
                "温度高于全设备 P75/P90/P95：+6/+12/+18",
                "电流高于全设备 P75/P90/P95：+5/+9/+14",
                "电压超出全设备 P05-P95：+6",
                "转速超出全设备 P05-P95：+5",
            ],
            "thresholds": {"low": "0-34", "medium": "35-54", "high": "55-74", "critical": "75-100"},
            "baseline": get_risk_baselines(),
        },
        "workflow_risk": {
            "name": "WF_analyze 正式预测",
            "source": "Coze 工作流返回的 risk_level、risk_score、predicted_rul_hours、anomaly_events、heatmap_data",
            "note": "页面会标注来源；Coze 不可用时只显示本地 CSV 基线，不冒充模型预测。",
        },
    }})


@app.route("/api/devices")
def list_devices():
    line = (request.args.get("line") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    data = []
    for item in rows("SELECT * FROM devices ORDER BY id"):
        risk = risk_breakdown(item)
        item["risk_score"] = risk["score"]
        item["risk_level"] = risk["level"]
        item["risk_factors"] = risk["factors"]
        item["risk_method"] = risk["method"]
        item["status"] = status_of(item)
        if line == "__unassigned__" and item.get("line"):
            continue
        if line and line != "__unassigned__" and item.get("line") != line:
            continue
        if q and q not in ("%s %s %s" % (item.get("id", ""), item.get("type", ""), item.get("line", ""))).lower():
            continue
        if status and item["status"] != status:
            continue
        data.append(item)
    return jsonify({"code": 0, "data": data, "total": len(data)})


@app.route("/api/devices/<device_id>")
def get_device(device_id):
    item = load_device(device_id)
    if not item:
        return jsonify({"code": 404, "msg": "device not found"}), 404
    risk = risk_breakdown(item)
    item["risk_score"] = risk["score"]
    item["risk_level"] = risk["level"]
    item["risk_factors"] = risk["factors"]
    item["risk_method"] = risk["method"]
    item["logs"] = load_logs(device_id, 20)
    item["tests"] = load_tests(device_id, 20)
    return jsonify({"code": 0, "data": item})


@app.route("/api/logs")
def list_logs():
    equipment_id = (request.args.get("equipment_id") or "").strip() or None
    limit = max(1, min(parse_int(request.args.get("limit"), 100), 500))
    data = load_logs(equipment_id, limit)
    return jsonify({"code": 0, "data": data, "total": len(data)})


@app.route("/api/tests")
def list_tests():
    machine = (request.args.get("machine") or "").strip() or None
    limit = max(1, min(parse_int(request.args.get("limit"), 100), 500))
    data = load_tests(machine, limit)
    return jsonify({"code": 0, "data": data, "total": len(data)})


@app.route("/api/stats")
def stats():
    devices = rows("SELECT * FROM devices ORDER BY id")
    total = len(devices)
    counts = Counter(status_of(d) for d in devices)
    lines = Counter(d.get("line") or "unassigned" for d in devices)
    return jsonify({"code": 0, "data": {
        "total": total,
        "status_counts": dict(counts),
        "lines": dict(lines),
        "fault_count": counts.get("crit", 0),
        "risk_method": "真实CSV最新状态 + 全设备分位数基线；正式预测以 WF_analyze 输出为准",
        "risk_baseline": get_risk_baselines(),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
    }})


@app.route("/api/workflow/analyze", methods=["POST"])
def workflow_analyze():
    payload = request.get_json(force=True) or {}
    device_id = payload.get("device_id")
    query_type = payload.get("query_type", "fault_analysis")
    persist = payload.get("persist", True)
    if not device_id:
        return jsonify({"code": 400, "msg": "device_id required"}), 400
    fallback = local_analysis(device_id)
    if not fallback:
        return jsonify({"code": 404, "msg": "device not found"}), 404
    wf_params = build_workflow_params(device_id, query_type)

    def generate():
        yield sse({"event": "status", "message": "analysis started", "source": "coze_wf_analyze"})
        analysis = dict(fallback)
        analysis_text_parts = []
        used_coze = False
        try:
            for event_name, data_text in coze_workflow_events("analyze", wf_params):
                used_coze = True
                payload, message = parse_workflow_event_data(data_text)
                if isinstance(payload, dict):
                    analysis.update(payload)
                if message:
                    analysis_text_parts.append(message)
                    yield sse({"event": "message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            analysis["report_text"] = normalize_coze_markdown("\n".join(analysis_text_parts).strip() or fallback.get("report_text", ""))
            analysis["workflow_source"] = "coze_wf_analyze"
            analysis["source"] = "coze_wf_analyze"
            analysis["data_source"] = analysis.get("data_source") or "coze_wf_analyze"
            analysis = normalize_analysis(analysis)
        except Exception as exc:
            analysis = dict(fallback)
            analysis["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "WF_analyze unavailable, using transparent local CSV baseline", "error": str(exc)})
        if not used_coze:
            time.sleep(0.2)
            yield sse({"event": "message", "message": analysis.get("report_text", "")})
        result = save_prediction(analysis) if persist else analysis
        yield sse({"event": "completed", "data": result})

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/api/workflow/workorder", methods=["POST"])
def workflow_workorder():
    payload = request.get_json(force=True) or {}
    device_id = payload.get("device_id")
    analysis = payload.get("analysis") or {}
    if not device_id:
        return jsonify({"code": 400, "msg": "device_id required"}), 400
    if not analysis:
        analysis = local_analysis(device_id) or {}
    analysis = merge_analysis_context(device_id, analysis)
    wf_inputs = build_workorder_inputs(analysis)

    def generate():
        yield sse({"event": "status", "message": "workorder workflow started", "source": "coze_wf3"})
        payload = {}
        try:
            for event_name, data_text in coze_workflow_events("workorder", wf_inputs):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    payload.update(parsed)
                if message:
                    yield sse({"event": "message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            work_order = parse_workorder_text(json.dumps(payload, ensure_ascii=False, default=str), analysis)
        except Exception as exc:
            work_order = parse_workorder_text("", analysis)
            work_order["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "WF3 unavailable, using local work-order template", "error": str(exc)})
        saved = save_workorder_payload(work_order, analysis.get("prediction_id"))
        yield sse({"event": "completed", "data": saved})

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/api/workflow/charts", methods=["POST"])
def workflow_charts():
    payload = request.get_json(force=True) or {}
    device_id = payload.get("device_id")
    analysis = payload.get("analysis") or {}
    if not device_id:
        return jsonify({"code": 400, "msg": "device_id required"}), 400
    if not analysis:
        analysis = local_analysis(device_id) or {}
    analysis = merge_analysis_context(device_id, analysis)
    wf_inputs = build_chart_inputs(analysis)

    def generate():
        yield sse({"event": "status", "message": "chart workflow started", "source": "coze_charts"})
        payload = {}
        try:
            for event_name, data_text in coze_workflow_events("charts", wf_inputs):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    payload.update(parsed)
                if message:
                    yield sse({"event": "message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            chart_payload = parse_charts_text(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as exc:
            chart_payload = parse_charts_text("")
            chart_payload["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "绘图工作流不可用，返回占位结果", "error": str(exc)})
        yield sse({"event": "completed", "data": chart_payload})

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/api/workflow/full", methods=["POST"])
def workflow_full():
    payload = request.get_json(force=True) or {}
    device_id = payload.get("device_id")
    query_type = payload.get("query_type", "fault_analysis")
    if not device_id:
        return jsonify({"code": 400, "msg": "device_id required"}), 400
    analyze_payload = build_workflow_params(device_id, query_type)
    if not analyze_payload:
        return jsonify({"code": 404, "msg": "device not found"}), 404
    fallback = local_analysis(device_id) or {}

    def generate():
        yield sse({"event": "status", "message": "full pipeline started", "source": "pipeline"})
        analysis = None
        analysis_payload = {}
        try:
            analysis_text_parts = []
            for event_name, data_text in coze_workflow_events("analyze", analyze_payload):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    analysis_payload.update(parsed)
                if message:
                    analysis_text_parts.append(message)
                    yield sse({"event": "analyze_message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            analysis_payload["report_text"] = normalize_coze_markdown("\n".join(analysis_text_parts).strip() or fallback.get("report_text", ""))
            merged_analysis = dict(fallback)
            merged_analysis.update(analysis_payload)
            merged_analysis["workflow_source"] = "coze_wf_analyze"
            merged_analysis["source"] = "coze_wf_analyze"
            merged_analysis["data_source"] = merged_analysis.get("data_source") or "coze_wf_analyze"
            analysis = normalize_analysis(merged_analysis)
        except Exception as exc:
            analysis = dict(fallback)
            analysis["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "WF_analyze unavailable, using local CSV baseline", "error": str(exc)})
        saved_prediction = save_prediction(analysis)
        yield sse({"event": "analyze_completed", "data": saved_prediction})

        workorder_result = None
        try:
            wf_inputs = build_workorder_inputs(saved_prediction)
            workorder_payload = {}
            for event_name, data_text in coze_workflow_events("workorder", wf_inputs):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    workorder_payload.update(parsed)
                if message:
                    yield sse({"event": "workorder_message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            workorder_result = parse_workorder_text(json.dumps(workorder_payload, ensure_ascii=False, default=str), saved_prediction)
        except Exception as exc:
            workorder_result = parse_workorder_text("", saved_prediction)
            workorder_result["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "WF3 unavailable, using local work-order template", "error": str(exc)})
        saved_workorder = save_workorder_payload(workorder_result, saved_prediction.get("prediction_id"))
        yield sse({"event": "workorder_completed", "data": saved_workorder})

        charts_result = None
        try:
            wf_inputs = build_chart_inputs(saved_prediction)
            charts_payload = {}
            for event_name, data_text in coze_workflow_events("charts", wf_inputs):
                parsed, message = parse_workflow_event_data(data_text)
                if isinstance(parsed, dict):
                    charts_payload.update(parsed)
                if message:
                    yield sse({"event": "charts_message", "message": message})
                if (event_name or "").lower() == "done":
                    break
            charts_result = parse_charts_text(json.dumps(charts_payload, ensure_ascii=False, default=str))
        except Exception as exc:
            charts_result = parse_charts_text("")
            charts_result["workflow_error"] = str(exc)
            yield sse({"event": "warning", "message": "绘图工作流不可用，返回占位结果", "error": str(exc)})
        save_workflow_snapshot(saved_prediction.get("device_id"), query_type, saved_prediction, saved_workorder, charts_result)
        yield sse({"event": "charts_completed", "data": charts_result})
        yield sse({"event": "completed", "data": {"prediction": saved_prediction, "workorder": saved_workorder, "charts": charts_result}})

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/api/predictions")
def list_predictions():
    limit = max(1, min(parse_int(request.args.get("limit"), 50), 200))
    data = rows("SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,))
    return jsonify({"code": 0, "data": data, "total": len(data)})


@app.route("/api/workorders")
def list_workorders():
    status = (request.args.get("status") or "").strip()
    if status:
        data = rows("SELECT * FROM work_orders WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        data = rows("SELECT * FROM work_orders ORDER BY created_at DESC LIMIT 200")
    return jsonify({"code": 0, "data": data, "total": len(data)})


@app.route("/api/workorders", methods=["POST"])
def create_workorder():
    data = request.get_json(force=True) or {}
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"code": 400, "msg": "device_id required"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO work_orders (device_id, title, priority, failure_type, estimated_cost, steps)
        VALUES (?,?,?,?,?,?)
    """, (
        device_id,
        data.get("title") or "%s manual maintenance" % device_id,
        data.get("priority") or "medium",
        data.get("failure_type") or "manual",
        parse_float(data.get("estimated_cost"), 500),
        json.dumps(data.get("steps") or ["Review device", "Inspect sensors", "Record result"], ensure_ascii=False),
    ))
    wid = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"code": 0, "data": {"id": wid}})


@app.route("/api/workorders/<int:wid>/status", methods=["PUT"])
def update_workorder_status(wid):
    data = request.get_json(force=True) or {}
    status = data.get("status", "in_progress")
    conn = get_db()
    conn.execute("UPDATE work_orders SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, wid))
    conn.commit()
    conn.close()
    return jsonify({"code": 0, "msg": "ok"})


@app.route("/api/workorders/<int:wid>", methods=["DELETE"])
def delete_workorder(wid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM work_orders WHERE id=?", (wid,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if not deleted:
        return jsonify({"code": 404, "msg": "workorder not found"}), 404
    return jsonify({"code": 0, "msg": "deleted", "data": {"id": wid}})


def _load_case_records(limit=100):
    ensure_case_seeded_from_txt()
    db_cases = [case_row_to_item(r) for r in rows(
        "SELECT * FROM fault_cases ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )]
    return {
        "source": "case_database",
        "items": db_cases,
        "payload": {"database": DB_PATH},
        "error": None,
    }


def _find_case_item(case_id, limit=200):
    ensure_case_seeded_from_txt()
    found = row("SELECT * FROM fault_cases WHERE id=?", (case_id,))
    record = {
        "source": "case_database",
        "items": [],
        "payload": {"database": DB_PATH},
        "error": None,
    }
    if not found:
        return None, record
    return case_row_to_item(found), record


@app.route("/api/cases")
def list_cases():
    limit = max(1, min(parse_int(request.args.get("limit"), 50), 200))
    q = (request.args.get("q") or "").strip().lower()
    record = _load_case_records(limit=1000)
    items = record.get("items") or []
    if q:
        items = [
            item for item in items
            if q in " ".join([
                str(item.get("title", "")),
                str(item.get("equipment_id", "")),
                str(item.get("fault_type", "")),
                str(item.get("summary", "")),
                str(item.get("case_date", "")),
            ]).lower()
        ]
    return jsonify({
        "code": 0,
        "data": {
            "items": items[:limit],
            "source": record.get("source"),
            "error": record.get("error"),
            "count": len(items),
            "knowledge_id": COZE_CASE_KNOWLEDGE_ID,
        },
    })


@app.route("/api/cases/<case_id>")
def get_case(case_id):
    item, record = _find_case_item(case_id, limit=200)
    if not item:
        return jsonify({
            "code": 404,
            "msg": "case not found",
            "data": {"source": record.get("source"), "error": record.get("error")},
        }), 404
    return jsonify({
        "code": 0,
        "data": {
            "item": item,
            "source": record.get("source"),
            "error": record.get("error"),
            "knowledge_id": COZE_CASE_KNOWLEDGE_ID,
        },
    })


@app.route("/api/cases/import", methods=["POST"])
def import_case():
    if "file" not in request.files:
        return jsonify({"code": 400, "msg": "file required"}), 400
    upload = request.files["file"]
    filename = (upload.filename or "").strip()
    if not filename.lower().endswith(".txt"):
        return jsonify({"code": 400, "msg": "only txt file supported"}), 400
    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({"code": 400, "msg": "empty file"}), 400
    if len(file_bytes) > 2 * 1024 * 1024:
        return jsonify({"code": 413, "msg": "file too large"}), 413
    try:
        case_content = _prepare_local_case_text(file_bytes, filename)
        local_item = normalize_case_item({"content": case_content, "file_name": filename}, "case_database", 0)
        saved_item = save_case_to_db(local_item, file_name=filename, sync={"status": "local_saved"})
        sync = {
            "status": "skipped" if not COZE_CASE_SYNC_TOKEN else "pending",
            "source": "case_database",
            "message": "案例已写入数据库",
        }
        upload_payload = {}
        if COZE_CASE_SYNC_TOKEN:
            try:
                file_id, upload_payload = coze_upload_case_file(file_bytes, filename, COZE_CASE_SYNC_TOKEN)
                wf_inputs = {
                    "anli_file": {
                        "file_id": file_id,
                        "file_name": filename,
                    },
                }
                messages = []
                structured = {}
                for event_name, data_text in coze_workflow_events("case_import", wf_inputs):
                    parsed, message = parse_workflow_event_data(data_text)
                    if isinstance(parsed, dict):
                        structured.update(parsed)
                    if message:
                        messages.append(message)
                    if (event_name or "").lower() == "done":
                        break
                workflow_error = ""
                if isinstance(structured, dict):
                    error_code = structured.get("error_code")
                    error_message = structured.get("error_message") or structured.get("msg")
                    if error_message or str(error_code or "") not in ("", "0", "None"):
                        workflow_error = error_message or ("Coze workflow error_code=%s" % error_code)
                sync = {
                    "status": "failed" if workflow_error else "synced",
                    "source": "coze_case_workflow",
                    "file_id": file_id,
                    "workflow_id": COZE_CASE_WORKFLOW_ID,
                    "messages": messages,
                    "result": structured,
                    "upload": upload_payload,
                }
                if workflow_error:
                    sync["error"] = classify_coze_error(str(workflow_error))
            except Exception as sync_exc:
                sync = {
                    "status": "failed",
                    "source": "case_database",
                    "error": classify_coze_error(str(sync_exc)),
                    "detail": str(sync_exc),
                }
        saved_item = save_case_to_db(local_item, file_name=filename, sync=sync)
        data = {
            "item": saved_item,
            "file_name": filename,
            "storage": "fault_cases",
            "sync": sync,
            "knowledge_id": COZE_CASE_KNOWLEDGE_ID,
        }
        return jsonify({"code": 0, "data": data})
    except Exception as exc:
        return jsonify({
            "code": 502,
            "msg": "案例写入失败：" + str(exc),
            "data": {"file_name": filename, "knowledge_id": COZE_CASE_KNOWLEDGE_ID, "storage": "fault_cases"},
        }), 502


def build_report_document(device_id=None, allow_remote=True, chart_images=None, prefer_template=False):
    context = build_report_context(device_id, hydrate=False, include_charts=False)
    prompt = build_report_prompt(context)
    if prefer_template or not allow_remote:
        report_text = render_template_report(context, chart_images=chart_images)
        source = "wf_analyze_template"
        error = None
    else:
        try:
            report_text = coze_report_chat(prompt, timeout=COZE_REPORT_EXPORT_TIMEOUT, tokens=[COZE_REPORT_TOKEN])
            source = "coze_report_bot"
            error = None
        except Exception as exc:
            try:
                report_text = coze_assistant_chat(prompt, timeout=COZE_REPORT_EXPORT_TIMEOUT)
                source = "coze_chat_bot"
                error = "coze_report_bot_failed: %s" % classify_coze_error(str(exc))
            except Exception as exc2:
                report_text = render_template_report(context, chart_images=chart_images)
                source = "wf_analyze_template"
                error = str(exc2)
    report_text = sanitize_coze_response_text(report_text)
    header = [
        "# 智维先知 - 设备预测性维护报告",
        "",
        f"- 生成时间: {context.get('generated_at')}",
        f"- 设备ID: {context.get('focus_device_id') or '未提供'}",
        f"- 报告来源: {source}",
    ]
    if error:
        header.append(f"- 失败原因: {error}")
    header.extend(["", "---", ""])
    body = "\n".join(header) + "\n" + report_text.strip()
    return body, source, error


def build_coze_maintenance_report_prompt(context, request_text=None):
    request_text = (request_text or "完整设备维护报告").strip() or "完整设备维护报告"
    compact_context, summary_text = build_coze_prompt_context(context)
    device_id = context.get("focus_device_id") or (context.get("device") or {}).get("id") or "not_provided"
    ready = compact_context.get("report_ready_data") if isinstance(compact_context.get("report_ready_data"), dict) else {}
    core = ready.get("core") if isinstance(ready.get("core"), dict) else {}
    device = compact_context.get("device") if isinstance(compact_context.get("device"), dict) else {}
    raw_overview = ready.get("raw_overview") if isinstance(ready.get("raw_overview"), dict) else {}
    report_context = {
        "device": {
            "id": device_id,
            "type": device.get("type"),
            "line": device.get("line") or "not_provided",
            "latest_temperature": device.get("temperature"),
            "latest_current": device.get("amperage"),
            "latest_voltage": device.get("voltage"),
            "latest_speed": device.get("rotor_speed"),
        },
        "core": core,
        "metric_baselines": {
            key: _compact_metric_stats(value)
            for key, value in raw_overview.items()
            if key in ("temperature", "current", "voltage", "speed", "power") and isinstance(value, dict)
        },
        "rul_analysis": ready.get("rul_analysis"),
        "anomaly_summary": ready.get("anomaly_summary"),
        "predicted_fault": ready.get("predicted_fault"),
        "top3_faults": _as_list(ready.get("top3_faults"))[:3],
        "diagnosis_engine": ready.get("diagnosis_engine"),
        "cost_analysis": ready.get("cost_analysis"),
        "work_order": ready.get("work_order") or compact_context.get("workflow_workorder"),
        "recent_work_orders": _as_list(compact_context.get("recent_work_orders"))[:3],
        "latest_logs": _as_list(compact_context.get("latest_logs"))[:3],
    }
    return (
        "当前前端系统选中的设备ID：%s\n"
        "用户请求：%s\n\n"
        "请基于下方真实数据生成一份标准Markdown设备维护报告。\n"
        "要求：只输出最终报告；不要输出JSON、代码块、HTML、调试信息或思考过程；表格必须是Markdown表格；缺失数据写“未提供”。\n"
        "篇幅控制在1000到1400个中文字符，必须包含：设备概况、核心指标、RUL剩余寿命、异常与风险证据、根因诊断、维护建议与工单、成本影响、结论。\n\n"
        "设备摘要：\n%s\n\n"
        "关键数据包：\n%s"
    ) % (
        device_id,
        request_text,
        summary_text,
        json.dumps(report_context, ensure_ascii=False, default=str, separators=(",", ":")),
    )


def generate_coze_maintenance_report(device_id=None, request_text=None):
    context = build_report_context(device_id, hydrate=False, include_charts=False)
    prompt = build_coze_maintenance_report_prompt(context, request_text)
    timeout = max(float(COZE_REPORT_EXPORT_TIMEOUT), 90.0)
    raw_report = coze_report_chat(prompt, timeout=timeout)
    report_markdown, warnings = standardize_report_markdown(raw_report)
    return {
        "device_id": context.get("focus_device_id") or device_id or "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "coze_report_bot",
        "report_markdown": report_markdown,
        "raw_report": raw_report,
        "format_warnings": warnings,
    }


def find_chrome_executable():
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for item in candidates:
        if item and os.path.exists(item):
            return item
    return None


def render_report_pdf(markdown_text, title="智维先知 - 设备预测性维护报告"):
    chrome = find_chrome_executable()
    if not chrome:
        raise RuntimeError("Chrome/Edge executable not found; cannot render PDF")
    html_body = markdown_to_html(markdown_text)
    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
@page {{ size:A4; margin:18mm 16mm; }}
body {{ font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif; color:#111827; line-height:1.7; font-size:12px; }}
h1 {{ font-size:22px; margin:0 0 14px; }}
h2 {{ font-size:17px; margin:20px 0 8px; page-break-after:avoid; }}
h3 {{ font-size:14px; margin:16px 0 6px; page-break-after:avoid; }}
p {{ margin:0 0 8px; }}
ul,ol {{ margin:0 0 10px 20px; padding:0; }}
li {{ margin:0 0 4px; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0 12px; page-break-inside:auto; }}
tr {{ page-break-inside:avoid; }}
td,th {{ border:1px solid #d4d4d8; padding:7px 8px; vertical-align:top; }}
th,td:first-child {{ background:#fafafa; font-weight:600; }}
code {{ font-family:Consolas,monospace; background:#f4f4f5; padding:1px 3px; border-radius:4px; }}
pre {{ white-space:pre-wrap; border:1px solid #e4e4e7; background:#fafafa; padding:10px; border-radius:8px; }}
img {{ max-width:100%; height:auto; display:block; margin:8px 0; border:1px solid #e4e4e7; border-radius:8px; }}
.md {{ max-width:100%; }}
</style>
</head><body>{body}</body></html>""".format(title=html_escape(title), body=html_body)
    tmpdir = tempfile.mkdtemp(prefix="task6_report_")
    html_path = os.path.join(tmpdir, "report.html")
    pdf_path = os.path.join(tmpdir, "report.pdf")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    file_url = "file:///" + urllib.request.pathname2url(os.path.abspath(html_path)).replace("%5C", "/")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        "--print-to-pdf=%s" % pdf_path,
        file_url,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if proc.returncode != 0 or not os.path.exists(pdf_path):
        raise RuntimeError((proc.stderr or proc.stdout).decode("utf-8", "replace")[:500])
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    return pdf_bytes


@app.route("/api/report/generate", methods=["POST"])
def generate_report():
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip() or None
    request_text = (data.get("request_text") or "完整设备维护报告").strip() or "完整设备维护报告"
    try:
        result = generate_coze_maintenance_report(device_id, request_text)
        return jsonify({"code": 0, "msg": "ok", "data": result})
    except Exception as exc:
        return jsonify({"code": 502, "msg": classify_coze_error(str(exc)), "data": {"device_id": device_id or ""}}), 502


@app.route("/api/report/export", methods=["GET", "POST"])
def export_report():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        device_id = (data.get("device_id") or "").strip() or None
        report_format = (data.get("format") or "pdf").strip().lower()
        report_markdown = str(data.get("report_markdown") or "").strip()
        if report_markdown:
            text, warnings = standardize_report_markdown(report_markdown)
            chart_images = _sanitize_chart_images(data.get("chart_images"))
            if chart_images and not re.search(r"(?m)^#{1,6}\s*可视化图表\s*$", text):
                text = (text.rstrip() + "\n\n## 可视化图表\n\n" + _build_chart_section(chart_images)).strip()
                warnings.append("已追加前端本地图表")
            source = data.get("source") or "coze_report_markdown"
            error = "; ".join(warnings)
        else:
            return jsonify({"code": 400, "msg": "请先生成 Coze 维护报告后再导出。", "data": {"device_id": device_id or ""}}), 400
    else:
        device_id = (request.args.get("device_id") or "").strip() or None
        allow_remote = (request.args.get("remote") or "").strip() in ("1", "true", "yes")
        chart_images = []
        text, source, error = build_report_document(device_id, allow_remote=allow_remote, chart_images=chart_images)
        report_format = (request.args.get("format") or "md").strip().lower()
    filename = "maintenance_report_%s.md" % date.today().isoformat()
    header_error = urllib.parse.quote(error or "", safe="")
    payload = text
    mimetype = "text/markdown"
    if report_format == "pdf":
        filename = "maintenance_report_%s.pdf" % date.today().isoformat()
        try:
            payload = render_report_pdf(text)
            mimetype = "application/pdf"
        except Exception as exc:
            source = source + "+pdf_failed"
            if error:
                error = "%s; pdf_failed: %s" % (error, classify_coze_error(str(exc)))
            else:
                error = "pdf_failed: %s" % classify_coze_error(str(exc))
            header_error = urllib.parse.quote(error or "", safe="")
            payload = text
            mimetype = "text/markdown"
            filename = "maintenance_report_%s.md" % date.today().isoformat()
    return Response(
        payload,
        mimetype=mimetype,
        headers={
            "Content-Disposition": "attachment; filename=%s" % filename,
            "X-Report-Source": source,
            "X-Report-Error": header_error,
        },
    )


@app.route("/api/maintenance-data/explain")
def maintenance_data_explain():
    device_id = (request.args.get("device_id") or "").strip() or None
    return jsonify({"code": 0, "msg": "ok", "data": build_maintenance_explain(device_id)})


@app.route("/api/image-proxy")
def image_proxy():
    url = (request.args.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return jsonify({"code": 400, "msg": "invalid image url"}), 400
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    try:
        if requests is not None:
            resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            if not resp.ok:
                return jsonify({"code": resp.status_code, "msg": "image fetch failed"}), resp.status_code
            ctype = resp.headers.get("Content-Type", "image/png").split(";")[0]
            return Response(resp.content, mimetype=ctype, headers={"Cache-Control": "public, max-age=3600"})
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            ctype = resp.headers.get("Content-Type", "image/png").split(";")[0]
            return Response(content, mimetype=ctype, headers={"Cache-Control": "public, max-age=3600"})
    except Exception as exc:
        return jsonify({"code": 502, "msg": classify_coze_error(str(exc))}), 502


@app.route("/api/chat", methods=["POST"])
def chat_assistant():
    data = request.get_json(force=True) or {}
    message = (data.get("message") or data.get("query") or "").strip()
    device_id = (data.get("device_id") or "").strip() or None
    device_from_text = extract_device_id_from_text(message)
    if device_from_text:
        device_id = device_from_text
    conversation_id = (data.get("conversation_id") or "").strip() or None
    if not message:
        return jsonify({"code": 400, "msg": "message is required"}), 400
    need_workflow = True
    need_charts = False
    context = build_report_context(device_id, hydrate=need_workflow, include_charts=need_charts)
    prompt = build_chat_prompt(message, device_id, context=context)
    try:
        reply = sanitize_coze_response_text(coze_assistant_chat(prompt, conversation_id=conversation_id))
        return jsonify(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "reply": reply,
                    "source": "coze_chat_bot",
                    "device_id": device_id,
                },
            }
        )
    except Exception as exc:
        context = build_report_context(device_id, hydrate=False, include_charts=False)
        fallback = normalize_coze_markdown(build_local_report(context, source_label="local_chat_fallback", error=str(exc)))
        return jsonify(
            {
                "code": 0,
                "msg": "fallback",
                "data": {
                    "reply": "Coze 智能体暂时不可用：%s\n\n%s" % (classify_coze_error(str(exc)), fallback),
                    "source": "local_fallback",
                    "error": str(exc),
                    "device_id": device_id,
                },
            }
        )


@app.route("/api/chat/stream", methods=["POST"])
def chat_assistant_stream():
    data = request.get_json(force=True) or {}
    message = (data.get("message") or data.get("query") or "").strip()
    device_id = (data.get("device_id") or "").strip() or None
    device_from_text = extract_device_id_from_text(message)
    if device_from_text:
        device_id = device_from_text
    conversation_id = (data.get("conversation_id") or "").strip() or None
    if not message:
        return jsonify({"code": 400, "msg": "message is required"}), 400
    context = build_report_context(device_id, hydrate=True, include_charts=True)
    prompt = build_chat_prompt(message, device_id, context=context)

    def generate():
        try:
            for item in coze_bot_chat_stream(prompt, COZE_CHAT_BOT_ID, COZE_CHAT_TOKEN, COZE_CHAT_USER_ID, conversation_id):
                yield sse({"event": item["event"], "text": item["text"], "raw_text": item.get("raw_text", "")})
        except Exception as exc:
            fallback = sanitize_coze_response_text(build_local_report(build_report_context(device_id, hydrate=False, include_charts=False), source_label="local_chat_fallback", error=str(exc)))
            yield sse({"event": "error", "message": classify_coze_error(str(exc)), "fallback": fallback})
        yield "event: done\ndata: [DONE]\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream; charset=utf-8")


if __name__ == "__main__":
    init_db()
    print("[API] http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
