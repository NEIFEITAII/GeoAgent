"""标准问题库加载与可解释规则匹配。"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def load_question_library(path: str | Path) -> dict[str, Any]:
    """读取并做最小结构校验，不执行问题库中的任何内容。"""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("问题库缺少非空 entries 数组。")
    required = {"id", "question", "strategy", "unit"}
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise ValueError(f"问题库第 {index} 项缺少必要字段。")
        entry_id = str(entry["id"])
        if entry_id in seen:
            raise ValueError(f"问题库存在重复 id: {entry_id}")
        seen.add(entry_id)
    return data


def _normalize(text: str) -> str:
    text = re.sub(r"\*+|＊+|_{2,}|＿{2,}|\{\{[^{}]+\}\}", "待填值", text)
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()
    # 只做可解释的同义词扩展，不用模型猜测业务口径。
    if "全库" in normalized and "图斑" in normalized and any(
        word in normalized for word in ("共有", "数量", "多少个")
    ):
        normalized += "总数总量全部"
    if any(word in normalized for word in ("各县", "每个县", "县市区")):
        normalized += "分县区县行政区"
    return normalized


def _candidate(query: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    normalized = _normalize(query)
    if not normalized:
        return None
    corpus = _normalize(" ".join([
        str(entry.get("question", "")),
        str(entry.get("intent", "")),
        *[str(item) for item in entry.get("aliases", [])],
        *[str(item) for item in entry.get("keywords", [])],
    ]))
    if "疑似新增违法" in normalized and "违法" not in corpus:
        return None
    reasons: list[str] = []
    raw_score = 0.0
    standard = _normalize(str(entry.get("question", "")))
    aliases = [_normalize(str(item)) for item in entry.get("aliases", []) if str(item).strip()]
    keywords = [_normalize(str(item)) for item in entry.get("keywords", []) if str(item).strip()]

    if standard and (standard in normalized or normalized in standard):
        raw_score += 8.0
        reasons.append("标准问题文本相互包含")
    matched_aliases = [item for item in aliases if item and item in normalized]
    if matched_aliases:
        raw_score += 5.0 + min(len(matched_aliases) - 1, 2)
        reasons.append("命中别名：" + "、".join(matched_aliases[:3]))
    matched_keywords = [item for item in keywords if item and item in normalized]
    if matched_keywords:
        ratio = len(matched_keywords) / max(len(keywords), 1)
        raw_score += 6.0 * ratio
        reasons.append("命中关键词：" + "、".join(matched_keywords[:5]))

    query_chars = set(normalized)
    standard_chars = set(standard)
    if query_chars and standard_chars:
        overlap = len(query_chars & standard_chars) / len(query_chars | standard_chars)
        raw_score += 2.0 * overlap
    if raw_score < 1.5:
        return None
    confidence = min(raw_score / 16.0, 1.0)
    return {
        "id": entry["id"],
        "question": entry["question"],
        "intent": entry.get("intent"),
        "strategy": entry.get("strategy"),
        "tool": entry.get("tool") or None,
        "unit": entry.get("unit"),
        "score": round(raw_score, 3),
        "confidence": round(confidence, 3),
        "match_reasons": reasons,
    }


def match_question(
    question: str,
    library: dict[str, Any],
    *,
    top_k: int = 3,
    min_confidence: float = 0.18,
) -> dict[str, Any]:
    """返回候选问题；只推荐，不替用户确认业务口径。"""
    candidates = [
        candidate
        for entry in library["entries"]
        if (candidate := _candidate(question, entry)) is not None
        and candidate["confidence"] >= min_confidence
    ]
    candidates.sort(key=lambda item: (-item["score"], item["id"]))
    candidates = candidates[:top_k]
    best = candidates[0] if candidates else None
    status = "unmatched"
    if best:
        status = "candidate_found" if best["confidence"] >= 0.35 else "needs_review"
    return {
        "status": status,
        "matched_question_id": best["id"] if best else None,
        "confidence": best["confidence"] if best else None,
        "match_reasons": best["match_reasons"] if best else [],
        "candidates": candidates,
    }


def match_parsed_questions(
    questions: list[dict[str, Any]],
    library: dict[str, Any],
) -> list[dict[str, Any]]:
    """复制问题列表并补入候选，保留人工确认状态。"""
    matched: list[dict[str, Any]] = []
    for question in questions:
        item = dict(question)
        item["question_library_match"] = match_question(question["question"], library)
        matched.append(item)
    return matched
