"""Agent#3 —— 质量检查（吊装/高处/动土作业）"""
from __future__ import annotations
import json
from ..models import QualityCheckInput
from ..runtime.agent_base import BaseAgent
from ..runtime.tools import (
    make_retrieve_knowledge_tool,
    make_get_table_schema_tool,
)


class QualityCheckAgent(BaseAgent):
    agent_id = "quality-check"
    progress_steps = [
        "解析作业输入信息",
        "匹配历史检查表样",
        "AI 生成 质量检查表",
        "AI 计算 合格率统计",
        "后处理 结果规范化",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.register_tool(make_retrieve_knowledge_tool())
        self.register_tool(make_get_table_schema_tool())

    def system_prompt(self, user_input: QualityCheckInput) -> str:
        wt = user_input.work_type if hasattr(user_input, "work_type") else ""

        common = {
            "吊装": [
                "起重机械在检验有效期内，司机/指挥持证（特种作业证）",
                "吊装前检查索具（钢丝绳、卸扣）无断丝/磨损超标",
                "吊点位置符合方案，构件绑扎牢固",
                "支腿全伸、地面坚实，必要时铺垫钢板",
                "作业半径内禁止无关人员进入，设警戒",
                "风速超 6 级、能见度低、夜间无照明时禁止吊装",
                "大型/异形构件编制专项吊装方案并交底",
                "试吊：起吊 10-20cm 停留 5 分钟检查稳定性",
            ],
            "高处": [
                "2m 以上作业系挂安全带，高挂低用，挂点牢固",
                "临边/孔洞设 1.2m 防护栏杆 + 18cm 挡脚板 + 安全网",
                "脚手架验收合格挂牌，搭设符合规范（扫地杆、剪刀撑、连墙件）",
                "作业面脚手板满铺，无探头板",
                "交叉作业设水平隔离层（安全网/防护棚）",
                "作业人员定期体检，高血压/贫血/晕高者禁止高空作业",
                "雨雪天气、6 级以上大风停止高处作业",
                "随身携带的工具放工具袋，禁止抛掷",
            ],
            "动土": [
                "必须办理动土作业票，明确地下管线（附探测报告）",
                "作业前用探铲或仪器探明地下管线位置和深度",
                "沟槽开挖深度 ≥1.2m 设防护栏杆；≥2m 设支护或放坡",
                "沟槽边 1m 内严禁堆载（土、设备、材料）",
                "沟槽深度 ≥1.5m 设上下通道（爬梯或坡道）",
                "作业区域设警示标识，夜间挂红灯警示",
                "发现异常（涌水、裂缝、管线暴露）立即停工上报",
                "多人作业保持安全距离（≥2m），专人监护",
            ],
        }
        base_points = common.get(wt, [
            "作业人员持证上岗",
            "作业票 / 许可证齐全有效",
            "设备工具检查合格在有效期内",
            "安全防护用品规范佩戴",
            "作业区域警戒标识到位",
            "专项方案已审批并交底",
        ])

        return f"""你是 PCM 项目质量检查 Agent。请为 [{wt}] 作业生成质量检查表。

## 该作业类型的常见检查点（保底知识）
{chr(10).join(f'{i+1}. {x}' for i, x in enumerate(base_points))}

## 输出要求
只输出一个 JSON 对象，不要 markdown 包裹，结构：
{{
  "checklist": [
    {{"seq":1,"item":"","standard":"","is_conforming":true,"detail":""}}
  ],
  "conforming_items": 数字,
  "non_conforming_items": 数字,
  "suggestions": ["建议1","建议2"]
}}

## 规则
- checklist 至少 6 项，建议 8-10 项
- standard 字段引用具体规范/方案条款
- Demo 阶段可合理假设 is_conforming（不用真的去现场查），但要给出现场判断逻辑
- conforming_items 和 non_conforming_items 必须与 checklist 一致
"""

    def build_user_message(self, user_input: QualityCheckInput) -> str:
        d = user_input.model_dump(exclude_none=True)
        wt = d.get("work_type", "")

        self.trace.thinking.append(f"🔍 检索 [{wt}] 质量标准...")
        knowledge = self.call_tool("retrieve_knowledge", query=f"{wt} 质量 检查 标准 规范", top_k=4)

        self.trace.thinking.append("📋 获取检查表 schema...")
        schema = self.call_tool("get_table_schema", table_name="quality_checklist")

        return f"""## 作业参数
```json
{json.dumps(d, ensure_ascii=False, indent=2)}
```

## 从项目施工方案检索到的知识
{knowledge}

## 检查表 Schema
{schema}

## 任务
请结合上面的作业参数 + 项目-specific 知识 + 保底检查点，生成至少 6 项质量检查表。
只输出 JSON 对象。"""

    def finalize_output(self, raw, user_input: QualityCheckInput) -> dict:
        data = raw if isinstance(raw, dict) else {}
        wt = user_input.work_type if hasattr(user_input, "work_type") else ""

        raw_list = data.get("checklist") or []
        normalized = []
        for i, r in enumerate(raw_list):
            if not isinstance(r, dict):
                continue
            # is_conforming → √ / × / -（系统方原型格式）
            ic = r.get("is_conforming", True)
            if ic == "-" or ic == "N/A" or str(ic).lower() == "na":
                result = "-"
            elif ic:
                result = "√"
            else:
                result = "×"
            normalized.append({
                "seq": int(r.get("seq", i + 1)),
                "item": r.get("item") or r.get("安全措施描述") or r.get("检查项") or "",
                "result": result,
                "standard": r.get("standard") or r.get("规范/依据") or "",
            })

        # 兜底
        if len(normalized) < 6:
            normalized = self._default_checklist(wt)

        conforming = sum(1 for c in normalized if c["result"] == "√")
        non_conforming = sum(1 for c in normalized if c["result"] == "×")
        na_count = sum(1 for c in normalized if c["result"] == "-")

        # 计算 pass_rate（排除 N/A 项）
        total = conforming + non_conforming
        pass_rate = None
        if total > 0:
            pct = round(conforming * 100 / total)
            pass_rate = f"{pct}%"

        suggestions = data.get("suggestions") or self._default_suggestions(non_conforming)

        # 顶部检查表信息区 — 从 user_input 回填
        d = user_input.model_dump(exclude_none=True)
        import datetime
        today = d.get("inspect_date") or datetime.date.today().isoformat()
        check_header = {
            "project": d.get("project", ""),
            "work_type": wt,
            "work_name": d.get("work_name", ""),
            "contractor": d.get("contractor", ""),
            "inspector": d.get("inspector", ""),
            "inspect_date": today,
        }

        return {
            "checklist": normalized,
            "check_header": check_header,
            "conforming_items": conforming,
            "non_conforming_items": non_conforming,
            "not_applicable_items": na_count,
            "pass_rate": pass_rate,
            "work_type": wt,
            "generated_by": "PCM作业数据 × 安全规则库",
            "suggestions": suggestions,
        }

    def _default_checklist(self, work_type: str) -> list[dict]:
        """兜底模板，按系统方原型具体安全措施风格"""
        templates = {
            "临时用电": [
                ("作业人员身体条件和着装符合要求，携带必要的工具装备及安全绳。", "√", "-"),
                ("在有可能散布有毒有害气体的场所作业，作业人员携带正压空气呼吸器和便携式报警仪等相关安防器材。", "√", "-"),
                ("安全标志、工具、仪表、电气设施和各种设备确认完好。", "√", "-"),
                ("规范使用的安全带、安全绳符合安全规定。", "√", "-"),
                ("30米以上高处作业，作业人员已配备通信联络工具。", "√", "-"),
                ("作业平台、高空作业车、吊篮、梯子、挡板、跳板等符合安全规定。", "√", "-"),
                ("现场搭设的脚手架、安全网、围栏等符合安全规定，并验收合格挂牌。", "×", "-"),
                ("轻型棚的承重架、柱能承受作业过程中最大负荷的要求。", "√", "-"),
                ("安全带不系挂在移动或不牢固的物件上；无可靠挂点时，已设置符合标准要求的挂点装置、生命线。", "√", "-"),
                ("雨天和雪天作业时，已采取可靠的防滑、防雨淋措施。", "√", "-"),
                ("高处动火作业时，已采取防火火花飞溅措施。", "√", "-"),
            ],
            "深基坑": [
                ("作业前已进行专项技术交底，明确支护参数和开挖顺序。", "√", "-"),
                ("基坑周边2m内无堆载，3m内限制重型车辆通行。", "√", "-"),
                ("支护结构位移、沉降监测点已布置，数据在安全阈值内。", "√", "-"),
                ("分层开挖深度不超过2m，先撑后挖。", "√", "-"),
                ("毗邻地下管线已探明并采取保护措施。", "√", "-"),
                ("坑口设1.2m高防护栏杆和警示标志。", "×", "-"),
                ("降排水系统运行正常，水位降至坑底以下0.5m。", "√", "-"),
                ("应急物资（沙袋、水泵、钢管、警戒带）已备足。", "√", "-"),
            ],
            "吊装": [
                ("起重机械在检验有效期内，司机/指挥持证上岗。", "√", "-"),
                ("索具（钢丝绳、卸扣）起吊前已逐一检查无断丝磨损。", "√", "-"),
                ("吊点位置符合方案，构件绑扎牢固。", "√", "-"),
                ("支腿全伸、地面坚实，已铺垫钢板。", "√", "-"),
                ("作业半径内已设警戒隔离，禁止无关人员进入。", "×", "-"),
                ("试吊程序执行：起吊10-20cm停留5分钟检查稳定。", "√", "-"),
                ("风速不超6级、能见度良好、有足够照明。", "√", "-"),
                ("大型/异形构件已编制专项吊装方案并交底。", "√", "-"),
            ],
            "动土": [
                ("动土作业票已办理且在有效期内。", "√", "-"),
                ("地下管线已用探铲或仪器探明位置和深度。", "√", "-"),
                ("沟槽深度≥1.2m已设防护栏杆；≥2m已支护或放坡。", "√", "-"),
                ("沟槽边1m内无堆载（土、设备、材料）。", "√", "-"),
                ("沟槽深度≥1.5m已设上下通道（爬梯/坡道）。", "×", "-"),
                ("作业区域已设警示标识，夜间挂红灯警示。", "√", "-"),
                ("发现异常（涌水、裂缝、管线暴露）已停工上报。", "√", "-"),
                ("专人全程监护。", "√", "-"),
            ],
        }
        base = templates.get(work_type, [
            ("作业票/许可证齐全有效。", "√", "-"),
            ("作业人员持证上岗，身体条件和着装符合要求。", "√", "-"),
            ("安全标志、工具、仪表、设备确认完好。", "√", "-"),
            ("安全防护用品规范佩戴（安全带、安全帽等）。", "√", "-"),
            ("作业区域已设警戒隔离和警示标识。", "×", "-"),
            ("专项施工方案已审批并交底。", "√", "-"),
        ])
        result = []
        for i, (item, res, std) in enumerate(base, 1):
            result.append({"seq": i, "item": item, "result": res, "standard": std})
        return result

    @staticmethod
    def _default_suggestions(nc: int) -> list[str]:
        if nc == 0:
            return ["整体质量管控良好，保持现有巡检频率和标准。"]
        return [
            f"本次检查发现 {nc} 项不合格，建议立即整改后重新报验。",
            "加强班前安全教育，重点强调个体防护和作业票制度。",
            "完善巡检记录台账，落实整改责任人及完成时限。",
            "对分包单位加强专项交底，明确质量管理红线。",
        ]
