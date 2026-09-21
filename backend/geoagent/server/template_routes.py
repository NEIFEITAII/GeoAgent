"""模板解析、标准问题逐项执行和报告生成接口。"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..report.pdf import PdfConversionError, convert_docx_to_pdf
from ..report.question_library import load_question_library, match_parsed_questions
from ..report.template import MAX_UPLOAD, decompose_atomic_items, decompose_questions, parse_docx
from ..report.workflow import (
    REQUIRED_QUESTION_IDS,
    build_question_library_template,
    fill_question_library_report,
)
from ..tools.labels import TBLX_LABELS

router = APIRouter(prefix="/api/report-templates", tags=["report-templates"])


class ExecuteQuestionBody(BaseModel):
    library_id: str
    question: str = ""
    conversation_id: str = ""


class GenerateReportBody(BaseModel):
    conversation_id: str = ""
    request_text: str = "请根据以上问数结果生成Word和PDF快报。"


def _library_path(request: Request) -> Path:
    return request.app.state.settings.skills_dir / "qa-library" / "assets" / "question_library.json"


def _workflow(request: Request, workflow_id: str) -> dict[str, Any]:
    item = request.app.state.template_workflows.get(workflow_id)
    if item is None:
        raise HTTPException(404, "模板工作流不存在或后端已重启，请重新上传模板。")
    return item


def _display_result(result: dict[str, Any]) -> dict[str, Any]:
    """把问数过程中的面积统一转换为亩，原始平方米结果仍保留在工作流中。"""
    area_columns = {"area", "area_m2"}
    columns = ["area_mu" if item in area_columns else item for item in result.get("columns", [])]
    rows = []
    for source in result.get("rows", []):
        row = {}
        for key, value in source.items():
            if key in area_columns:
                row["area_mu"] = round(float(value) * 0.0015, 2) if value is not None else None
            elif key == "TBLX":
                row[key] = TBLX_LABELS.get(str(value), value)
            else:
                row[key] = value
        rows.append(row)
    return {
        **result,
        "columns": columns,
        "rows": rows,
        "row_count": result.get("row_count", len(rows)),
        "unit": "亩",
    }


def _column_label(value: str) -> str:
    return {
        "n": "图斑数量",
        "area_mu": "面积（亩）",
        "TBLX": "图斑类型",
        "region": "县（市、区）",
    }.get(value, value)


def _answer_summary(entry: dict[str, Any], result: dict[str, Any]) -> str:
    """为标准问题生成简洁、可追溯的确定性对话结论。"""
    rows = result.get("rows", [])
    lines = [f"## {entry['question']}"]
    if len(rows) == 1:
        values = "；".join(
            f"{_column_label(str(key))}：{value}" for key, value in rows[0].items()
        )
        lines.append(values + "。")
    else:
        lines.append(f"查询完成，共返回{len(rows)}行；完整结果见上方查询结果表。")
    lines.append(
        f"统计口径：{entry.get('answer_hint') or entry.get('intent', '')}；面积统一显示为亩。"
    )
    return "\n\n".join(lines)


def _append_conversation_result(
    request: Request,
    conversation_id: str,
    question: str,
    entry: dict[str, Any],
    sql: str,
    result: dict[str, Any],
) -> None:
    store = request.app.state.store
    if not conversation_id:
        return
    if store.get(conversation_id) is None:
        raise HTTPException(404, "当前智能体会话不存在。")
    tool_call_id = f"template-{uuid4().hex[:10]}"
    store.add_message(conversation_id, {"role": "user", "content": question})
    store.update_title_if_placeholder(conversation_id, question)
    store.add_message(conversation_id, {
        "role": "assistant",
        "content": "",
        "route": "sql",
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "run_sql",
                "arguments": json.dumps({"sql": sql}, ensure_ascii=False),
            },
        }],
    })
    store.add_message(conversation_id, {
        "role": "tool",
        "name": "run_sql",
        "tool_call_id": tool_call_id,
        "content": (
            f"标准问题 {entry['id']} 查询完成，"
            f"共返回{result.get('row_count', len(result.get('rows', [])))}行。"
        ),
        "artifacts": [{
            "kind": "table",
            "name": f"标准问题：{entry['question']}",
            "data": result,
        }],
    })
    store.add_message(conversation_id, {
        "role": "assistant",
        "route": "sql",
        "content": _answer_summary(entry, result),
    })


def _append_generation_result(
    request: Request,
    conversation_id: str,
    request_text: str,
    response: dict[str, Any],
) -> None:
    """把报告生成请求、过程及文件写入当前智能体会话。"""
    if not conversation_id:
        return
    store = request.app.state.store
    if store.get(conversation_id) is None:
        raise HTTPException(404, "当前智能体会话不存在。")
    tool_call_id = f"report-{uuid4().hex[:10]}"
    store.add_message(conversation_id, {
        "role": "user",
        "content": request_text.strip() or "请根据以上问数结果生成Word和PDF快报。",
    })
    store.add_message(conversation_id, {
        "role": "assistant",
        "content": "",
        "route": "sql",
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "generate_monitoring_report",
                "arguments": json.dumps({"formats": ["docx", "pdf"]}, ensure_ascii=False),
            },
        }],
    })
    artifacts = [{"kind": "file", "name": "report_docx", "data": response["word"]}]
    if response.get("pdf"):
        artifacts.append({"kind": "file", "name": "report_pdf", "data": response["pdf"]})
    result_text = "Word报告已生成。"
    if response.get("pdf"):
        result_text = "Word和PDF报告均已生成。"
    elif response.get("pdf_error"):
        result_text += f" PDF未生成：{response['pdf_error']}"
    store.add_message(conversation_id, {
        "role": "tool",
        "name": "generate_monitoring_report",
        "tool_call_id": tool_call_id,
        "content": result_text,
        "artifacts": artifacts,
    })
    store.add_message(conversation_id, {
        "role": "assistant",
        "route": "sql",
        "content": (
            "## 地类变化监测快报已生成\n\n"
            "报告使用本轮对话中已确认并保存的同一批问数结果完成回填，"
            "Word和可用的PDF文件见上方文件卡片。"
            + (f"\n\nPDF说明：{response['pdf_error']}" if response.get("pdf_error") else "")
        ),
    })
@router.get("/sample")
async def download_sample_template(request: Request) -> FileResponse:
    """生成并下载仅使用现有标准问题库口径的示例快报模板。"""
    reports_dir = request.app.state.settings.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"标准问题库示例模板_{uuid4().hex[:8]}.docx"
    await asyncio.to_thread(build_question_library_template, path)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="标准问题库示例模板.docx",
    )


@router.post("/parse")
async def upload_template(request: Request, filename: str) -> dict[str, Any]:
    """解析上传模板，并把候选标准问题和稳定回填位置放入同一工作流。"""
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "模板不得超过10MB。")
    try:
        parsed = await asyncio.to_thread(parse_docx, bytes(content), filename)
        atomic_items = await asyncio.to_thread(decompose_atomic_items, parsed)
        library = await asyncio.to_thread(load_question_library, _library_path(request))
        questions = await asyncio.to_thread(
            match_parsed_questions, decompose_questions(parsed), library
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    workflow_id = uuid4().hex
    request.app.state.template_workflows[workflow_id] = {
        "filename": filename,
        "content": bytes(content),
        "template": parsed,
        "atomic_items": atomic_items,
        "questions": questions,
        "library": library,
        "executions": {},
        "query_started_at": None,
    }
    return {
        "workflow_id": workflow_id,
        "template": parsed,
        "atomic_items": atomic_items,
        "questions": questions,
        "stage": "请确认问数清单",
        "notice": "模板已解析并与标准问题库匹配。请逐项核对候选问题，确认后执行；未确认内容不会查询或回填。",
    }


@router.post("/{workflow_id}/questions/{question_id}/execute")
async def execute_question(
    workflow_id: str,
    question_id: str,
    body: ExecuteQuestionBody,
    request: Request,
) -> dict[str, Any]:
    """执行用户确认的标准问题参考 SQL，并保存用于同次报告回填的结果。"""
    workflow = _workflow(request, workflow_id)
    question = next((item for item in workflow["questions"] if item["id"] == question_id), None)
    if question is None:
        raise HTTPException(404, "模板问题不存在。")
    entry = next(
        (item for item in workflow["library"]["entries"] if item["id"] == body.library_id),
        None,
    )
    if entry is None:
        raise HTTPException(400, "所选标准问题不存在。")
    sql = str(entry.get("sql", "")).strip()
    if not sql:
        raise HTTPException(400, "所选标准问题没有可执行的参考SQL。")
    if workflow["query_started_at"] is None:
        workflow["query_started_at"] = datetime.now().astimezone()
    try:
        result = await request.app.state.pg.run_sql(
            sql,
            conversation_id=f"template-workflow:{workflow_id}",
        )
    except Exception as exc:
        raise HTTPException(502, f"标准问题查询失败：{type(exc).__name__}: {exc}") from exc
    display_result = _display_result(result)
    _append_conversation_result(
        request,
        body.conversation_id,
        body.question.strip() or question["question"],
        entry,
        sql,
        display_result,
    )
    workflow["executions"][entry["id"]] = result
    question["question"] = body.question.strip() or question["question"]
    question["status"] = "已确认并完成查询"
    question["question_library_match"]["confirmed_question_id"] = entry["id"]
    question["question_library_match"]["status"] = "confirmed"
    return {
        "question_id": question_id,
        "library_id": entry["id"],
        "standard_question": entry["question"],
        "unit": entry.get("unit", ""),
        "result": display_result,
        "executed_count": len(workflow["executions"]),
    }


@router.post("/{workflow_id}/generate")
async def generate_report(
    workflow_id: str,
    body: GenerateReportBody,
    request: Request,
) -> dict[str, Any]:
    """用已保存的同批查询结果回填模板，并输出 Word 和可用时的 PDF。"""
    workflow = _workflow(request, workflow_id)
    missing = [item for item in REQUIRED_QUESTION_IDS if item not in workflow["executions"]]
    if missing:
        raise HTTPException(409, "以下标准问题尚未执行：" + "、".join(missing))
    reports_dir = request.app.state.settings.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid4().hex[:8]
    source = request.app.state.settings.data_dir / "template-workflows" / f"{workflow_id}.docx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(workflow["content"])
    word_path = reports_dir / f"地类变化监测快报_2026年第一期_{suffix}.docx"
    await asyncio.to_thread(
        fill_question_library_report,
        source,
        workflow["template"],
        workflow["executions"],
        word_path,
        query_started_at=workflow["query_started_at"],
    )
    pdf_path = word_path.with_suffix(".pdf")
    pdf_error = None
    try:
        await asyncio.to_thread(convert_docx_to_pdf, word_path, pdf_path)
    except PdfConversionError as exc:
        pdf_error = str(exc)

    trace = {
        "workflow_id": workflow_id,
        "query_started_at": workflow["query_started_at"].isoformat(),
        "template": workflow["template"],
        "questions": workflow["questions"],
        "executions": workflow["executions"],
        "outputs": {"word": str(word_path), "pdf": str(pdf_path) if pdf_error is None else None},
    }
    trace_path = source.with_suffix(".json")
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    response: dict[str, Any] = {
        "word": {
            "filename": word_path.name,
            "url": f"/api/files/reports/{quote(word_path.name)}",
        },
        "pdf": None,
        "pdf_error": pdf_error,
        "query_started_at": workflow["query_started_at"].isoformat(),
        "trace_saved": str(trace_path),
    }
    if pdf_error is None:
        response["pdf"] = {
            "filename": pdf_path.name,
            "url": f"/api/files/reports/{quote(pdf_path.name)}",
        }
    _append_generation_result(
        request,
        body.conversation_id,
        body.request_text,
        response,
    )
    return response
