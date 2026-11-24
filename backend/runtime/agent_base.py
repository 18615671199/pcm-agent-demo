"""Agent Runtime —— 同步工具链 + LLM 生成

两种运行模式：
  1. run(user_input) → 同步返回 (output, trace)  —— 向后兼容
  2. run_stream(user_input) → yield 进度事件 dict  —— SSE 流式输出
"""
from __future__ import annotations
import time, threading, queue
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterator

from ..models import AgentTrace, ToolStep
from .llm import LLMClient


class Tool:
    """Agent 可调用的工具"""
    def __init__(self, name: str, description: str, fn: Callable[..., Any]):
        self.name = name
        self.description = description
        self.fn = fn

    def run(self, **kwargs) -> Any:
        return self.fn(**kwargs)


# ========== 通用进度步骤文案（给子类默认用） ==========
RISK_STEPS = [
    "解析作业输入信息",
    "匹配历史知识库",
    "AI 生成 风险辨识表",
    "AI 生成 JSA LEC 法",
    "AI 生成 应急预案",
    "后处理 LEC 自动计算",
]

QUALITY_STEPS = [
    "解析作业输入信息",
    "匹配历史检查表样",
    "AI 生成 质量检查表",
    "AI 计算 合格率统计",
    "后处理 结果规范化",
]


class BaseAgent(ABC):
    """轻量 Agent：同步工具链 + 单次 LLM 调用"""

    agent_id: str = "base"
    progress_steps: list[str] = RISK_STEPS  # 子类可覆盖

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()
        self.tools: dict[str, Tool] = {}
        self.trace = AgentTrace(agent_id=self.agent_id)
        self.start_time: float = 0.0

    # ========== 子类实现 ==========
    @abstractmethod
    def system_prompt(self, user_input: Any) -> str: ...

    @abstractmethod
    def build_user_message(self, user_input: Any) -> str: ...

    @abstractmethod
    def finalize_output(self, raw: Any, user_input: Any) -> dict: ...

    # ========== Tool 注册 ==========
    def register_tool(self, tool: Tool):
        self.tools[tool.name] = tool

    def call_tool(self, name: str, **kwargs) -> Any:
        """调用 tool 并自动记录 trace step"""
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' not registered")
        tool = self.tools[name]
        t0 = time.time()
        self.trace.thinking.append(f"🔧 调用 Tool: {name}({kwargs})")
        try:
            result = tool.run(**kwargs)
            success = True
        except Exception as e:
            result = f"[Error: {e}]"
            success = False
        duration = int((time.time() - t0) * 1000)
        self.trace.steps.append(ToolStep(
            tool_name=name,
            input_summary=str(kwargs)[:200],
            output_summary=str(result)[:300],
            duration_ms=duration,
            success=success,
        ))
        return result

    # ========== 模式 1：同步运行 ==========
    def run(self, user_input: Any) -> tuple[dict, AgentTrace]:
        for _ in self.run_stream(user_input):
            pass  # 消耗完 generator，进度事件丢弃
        return self._last_output, self._last_trace

    # ========== 模式 2：SSE 流式 ==========
    def run_stream(self, user_input: Any) -> Iterator[dict]:
        """yield 进度事件 dict，格式：
        {"event":"progress","step":1,"total":6,"msg":"匹配知识库","detail":"深基坑开挖风险"}
        {"event":"tool","name":"retrieve_knowledge","msg":"知识库检索","detail":"深基坑开挖风险"}
        {"event":"llm_start","msg":"AI 推理中（约 30 秒）"}
        {"event":"llm_done","duration_ms":30500,"output_chars":1200}
        {"event":"result","success":true,"output":{...},"trace":{...}}
        {"event":"error","message":"..."}
        """
        self.start_time = time.time()
        self.trace = AgentTrace(agent_id=self.agent_id)
        total = len(self.progress_steps)
        self._last_output = {}
        self._last_trace = self.trace

        def progress(step_idx: int, msg: str, detail: str = ""):
            yield {"event": "progress", "step": step_idx, "total": total, "msg": msg, "detail": detail}

        try:
            # Step 1: 解析输入
            yield from progress(1, self.progress_steps[0], "解析作业参数")
            system = self.system_prompt(user_input)

            # Step 2: 检索知识库（子类 build_user_message 内部会 call_tool）
            yield from progress(2, self.progress_steps[1], "调用知识库检索...")
            # monkey-patch call_tool 来自动 emit tool 事件
            orig_call_tool = self.call_tool
            def traced_tool(name, **kwargs):
                yield {"event": "tool", "name": name, "msg": f"调用工具 {name}", "detail": str(kwargs)[:100]}
                result = orig_call_tool(name, **kwargs)
                yield {"event": "tool_done", "name": name, "msg": f"工具 {name} 完成"}
                return result
            self.call_tool = traced_tool  # type: ignore[assignment]
            try:
                user_msg = self.build_user_message(user_input)
            finally:
                self.call_tool = orig_call_tool  # 恢复

            # Step 3: LLM 推理 —— 后台线程跑 LLM，主线程边等边发心跳（每 15s 一次）
            yield from progress(3, self.progress_steps[2], "AI 深度分析中，这步最耗时...")
            yield {"event": "llm_start", "msg": "AI 推理中（约 30-60 秒）"}

            t0 = time.time()
            result_q: "queue.Queue" = queue.Queue(maxsize=1)  # 放 LLM 结果或异常

            def _llm_worker():
                try:
                    r = self.llm.call_for_json(system, user_msg, max_retries=0)
                    result_q.put(("ok", r))
                except Exception as e:
                    result_q.put(("err", e))

            llm_thread = threading.Thread(target=_llm_worker, daemon=True)
            llm_thread.start()

            # 边等边发心跳
            while llm_thread.is_alive():
                if result_q.empty():
                    # 等 15 秒，看 LLM 完没完
                    llm_thread.join(timeout=15)
                    if llm_thread.is_alive():
                        yield {"event": "ping", "msg": f"AI 仍在深度思考中，已耗时 {int(time.time()-t0)}秒..."}
                else:
                    break

            # 拿结果
            status, payload = result_q.get_nowait()
            llm_ms = int((time.time() - t0) * 1000)
            if status == "ok":
                raw = payload
                self.trace.thinking.append("✅ LLM 返回 JSON")
                yield {"event": "llm_done", "duration_ms": llm_ms, "output_chars": len(str(raw))}
            else:
                self.trace.thinking.append(f"⚠️ LLM 异常: {payload}")
                yield {"event": "error", "message": f"AI 推理失败：{payload}"}
                raw = {}

            self.trace.steps.append(ToolStep(
                tool_name="llm_infer",
                input_summary=f"system={len(system)}ch, user={len(user_msg)}ch",
                output_summary=f"keys={list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}",
                duration_ms=int((time.time() - t0) * 1000),
                success=bool(raw),
            ))

            # Step 4: 后续步骤（占位进度）
            for i in range(4, total):
                yield from progress(i, self.progress_steps[i], "后处理中...")

            # Step Final: 规范化输出
            output = self.finalize_output(raw, user_input)
            self.trace.total_duration_ms = int((time.time() - self.start_time) * 1000)

            self._last_output = output
            self._last_trace = self.trace

            # 最终 result 事件
            yield {"event": "result", "success": True, "output": output, "trace": self.trace.model_dump()}

        except Exception as e:
            yield {"event": "error", "message": str(e)}
            raise


# ========== 子类覆盖 progress_steps ==========
class _RiskAgentMixin:
    progress_steps = RISK_STEPS


class _QualityAgentMixin:
    progress_steps = QUALITY_STEPS
