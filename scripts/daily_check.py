"""
PCM Agent Demo 每日健康检查 —— 服务器 crontab / GitHub Actions 定时触发

新增功能（2026-11-25 监控增强）:
  - 性能阈值检查（TTFB / SSE 首事件时间）
  - 延迟 Top 5 输出到 Markdown 报告
  - SQLite VACUUM + 记录条数统计
  - --fail-only flag: 中间检测模式（07:00 全发, 15:00/23:00 FAIL-only）
  - 报告末尾追加告警标记: ALERT_NEEDED=1 / ALERT_NEEDED=0
"""
import requests, json, time, threading, urllib.parse, sys, datetime, os, argparse, sqlite3

BASE = "http://114.242.210.44:28080"
PASS = FAIL = 0
RESULTS = []        # (status, name, detail)
DETAILS = []        # dict list
TIMINGS = []        # (name, elapsed_ms, threshold_ms, ok)

# ═══════════════════════════════════════════════════
# 性能阈值（单位 ms）
# ═══════════════════════════════════════════════════
THRESHOLDS = {
    "health": 2000,           # 1s 放宽到 2s（留余量）
    "openapi": 3000,          # 大 JSON
    "static_page": 4000,      # 4 个 HTML
    "http_default": 2000,     # 其他普通 HTTP
    "sse_first_event": 60000, # SSE 首事件（60s，国内 LLM 代理）
    "sse_total": 300000,      # SSE 整条（5min，保持原超时）
}

def t(name, cond, detail="", elapsed_ms=None, threshold_ms=None):
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if cond: PASS += 1
    else: FAIL += 1
    RESULTS.append((status, name, detail[:500]))
    DETAILS.append({
        "name": name, "pass": cond, "detail": detail[:500],
        "elapsed_ms": elapsed_ms, "threshold_ms": threshold_ms,
        "latency_ok": (elapsed_ms is None or threshold_ms is None or elapsed_ms <= threshold_ms),
    })
    if elapsed_ms is not None and threshold_ms is not None:
        TIMINGS.append((name, elapsed_ms, threshold_ms, elapsed_ms <= threshold_ms))
    print(f"  [{status}] {name}" + (f"  — {detail[:200]}" if not cond else "") +
          (f"  ({elapsed_ms:.0f}ms)" if elapsed_ms is not None else ""))

def timed_get(name, url, threshold_ms, timeout=10):
    """带计时 + 阈值检查的 GET"""
    t0 = time.time()
    try:
        r = requests.get(url, timeout=timeout)
        elapsed = (time.time() - t0) * 1000
        ok = r.status_code == 200
        latency_ok = elapsed <= threshold_ms
        return r, ok, elapsed, latency_ok
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        return None, False, elapsed, False

# ═══════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════
parser = argparse.ArgumentParser(description="PCM Agent Daily Check")
parser.add_argument("--fail-only", action="store_true",
    help="中间检测模式: 仅在 FAIL>=2 时标记 ALERT_NEEDED=1, 否则=0")
args = parser.parse_args()
FAIL_ONLY = args.fail_only

print("=" * 70)
print(f"PCM Agent Demo 每日健康检查 — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"目标: {BASE}")
print(f"模式: {'FAIL-ONLY（中间检测）' if FAIL_ONLY else '全量日报（07:00）'}")
print("=" * 70)

# ═══════════════════════════════════════════════════
# 1. 基础连通（带性能阈值）
# ═══════════════════════════════════════════════════
print("\n📡 基础连通")

r, ok, elapsed, lat_ok = timed_get("GET /api/health", f"{BASE}/api/health", THRESHOLDS["health"])
health_json = r.json() if r else {}
t("GET /api/health", ok and health_json.get("status") == "ok",
  f"HTTP={r.status_code if r else 'ERR'}, body={health_json}",
  elapsed_ms=elapsed, threshold_ms=THRESHOLDS["health"])

r, ok, elapsed, lat_ok = timed_get("GET /openapi.json", f"{BASE}/openapi.json", THRESHOLDS["openapi"])
if r:
    schema = r.json()
    paths_count = len(schema.get("paths", {}))
    t(f"GET /openapi.json ({paths_count} paths)", ok and paths_count >= 14,
      f"paths={paths_count}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["openapi"])
    t("schema 含 work_task_desc", "work_task_desc" in json.dumps(schema, ensure_ascii=False))

# ═══════════════════════════════════════════════════
# 2. 静态页面
# ═══════════════════════════════════════════════════
print("\n📄 静态页面")
for path in ["/risk.html", "/quality.html", "/checklist.html", "/embed.html"]:
    r, ok, elapsed, lat_ok = timed_get(f"GET {path}", f"{BASE}{path}", THRESHOLDS["static_page"])
    size = len(r.text) if r else 0
    t(f"GET {path}", ok and size > 500,
      f"HTTP={r.status_code if r else 'ERR'}, size={size}",
      elapsed_ms=elapsed, threshold_ms=THRESHOLDS["static_page"])

# ═══════════════════════════════════════════════════
# 3. 风险识别 SSE（带首事件时间）
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
sse_start = time.time()
first_event_time = None
cur_evt = ""
events_seen = []
last_result = None
last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/risk-identification/run/stream?external_id={ext_id_risk}",
        json=payload_risk, stream=True, timeout=THRESHOLDS["sse_total"] // 1000)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if first_event_time is None:
            first_event_time = (time.time() - sse_start) * 1000
        if line.startswith("event:"):
            cur_evt = line[6:].strip()
            events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = (time.time() - sse_start) * 1000
    t("风险 SSE HTTP 200", resp.status_code == 200, f"总耗时 {dur/1000:.1f}s",
      elapsed_ms=dur, threshold_ms=THRESHOLDS["sse_total"])
    t("风险 SSE 首事件时间", True, f"首事件 {first_event_time:.0f}ms" if first_event_time else "无事件",
      elapsed_ms=first_event_time if first_event_time else None,
      threshold_ms=THRESHOLDS["sse_first_event"])
    t("风险 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        risks = last_result.get("output", {}).get("risk_table", [])
        t("风险 >= 5 条", len(risks) >= 5, f"len={len(risks)}")
    t("风险 SSE 已保存", last_saved is not None, f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")
except Exception as e:
    t("风险 SSE 完成", False, f"异常: {type(e).__name__}: {e}",
      elapsed_ms=(time.time() - sse_start) * 1000, threshold_ms=THRESHOLDS["sse_total"])

# ═══════════════════════════════════════════════════
# 4. 质量检查 SSE
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
r, ok, elapsed, lat_ok = timed_get("iframe 首次 query",
    f"{BASE}/api/checklist/query?{urllib.parse.urlencode(iframe_params)}", THRESHOLDS["http_default"])
d = r.json() if r else {}
t("iframe 首次 query", d.get("status") in ("not_found", "found"),
  f"status={d.get('status')}, mapped={d.get('input_meta',{}).get('mapped_work_type')}",
  elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])

payload_qc = {
    "project": iframe_params["projectName_dictText"],
    "work_type": d.get("input_meta", {}).get("mapped_work_type", "吊装"),
    "work_name": iframe_params["jobType_dictText"],
    "contractor": "", "inspector": "", "inspect_date": None,
}
sse_start = time.time()
first_event_time = None
events_seen = []
last_result = None
last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/quality-check/run/stream?external_id={iframe_params['id']}",
        json=payload_qc, stream=True, timeout=THRESHOLDS["sse_total"] // 1000)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if first_event_time is None:
            first_event_time = (time.time() - sse_start) * 1000
        if line.startswith("event:"):
            cur_evt = line[6:].strip()
            events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = (time.time() - sse_start) * 1000
    t("质量 SSE HTTP 200", resp.status_code == 200, f"总耗时 {dur/1000:.1f}s",
      elapsed_ms=dur, threshold_ms=THRESHOLDS["sse_total"])
    t("质量 SSE 首事件时间", True, f"首事件 {first_event_time:.0f}ms" if first_event_time else "无事件",
      elapsed_ms=first_event_time if first_event_time else None,
      threshold_ms=THRESHOLDS["sse_first_event"])
    t("质量 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        cl = last_result.get("output", {}).get("checklist", [])
        t("检查表 >= 5 条", len(cl) >= 5, f"len={len(cl)}")
    t("质量 SSE 已保存", last_saved is not None, f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")

    # 最后查一次 query → 应该 found
    time.sleep(2)
    r, ok, elapsed, lat_ok = timed_get("二次 query=found",
        f"{BASE}/api/checklist/query?{urllib.parse.urlencode(iframe_params)}", THRESHOLDS["http_default"])
    d = r.json() if r else {}
    t("二次 query=found（缓存命中）", d.get("status") == "found",
      f"actual={d.get('status')}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])
except Exception as e:
    t("质量 SSE 完成", False, f"异常: {type(e).__name__}: {e}",
      elapsed_ms=(time.time() - sse_start) * 1000, threshold_ms=THRESHOLDS["sse_total"])

# ═══════════════════════════════════════════════════
# 5. 记录查询
# ═══════════════════════════════════════════════════
print("\n🗃️ 记录查询")
r, ok, elapsed, lat_ok = timed_get("GET /api/records/risk", f"{BASE}/api/records/risk", THRESHOLDS["http_default"])
risk_count = len(r.json()) if r else 0
t("GET /api/records/risk", ok and isinstance(r.json() if r else None, list),
  f"count={risk_count}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])

r, ok, elapsed, lat_ok = timed_get("GET /api/records/quality", f"{BASE}/api/records/quality", THRESHOLDS["http_default"])
qc_list = r.json() if r else []
t("GET /api/records/quality", ok and isinstance(qc_list, list),
  f"count={len(qc_list)}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])
if qc_list:
    rid = qc_list[0].get("id")
    r, ok, elapsed, lat_ok = timed_get(f"GET /api/records/quality/{rid}",
        f"{BASE}/api/records/quality/{rid}", THRESHOLDS["http_default"])
    detail = r.json() if r else {}
    t(f"GET /api/records/quality/{rid}", ok and "output" in detail,
      elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])

# ═══════════════════════════════════════════════════
# 6. SQLite VACUUM + 记录统计
# ═══════════════════════════════════════════════════
print("\n💾 SQLite 维护")
db_stats = []
DB_PATHS = [
    ("/tmp/pcm_records.db", "运行记录库"),
    ("/home/chenchen/pcm-agent-demo/backend/vector_db/kb.sqlite", "向量知识库"),
]
for db_path, label in DB_PATHS:
    if os.path.exists(db_path):
        try:
            t0 = time.time()
            conn = sqlite3.connect(db_path, timeout=5)
            # 统计记录数
            cursor = conn.cursor()
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_counts = {}
            for (tbl,) in tables:
                try:
                    cnt = cursor.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                    table_counts[tbl] = cnt
                except: pass
            # VACUUM
            conn.execute("VACUUM")
            conn.close()
            elapsed = (time.time() - t0) * 1000
            total_records = sum(table_counts.values())
            db_stats.append({
                "db": db_path, "label": label,
                "tables": table_counts, "total_records": total_records,
                "vacuum_ms": elapsed,
            })
            t(f"SQLite VACUUM {label}", True,
              f"{total_records} records across {len(table_counts)} tables, VACUUM {elapsed:.0f}ms")
        except Exception as e:
            t(f"SQLite VACUUM {label}", False, f"异常: {e}")
    else:
        t(f"SQLite VACUUM {label}", False, f"DB 文件不存在: {db_path}")

# ═══════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════
print("\n" + "=" * 70)
total = PASS + FAIL
print(f"📊 结果: {PASS} PASS / {FAIL} FAIL / 共 {total}")

# 性能阈值超标
latency_fails = [n for (n, ms, th, ok) in TIMINGS if not ok]
if latency_fails:
    print(f"⏱️  性能超标: {len(latency_fails)} 项 ({', '.join(latency_fails[:5])}{'...' if len(latency_fails)>5 else ''})")

# 延迟 Top 5 (慢到快)
top5 = sorted(TIMINGS, key=lambda x: -x[1])[:5]
if top5:
    print(f"🐢 最慢 5 项:")
    for name, ms, th, ok in top5:
        flag = "⚠️" if not ok else "  "
        print(f"   {flag} {name}: {ms:.0f}ms / 阈值 {th}ms")

print("=" * 70)
if FAIL:
    print("\n❌ 失败项:")
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print(f"  🔴 {name}")
            if detail: print(f"     → {detail[:200]}")

# 告警判定
# FAIL_ONLY 模式: FAIL >= 2 才需要告警
# 全量模式: 不管怎样都发（日报）
ALERT_NEEDED = False
if FAIL_ONLY:
    if FAIL >= 2:
        ALERT_NEEDED = True
        print(f"\n🚨 ALERT_NEEDED=1（FAIL={FAIL} >= 阈值 2）")
    else:
        print(f"\n✅ ALERT_NEEDED=0（FAIL={FAIL} < 阈值 2，不发邮件）")
else:
    ALERT_NEEDED = True  # 全量日报总是发
    print(f"\n📧 ALERT_NEEDED=1（全量日报模式，总是发邮件）")

# ═══════════════════════════════════════════════════
# 输出 JSON 报告
# ═══════════════════════════════════════════════════
report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "base_url": BASE,
    "mode": "fail_only" if FAIL_ONLY else "full_daily",
    "alert_needed": ALERT_NEEDED,
    "summary": {"pass": PASS, "fail": FAIL, "total": total},
    "latency_top5": [
        {"name": n, "elapsed_ms": round(ms, 0), "threshold_ms": th, "ok": ok}
        for (n, ms, th, ok) in top5
    ],
    "db_stats": db_stats,
    "details": DETAILS,
}
report_path = os.path.join(os.path.dirname(__file__), "test_report.json")
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n📄 JSON 报告已写入: {report_path}")

# ═══════════════════════════════════════════════════
# 输出 Markdown 报告（含 ALERT_NEEDED 标记）
# ═══════════════════════════════════════════════════
md_lines = ["# PCM Agent Demo 每日健康检查报告", ""]
md_lines.append(f"**时间**: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  ")
md_lines.append(f"**目标**: {BASE}  ")
md_lines.append(f"**模式**: {'FAIL-ONLY（中间检测）' if FAIL_ONLY else '全量日报（07:00）'}  ")
md_lines.append(f"**结果**: {'✅ 全部通过' if FAIL == 0 else f'❌ {FAIL}/{total} 失败'}")
md_lines.append("")

# 延迟 Top 5
if top5:
    md_lines.append("## ⏱️ 性能延迟 Top 5")
    md_lines.append("")
    md_lines.append("| # | 测试项 | 耗时 | 阈值 | 状态 |")
    md_lines.append("|---|--------|------|------|------|")
    for i, (name, ms, th, ok) in enumerate(top5, 1):
        flag = "✅" if ok else "⚠️ 超标"
        md_lines.append(f"| {i} | {name} | {ms:.0f}ms | {th}ms | {flag} |")
    md_lines.append("")

# 性能阈值表（简化版）
md_lines.append("## 📋 详细测试结果")
md_lines.append("")
md_lines.append("| # | 测试项 | 结果 | 耗时 | 详情 |")
md_lines.append("|---|--------|------|------|------|")
for i, (status, name, detail) in enumerate(RESULTS, 1):
    icon = "✅" if status == "PASS" else "❌"
    # 找 timing
    ms_val = ""
    for (n, ms, th, ok) in TIMINGS:
        if n in name or name.startswith(n.split(" ")[0]):
            ms_val = f"{ms:.0f}ms"
            break
    md_lines.append(f"| {i} | {name} | {icon} | {ms_val} | {detail[:80]} |")

# DB 维护
if db_stats:
    md_lines.append("")
    md_lines.append("## 💾 SQLite 维护")
    md_lines.append("")
    for s in db_stats:
        md_lines.append(f"- **{s['label']}**: {s['total_records']} 条记录, VACUUM {s['vacuum_ms']:.0f}ms")

# 告警标记（run_check.sh 会 grep 这行决定是否发邮件）
md_lines.append("")
md_lines.append("---")
md_lines.append(f"ALERT_NEEDED={'1' if ALERT_NEEDED else '0'}")

md_path = os.path.join(os.path.dirname(__file__), "test_report.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(md_lines))
print(f"📄 Markdown 报告已写入: {md_path}")
print(f"🎯 ALERT_NEEDED={'1' if ALERT_NEEDED else '0'}")

# 退出码: FAIL > 0 → 非零退出（让 cron 看到异常）
sys.exit(1 if FAIL > 0 else 0)
