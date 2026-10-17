# 수집 서버에서 직접 실행. 비밀은 파일/명령행에 쓰지 않고 Windows 사용자 환경변수에 등록.
[CmdletBinding()]
param([switch]$SessionOnly)
$ErrorActionPreference = 'Stop'

function Read-SecretValue([string]$Prompt) {
    $secretInput = Read-Host $Prompt -AsSecureString
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretInput)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer); $secretInput.Dispose() }
}

$collectorValues = @{}
try {
    $collectorValues.REGIME_FSC_SERVICE_KEY = Read-SecretValue '공통 공공데이터 API 키 (3종 동일 키)'
    $collectorValues.REGIME_FSC_DB_HOST = Read-Host 'DB 호스트 (서버 내부 접속이면 127.0.0.1)'
    $collectorValues.REGIME_FSC_DB_USER = Read-Host 'DB 사용자'
    $collectorValues.REGIME_FSC_DB_PASSWORD = Read-SecretValue 'DB 비밀번호'
    $collectorDb = Read-Host 'DB 이름 (엔터: regime_lab)'
    $collectorPort = Read-Host 'DB 포트 (엔터: 3306)'
    $collectorValues.REGIME_FSC_DB_NAME = $(if ($collectorDb) { $collectorDb } else { 'regime_lab' })
    $collectorValues.REGIME_FSC_DB_PORT = $(if ($collectorPort) { $collectorPort } else { '3306' })
    foreach ($collectorEntry in $collectorValues.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace($collectorEntry.Value)) { throw '빈 설정 항목이 있습니다. 등록하지 않았습니다.' }
    }
    if ($collectorValues.REGIME_FSC_DB_NAME -notmatch '^[A-Za-z0-9_]+$') { throw 'DB 이름 형식 오류' }
    $parsedCollectorPort = 0
    if (-not [int]::TryParse($collectorValues.REGIME_FSC_DB_PORT, [ref]$parsedCollectorPort) -or
        $parsedCollectorPort -lt 1 -or $parsedCollectorPort -gt 65535) { throw '포트 형식 오류' }
    foreach ($collectorEntry in $collectorValues.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($collectorEntry.Key, $collectorEntry.Value, 'Process')
        if (-not $SessionOnly) { [Environment]::SetEnvironmentVariable($collectorEntry.Key, $collectorEntry.Value, 'User') }
    }
    Write-Host '등록 완료. 설정값은 표시하지 않았습니다.'
    if ($SessionOnly) { Write-Host '현재 PowerShell 세션에만 적용. 예약 실행에는 지속 설정 필요.' }
    else { Write-Host '현재 세션과 Windows 사용자 환경에 적용. 예약 작업도 같은 Windows 계정으로 실행하세요.' }
}
finally {
    foreach ($collectorName in @($collectorValues.Keys)) { $collectorValues[$collectorName] = $null }
    $collectorValues.Clear()
}
