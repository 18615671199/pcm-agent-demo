"""Agent#1 —— 风险识别（按作业类型差异化，对齐系统方真实表样）"""
from __future__ import annotations
import json
from ..models import RiskIdentificationInput
from ..runtime.agent_base import BaseAgent
from ..runtime.tools import (
    make_retrieve_knowledge_tool,
    make_get_table_schema_tool,
    make_validate_output_tool,
)

# ========== 作业类型 → 风险类别映射（让不同作业出不同结果）==========
WORK_TYPE_RISKS = {
    "深基坑": {
        "categories": ["坍塌类", "水害类", "机械类", "环境类", "人员类", "爆炸类", "结构类", "监测类"],
        "examples": [
            ("坍塌类", "支护结构失稳 / 土体滑塌 / 超挖 / 临近建筑沉降 / 支撑拆除顺序错误"),
            ("水害类", "涌水 / 涌砂 / 降水不充分 / 侧壁渗漏 / 承压水突涌 / 雨水倒灌"),
            ("机械类", "挖掘机碰撞 / 吊车倾覆 / 交叉作业伤害 / 推土机碾压"),
            ("环境类", "毗邻管线破损（天然气、自来水、通信）/ 地下水位突变 / 地面沉降"),
            ("人员类", "高处坠落 / 物体打击 / 缺氧中毒（密闭空间）/ 中暑 / 触电"),
            ("爆炸类", "毗邻油气管道施工引发爆炸 / 气体泄漏"),
            ("结构类", "支撑体系强度不足 / 冠梁开裂 / 桩顶位移超限"),
            ("监测类", "监测点布置不足 / 数据采集滞后 / 预警机制失效"),
        ],
    },
    "非开挖": {
        "categories": ["管线类", "地层类", "机械类", "环境类", "人员类", "电力类", "穿越类"],
        "examples": [
            ("管线类", "穿越时损坏既有管线（天然气、自来水、电力、通信光缆）/ 回拖时管道拉伤破裂"),
            ("地层类", "导向孔偏位 / 扩孔坍塌 / 泥浆循环中断 / 冒浆污染路面"),
            ("机械类", "钻机倾覆 / 泥浆泵高压管爆裂 / 钻杆断裂飞出 / 回拖力过大断裂"),
            ("环境类", "泥浆污染土壤水体 / 施工区域地面沉陷 / 扰民（噪音、扬尘）"),
            ("人员类", "钻机旁作业人员卷入 / 高压泥浆溅射伤害 / 触电 / 机械碰撞"),
            ("电力类", "穿越高压电缆 / 钻机接地不良触电 / 夜间施工照明不足"),
            ("穿越类", "穿越交通繁忙路段影响通行安全 / 穿越河流汛期水位上涨 / 穿越铁路航空管制区"),
        ],
    },
    "吊装": {
        "categories": ["设备类", "环境类", "操作类", "天气类", "人员类", "信号类"],
        "examples": [
            ("设备类", "吊机倾覆 / 吊臂断裂 / 钢丝绳索具断裂 / 液压系统失效"),
            ("环境类", "高压线附近作业触电 / 吊装半径内障碍物 / 地基承载力不足塌陷"),
            ("操作类", "超额定吊重 / 斜拉硬拽 / 物体摆动失控 / 就位时手指挤压"),
            ("天气类", "大风超过6级 / 雷电 / 大雾视线不清 / 地面湿滑"),
            ("人员类", "高处坠落（高空作业）/ 物体打击 / 被吊物挤压 / 指挥盲区误入"),
            ("信号类", "信号工缺位或信号不清 / 对讲机没电干扰 / 夜间信号灯看不清"),
        ],
    },
    "高处": {
        "categories": ["坠落类", "物体打击", "防护类", "作业类", "环境类", "器具类"],
        "examples": [
            ("坠落类", "临边无防护 / 安全绳未系挂 / 脚手架不牢 / 操作平台超载"),
            ("物体打击", "工具材料坠落 / 上方扔东西 / 安全帽未戴 / 交叉作业无隔离"),
            ("防护类", "临边洞口未封闭 / 防护栏杆不达标 / 安全网破损 / 安全带低挂高用"),
            ("作业类", "高空作业人员体检不合格 / 酒后作业 / 疲劳作业 / 大风天气仍作业"),
            ("环境类", "夜间照明不足 / 高温中暑 / 冬季结冰打滑 / 高空坠冰"),
            ("器具类", "梯子不稳 / 吊篮钢丝绳断丝超标 / 脚手架扣件松动"),
        ],
    },
    "动土": {
        "categories": ["管线类", "坍塌类", "水害类", "机械类", "人员类", "爆破类", "防护类"],
        "examples": [
            ("管线类", "挖断天然气管道 / 挖断电力电缆 / 挖断自来水管道 / 挖断通信光缆"),
            ("坍塌类", "沟槽边坡失稳 / 挖掘机管沟坍塌 / 堆土离坑太近超载"),
            ("水害类", "地下水位高涌水 / 雨季雨水灌入 / 挖破自来水管泡水坍塌"),
            ("机械类", "挖掘机碰撞 / 装载机碾压 / 推土机倾覆 / 运输车辆倒车伤人"),
            ("人员类", "槽内作业被埋 / 机械碰撞 / 触电 / 塌方被埋"),
            ("爆破类", "爆破飞石伤人 / 震动影响建筑 / 炸药管理丢失 / 盲炮处理不当"),
            ("防护类", "沟槽周边无防护栏 / 夜间无警示灯 / 未设警戒区 / 开挖前未探挖"),
        ],
    },
}

DEFAULT_WORK_TYPE = {
    "categories": ["坍塌类", "水害类", "机械类", "环境类", "人员类"],
    "examples": [
        ("坍塌类", "作业面土体滑塌 / 作业平台失稳 / 支撑体系失效"),
        ("水害类", "地下水渗漏 / 雨水倒灌 / 降水不充分"),
        ("机械类", "施工机械碰撞 / 车辆伤害 / 高空坠物"),
        ("环境类", "毗邻管线破损 / 地面沉降 / 噪音扬尘扰民"),
        ("人员类", "高处坠落 / 物体打击 / 触电 / 中暑"),
    ],
}


class RiskIdentificationAgent(BaseAgent):
    agent_id = "risk-identification"
    progress_steps = [
        "解析作业输入信息",
        "匹配历史知识库",
        "AI 生成 风险辨识表",
        "AI 生成 JSA 分析表",
        "AI 生成 应急预案",
        "后处理 结果规范化",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.register_tool(make_retrieve_knowledge_tool())
        self.register_tool(make_get_table_schema_tool())
        self.register_tool(make_validate_output_tool())

    def system_prompt(self, user_input: RiskIdentificationInput) -> str:
        wt = user_input.work_type or "深基坑"
        wt_risks = WORK_TYPE_RISKS.get(wt, DEFAULT_WORK_TYPE)
        cats_str = "、".join(wt_risks["categories"])
        examples_str = "\n".join(f"  · {c}：{d}" for c, d in wt_risks["examples"])

        return f"""你是 PCM 项目风险识别专家 Agent。请严格按以下要求生成三张表的 JSON 输出。

## 当前作业类型
【{wt}】— 必须针对此作业类型的特点生成结果，不能用其他作业类型的通用内容！

## 本次作业的风险类别范围
{cats_str}

### 各风险类别常见场景参考
{examples_str}

## 重要：让结果更丰富的提示词策略
1. risk_table 至少 **8 条**，覆盖上述所有风险类别，每个类别至少 1 条
2. jsa_table 至少 **8 条**，每条对应 risk_table 中不同的风险点
3. emergency_plan 至少 **600 字**，分四段结构

## JSA 评分法：LEC 法
- 可能性 L：1=极不可能 / 2=不太可能 / 3=可能 / 4=很可能 / 5=几乎确定
- 暴露频率 E：1=罕见 / 2=偶尔 / 3=经常 / 4=频繁 / 5=持续
- 严重度 C：1=轻微 / 2=一般 / 3=严重 / 4=重大 / 5=特大
- 风险值 = L × E × C（自动计算）

## 风险辨识表危险级别分级
- 重大风险：LEC ≥ 160 或可能导致群死群伤 / 重大经济损失 / 重大环境影响
- 较大风险：LEC 70-159 或可能导致重大伤害 / 较大经济损失
- 一般风险：LEC 20-69 或可能导致一般伤害 / 轻微经济损失

## JSA 作业类型勾选（类型字段必填，勾选框用 true/false）
type_flags: {{
  "常规作业": true/false,
  "临时作业": true/false,
  "交叉作业": true/false,
  "承包作业": true/false,
  "办理作业许可": true/false,
  "新(首次)作业": true/false,
  "无操作规程作业": true/false,
  "变更规程作业": true/false,
  "特种作业人员作业": true/false
}}
请根据实际情况勾选（true/false），承包作业通常是 true。

## 输出格式
最终只输出一个 JSON 对象，不要 markdown 代码块，不要加任何解释文字。结构：
{{
  "risk_table": [
    {{"seq":1,"hazard_name":"","hazard_level":"一般/较大/重大","hazard_level_desc":"对风险等级的简要描述","risk_cause":"","prevention":""}}
  ],
  "jsa_table": [
    {{
      "seq":1,"work_step":"","hazard_factor":"",
      "likelihood":3,"exposure":2,"consequence":3,"lec_score":18,
      "existing_control":"","further_improvement":"",
      "remaining_risk_acceptable":"是/否"
    }}
  ],
  "emergency_plan": "四段结构（至少600字）：一、预防措施\\n二、应急响应流程\\n三、救援措施\\n四、事后处理"
}}

## 最低行数要求
- risk_table: 至少 8 行（覆盖所有风险类别）
- jsa_table: 至少 8 行
"""

    def build_user_message(self, user_input: RiskIdentificationInput) -> str:
        d = user_input.model_dump(exclude_none=True)
        wt = d.get("work_type", "深基坑")

        # 按作业类型检索不同知识
        self.trace.thinking.append(f"🔍 检索 {wt} 相关风险知识...")
        knowledge1 = self.call_tool("retrieve_knowledge", query=f"{wt} 风险 事故案例", top_k=6)
        knowledge2 = self.call_tool("retrieve_knowledge", query=f"{wt} 安全操作规程 风险辨识", top_k=4)

        self.trace.thinking.append("📋 获取表样 schema...")
        risk_schema = self.call_tool("get_table_schema", table_name="risk_table")
        jsa_schema = self.call_tool("get_table_schema", table_name="jsa_table")
        ep_schema = self.call_tool("get_table_schema", table_name="emergency_plan")

        return f"""## 作业参数
```json
{json.dumps(d, ensure_ascii=False, indent=2)}
```

**注意**：`work_task_desc`（工作任务简述）描述了本次作业的具体步骤，是风险辨识的关键依据，请重点参考。

## 从项目施工方案检索到的知识
### 检索 1：{wt} 风险案例
{knowledge1}

### 检索 2：{wt} 安全操作要点
{knowledge2}

## 表样 Schema
### risk_table
{risk_schema}

### jsa_table
{jsa_schema}

### emergency_plan
{ep_schema}

## 任务
请结合上面的作业参数 + 检索知识 + 当前作业类型【{wt}】的特点，输出三张表的 JSON。
**重要提示**：
1. 风险条数 ≥ 8，覆盖 prompt 里列出的所有风险类别
2. jsa_table 条数 ≥ 8，每行对应一个不同的风险点
3. emergency_plan ≥ 600 字，四段结构
4. 结果必须体现【{wt}】的专业特点，不能是放之四海而皆准的通用答案
只输出 JSON 对象，不要其他文字。"""

    def finalize_output(self, raw, user_input: RiskIdentificationInput) -> dict:
        data = raw if isinstance(raw, dict) else {}

        # risk_table — 保底 8 条
        risk_table = data.get("risk_table") or []
        normalized_risk = []
        for i, r in enumerate(risk_table):
            if not isinstance(r, dict):
                continue
            normalized_risk.append({
                "seq": int(r.get("seq", i + 1)),
                "hazard_name": r.get("hazard_name") or r.get("危险名称") or "",
                "hazard_level": r.get("hazard_level") or r.get("危险级别") or "一般",
                "hazard_level_desc": r.get("hazard_level_desc") or r.get("风险等级描述") or "",
                "risk_cause": r.get("risk_cause") or r.get("风险产生原因") or "",
                "prevention": r.get("prevention") or r.get("预防措施") or "",
            })

        # jsa_table — LEC 法 + 保底 8 条
        jsa_table = data.get("jsa_table") or []
        normalized_jsa = []
        for i, r in enumerate(jsa_table):
            if not isinstance(r, dict):
                continue
            lik = int(r.get("likelihood") or r.get("可能性") or r.get("L") or 3)
            exp = int(r.get("exposure") or r.get("暴露频率") or r.get("E") or 2)
            con = int(r.get("consequence") or r.get("严重度") or r.get("C") or 3)
            lik, exp, con = max(1, min(5, lik)), max(1, min(5, exp)), max(1, min(5, con))
            lec = lik * exp * con
            acceptable = "是" if lec <= 9 else "否"
            normalized_jsa.append({
                "seq": int(r.get("seq") or r.get("编号") or i + 1),
                "work_step": r.get("work_step") or r.get("工作步骤") or "",
                "hazard_factor": r.get("hazard_factor") or r.get("危害因素描述") or "",
                "likelihood": lik, "exposure": exp, "consequence": con,
                "lec_score": lec,
                "existing_control": r.get("existing_control") or r.get("现有控制措施") or "",
                "further_improvement": r.get("further_improvement") or r.get("进一步改进措施") or "",
                "remaining_risk_acceptable": r.get("remaining_risk_acceptable") or acceptable,
            })

        # 兜底模板 — 按 work_type 差异化
        wt = user_input.work_type or "深基坑"
        if len(normalized_risk) < 8:
            normalized_risk = self._fill_risk(normalized_risk, wt)
        if len(normalized_jsa) < 8:
            normalized_jsa = self._fill_jsa(normalized_jsa, wt)

        emergency_plan = data.get("emergency_plan") or ""
        if len(emergency_plan) < 500:
            emergency_plan = self._default_emergency_plan(wt)

        # jsa_header — 从 user_input 回填
        import datetime
        today = datetime.date.today().isoformat()
        d = user_input.model_dump(exclude_none=True)
        jsa_header_data = data.get("jsa_header") or {}
        jsa_header = {
            "project": jsa_header_data.get("project") or d.get("project", ""),
            "work_type": jsa_header_data.get("work_type") or wt,
            "work_location": jsa_header_data.get("work_location") or d.get("work_location", ""),
            "contractor": jsa_header_data.get("contractor") or d.get("contractor", ""),
            "work_leader": jsa_header_data.get("work_leader") or "",
            "jsa_analyst": jsa_header_data.get("jsa_analyst") or "",
            "analyze_date": jsa_header_data.get("analyze_date") or today,
        }

        # jsa_type_flags — LLM 输出或按作业类型推断
        raw_flags = data.get("jsa_type_flags") or {}
        type_flags_default = {
            "常规作业": True,
            "临时作业": False,
            "交叉作业": False,
            "承包作业": True,
            "办理作业许可": True,
            "新(首次)作业": False,
            "无操作规程作业": False,
            "变更规程作业": False,
            "特种作业人员作业": wt in ("吊装", "高处"),
        }
        jsa_type_flags = {k: raw_flags.get(k, v) for k, v in type_flags_default.items()}

        return {
            "risk_table": normalized_risk,
            "jsa_table": normalized_jsa,
            "jsa_header": jsa_header,
            "jsa_type_flags": jsa_type_flags,
            "emergency_plan": emergency_plan,
        }

    def _fill_risk(self, existing: list, wt: str) -> list:
        """按作业类型补齐风险条数到至少 8 条"""
        risk_pool = {
            "深基坑": [
                ("基坑坍塌", "重大", "支护结构失稳 / 超挖 / 支撑拆除顺序错误", "分层开挖、先撑后挖、实时监测、严禁超挖、按方案顺序拆除支撑"),
                ("涌水涌砂", "较大", "降水不到位 / 侧壁渗漏 / 承压水突涌 / 地下水位监测失效", "提前降水至坑底0.5m以下、加强巡查、备足沙袋水泵、监测水位"),
                ("毗邻管线破损（天然气）", "重大", "未探明地下管线 / 机械误挖 / 土体位移拉裂 / 悬吊装置失效", "施工前详细探挖、人工探沟、悬吊加固、安装泄漏报警、与燃气公司联动"),
                ("机械碰撞 / 碾压", "一般", "交叉作业指挥缺位 / 视线盲区 / 设备维护不良", "专人指挥、警戒区隔离、班前交底、定期检查设备"),
                ("高空坠落 / 物体打击", "一般", "临边无防护 / 坑边堆载 / 未戴安全帽安全带", "临边1.2m防护栏杆、坑边1m内不堆载、强制佩戴劳保、定期巡检"),
                ("缺氧中毒（密闭空间）", "一般", "深基坑内通风不良 / 有害气体积聚（沼气、CO）", "强制通风、气体检测、配备防毒面具和急救器材、设监护人员"),
                ("结构变形失稳", "较大", "支护桩位移超限 / 冠梁开裂 / 支撑体系强度不足", "加密监测频率、及时回填反压、注浆加固、增加监测点"),
                ("触电 / 用电伤害", "一般", "潜水泵电缆破损 / 配电箱接地不良 / 雨天作业", "电缆架空敷设、配电箱装漏电保护器、雨天断电作业、专人电工值守"),
            ],
            "非开挖": [
                ("穿越损坏既有管线（天然气）", "重大", "导向孔偏移 / 扩孔器刮蹭 / 回拖力过大拉伤", "施工前详细探挖、导向孔精度校准、关键管线悬吊保护、制定应急预案"),
                ("冒浆污染", "较大", "泥浆压力过高 / 地层空隙大 / 回拖时冒浆", "控制泥浆比重和压力、实时监测返浆、备足应急封堵器材、划定污染警戒区"),
                ("钻机倾覆", "较大", "地基承载力不足 / 支腿不稳 / 大风天气", "施工前地基加固、支腿垫板、6级以上大风停止作业、定期检查支腿"),
                ("钻杆断裂 / 飞出", "一般", "钻杆磨损超标 / 螺纹松动 / 扭矩超限", "班前检查钻杆、定期探伤、扭矩限制器维护、划定安全区"),
                ("回拖管道拉伤 / 破裂", "较大", "回拖力过大 / 扩孔孔径不足 / 管道壁厚不均", "扩孔孔径至少1.5倍管道外径、逐步增大回拖力、回拖前清孔、管材进场检验"),
                ("泥浆泵高压管爆裂", "一般", "高压管磨损 / 接头松动 / 压力超限", "高压管定期更换、接头紧固、装压力安全阀、划定禁区"),
                ("人员卷入钻机", "一般", "钻机旁作业人员安全距离不足 / 长发卷入 / 指挥盲区", "强制戴安全帽和工作服、长发盘起、安全距离≥3m、专人监护"),
                ("触电", "一般", "钻机接地不良 / 夜间照明不足 / 电缆破损", "钻机可靠接地、配电箱装漏电保护、夜间充足照明、架空敷设电缆"),
            ],
            "吊装": [
                ("吊机倾覆", "重大", "支腿不实 / 地基承载力不足 / 超额定吊重 / 大风天气", "地基加固、支腿垫板、严禁超载、6级以上大风停工、力矩限制器完好"),
                ("钢丝绳索具断裂", "较大", "钢丝绳磨损超标 / 索具脱扣 / 绑扎不当", "班前检查钢丝绳、定期探伤、索具年检、专人绑扎复核"),
                ("高压线附近触电", "重大", "吊装半径内高压线 / 吊臂摆动触碰 / 安全距离不足", "提前探明高压线、保持≥6m安全距离、专人监护、划定禁区"),
                ("人员高空坠落", "较大", "高空作业未系安全带 / 安全带低挂高用 / 操作平台不稳", "强制系安全带、高挂低用、平台满铺防滑板、临边防护"),
                ("物体打击", "较大", "吊物下方有人 / 交叉作业无隔离 / 安全帽未戴", "划定吊装警戒区、专人监护、戴安全帽、禁止交叉作业"),
                ("信号不清误操作", "一般", "信号工缺位 / 对讲机没电干扰 / 夜间信号不清", "持证信号工、对讲机备用电池、夜间信号灯、信号复核"),
                ("液压系统失效", "一般", "液压油泄漏 / 支腿伸缩不同步 / 液压阀损坏", "班前检查液压油、定期更换滤芯、备足密封件、液压系统年检"),
                ("斜拉硬拽", "一般", "吊物挂住障碍物 / 超角度起吊 / 指挥指令不当", "起吊前检查吊物、不得超60°、指挥员持证、禁止强行斜拉"),
            ],
            "动土": [
                ("挖断天然气管道", "重大", "未探明地下管线 / 机械误挖 / 人工探沟不足", "施工前详细探挖、人工探沟≥1.5m、管线标识明显、燃气公司到场"),
                ("沟槽坍塌", "较大", "边坡失稳 / 堆土离坑太近 / 深度超1.5m未支护", "放坡或支护、堆土离坑≥1m、深度超1.5m必须支护、每日检查边坡"),
                ("涌水 / 泡水坍塌", "较大", "地下水位高 / 挖破自来水管 / 雨季雨水灌入", "降水至坑底0.5m以下、备足水泵、开挖后及时支护、雨季停工"),
                ("机械碰撞 / 碾压", "一般", "交叉作业指挥缺位 / 视线盲区 / 倒车无人指挥", "专人指挥、划定作业警戒区、倒车专人监护、保持安全距离"),
                ("触电", "较大", "挖断电力电缆 / 钻机接地不良 / 配电箱漏电", "施工前探挖电缆、人工开挖探沟、电缆架空敷设、漏电保护器"),
                ("爆破飞石伤人", "重大", "装药量过大 / 警戒距离不足 / 盲炮处理不当", "持证爆破员、测算安全距离、飞石防护网、盲炮由专业人员处理"),
                ("沟槽内作业被埋", "较大", "边坡突然坍塌 / 降雨滑坡 / 堆土滑塌", "沟槽边设1.2m防护栏、降雨时撤离、专人监测、沟槽深度超2m设安全梯"),
                ("管线探测失效", "一般", "地下管线图不准 / 管线探测仪未校准 / 施工前未探挖", "施工前请管线单位交底、管线探测仪校准、开挖前人工探沟、保留探沟记录"),
            ],
            "高处": [
                ("临边坠落", "重大", "临边无防护 / 防护栏杆不达标 / 安全网破损", "临边设1.2m防护栏杆+挡脚板、防护栏刷红白漆、安全网密目封闭、每日巡检"),
                ("高空坠物", "较大", "工具未绑安全绳 / 交叉作业无隔离 / 下方无警戒区", "工具绑10cm安全绳、交叉作业设置隔离层、下方设警戒区专人监护、戴安全帽"),
                ("安全带失效", "较大", "安全带低挂高用 / 安全带磨损超期 / 未正确系挂", "强制高挂低用、安全带每年检验、挂点必须牢固、作业前检查"),
                ("脚手架坍塌", "重大", "脚手架搭设不合格 / 超载 / 扣件松动", "脚手架方案经审批、搭设后验收、严禁超载、定期检查扣件"),
                ("人员中暑", "一般", "高温天作业无防护 / 饮水供应不足 / 连续工作超时", "气温超37°C停止高空作业、备足防暑药品、轮流作业、设遮阳棚"),
                ("梯子不稳坠落", "一般", "梯子底部未固定 / 人字梯角度不当 / 梯子超期使用", "梯子底部有人扶住、人字梯夹角60-70°、定期检查梯子、不使用超期梯子"),
                ("吊篮钢丝绳断裂", "重大", "钢丝绳断丝超标 / 制动器失效 / 超载", "钢丝绳每3个月检查断丝、制动器每日检查、不超额定载重量、吊篮年检"),
                ("雷电 / 强风", "较大", "雷雨天气高空作业 / 大风天未停止 / 防雷装置失效", "雷雨、6级以上大风立即停止、检测防雷装置、转移至安全区域、设应急集结点"),
            ],
        }
        pool = risk_pool.get(wt, risk_pool.get("深基坑"))
        result = list(existing)
        used_names = {r["hazard_name"] for r in result}
        idx = len(result)
        for name, level, cause, prev in pool:
            if name in used_names:
                continue
            # hazard_level_desc 根据 level 自动补
            level_desc_map = {"一般": "一般风险，可接受，需常规防控", "较大": "较大风险，需要采取防控措施后才能作业", "重大": "重大风险，必须落实专项方案和管控措施"}
            desc = level_desc_map.get(level, "")
            result.append({"seq": idx + 1, "hazard_name": name, "hazard_level": level, "hazard_level_desc": desc, "risk_cause": cause, "prevention": prev})
            idx += 1
            if len(result) >= 8:
                break
        return result

    def _fill_jsa(self, existing: list, wt: str) -> list:
        """按作业类型补齐 JSA 到至少 8 条"""
        jsa_pool = {
            "深基坑": [
                ("基坑分层开挖", "土体坍塌、机械碰撞、支护结构失稳", 3, 3, 4),
                ("降水井施工与运行", "高空坠物、用电伤害、降水不充分", 2, 3, 3),
                ("支护结构安装（挡土桩/内支撑）", "钢管构件坠落、吊装伤害、连接松动", 2, 2, 4),
                ("管线保护悬吊", "管线拉裂泄漏、悬吊装置失效", 1, 1, 5),
                ("冠梁施工", "高处坠落、模板坍塌、钢筋绑扎受伤", 2, 2, 3),
                ("监测点埋设与观测", "监测数据不准、监测频率不足、预警失效", 2, 2, 2),
                ("夜间施工照明", "照明不足、眩光、人员误踩防护栏杆", 2, 2, 2),
                ("基坑底清理与验槽", "坍塌坠落、触电、缺氧", 2, 2, 3),
            ],
            "非开挖": [
                ("导向孔钻进", "钻机倾覆、钻杆断裂、导向偏位", 2, 2, 4),
                ("扩孔作业", "扩孔器刮蹭管线、泥浆压力过高、扩孔坍塌", 2, 2, 4),
                ("回拖管道", "回拖力过大断裂、拉伤既有管线、泥浆污染", 3, 3, 3),
                ("泥浆循环与处置", "泥浆泵高压管爆裂、泥浆污染土壤水体、钻机旁飞溅伤人", 2, 3, 3),
                ("穿越交通路段警戒", "警戒区被闯入、过往车辆超速、噪音扰民", 2, 2, 2),
                ("夜间穿越施工", "照明不足、信号不清、人员疲劳作业", 2, 2, 3),
                ("钻机吊装就位", "吊机倾覆、支腿塌陷、钢丝绳断裂", 2, 2, 4),
                ("穿越河流防汛", "汛期水位上涨、钻机被淹、泥浆污染水体", 2, 2, 3),
            ],
            "吊装": [
                ("吊机就位与支腿架设", "支腿塌陷、吊机倾覆、地基承载力不足", 2, 2, 5),
                ("钢丝绳索具检查与绑扎", "索具断裂、脱扣、绑扎不当", 2, 2, 4),
                ("起吊与就位", "吊物摆动失控、就位时手指挤压、超角度起吊", 2, 2, 4),
                ("高压线附近作业", "吊臂触碰高压线、人员触电、吊物晃动", 1, 1, 5),
                ("高空作业（安装人员）", "高处坠落、工具坠落、操作平台不稳", 2, 3, 4),
                ("信号工指挥", "信号不清、盲区作业、对讲机没电", 2, 2, 2),
                ("风力监测与停工", "大风超过6级仍作业、雷电影响、大雾视线不清", 1, 1, 4),
                ("夜间吊装", "照明不足、信号灯不清、人员疲劳作业", 2, 2, 3),
            ],
            "动土": [
                ("开挖前探挖", "机械直接挖断管线、探沟深度不足、探管不准", 2, 2, 5),
                ("沟槽边坡支护", "边坡失稳、支护不牢、深度超1.5m未支护", 3, 3, 4),
                ("降水与排水", "地下水位高涌水、雨水倒灌、挖破自来水管泡水", 2, 3, 3),
                ("沟槽内作业", "被边坡塌方埋没、机械碰撞、缺氧中毒", 3, 3, 4),
                ("爆破作业", "飞石伤人、震动影响建筑、盲炮处理不当", 1, 1, 5),
                ("管线悬吊保护", "悬吊装置失效、管线拉裂、天然气泄漏", 1, 1, 5),
                ("沟槽周边防护", "防护栏被撞开、夜间无警示灯、警戒区被闯入", 2, 2, 2),
                ("回填与压实", "分层回填不实、压实度不够、回填顺序错误", 2, 2, 3),
            ],
            "高处": [
                ("脚手架搭设与拆除", "脚手架坍塌、扣件松动、高空坠物", 2, 2, 4),
                ("高处作业系挂安全带", "安全带低挂高用、挂点不牢、未系挂作业", 2, 3, 5),
                ("临边洞口防护", "防护栏被拆除、安全网破损、洞口未封闭", 2, 3, 4),
                ("物料传递与堆放", "工具未绑安全绳、交叉作业扔东西、高处超载", 2, 2, 3),
                ("吊篮作业", "钢丝绳断丝、制动器失效、超载、大风仍作业", 1, 1, 5),
                ("梯子与操作平台", "梯子不稳、人字梯角度不当、平台超载", 2, 2, 3),
                ("极端天气作业", "高温中暑、雷电、6级以上强风、结冰打滑", 1, 1, 4),
                ("动火作业（高处）", "火花坠落引发火灾、防护措施不足、消防器材缺位", 2, 2, 4),
            ],
        }
        pool = jsa_pool.get(wt, jsa_pool.get("深基坑"))
        result = list(existing)
        used_steps = {r["work_step"] for r in result}
        idx = len(result)
        for step, hazard, L, E, C in pool:
            if step in used_steps:
                continue
            lec = L * E * C
            result.append({
                "seq": idx + 1, "work_step": step, "hazard_factor": hazard,
                "likelihood": L, "exposure": E, "consequence": C,
                "lec_score": lec,
                "existing_control": "执行三级交底、专人监护、持证上岗、定期巡检",
                "further_improvement": "增加专项检查频次、安装可视监控系统、加强班前教育",
                "remaining_risk_acceptable": "是" if lec <= 9 else "否",
            })
            idx += 1
            if len(result) >= 8:
                break
        return result

    @staticmethod
    def _default_emergency_plan(wt: str) -> str:
        return (
            f"一、预防措施\n"
            f"1. 开工前进行 {wt} 专项技术交底，明确作业参数、安全措施及应急预案。\n"
            f"2. 布置必要的监测设备和安全防护设施，定期检查维护。\n"
            f"3. 配备应急物资：沙袋、钢管、木板、水泵、应急照明、警戒带、便携式检测仪、担架及急救药品。\n"
            f"4. 对毗邻管线采取保护措施，与管线权属单位建立 24 小时联动机制。\n"
            f"5. 加强班前安全教育，确保作业人员知晓风险点和逃生路线。\n"
            f"\n二、应急响应流程\n"
            f"隐患/事故发现 → 第一发现人立即停止作业并呼喊示警 → 报告项目经理（5 分钟内） → 现场警戒（拉起警戒带、疏散无关人员） → 启动对应预案 → 分级上报（监理/业主/住建/安监，15 分钟内） → 配合调查。\n"
            f"\n三、救援措施\n"
            f"1. 坍塌：严禁盲目施救，立即回填反压，使用生命探测仪定位后由专业救援队伍实施救助。\n"
            f"2. 水害：启动备用水泵强排，漏点采用沙袋+棉被封堵，较大渗漏采用化学注浆或聚氨酯发泡。\n"
            f"3. 管线破损：立即停止施工、疏散至安全距离、关闭相关阀门，通知管线权属单位到场处置，严禁明火。\n"
            f"4. 人员伤害：现场急救（止血、固定、心肺复苏），同时拨打 120 急救电话，保护事故现场。\n"
            f"5. 泄漏中毒：立即疏散、切断泄漏源、通风排毒，必要时佩戴防毒面具进入抢救。\n"
            f"\n四、事后处理\n"
            f"1. 事故/险情结束后 24 小时内组织专家组评估，形成书面评估报告。\n"
            f"2. 根据评估结论补充安全措施，经审批后重新技术交底。\n"
            f"3. 逐步恢复施工，加强监测频率直至稳定。\n"
            f"4. 形成完整的事故/险情调查报告，包括原因分析、责任认定、整改措施，存档并报上级主管部门。"
        )
