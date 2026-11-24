# PCM Agent Daily Health Check + Send Email
# Triggered by Windows Task Scheduler at 07:00 every day
$ErrorActionPreference = "Stop"

$projectRoot = "C:\Users\18615\Documents\Trae-CN\default\pcm-agent-demo"
$scriptsDir = Join-Path $projectRoot "scripts"

Write-Host "=== PCM Agent Daily Check $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

# 1. Run tests
Set-Location $scriptsDir
python daily_check.py
$testExit = $LASTEXITCODE
Write-Host "Test exit code: $testExit"

# 2. Read report and send email
$mdPath = Join-Path $scriptsDir "test_report.md"
if (Test-Path $mdPath) {
    $body = Get-Content $mdPath -Raw -Encoding UTF8
    if ($testExit -eq 0) {
        $subject = "[PCM Monitor] ALL PASS - $(Get-Date -Format 'yyyy-MM-dd')"
    } else {
        $subject = "[PCM Monitor] FAILURES DETECTED - $(Get-Date -Format 'yyyy-MM-dd')"
    }
    
    Write-Host "Sending email: $subject"
    agently-cli message +send `
        --to chenchen@zhongshuruizhi.com `
        --to liutianfeng@zhongshuruizhi.com `
        --to zhaozhen@zhongshuruizhi.com `
        --subject $subject `
        --body $body `
        --confirmed
    
    Write-Host "Email sent."
} else {
    Write-Host "Report not found: $mdPath"
    exit 1
}

Write-Host "=== Done ==="
exit $testExit
