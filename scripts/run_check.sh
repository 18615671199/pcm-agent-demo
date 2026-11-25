#!/bin/bash
# run_check.sh — 每日健康检查入口脚本
# 用法: run_check.sh [daily|failonly]
#   daily    全量日报（07:00 北京，发给 3 人：chenchen + liutianfeng + zhaozhen）
#   failonly 中间检测（15:00/23:00 北京，FAIL≥2 才发给 1 人：chenchen）
# 退出码: 0=正常（不管发没发邮件）, 非零=脚本异常

set -e
MODE=${1:-daily}

export PATH=$HOME/.local/node/bin:$PATH
cd $HOME/pcm-monitor/scripts

echo "=== $(date): 模式=$MODE 开始检查 ==="

# 跑 daily_check.py
if [ "$MODE" = "failonly" ]; then
    python3 daily_check.py --fail-only 2>&1 | tee ../cron_run.log || true
else
    python3 daily_check.py 2>&1 | tee ../cron_run.log || true
fi

# 读 ALERT_NEEDED 标记（从 markdown 报告最后一行）
ALERT_NEEDED=$(grep '^ALERT_NEEDED=' test_report.md | tail -1 | cut -d= -f2)
echo "ALERT_NEEDED=$ALERT_NEEDED"

if [ "$ALERT_NEEDED" != "1" ]; then
    echo "✅ ALERT_NEEDED=0，跳过发邮件（FAIL<2 或 PASS）"
    exit 0
fi

echo "🚨 ALERT_NEEDED=1，准备发邮件"

# 根据模式决定收件人
if [ "$MODE" = "daily" ]; then
    RECIPIENTS="chenchen@zhongshuruizhi.com liutianfeng@zhongshuruizhi.com zhaozhen@zhongshuruizhi.com"
    SUBJ_PREFIX="[PCM Daily]"
else
    RECIPIENTS="chenchen@zhongshuruizhi.com"
    SUBJ_PREFIX="[PCM Alert]"
fi

SUBJECT="$SUBJ_PREFIX Auto Report $(TZ=Asia/Shanghai date +%Y-%m-%d_%H:%M_北京)"

# 拼 --to 参数
SEND_ARGS=""
for r in $RECIPIENTS; do
    SEND_ARGS="$SEND_ARGS --to $r"
done

echo "收件人: $RECIPIENTS"
echo "主题: $SUBJECT"

agently-cli message +send $SEND_ARGS \
    --subject "$SUBJECT" \
    --body-file test_report.md \
    --confirmed 2>&1 | tee ../cron_send.log

echo "✅ 邮件发送完成"
exit 0
