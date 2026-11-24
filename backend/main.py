"""FastAPI 入口 —— Supervisor Router"""
from __future__ import annotations
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .models import (
    AgentRunResponse,
    HealthResponse,
    RiskIdentificationInput,
    QualityCheckInput,
)
from .agents.risk_agent import RiskIdentificationAgent
from .agents.quality_agent import QualityCheckAgent
from .knowledge.kb import ensure_kb_built
from . import storage


# ---------- 全局状态 ----------
# 内存级 "正在生成" tracker — 让 query 接口能返回 generating 状态
# 防止同一 external_id 被多个并发请求无限重复生成
_GENERATING: set[str] = set()
_GENERATING_LOCK = threading.Lock()
_JOB_TYPE_MAP = {
    "吊装": "吊装", "起重": "吊装", "吊机": "吊装",
    "高处": "高处", "高空": "高处",
    "深基坑": "深基坑", "基坑": "深基坑",
    "动土": "动土", "开挖": "动土", "管线": "动土", "土方": "动土",
    "非开挖": "非开挖", "定向钻": "非开挖", "顶管": "非开挖", "盾构": "非开挖",
}
# 兜底关键字 — 都没命中时走动土，但把原始类型完整传给 LLM
_DEFAULT_WORK_TYPE = "动土"


def map_job_type(raw: str | None) -> str:
    """把集成方传入的 jobType_dictText 宽松匹配到内部 5 种 work_type"""
    if not raw:
        return _DEFAULT_WORK_TYPE
    text = raw.strip()
    for kw, wt in _JOB_TYPE_MAP.items():
        if kw in text:
            return wt
    return _DEFAULT_WORK_TYPE


def build_quality_input_from_query(params: dict) -> QualityCheckInput:
    """从集成方 URL query 参数构建 QualityCheckInput"""
    raw_job_type = params.get("jobType_dictText", "") or ""
    mapped_wt = map_job_type(raw_job_type)
    return QualityCheckInput(
        project=params.get("projectName_dictText") or "未知项目",
        work_type=mapped_wt,
        work_name=raw_job_type or params.get("jobType_dictText") or "质量检查表",
        contractor="",
        inspector="",
        inspect_date=None,
    )


# ---------- 启动生命周期 ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=== PCM Agent Demo 启动 ===")
    ensure_kb_built()
    storage.ensure_tables()
    yield
    print("=== PCM Agent Demo 关闭 ===")


app = FastAPI(
    title="PCM Agent Demo API",
    description="ReAct + Tools Agent Demo —— 风险识别 / 质量检查",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Agent 实例（共享） ----------
risk_agent = RiskIdentificationAgent()
quality_agent = QualityCheckAgent()


# ---------- 路由 ----------
@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/api/agents/risk-identification/run", response_model=AgentRunResponse)
async def run_risk_agent(inp: RiskIdentificationInput):
    try:
        output, trace = risk_agent.run(inp)
        rid = storage.save_risk(inp, output)
        resp = AgentRunResponse(
            agent_id="risk-identification",
            success=True,
            output=output,
            trace=trace,
        )
        # 把记录 id 塞到 trace 里方便前端回查
        resp.trace["record_id"] = rid
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/risk-identification/run/stream")
async def run_risk_stream(inp: RiskIdentificationInput):
    """SSE 流式输出：进度事件 + 最终结果，结束后自动保存"""
    def gen():
        last_result = None
        for evt in risk_agent.run_stream(inp):
            if evt.get("event") == "result" and evt.get("success"):
                last_result = evt
            yield _sse(evt)
        # result 事件发出后保存
        if last_result:
            try:
                rid = storage.save_risk(inp, last_result.get("output", {}))
                yield _sse({"event": "saved", "record_id": rid})
            except Exception:
                pass
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/agents/quality-check/run", response_model=AgentRunResponse)
async def run_quality_agent(inp: QualityCheckInput):
    try:
        output, trace = quality_agent.run(inp)
        rid = storage.save_quality(inp, output)
        resp = AgentRunResponse(
            agent_id="quality-check",
            success=True,
            output=output,
            trace=trace,
        )
        resp.trace["record_id"] = rid
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/quality-check/run/stream")
async def run_quality_stream(inp: QualityCheckInput, request: Request):
    """SSE 流式输出：进度事件 + 最终结果，结束后自动保存。
    可选通过 header X-External-Id 或 query ?external_id=xxx 去重缓存。"""
    external_id = request.headers.get("X-External-Id") or request.query_params.get("external_id") or None

    # 内存级 "生成中" tracker — 让 query 接口能返回 generating 状态
    if external_id:
        with _GENERATING_LOCK:
            _GENERATING.add(external_id)

    def gen():
        last_result = None
        try:
            for evt in quality_agent.run_stream(inp):
                if evt.get("event") == "result" and evt.get("success"):
                    last_result = evt
                yield _sse(evt)
            if last_result:
                try:
                    rid = storage.save_quality(inp, last_result.get("output", {}), external_id)
                    yield _sse({"event": "saved", "record_id": rid, "external_id": external_id})
                except Exception as e:
                    import sys
                    print(f"[storage] save_quality failed: {e}", file=sys.stderr)
                    yield _sse({"event": "error", "message": f"保存失败: {e}"})
        finally:
            if external_id:
                with _GENERATING_LOCK:
                    _GENERATING.discard(external_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- SSE 格式化 ----------
def _sse(evt: dict) -> str:
    """把一个事件 dict 格式化为 SSE 协议字符串"""
    event_type = evt.pop("event", "message")
    data = json.dumps(evt, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


# ---------- 持久化查询 ----------
@app.get("/api/records/risk")
async def list_risk(limit: int = 50):
    """风险识别历史列表（轻量，不含 output）"""
    return storage.list_risk(limit)


@app.get("/api/records/risk/{rid}")
async def get_risk(rid: int):
    """风险识别历史详情（完整 input + output）"""
    d = storage.get_risk(rid)
    if not d:
        raise HTTPException(404, "record not found")
    return d


@app.get("/api/records/quality")
async def list_quality(limit: int = 50):
    """质量检查历史列表"""
    return storage.list_quality(limit)


@app.get("/api/records/quality/{rid}")
async def get_quality(rid: int):
    """质量检查历史详情"""
    d = storage.get_quality(rid)
    if not d:
        raise HTTPException(404, "record not found")
    return d


# ---------- 集成方：URL 参数驱动的查询接口 ----------
@app.get("/api/checklist/query")
async def checklist_query(
    id: str = Query(..., description="集成方业务唯一标识"),
    projectName_dictText: Optional[str] = None,
    projectCategory_dictText: Optional[str] = None,
    projectType_dictText: Optional[str] = None,
    jobType_dictText: Optional[str] = None,
    jobSite: Optional[str] = None,
    jobCount: Optional[str] = None,
):
    """查询质量检查表是否已生成。传参和 checklist.html URL 参数一致。
    
    返回:
      - status: "found" → 已生成，data 包含完整 checklist
      - status: "generating" → 正在生成中，前端应轮询等待
      - status: "not_found" → 未开始生成，前端应自动触发
    """
    found = storage.find_quality_by_external_id(id)
    input_meta = {
        "id": id,
        "projectName_dictText": projectName_dictText,
        "projectCategory_dictText": projectCategory_dictText,
        "projectType_dictText": projectType_dictText,
        "jobType_dictText": jobType_dictText,
        "jobSite": jobSite,
        "jobCount": jobCount,
        "mapped_work_type": map_job_type(jobType_dictText),
    }
    if found:
        return {
            "status": "found",
            "data": found["output"],
            "input_meta": found["input"],
            "created_at": found["created_at"],
            "record_id": found["id"],
        }
    # 内存级 "正在生成" 状态
    with _GENERATING_LOCK:
        is_generating = id in _GENERATING
    if is_generating:
        return {
            "status": "generating",
            "data": None,
            "input_meta": input_meta,
            "created_at": None,
            "record_id": None,
        }
    return {
        "status": "not_found",
        "data": None,
        "input_meta": input_meta,
        "created_at": None,
        "record_id": None,
    }


# ---------- 静态：embed.html ----------
def _static_html(name: str) -> FileResponse:
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "frontend" / name
    return FileResponse(str(p), media_type="text/html")

@app.get("/embed.html")
async def embed():
    return _static_html("embed.html")

@app.get("/risk.html")
async def risk_page():
    """iframe 嵌入页 1：风险识别独立页"""
    return _static_html("risk.html")

@app.get("/quality.html")
async def quality_page():
    """iframe 嵌入页 2：质量检查独立页"""
    return _static_html("quality.html")

@app.get("/checklist.html")
async def checklist_page():
    """iframe 嵌入页 3：AI 生成质量检查表弹窗页"""
    return _static_html("checklist.html")

@app.get("/questions.html")
async def questions_html():
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "待确认问题清单.html"
    return FileResponse(str(p), media_type="text/html")

@app.get("/DELIVERY.html")
async def delivery_html():
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "DELIVERY.html"
    return FileResponse(str(p), media_type="text/html")
