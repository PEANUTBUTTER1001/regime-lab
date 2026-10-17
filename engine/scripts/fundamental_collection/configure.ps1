# 서버 PC에서 실행. 기존 DB 환경 재사용, 비밀값은 마스킹 입력.
[CmdletBinding()]
param([switch]$SessionOnly, [switch]$Reconfigure)
$ErrorActionPreference = 'Stop'

function Read-FundSecret([string]$Prompt) {
    $fundSecret = Read-Host $Prompt -AsSecureString
    $fundPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($fundSecret)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($fundPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($fundPointer); $fundSecret.Dispose() }
}

function Get-FundExisting([string]$Name) {
    $fundValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not $fundValue) { $fundValue = [Environment]::GetEnvironmentVariable($Name, 'User') }
    return $fundValue
}

$fundValues = @{}
try {
    $fundFields = @(
        @('REGIME_FSC_DB_HOST', 'DB 호스트 (같은 서버 PC의 MariaDB이면 127.0.0.1)', 'plain', ''),
        @('REGIME_FSC_DB_USER', 'DB 사용자 (신규 표 SELECT/INSERT 권한을 받은 MariaDB 계정)', 'plain', ''),
        @('REGIME_FSC_DB_PASSWORD', 'DB 계정 비밀번호 (Windows 암호 아님)', 'secret', ''),
        @('REGIME_FSC_DB_NAME', '실제 대상 DB명 (기존 주가 수집 DB와 동일)', 'plain', 'regime_lab'),
        @('REGIME_FSC_DB_PORT', 'MariaDB 포트', 'plain', '3306'),
        @('DART_API_KEY', 'DART API 키 (빈 입력: 해당 출처 미설정)', 'optional', ''),
        @('ECOS_API_KEY', 'ECOS API 키 (금융위 공통 키와 별개, 빈 입력: 미설정)', 'optional', '')
    )
    foreach ($fundField in $fundFields) {
        $fundName, $fundPrompt, $fundKind, $fundDefault = $fundField
        $fundExisting = Get-FundExisting $fundName
        if ($fundExisting -and -not $Reconfigure) {
            $fundValues[$fundName] = $fundExisting
            Write-Host "$fundName : 기존 등록값 재사용 (값 표시 안 함)"
            continue
        }
        if ($fundKind -eq 'plain') { $fundEntered = Read-Host $fundPrompt }
        else { $fundEntered = Read-FundSecret $fundPrompt }
        if (-not $fundEntered -and $fundExisting) { $fundEntered = $fundExisting }
        if (-not $fundEntered -and $fundDefault) { $fundEntered = $fundDefault }
        if ([string]::IsNullOrWhiteSpace($fundEntered)) {
            if ($fundKind -eq 'optional') { Write-Host "$fundName : 미설정, 다른 출처와 분리하여 보고"; continue }
            throw '필수 DB 설정이 비어 있습니다. 등록하지 않았습니다.'
        }
        $fundValues[$fundName] = $fundEntered
    }
    if ($fundValues.REGIME_FSC_DB_NAME -notmatch '^[A-Za-z0-9_]+$') { throw 'DB명 형식 오류' }
    $fundPort = 0
    if (-not [int]::TryParse($fundValues.REGIME_FSC_DB_PORT, [ref]$fundPort) -or $fundPort -lt 1 -or $fundPort -gt 65535) {
        throw 'DB 포트 형식 오류'
    }
    foreach ($fundEntry in $fundValues.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($fundEntry.Key, $fundEntry.Value, 'Process')
        if (-not $SessionOnly) { [Environment]::SetEnvironmentVariable($fundEntry.Key, $fundEntry.Value, 'User') }
    }
    Write-Host '설정 완료. DB 설정은 기존 주가 수집과 공유합니다. 예약도 같은 Windows 계정으로 실행하세요.'
    if ($SessionOnly) { Write-Host '현재 세션에만 적용. 무인 예약에는 지속 설정 필요.' }
    else { Write-Host '현재 세션·사용자 환경에 적용. 별도 터미널에서 실행했다면 새 터미널을 여세요.' }
}
finally {
    foreach ($fundName in @($fundValues.Keys)) { $fundValues[$fundName] = $null }
    $fundValues.Clear()
    $fundExisting = $fundEntered = $null
}
