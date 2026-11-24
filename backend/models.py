"""Pydantic 输入输出模型"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


# ==================== 可观测性 Trace ====================

class ToolStep(BaseModel):
    """单次 Tool 调用记录"""
    tool_name: str
    input_summary: str
    output_summary: str
    duration_ms: int
    success: bool = True


class AgentTrace(BaseModel):
    """Agent 执行 step-by-step trace"""
    agent_id: str
    steps: list[ToolStep] = Field(default_factory=list)
    total_duration_ms: int = 0
    thinking: list[str] = Field(default_factory=list)  # 给前端展示的思考过程


# ==================== Agent#1 风险识别 ====================

class RiskIdentificationInput(BaseModel):
    """风险识别 Agent 输入 — 按系统方实际业务字段"""
    project: str = Field(..., description="项目名称")
    project_unit: Optional[str] = Field(None, description="项目单位")
    work_type: str = Field("深基坑", description="作业类型（深基坑/非开挖/吊装/高处/动土）")
    work_name: str = Field(..., description="作业名称（短名称，如'吊机就位'）")
    work_task_desc: Optional[str] = Field(None, description="工作任务简述（长文本，描述作业步骤）")
    work_location: Optional[str] = Field(None, description="作业位置")
    contractor: Optional[str] = Field(None, description="作业单位（施工单位）")
    project_id: Optional[str] = Field(None, description="项目编号")
    work_environment: Optional[str] = Field(None, description="作业环境（地貌、地质、地下水等）")
    technical_requirements: Optional[str] = Field(None, description="技术要求（深度、宽度、长度、穿越参数等）")
    equipment: Optional[str] = Field(None, description="施工设备（多选用顿号分隔）")


class RiskRow(BaseModel):
    seq: int = Field(..., description="序号")
    hazard_name: str = Field(..., description="危险名称")
    hazard_level: str = Field(..., description="危险级别（一般/较大/重大）")
    risk_cause: str = Field(..., description="风险产生原因")
    prevention: str = Field(..., description="预防措施")


class JSARow(BaseModel):
    """JSA LEC 法 10 列 — 系统方 PDF 正式模板"""
    seq: int = Field(..., description="编号")
    work_step: str = Field(..., description="工作步骤")
    hazard_factor: str = Field(..., description="危害因素描述")
    likelihood: int = Field(..., ge=1, le=5, description="可能性 L (1-5)")
    exposure: int = Field(..., ge=1, le=5, description="暴露频率 E (1-5)")
    consequence: int = Field(..., ge=1, le=5, description="严重度 C (1-5)")
    lec_score: int = Field(..., description="风险值 L×E×C（自动计算）")
    existing_control: str = Field(..., description="现有控制措施")
    further_improvement: str = Field(..., description="进一步改进措施")
    remaining_risk_acceptable: str = Field(..., description="剩余风险是否可接受（是/否）")


class JSAHeader(BaseModel):
    """JSA 顶部作业信息区 — 系统方正式模板"""
    project: str = Field("", description="项目名称")
    work_type: str = Field("", description="作业类型")
    work_location: str = Field("", description="作业位置")
    contractor: str = Field("", description="施工单位")
    work_leader: str = Field("", description="作业负责组长（可空）")
    jsa_analyst: str = Field("", description="JSA 分析人员（可空）")
    analyze_date: str = Field("", description="分析日期 YYYY-MM-DD")


class RiskIdentificationOutput(BaseModel):
    risk_table: list[RiskRow]
    jsa_table: list[JSARow]
    jsa_header: Optional[JSAHeader] = None
    jsa_type_flags: Optional[dict[str, bool]] = Field(None, description="JSA 作业类型勾选框")
    emergency_plan: str


# ==================== Agent#3 质量检查 ====================

class QualityCheckInput(BaseModel):
    """质量检查 Agent 输入 — 按系统方实际业务字段"""
    project: str = Field(..., description="项目名称")
    work_type: str = Field(..., description="作业类型（深基坑/非开挖/吊装/高处/动土）")
    work_name: str = Field(..., description="作业名称")
    contractor: Optional[str] = Field(None, description="施工单位")
    inspector: Optional[str] = Field(None, description="检查人")
    inspect_date: Optional[str] = Field(None, description="检查日期（YYYY-MM-DD）")


class CheckItem(BaseModel):
    """质量检查表项 — 系统方原型格式"""
    seq: int
    item: str = Field(..., description="安全措施描述 / 检查项")
    result: str = Field(..., pattern="^(√|×|-)$", description="检查结果：√通过 / ×未通过 / -不涉及")
    standard: str = Field(..., description="规范/依据")


class QualityCheckOutput(BaseModel):
    checklist: list[CheckItem]
    conforming_items: int = 0
    non_conforming_items: int = 0
    not_applicable_items: int = 0
    pass_rate: Optional[str] = None   # 合格率百分比，如 "83%"
    work_type: str = ""
    generated_by: str = "PCM作业数据 × 安全规则库"
    suggestions: list[str] = Field(default_factory=list)


# ==================== 统一响应 ====================

class AgentRunResponse(BaseModel):
    agent_id: str
    success: bool
    output: Optional[dict[str, Any]] = None
    trace: AgentTrace
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
