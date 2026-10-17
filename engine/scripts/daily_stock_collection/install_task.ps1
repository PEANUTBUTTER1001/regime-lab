[CmdletBinding()]
param(
    [string]$TaskName = 'regime-lab-fsc-daily',
    [switch]$RunWhenLoggedOff,
    [switch]$PrintOnly,
    [string]$PythonPath,
    [string]$UvPath,
    [string]$ConfigPath
)
$ErrorActionPreference = 'Stop'
$collectorScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'collect_fsc.py')).Path
$collectorRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$escapeCollectorXml = { param([string]$Text) [Security.SecurityElement]::Escape($Text) }
$collectorConfigArguments = @()
$collectorConfigText = ''
if ($ConfigPath) {
    $collectorConfigFile = (Resolve-Path -LiteralPath $ConfigPath).Path
    $collectorConfigArguments = @('--config', $collectorConfigFile)
    $collectorConfigText = ' --config "' + $collectorConfigFile + '"'
}

if ($PythonPath) {
    $collectorExecutable = (Get-Command $PythonPath -ErrorAction Stop).Source
    $collectorScheduleJson = & $collectorExecutable $collectorScript @collectorConfigArguments schedule-info
    $collectorArguments = '"' + $collectorScript + '"' + $collectorConfigText + ' --server-pc update'
} else {
    $collectorExecutable = $(if ($UvPath) { (Get-Command $UvPath -ErrorAction Stop).Source } else { (Get-Command uv -ErrorAction Stop).Source })
    $collectorScheduleJson = & $collectorExecutable run --script $collectorScript @collectorConfigArguments schedule-info
    $collectorArguments = 'run --script "' + $collectorScript + '"' + $collectorConfigText + ' --server-pc update'
}
if ($LASTEXITCODE -ne 0) { throw '수집기 실행 환경 확인 실패. 예약 작업을 등록하지 않았습니다.' }
$collectorSchedule = ($collectorScheduleJson -join "`n") | ConvertFrom-Json
$collectorOffset = [TimeSpan]::FromHours(9)
$collectorKstNow = [DateTimeOffset]::UtcNow.ToOffset($collectorOffset)
$collectorStart = [DateTimeOffset]::new($collectorKstNow.Year, $collectorKstNow.Month, $collectorKstNow.Day,
    $collectorSchedule.schedule_hour, $collectorSchedule.schedule_minute, 0, $collectorOffset)
$collectorDurationMinutes = [int]$collectorSchedule.delayed_retry_minutes * [int]$collectorSchedule.delayed_retries + 1
$collectorRepeat = ''
if ([int]$collectorSchedule.delayed_retries -gt 0) {
    $collectorRepeat = '<Repetition><Interval>PT' + $collectorSchedule.delayed_retry_minutes +
        'M</Interval><Duration>PT' + $collectorDurationMinutes + 'M</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>'
}
$collectorAccount = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$collectorLogon = $(if ($RunWhenLoggedOff) { 'Password' } else { 'InteractiveToken' })
$collectorXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>금융위 API 3종 Python 수집. 매일 13:30 KST 및 게시 지연 재조회.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger>$collectorRepeat<StartBoundary>$($collectorStart.ToString('yyyy-MM-ddTHH:mm:sszzz'))</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$(& $escapeCollectorXml $collectorAccount)</UserId><LogonType>$collectorLogon</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable><ExecutionTimeLimit>PT6H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>$(& $escapeCollectorXml $collectorExecutable)</Command><Arguments>$(& $escapeCollectorXml $collectorArguments)</Arguments><WorkingDirectory>$(& $escapeCollectorXml $collectorRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($PrintOnly) { $collectorXml; return }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw '같은 이름의 예약 작업이 있습니다. 덮어쓰지 않았습니다.' }
if ($RunWhenLoggedOff) {
    $collectorWindowsCredential = Get-Credential -UserName $collectorAccount -Message '로그아웃 중 실행할 Windows 계정 암호 (DB 암호 아님)'
    if (-not $collectorWindowsCredential) { throw '취소됨. 예약 작업 미등록.' }
    if ($collectorWindowsCredential.UserName -ne $collectorAccount) { throw '환경변수를 등록한 현재 Windows 계정과 같아야 합니다.' }
    Register-ScheduledTask -TaskName $TaskName -Xml $collectorXml -User $collectorAccount `
        -Password $collectorWindowsCredential.GetNetworkCredential().Password | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Xml $collectorXml | Out-Null
}
Write-Host '예약 등록 완료. KST 13:30 시작, 게시 지연 시 추가 조회. status 명령으로 DB 기록을 확인하세요.'
if (-not $RunWhenLoggedOff) { Write-Host '현재 설정은 Windows 로그인 중에만 실행. 상시 서버는 -RunWhenLoggedOff 사용 권장.' }
