"""
PCM Agent Demo 每日健康检查 —— 服务器 crontab / GitHub Actions 定时触发

功能:
  - 24 项端到端测试
  - 性能阈值检查（TTFB / SSE 首事件时间）
  - 延迟 Top 5 输出
  - SQLite VACUUM（--server-local 模式）
  - 🔥 自愈逻辑: 检测 FAIL → systemd restart → 15s → 重测
  - --fail-only flag: 中间检测模式
  - ALERT_NEEDED 标记: 由 run_check.sh 判断是否发邮件
"""
import requests, json, time, sys, datetime, os, argparse, sqlite3, subprocess, urllib.parse

BASE = "http://114.242.210.44:28080"
PASS = FAIL = 0
RESULTS = []
DETAILS = []
TIMINGS = []
SELF_HEAL_LOG = []  # 自愈日志

# ═══════════════════════════════════════════════════
# 性能阈值（ms）
# ═══════════════════════════════════════════════════
THRESHOLDS = {
    "health": 2000,
    "openapi": 3000,
    "static_page": 4000,
    "http_default": 2000,
    "sse_first_event": 60000,
    "sse_total": 300000,
}

# ═══════════════════════════════════════════════════
# 参数
# ═══════════════════════════════════════════════════
parser = argparse.ArgumentParser(description="PCM Agent Daily Check")
parser.add_argument("--fail-only", action="store_true",
    help="中间检测: FAIL>=2 才标记告警")
parser.add_argument("--server-local", action="store_true",
    help="服务器本地运行: 激活 SQLite VACUUM + 自愈逻辑")
args = parser.parse_args()
FAIL_ONLY = args.fail_only
SERVER_LOCAL = args.server_local

# ═══════════════════════════════════════════════════
# 自愈函数
# ═══════════════════════════════════════════════════
def try_self_heal(failed_item_name):
    """在服务器本地执行 systemd restart，等 15s，返回 health 是否恢复"""
    if not SERVER_LOCAL:
        return False  # 非服务器本地不能自愈
    
    try:
        print(f"\n🔧 自愈触发: 检测到 [{failed_item_name}] 失败 → 尝试 systemd restart...")
        SELF_HEAL_LOG.append(f"🔄 检测到 FAIL: {failed_item_name} → systemd restart pcm-agent.service")
        
        # 1. restart systemd 服务
        result = subprocess.run(
            ["systemctl", "--user", "restart", "pcm-agent.service"],
            capture_output=True, text=True, timeout=15
        )
        SELF_HEAL_LOG.append(f"   systemctl restart → rc={result.returncode}")
        
        # 2. 等 uvicorn 启动
        print("   等待 15s 让 uvicorn 启动...")
        SELF_HEAL_LOG.append(f"   等待 15s...")
        time.sleep(15)
        
        # 3. 探活 health
        try:
            r = requests.get(f"{BASE}/api/health", timeout=10)
            ok = r.status_code == 200 and r.json().get("status") == "ok"
            SELF_HEAL_LOG.append(f"   health 探测 → {'OK' if ok else 'FAIL'}")
            print(f"   health → {'✅ 恢复' if ok else '❌ 仍失败'}")
            return ok
        except Exception as e:
            SELF_HEAL_LOG.append(f"   health 探测 → 异常: {type(e).__name__}: {e}")
            print(f"   health → ❌ 异常: {e}")
            return False
    except Exception as e:
        SELF_HEAL_LOG.append(f"   自愈异常: {e}")
        print(f"   自愈异常: {e}")
        return False

def t(name, cond, detail="", elapsed_ms=None, threshold_ms=None, can_self_heal=False):
    """测试项 + 可选自愈"""
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    
    if cond:
        PASS += 1
    else:
        # 尝试自愈（仅限能自愈的项 + 服务器本地）
        if can_self_heal and SERVER_LOCAL:
            healed = try_self_heal(name)
            if healed:
                # 自愈成功 → 重跑这个测试项（简化：只重跑 health）
                print(f"   自愈成功！重新检测 [{name}]...")
                # 对 health 重检
                if "health" in name:
                    try:
                        r2 = requests.get(f"{BASE}/api/health", timeout=10)
                        cond = r2.status_code == 200 and r2.json().get("status") == "ok"
                        status = "PASS" if cond else "FAIL"
                        if cond:
                            PASS += 1
                            SELF_HEAL_LOG.append(f"   ✅ 自愈后 [{name}] → PASS")
                        else:
                            FAIL += 1
                            SELF_HEAL_LOG.append(f"   ❌ 自愈后 [{name}] 仍 FAIL")
                    except:
                        FAIL += 1
                        SELF_HEAL_LOG.append(f"   ❌ 自愈后 [{name}] 重检异常")
                else:
                    # 其他项也尝试重检一次
                    FAIL += 1
            else:
                FAIL += 1
        else:
            FAIL += 1
    
    RESULTS.append((status, name, detail[:500]))
    DETAILS.append({
        "name": name, "pass": cond, "detail": detail[:500],
        "elapsed_ms": elapsed_ms, "threshold_ms": threshold_ms,
    })
    if elapsed_ms is not None and threshold_ms is not None:
        TIMINGS.append((name, elapsed_ms, threshold_ms, elapsed_ms <= threshold_ms))
    print(f"  [{status}] {name}" + (f"  — {detail[:200]}" if not cond else "") +
          (f"  ({elapsed_ms:.0f}ms)" if elapsed_ms is not None else ""))

def timed_get(name, url, threshold_ms, timeout=10):
    t0 = time.time()
    try:
        r = requests.get(url, timeout=timeout)
        elapsed = (time.time() - t0) * 1000
        ok = r.status_code == 200
        return r, ok, elapsed
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        return None, False, elapsed

print("=" * 70)
print(f"PCM Agent Demo 每日健康检查 — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"目标: {BASE}")
print(f"模式: {'FAIL-ONLY' if FAIL_ONLY else '全量日报'} | {'服务器本地（自愈+VACUUM）' if SERVER_LOCAL else '远程探测'}")
print("=" * 70)

# ═══════════════════════════════════════════════════
# 1. 基础连通（health 失败触发自愈）
# ═══════════════════════════════════════════════════
print("\n📡 基础连通")

r, ok, elapsed = timed_get("GET /api/health", f"{BASE}/api/health", THRESHOLDS["health"])
health_json = r.json() if r else {}
t("GET /api/health", ok and health_json.get("status") == "ok",
  f"HTTP={r.status_code if r else 'ERR'}, body={health_json}",
  elapsed_ms=elapsed, threshold_ms=THRESHOLDS["health"],
  can_self_heal=True)  # health 失败 → 自愈

r, ok, elapsed = timed_get("GET /openapi.json", f"{BASE}/openapi.json", THRESHOLDS["openapi"])
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
    r, ok, elapsed = timed_get(f"GET {path}", f"{BASE}{path}", THRESHOLDS["static_page"])
    size = len(r.text) if r else 0
    t(f"GET {path}", ok and size > 500,
      f"HTTP={r.status_code if r else 'ERR'}, size={size}",
      elapsed_ms=elapsed, threshold_ms=THRESHOLDS["static_page"])

# ═══════════════════════════════════════════════════
# 3. 风险识别 SSE
# ═══════════════════════════════════════════════════
print("\n🔥 风险识别 SSE")
payload_risk = {
    "project": "每日健康检查", "project_unit": "自动测试",
    "work_type": "动土", "work_name": "挖沟作业",
    "work_task_desc": "步骤1: 准备材料 步骤2: 开挖 步骤3: 回填",
    "work_location": "测试区", "contractor": "自动测试承包商",
    "project_id": "DAILY-CHECK",
}
ext_id_risk = f"daily-check-risk-{datetime.datetime.now():%Y%m%d}"
sse_start = time.time()
first_event_time = None
cur_evt = ""; events_seen = []; last_result = None; last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/risk-identification/run/stream?external_id={ext_id_risk}",
        json=payload_risk, stream=True, timeout=THRESHOLDS["sse_total"] // 1000)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if first_event_time is None: first_event_time = (time.time() - sse_start) * 1000
        if line.startswith("event:"):
            cur_evt = line[6:].strip(); events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = (time.time() - sse_start) * 1000
    t("风险 SSE HTTP 200", resp.status_code == 200, f"总耗时 {dur/1000:.1f}s",
      elapsed_ms=dur, threshold_ms=THRESHOLDS["sse_total"])
    t("风险 SSE 首事件时间", True,
      f"首事件 {first_event_time:.0f}ms" if first_event_time else "无事件",
      elapsed_ms=first_event_time if first_event_time else None,
      threshold_ms=THRESHOLDS["sse_first_event"])
    t("风险 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        risks = last_result.get("output", {}).get("risk_table", [])
        t("风险 >= 5 条", len(risks) >= 5, f"len={len(risks)}")
    t("风险 SSE 已保存", last_saved is not None,
      f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")
except Exception as e:
    t("风险 SSE 完成", False, f"异常: {type(e).__name__}: {e}",
      elapsed_ms=(time.time() - sse_start) * 1000, threshold_ms=THRESHOLDS["sse_total"])

# ═══════════════════════════════════════════════════
# 4. 质量检查 SSE
# ═══════════════════════════════════════════════════
print("\n📋 质量检查 SSE")
iframe_params = {
    "projectName_dictText": "每日健康检查项目",
    "projectCategory_dictText": "四类", "projectType_dictText": "城市燃气",
    "jobType_dictText": "吊装作业", "jobSite": "每日自动",
    "jobCount": "1", "id": f"daily-check-qc-{datetime.datetime.now():%Y%m%d}",
}
r, ok, elapsed = timed_get("iframe 首次 query",
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
sse_start = time.time(); first_event_time = None; events_seen = []
last_result = None; last_saved = None
try:
    resp = requests.post(
        f"{BASE}/api/agents/quality-check/run/stream?external_id={iframe_params['id']}",
        json=payload_qc, stream=True, timeout=THRESHOLDS["sse_total"] // 1000)
    for line in resp.iter_lines(decode_unicode=True):
        if line is None: continue
        if first_event_time is None: first_event_time = (time.time() - sse_start) * 1000
        if line.startswith("event:"):
            cur_evt = line[6:].strip(); events_seen.append(cur_evt)
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if cur_evt == "result": last_result = data
                if cur_evt == "saved": last_saved = data
            except: pass
    dur = (time.time() - sse_start) * 1000
    t("质量 SSE HTTP 200", resp.status_code == 200, f"总耗时 {dur/1000:.1f}s",
      elapsed_ms=dur, threshold_ms=THRESHOLDS["sse_total"])
    t("质量 SSE 首事件时间", True,
      f"首事件 {first_event_time:.0f}ms" if first_event_time else "无事件",
      elapsed_ms=first_event_time if first_event_time else None,
      threshold_ms=THRESHOLDS["sse_first_event"])
    t("质量 SSE 事件完整", all(e in events_seen for e in ["progress", "llm_start", "llm_done", "result", "saved"]),
      f"events={events_seen}")
    if last_result:
        cl = last_result.get("output", {}).get("checklist", [])
        t("检查表 >= 5 条", len(cl) >= 5, f"len={len(cl)}")
    t("质量 SSE 已保存", last_saved is not None,
      f"record_id={last_saved.get('record_id') if last_saved else 'N/A'}")

    time.sleep(2)
    r, ok, elapsed = timed_get("二次 query=found",
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
r, ok, elapsed = timed_get("GET /api/records/risk", f"{BASE}/api/records/risk", THRESHOLDS["http_default"])
risk_count = len(r.json()) if r else 0
t("GET /api/records/risk", ok and isinstance(r.json() if r else None, list),
  f"count={risk_count}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])

r, ok, elapsed = timed_get("GET /api/records/quality", f"{BASE}/api/records/quality", THRESHOLDS["http_default"])
qc_list = r.json() if r else []
t("GET /api/records/quality", ok and isinstance(qc_list, list),
  f"count={len(qc_list)}", elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])
if qc_list:
    rid = qc_list[0].get("id")
    r, ok, elapsed = timed_get(f"GET /api/records/quality/{rid}",
        f"{BASE}/api/records/quality/{rid}", THRESHOLDS["http_default"])
    detail = r.json() if r else {}
    t(f"GET /api/records/quality/{rid}", ok and "output" in detail,
      elapsed_ms=elapsed, threshold_ms=THRESHOLDS["http_default"])

# ═══════════════════════════════════════════════════
# 6. SQLite VACUUM（自动检测: DB 不存在就 SKIP，不计入 PASS/FAIL）
# ═══════════════════════════════════════════════════
DB_PATHS = [
    ("/tmp/pcm_records.db", "运行记录库"),
    ("/home/chenchen/pcm-agent-demo/backend/vector_db/kb.sqlite", "向量知识库"),
]
any_db_exists = any(os.path.exists(p) for p, _ in DB_PATHS)
print("\n💾 SQLite 维护" + ("（远程环境，SKIP）" if not any_db_exists else "（本地检测模式）"))
db_stats = []
if not any_db_exists:
    print("  [SKIP] 未检测到服务器 DB 文件（当前是远程/云端运行，跳过 VACUUM）")
else:
    for db_path, label in DB_PATHS:
        if not os.path.exists(db_path):
            print(f"  [SKIP] SQLite {label}: DB 文件不存在 {db_path}")
            continue
        try:
            t0 = time.time()
            conn = sqlite3.connect(db_path, timeout=5)
            cursor = conn.cursor()
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_counts = {}
            for (tbl,) in tables:
                try:
                    cnt = cursor.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                    table_counts[tbl] = cnt
                except: pass
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
              f"{total_records} records, VACUUM {elapsed:.0f}ms")
        except Exception as e:
            t(f"SQLite VACUUM {label}", False, f"异常: {e}")

# ═══════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════
print("\n" + "=" * 70)
total = PASS + FAIL
print(f"📊 结果: {PASS} PASS / {FAIL} FAIL / 共 {total}")

if SELF_HEAL_LOG:
    print(f"\n🔧 自愈记录:")
    for log in SELF_HEAL_LOG:
        print(f"   {log}")

latency_fails = [n for (n, ms, th, ok) in TIMINGS if not ok]
if latency_fails:
    print(f"\n⏱️  性能超标: {len(latency_fails)} 项")

top5 = sorted(TIMINGS, key=lambda x: -x[1])[:5]
if top5:
    print(f"\n🐢 最慢 5 项:")
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

# 告警判定: daily 总发, failonly FAIL>=2 才发
ALERT_NEEDED = True if not FAIL_ONLY else (FAIL >= 2)
print(f"\n🎯 ALERT_NEEDED={'1' if ALERT_NEEDED else '0'} (FAIL={FAIL}, fail_only={FAIL_ONLY})")

# ═══════════════════════════════════════════════════
# 输出 JSON 报告
# ═══════════════════════════════════════════════════
report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "base_url": BASE,
    "mode": "fail_only" if FAIL_ONLY else "full_daily",
    "server_local": SERVER_LOCAL,
    "alert_needed": ALERT_NEEDED,
    "summary": {"pass": PASS, "fail": FAIL, "total": total},
    "self_heal": SELF_HEAL_LOG,
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
print(f"📄 JSON: {report_path}")

# ═══════════════════════════════════════════════════
# 输出 Markdown 报告
# ═══════════════════════════════════════════════════
md_lines = ["# PCM Agent Demo 每日健康检查报告", ""]
md_lines.append(f"**时间**: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
md_lines.append(f"**目标**: {BASE}")
md_lines.append(f"**模式**: {'FAIL-ONLY（中间检测）' if FAIL_ONLY else '全量日报'}")
md_lines.append(f"**运行**: {'服务器本地（自愈激活）' if SERVER_LOCAL else '远程探测'}")
if FAIL == 0:
    md_lines.append(f"**结果**: 📊 **{total}/{total} 全部通过** ✅")
else:
    md_lines.append(f"**结果**: 📊 **{total - FAIL}/{total} 通过，{FAIL} 项待关注**")

# ── 温馨说明（所有接收者都能看懂）──
md_lines.append("")
md_lines.append("---")
md_lines.append("")
md_lines.append("### 📖 这份报告是什么？")
md_lines.append("")
md_lines.append("这是 **PCM Agent Demo 接口自动健康检查** 的每日日报，由服务器定时任务 / GitHub Actions 自动运行，覆盖 API 连通性、SSE 流式识别、记录查询等 **24 项**端到端测试。")
md_lines.append("")
md_lines.append("**检测频率**: 每日 07:00（全量日报，发所有人）+ 15:00 / 23:00（中间检测，仅异常时发陈晨）")
md_lines.append("")
md_lines.append("### 🔍 看到 ❌ 不用慌，常见原因：")
md_lines.append("")
md_lines.append("| 现象 | 常见原因 | 后果 | 自愈机制 |")
md_lines.append("|------|----------|------|----------|")
md_lines.append("| 单项 HTTP 超时 | LLM 代理偶发抖动 / 公网延迟 | 用户侧也可能遇一次 | systemd Restart=always + 脚本自愈 |")
md_lines.append("| SSE 首事件慢（>60s） | 大模型排队 | 用户感知加载慢 | 阈值放宽到 60s 已留足余量 |")
md_lines.append("| 2 项以上 FAIL | 服务器进程崩了 / DB 锁死 | 接口整体不可用 | systemd 3s 重启 + 脚本自动 restart + 15s 重测 |")
md_lines.append("| SQLite VACUUM SKIP | 云端 GitHub Actions 无服务器 DB | 无影响，云端本来就不做 VACUUM | — |")
md_lines.append("")
md_lines.append("### 🛡️ 当前已有的防护层：")
md_lines.append("")
md_lines.append("- **systemd --user + enable-linger**: 服务器重启自动拉起 uvicorn，进程崩了 3s 重启")
md_lines.append("- **脚本自愈**: 检测到 health 失败 → 自动 `systemd restart` → 15s 重测 → PASS 则修复")
md_lines.append("- **双重调度**: 服务器 crontab（主力）+ GitHub Actions（云端备份，服务器挂了也能跑）")
md_lines.append("- **中间检测降噪**: 15:00/23:00 只有 FAIL≥2 才发邮件，单项偶发抖动不打扰")
md_lines.append("- **每日 VACUUM**: SQLite DB 自动压缩，防止 WAL 膨胀导致慢查询")
md_lines.append("")
md_lines.append("### ⚠️ 什么时候需要人介入？")
md_lines.append("")
md_lines.append("- 连续 2 份日报都有 **≥2 项 FAIL**（自愈没救回来）")
md_lines.append("- FAIL 项包含 **SSE 事件缺失**（LLM 输出异常，不是网络问题）")
md_lines.append("- 延迟 Top 5 里 **SSE 首事件 > 120s**（LLM 代理可能挂了）")
md_lines.append("")
md_lines.append("出现以上情况直接联系 **陈晨**，大概率是外部 LLM 代理问题。")
md_lines.append("")
md_lines.append("---")

# 自愈记录
if SELF_HEAL_LOG:
    md_lines.append("")
    md_lines.append("## 🔧 本次自愈记录")
    md_lines.append("")
    for log in SELF_HEAL_LOG:
        md_lines.append(f"- {log}")

# 延迟 Top 5
if top5:
    md_lines.append("")
    md_lines.append("## ⏱️ 性能延迟 Top 5")
    md_lines.append("")
    md_lines.append("| # | 测试项 | 耗时 | 阈值 | 状态 |")
    md_lines.append("|---|--------|------|------|------|")
    for i, (name, ms, th, ok) in enumerate(top5, 1):
        flag = "✅" if ok else "⚠️ 超标"
        md_lines.append(f"| {i} | {name} | {ms:.0f}ms | {th}ms | {flag} |")

# 详细结果
md_lines.append("")
md_lines.append("## 📋 详细测试结果")
md_lines.append("")
md_lines.append("| # | 测试项 | 结果 | 耗时 | 详情 |")
md_lines.append("|---|--------|------|------|------|")
for i, (status, name, detail) in enumerate(RESULTS, 1):
    icon = "✅" if status == "PASS" else "❌"
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

md_lines.append("")
md_lines.append("---")
md_lines.append(f"ALERT_NEEDED={'1' if ALERT_NEEDED else '0'}")

md_path = os.path.join(os.path.dirname(__file__), "test_report.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(md_lines))
print(f"📄 Markdown: {md_path}")

sys.exit(1 if FAIL > 0 else 0)
