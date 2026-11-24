"""
PCM Agent Demo 每日邮件发送 —— 读取测试报告 JSON → 格式化 → SMTP 发送
由 GitHub Actions 调用（SMTP 凭据从环境变量/GitHub Secrets 读）

用法:
  python send_email.py                    # 默认收件人
  python send_email.py --to a@b.com       # 指定收件人（逗号分隔）
  python send_email.py --report path.json # 指定报告路径（默认同目录 test_report.json）

环境变量（GitHub Secrets 注入）:
  SMTP_HOST      SMTP 服务器地址
  SMTP_PORT      端口 (587 STARTTLS / 465 SSL / 25)
  SMTP_USER      用户名
  SMTP_PASS      授权码/密码
  SMTP_FROM      发件人地址
"""
import smtplib, ssl, json, argparse, os, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid

def load_report(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def format_markdown(report):
    s = report.get("summary", {})
    total = s.get("total", 0)
    passed = s.get("pass", 0)
    failed = s.get("fail", 0)
    ts = report.get("timestamp", "N/A").replace("T", " ")
    base = report.get("base_url", "")
    
    emoji = "✅" if failed == 0 else "❌"
    status_line = f"{emoji} {'全部通过' if failed == 0 else f'{failed}/{total} 失败'}"
    
    lines = []
    lines.append(f"# PCM Agent Demo 每日健康检查  {status_line}")
    lines.append("")
    lines.append(f"**时间**: {ts}")
    lines.append(f"**目标**: `{base}`")
    lines.append(f"**总计**: {passed} ✅ / {failed} ❌ / {total}")
    lines.append("")
    lines.append("| # | 测试项 | 结果 | 详情 |")
    lines.append("|---|--------|------|------|")
    for i, d in enumerate(report.get("details", []), 1):
        icon = "✅" if d.get("pass") else "❌"
        name = d.get("name", "")
        detail = (d.get("detail") or "").replace("|", "/")[:100]
        lines.append(f"| {i} | {name} | {icon} | {detail} |")
    
    if failed > 0:
        lines.append("")
        lines.append("## ❌ 失败项详情")
        for d in report.get("details", []):
            if not d.get("pass"):
                lines.append(f"- **{d.get('name')}**: {d.get('detail', '无详情')[:300]}")
    
    return "\n".join(lines)

def md_to_html(md):
    """极简 Markdown → HTML（只支持我们用到的语法）"""
    import re
    html = md
    # 标题
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    # 粗体
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    # 表格：简单处理
    def table_to_html(text):
        lines = text.split('\n')
        in_table = False
        result = []
        for line in lines:
            if line.startswith('|'):
                if not in_table:
                    result.append('<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;font-size:12px">')
                    in_table = True
                cells = [c.strip() for c in line.strip('|').split('|')]
                if all(set(c) <= set('-: ') for c in cells):
                    continue
                tag = 'th' if result[-1] == '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;font-size:12px">' else 'td'
                result.append('<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>')
            elif in_table:
                result.append('</table>')
                in_table = False
                result.append(line)
            else:
                result.append(line)
        if in_table:
            result.append('</table>')
        return '\n'.join(result)
    
    html = table_to_html(html)
    html = html.replace('\n\n', '<br><br>').replace('\n', '<br>')
    html = html.replace('✅', '✅').replace('❌', '❌')
    return f'<html><body style="font-family:Arial,sans-serif;font-size:13px;padding:20px">{html}</body></html>'

def send_email(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, to_addrs, subject, body_html, body_text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("PCM Agent 自动监控", smtp_from))
    msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    
    port = int(smtp_port)
    if port == 465:
        # SSL 直连
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, port, context=ctx, timeout=30) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, to_addrs, msg.as_string())
    else:
        # STARTTLS (587 / 25)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, port, timeout=30) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, to_addrs, msg.as_string())
    
    print(f"✅ 邮件已发送至: {', '.join(to_addrs)}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default=None, help="收件人（逗号分隔，覆盖默认）")
    ap.add_argument("--report", default=None, help="测试报告 JSON 路径")
    ap.add_argument("--smtp-host", default=None)
    ap.add_argument("--smtp-port", default=None)
    ap.add_argument("--smtp-user", default=None)
    ap.add_argument("--smtp-pass", default=None)
    ap.add_argument("--smtp-from", default=None)
    args = ap.parse_args()
    
    # 默认收件人
    default_to = [
        "chenchen@zhongshuruizhi.com",
        "liutianfeng@zhongshuruizhi.com",
        "zhaozhen@zhongshuruizhi.com",
    ]
    to_addrs = (args.to or ",".join(default_to)).split(",")
    to_addrs = [t.strip() for t in to_addrs if t.strip()]
    
    # SMTP 配置（命令行 > 环境变量）
    smtp_host = args.smtp_host or os.environ.get("SMTP_HOST")
    smtp_port = args.smtp_port or os.environ.get("SMTP_PORT", "587")
    smtp_user = args.smtp_user or os.environ.get("SMTP_USER")
    smtp_pass = args.smtp_pass or os.environ.get("SMTP_PASS")
    smtp_from = args.smtp_from or os.environ.get("SMTP_FROM")
    
    missing = []
    if not smtp_host: missing.append("SMTP_HOST")
    if not smtp_user: missing.append("SMTP_USER")
    if not smtp_pass: missing.append("SMTP_PASS")
    if not smtp_from: missing.append("SMTP_FROM")
    if missing:
        print(f"❌ 缺少 SMTP 配置: {', '.join(missing)}")
        sys.exit(2)
    
    # 加载测试报告
    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "test_report.json")
    try:
        report = load_report(report_path)
    except Exception as e:
        print(f"❌ 读取测试报告失败 {report_path}: {e}")
        # 发一封报警邮件
        report = {"summary": {"pass": 0, "fail": 1, "total": 1},
                  "timestamp": "now", "base_url": "",
                  "details": [{"name": "测试脚本异常退出", "pass": False,
                               "detail": f"无法读取 {report_path}: {e}"}]}
    
    md = format_markdown(report)
    html = md_to_html(md)
    summary = report.get("summary", {})
    status = "✅ 通过" if summary.get("fail", 0) == 0 else f"❌ {summary.get('fail')}/{summary.get('total')} 失败"
    subject = f"[PCM 自动监控] {status} — {report.get('timestamp', '')[:10]}"
    
    print(f"📧 发件: {smtp_from}")
    print(f"📧 收件: {to_addrs}")
    print(f"📧 主题: {subject}")
    
    send_email(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, to_addrs, subject, html, md)

if __name__ == "__main__":
    main()
