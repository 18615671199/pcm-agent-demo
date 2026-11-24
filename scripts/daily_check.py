"""
PCM Agent Demo 每日健康检查 —— 由 GitHub Actions 每日 07:00 定时触发
输出: JSON 测试报告 → 由调用方（GitHub Actions）负责发邮件
"""
import requests, json, time, threading, urllib.parse, sys, datetime, os

BASE = "http://114.242.210.44:28080"
PASS = FAIL = 0
RESULTS = []
DETAILS = []

def t(name, cond, detail=""):
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if cond: PASS += 1
    else: FAIL += 1
    RESULTS.append((status, name, detail[:500]))
    DETAILS.append({"name": name, "pass": cond, "detail": detail[:500]})
    print(f"  [{status}] {name}" + (f"  — {detail[:200]}" if not cond else ""))

print("=" * 70)
print(f"PCM Agent Demo 每日健康检查 — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"目标: {BASE}")
print("=" * 70)

# ═══════════════════════════════════════════════════
# 1. 基础连通
# ═══════════════════════════════════════════════════
print("\n📡 基础连通")

r = requests.get(f"{BASE}/api/health", timeout=10)
t("GET /api/health", r.status_code == 200 and r.json().get("status") == "ok",
  f"HTTP={r.status_code}, body={r.text[:100]}")

r = requests.get(f"{BASE}/openapi.json", timeout=10)
schema = r.json()
paths_count = len(schema.get("paths", {}))
t(f"GET /openapi.json ({paths_count} paths)", r.status_code == 200 and paths_count >= 14,
  f"paths={paths_count}")
t("schema 含 work_task_desc", "work_task_desc" in json.dumps(schema, ensure_ascii=False))

# ═══════════════════════════════════════════════════
# 2. 静态页面
# ═══════════════════════════════════════════════════
print("\n📄 静态页面")
for path in ["/risk.html", "/quality.html", "/checklist.html", "/embed.html"]:
    r = requests.get(f"{BASE}{path}", timeout=10)
    t(f"GET {path}", r.status_code == 200 and len(r.text) > 500,
      f"HTTP={r.status_code}, size={len(r.text)}")

# ═══════════════════════════════════════════════════
# 3. 风险识别 SSE
# ═══════════════════════════════════════════════════
print("\n🔥 风险识别 SSE")
payload_risk = {
    "project": "每日健康检查",
    "project_unit": "自动测试",
    "work_type": "动土",
    "work_name": "挖沟作业",
    "work_task_desc": "步骤1: 准备材料 步骤2: 开挖 步骤3: 回填",
    "work_location": "测试区",
    "contractor": "自动测试承包商",
    "project_id": "DAILY-CHECK",
}
ext_id_risk = f"daily-check-risk-{datetime.datetime.now():%Y%m%d}"
start = time.time()
cur_evt = ""
events_seen = []
last_result = None
last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/risk-identification/run/stream?external_id={ext_id_risk}",
        json=payload_risk, stream=True, timeout=300)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if line.startswith("event:"):
            cur_evt = line[6:].strip()
            events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = time.time() - start
    t("风险 SSE HTTP 200", resp.status_code == 200, f"耗时 {dur:.1f}s")
    t("风险 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        risks = last_result.get("output", {}).get("risk_table", [])
        t("风险 >= 5 条", len(risks) >= 5, f"len={len(risks)}")
    t("风险 SSE 已保存", last_saved is not None, f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")
except Exception as e:
    t("风险 SSE 完成", False, f"异常: {type(e).__name__}: {e}")

# ═══════════════════════════════════════════════════
# 4. 质量检查 SSE（模拟集成方真实 iframe 参数）
# ═══════════════════════════════════════════════════
print("\n📋 质量检查 SSE（模拟 iframe 真实参数）")
iframe_params = {
    "projectName_dictText": "每日健康检查项目",
    "projectCategory_dictText": "四类",
    "projectType_dictText": "城市燃气",
    "jobType_dictText": "吊装作业",
    "jobSite": "每日自动",
    "jobCount": "1",
    "id": f"daily-check-qc-{datetime.datetime.now():%Y%m%d}",
}
# 先查 query
r = requests.get(f"{BASE}/api/checklist/query", params=iframe_params, timeout=10)
d = r.json()
t("iframe 首次 query", d.get("status") in ("not_found", "found"),
  f"status={d.get('status')}, mapped={d.get('input_meta',{}).get('mapped_work_type')}")

payload_qc = {
    "project": iframe_params["projectName_dictText"],
    "work_type": d.get("input_meta", {}).get("mapped_work_type", "吊装"),
    "work_name": iframe_params["jobType_dictText"],
    "contractor": "", "inspector": "", "inspect_date": None,
}
start = time.time()
events_seen = []
last_result = None
last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/quality-check/run/stream?external_id={iframe_params['id']}",
        json=payload_qc, stream=True, timeout=300)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if line.startswith("event:"):
            cur_evt = line[6:].strip()
            events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = time.time() - start
    t("质量 SSE HTTP 200", resp.status_code == 200, f"耗时 {dur:.1f}s")
    t("质量 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        cl = last_result.get("output", {}).get("checklist", [])
        t("检查表 >= 5 条", len(cl) >= 5, f"len={len(cl)}")
    t("质量 SSE 已保存", last_saved is not None, f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")
    
    # 最后查一次 query → 应该 found
    time.sleep(2)
    r = requests.get(f"{BASE}/api/checklist/query", params=iframe_params, timeout=10)
    d = r.json()
    t("二次 query=found（缓存命中）", d.get("status") == "found",
      f"actual={d.get('status')}")
except Exception as e:
    t("质量 SSE 完成", False, f"异常: {type(e).__name__}: {e}")

# ═══════════════════════════════════════════════════
# 5. 记录查询
# ═══════════════════════════════════════════════════
print("\n🗃️ 记录查询")
r = requests.get(f"{BASE}/api/records/risk", timeout=10)
t("GET /api/records/risk", r.status_code == 200 and isinstance(r.json(), list),
  f"count={len(r.json()) if r.status_code==200 else 'N/A'}")

r = requests.get(f"{BASE}/api/records/quality", timeout=10)
qc_list = r.json() if r.status_code == 200 else []
t("GET /api/records/quality", r.status_code == 200 and isinstance(qc_list, list),
  f"count={len(qc_list)}")
if qc_list:
    rid = qc_list[0].get("id")
    r = requests.get(f"{BASE}/api/records/quality/{rid}", timeout=10)
    detail = r.json() if r.status_code == 200 else {}
    t(f"GET /api/records/quality/{rid}", r.status_code == 200 and "output" in detail)

# ═══════════════════════════════════════════════════
# 汇总 & 输出 JSON
# ═══════════════════════════════════════════════════
print("\n" + "=" * 70)
total = PASS + FAIL
print(f"📊 结果: {PASS} PASS / {FAIL} FAIL / 共 {total}")
print("=" * 70)
if FAIL:
    print("\n❌ 失败项:")
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print(f"  🔴 {name}")
            if detail: print(f"     → {detail[:200]}")

report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "base_url": BASE,
    "summary": {"pass": PASS, "fail": FAIL, "total": total},
    "details": DETAILS,
}

report_path = os.path.join(os.path.dirname(__file__), "test_report.json")
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n📄 JSON 报告已写入: {report_path}")

# 同时输出 markdown 格式到 stdout（方便 GitHub Actions 抓）
md_lines = ["# PCM Agent Demo 每日健康检查报告", ""]
md_lines.append(f"**时间**: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  ")
md_lines.append(f"**目标**: {BASE}  ")
md_lines.append(f"**结果**: {'✅ 全部通过' if FAIL == 0 else f'❌ {FAIL}/{total} 失败'}")
md_lines.append("")
md_lines.append("| # | 测试项 | 结果 | 详情 |")
md_lines.append("|---|--------|------|------|")
for i, (status, name, detail) in enumerate(RESULTS, 1):
    icon = "✅" if status == "PASS" else "❌"
    md_lines.append(f"| {i} | {name} | {icon} | {detail[:80]} |")

md_path = os.path.join(os.path.dirname(__file__), "test_report.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(md_lines))
print(f"📄 Markdown 报告已写入: {md_path}")

sys.exit(0 if FAIL == 0 else 1)
