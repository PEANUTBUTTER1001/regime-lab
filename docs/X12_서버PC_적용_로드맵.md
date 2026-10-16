# X12 서버 PC 적용 로드맵 — develop 병합 이후

| 항목 | 기준 |
|---|---|
| 작성일 | 2026-10-15 KST |
| 대상 | Windows 데이터 서버 PC에서 실행하는 regime-lab 앱·같은 망 팀원 브라우저 |
| 병합 확인 | 이 문서 작성 PC의 `develop`·로컬 `origin/develop`에서 `c092d82`, PR #94 확인. 서버 PC의 반영 상태는 미확인 |
| 작업 담당 | X12 구현·결과 기록 `PEANUTBUTTER1001`, 리뷰 `hongsungmin0315`, 서버 환경 확인·적용 협업 `Seonghwanaa` |
| 현재 판정 | 코드 병합 완료. 실제 서버 적용·허용/비허용 기기·두 PC 수용 결과 미확인 |
| 이번 산출물 | 실행 절차를 설명하는 문서. 이 문서 작성 과정에서 실제 서버·공유기·방화벽·작업 스케줄러 변경 없음 |
| 완료 조건 | 이 문서 §12~§15의 필수 수용 통과 후 X12 전체 완료 판정 |

## 0. 이 문서의 적용 범위와 읽는 순서

목표: 서버 PC 한 곳의 앱을 같은 망 팀원에게만 제공하고, 팀원 PC에는 원본·기사 사본·앱 서버를 배포하지 않는 운영.

순서: **현황 확인 → 백업 → IP 예약 → 방화벽 점검 → 유지보수·기존 앱 정지 → develop 반영 → 환경 검증 → 시작 파일 수정 → 방화벽 적용 → 앱 기동 → 허용/차단·LLM·두 PC 수용 → 자동 시작 확인 → 완료 기록**.

- 실제 서버 재시작은 사용자·서버 담당과 실행 시점을 합의한 뒤 수행. 이 문서 작성 요청 자체는 현재 서버를 중단하라는 지시가 아님.
- 문서의 명령은 지정한 PC·터미널에서 순서대로 실행. 전체 문서를 통째로 PowerShell에 붙여 넣지 않음.
- 코드·환경·방화벽·자동 시작 네 가지가 같은 구성을 가리켜야 적용 완료. 터미널에서 한 번 성공한 상태와 재부팅 뒤 유지되는 상태 구분.
- 원본 데이터 이동·재수집·전체 덤프 재변환·DB 포트 변경·새벽 자동 갱신 구현은 X12 선행 작업 아님. 기존 정상 환경 유지.
- 실제 주소·팀원 MAC·키·서버 환경 파일·원문 로그는 서버 PC에서만 취급. 공개 저장소에 올리는 이 문서에는 자리표시자만 사용.
- 이전 전달 문서의 “X12 미구현”, 이전 담당자, `/24` 전체 허용 예시는 현재 기준으로 사용하지 않음. 확정 기준은 아래 표.

### 0.1 확정 정책 — 재선택 없이 적용

| 항목 | 적용 기준 | 운영 의미 |
|---|---|---|
| 1A 허용 기기 | 팀원별 IPv4 `/32` | 같은 공유기의 모든 기기를 허용하는 `/24` 운영 제외 |
| 2B 주소 범위 | RFC1918 IPv4 | `10.0.0.0/8`·`172.16.0.0/12`·`192.168.0.0/16`에 포함되는 주소만. 실제 설정은 각 기기의 `/32` |
| 3B Supabase | LAN 변수 파싱·검증·필터 전부 무시 | 이번 절차는 `REGIME_AUTH_MODE=demo`를 명시. Supabase에 허용 목록을 넣어도 접속 제한 효과 없음 |
| 서버 자신의 접속 | loopback 자동 허용 | 서버 브라우저·상태 점검은 `127.0.0.1` 사용. 서버의 LAN 주소로 자기 자신을 호출하면 원격 IPv4 기준 판정 가능 |
| 원격 IPv6 | native IPv6 원격 허용 제외 | 팀원 주소는 IPv4 사용. IPv6 loopback·IPv4-mapped 표현 처리는 별도 보존 |
| 실행 경로 | `uv run --project api regime-api` | 공식 데모 진입점의 `proxy_headers=False` 적용. 직접 `uvicorn ...` 명령으로 대체하지 않음 |
| 연결 경로 | 브라우저 → 앱 직접 연결 | Nginx·IIS·로컬 역방향 프록시·포트 프록시·터널·외부 포트포워딩 제외 |
| 앱 거부 응답 | HTTP 403, `network_not_allowed` | API·정적 파일·없는 경로·OPTIONS 모두 동일 경계 |
| 원격 LLM 변경 | HTTP 403, `local_only` | 허용된 팀원도 모델·키 적용/삭제 불가. 서버 PC의 loopback에서만 변경 |
| 작업 대기열 | 실행·탐색 공용 FIFO·동시 계산 1개 | 데모 요청자 구분은 실제 접속 IP. 같은 PC의 브라우저 두 개는 독립 요청자 아님 |
| 데모 데이터 공유 | 허용된 팀원이 실행 기록 공유 | 서로의 실행 조회·취소·삭제 가능. 개인별 소유권 격리 기대 금지 |

### 0.2 어느 PC에서 무엇을 하는지

| 구분 | 수행 내용 | 제외 |
|---|---|---|
| 서버 PC 일반 PowerShell | Git·파일 확인·환경 검증·직접 실행·상태 조회 | 관리자 필요 명령 강행 |
| 서버 PC 관리자 PowerShell | 작업·방화벽 확인 및 승인된 적용 | Python 전체 강제 종료·전체 방화벽 초기화 |
| 공유기 관리 화면 | 서버·팀원 IPv4 DHCP 예약 | 외부 공개용 포트포워딩 추가 |
| 허용 팀원 PC A·B | 접속·실행·탐색·대기열 수용 | 앱 설치·데이터 다운로드·API 키 설정 |
| 비허용 시험 기기 C | 방화벽 차단·앱 403·헤더 우회 불가 확인 | 최종 운영 허용 목록 추가 |

서버의 공통 변수는 **기존 운영 계정으로 승격한 관리자 PowerShell 한 창**에서 §2.1부터 준비하는 방식 권장. 그 창에서는 일반 조회·Git·설정 명령도 실행 가능. 다른 관리자 계정으로 환경을 새로 설치하는 방식과 구분. 앱을 띄우는 별도 콘솔·팀원 PC에서는 필요한 변수를 다시 설정하며, 최종 실행 권한은 기존 작업 계정 기준으로 §15에서 재확인.

## 1. 착수 전 준비물·중단 조건

### 1.1 준비물

- [ ] 서버 PC에 직접 접근하거나 복구 가능한 관리 경로 확보
- [ ] X12 병합 커밋 `c092d82` 또는 이를 포함하는 이후 develop 식별
- [ ] 서버 PC의 실제 저장소·시작 파일·관리 스크립트·작업 이름 확인
- [ ] 서버·팀원 A·팀원 B·시험 기기 C의 현재 IPv4와 유효 네트워크 확인
- [ ] A·B가 서버와 직접 통신 가능한 네트워크에 연결됨. 게스트 Wi-Fi 격리·VPN·NAT 우회 경로 확인
- [ ] 실제 앱 포트 확인. 아래 기본값 8000은 기존 서버 설정이 8000일 때 사용
- [ ] 기존 실행·탐색 작업이 없는 유지보수 시간 확보
- [ ] 기존 시작 파일·작업 XML·방화벽 상태·Git 커밋·경로 설정 백업
- [ ] Git·uv 및 Python 3.12 환경 사용 가능
- [ ] 실제 기기 수용에 사용할 정상 가격 Parquet·준비 환경 확보

### 1.2 이 경우 다음 단계 중단

| 상황 | 이유 | 조치 |
|---|---|---|
| 서버 저장소에 출처 불명 미커밋 변경 | pull·전환 시 운영 수정 유실 가능 | 담당자와 변경 출처 확인·별도 보존 후 재개. 자동 stash·reset 금지 |
| 실행 중인 작업 존재 | 재시작 시 queued/running이 `failed(server_restarted)`로 처리 | 완료 대기 또는 해당 작업 사용자와 취소 합의 |
| 현재 앱이 Supabase 운영 | 데모 전환은 접근·소유권 의미 변경 | 이번 F1b 데모 운영 대상인지 담당자 확인 후 진행 |
| 실제 실행이 프록시·여러 worker·별도 Uvicorn 명령 | IP·LLM·대기열 경계가 명세와 다름 | 공식 단일 프로세스 직접 실행 구성 확인 후 재개 |
| 방화벽이 꺼짐·광범위 허용·그룹 정책으로 관리 | 좁은 허용 규칙 하나만으로 제한 보장 불가 | 기존 영향 확인 후 담당자가 관련 규칙 조정 |
| 가격 준비 실패 | 접속만 성공하고 실행·대기 완료 검증 불가 | 데이터 준비 원인을 먼저 해결. X12 전체 완료 처리 금지 |
| 백업·복구 경로 불명확 | 변경 실패 시 기존 서비스 복원 불가 | §3 백업·§17 복구 방법 먼저 확보 |

## 2. 서버 PC 현재 상태 확인 — 읽기 전용

### 2.1 실제 경로 입력

이전 전달 자료의 경로는 과거 점검 정보. 현재 서버에서 파일 존재·작업 동작과 대조한 뒤 사용.

| 대상 | 이전 전달 자료의 경로·이름 | 현재 확인 |
|---|---|---|
| 저장소 | `C:\Users\user\regime-server\regime-lab` | `api`·`engine`·`.git` 존재 확인 |
| 서버 운영 폴더 | `C:\Users\user\Desktop\Regime_lab\app` | 저장소 밖 서버 전용 폴더인지 확인 |
| 시작 파일 | 운영 폴더의 `run_app_task.bat` | 작업 스케줄러가 실제로 이 파일을 실행하는지 확인 |
| 관리 스크립트 | 운영 폴더의 `ops_app_autostart.ps1` | 실제 지원 인자·종료·재시작 동작 확인 |
| 자동 시작 작업 | `regime_lab_app` | TaskPath·실행 계정·작업 동작 확인 |

서버 PC PowerShell에서 실제 값 입력. 이 창을 닫으면 변수는 사라지므로 다른 창에서도 필요한 변수 재설정.

```powershell
$X12Repo = Read-Host '서버의 regime-lab 저장소 절대 경로'
$X12OpsDir = Read-Host '서버 운영 폴더 절대 경로'
$X12TaskName = 'regime_lab_app'
$X12Port = 8000  # 기존 실제 포트와 다르면 여기부터 일관되게 수정
$X12Bat = Join-Path $X12OpsDir 'run_app_task.bat'
$X12Manager = Join-Path $X12OpsDir 'ops_app_autostart.ps1'

if (-not (Test-Path -LiteralPath (Join-Path $X12Repo 'api/pyproject.toml'))) {
    throw '저장소 경로 확인 필요'
}
if (-not (Test-Path -LiteralPath $X12Bat)) {
    throw '기존 시작 파일 확인 필요. 새 파일로 추측 대체하지 않음'
}
$X12Repo = (Resolve-Path -LiteralPath $X12Repo).Path
$X12OpsDir = (Resolve-Path -LiteralPath $X12OpsDir).Path
$X12RepoPrefix = $X12Repo.TrimEnd('\') + '\'
if ($X12OpsDir.Equals($X12Repo, [StringComparison]::OrdinalIgnoreCase) -or $X12OpsDir.StartsWith($X12RepoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw '운영 폴더·백업은 Git 저장소 밖에 위치해야 함'
}
Set-Location -LiteralPath $X12Repo
git status --short --branch
git log -5 --oneline
git branch --show-current
git remote -v
git --version
uv --version
```

Git safe.directory 오류가 발생하면 저장소 소유권을 먼저 확인. 신뢰가 확인된 해당 호출에만 `git -c "safe.directory=$X12Repo" ...` 사용 가능. 전역 `safe.directory=*` 설정 제외.

### 2.2 네트워크·포트·프로세스

```powershell
Get-NetConnectionProfile |
    Select-Object InterfaceAlias, InterfaceIndex, NetworkCategory, IPv4Connectivity
Get-NetIPConfiguration |
    Select-Object InterfaceAlias, IPv4Address, IPv4DefaultGateway
Get-NetAdapter |
    Select-Object Name, Status, MacAddress, InterfaceDescription

$X12Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $X12Port -ErrorAction SilentlyContinue)
$X12Listeners | Select-Object LocalAddress, LocalPort, OwningProcess
$X12Listeners.OwningProcess | Sort-Object -Unique | ForEach-Object {
    Get-Process -Id $_ | Select-Object Id, ProcessName, Path
}
```

- 기존 loopback 운영이면 `127.0.0.1:<포트>` 또는 의도한 loopback 리슨 확인.
- 다른 프로그램이 같은 포트를 사용하면 포트 주인 확인 후 해결. 포트를 피하려고 무작정 새 앱을 추가 기동하지 않음.
- 실행 파일 경로로 어떤 Python 환경인지 확인. 명령줄 추가 점검은 서버 화면에서만 수행하며 키가 들어 있을 수 있는 전체 명령줄을 공개 기록에 복사하지 않음.
- 이전 문서에 적힌 서버 IP·Private 프로필·8100/3306 상태를 현재 사실로 간주하지 않음.

### 2.3 작업 스케줄러·시작 파일

관리자 PowerShell에서 확인.

```powershell
$X12Tasks = @(Get-ScheduledTask -TaskName $X12TaskName -ErrorAction Stop)
if ($X12Tasks.Count -ne 1) {
    throw '동일 이름 작업이 여러 개이거나 없음. 정확한 TaskPath 확인 필요'
}
$X12Task = $X12Tasks[0]
$X12Task | Select-Object TaskName, TaskPath, State
$X12Task.Actions | Format-List Execute, Arguments, WorkingDirectory
$X12Task.Principal | Format-List UserId, LogonType, RunLevel
$X12Task.Triggers | Format-List *
$X12Task.Settings | Format-List MultipleInstances, RestartCount, RestartInterval, ExecutionTimeLimit
Get-ScheduledTaskInfo -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath
Get-Content -LiteralPath $X12Bat
if (Test-Path -LiteralPath $X12Manager) {
    Get-Content -LiteralPath $X12Manager
}
```

확인 항목:

1. BAT의 저장소 이동 경로가 §2.1의 저장소와 일치하는지
2. 실제 실행이 `uv run --project api regime-api`인지. `--no-sync` 등 운영 옵션은 확인한 환경에 따라 유지 가능
3. 환경변수가 앱 실행보다 앞에 설정되는지. 뒤쪽에서 HOST·AUTH_MODE·ALLOWED_NETS를 덮어쓰는 줄이 없는지
4. BAT가 다른 BAT·PowerShell 파일을 호출하면 최종 앱 프로세스까지 호출 흐름 확인
5. 현재 `REGIME_SAMPLE`·`REGIME_RUNS_DIR`·뉴스 갱신·키 전달 설정의 존재와 보존 방법 확인. 비밀값 출력·수집은 최소화
6. 작업 실행 계정이 파일·가상환경·데이터·결과·캐시 경로에 접근 가능한지
7. 실패 시 중복 프로세스를 계속 만드는 재시도 구조인지, 기존 관리 스크립트가 PID·포트를 확인하는지
8. `ops_app_autostart.ps1 -Restart`가 실제로 지원되는지. 이 문서는 서버 전용 스크립트 원문을 보유하지 않아 구현을 보증하지 않음

작업 상태 정보와 XML은 Microsoft 공식 [Get-ScheduledTaskInfo](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtaskinfo), [Export-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/export-scheduledtask) 기준. 실행 중인 장기 작업의 LastTaskResult만으로 앱 정상 판정 불가, 실제 리슨·HTTP 준비 상태 병행 확인.

### 2.4 기존 웹·작업 상태

```powershell
$X12LocalBase = "http://127.0.0.1:$X12Port"
Invoke-RestMethod -Uri "$X12LocalBase/api/health" -TimeoutSec 10
Invoke-RestMethod -Uri "$X12LocalBase/api/auth/config" -TimeoutSec 10
```

- 데모 응답 `enabled: false` 확인. Supabase라면 §1.2 중단 조건 적용.
- 웹의 백테스트 기록·탐색 기록과 아래 조회에서 `queued`·`running` 유무 확인.

```powershell
(Invoke-RestMethod -Uri "$X12LocalBase/api/runs" -TimeoutSec 10).runs |
    Where-Object { $_.status -in @('queued', 'running') } |
    Select-Object run_id, status
(Invoke-RestMethod -Uri "$X12LocalBase/api/searches" -TimeoutSec 10).searches |
    Where-Object { $_.status -in @('queued', 'running') } |
    Select-Object search_id, status
```

목록 조회는 기본 반환 개수 제한이 있으므로 HTTP 목록만으로 무작업 상태를 단정하지 않음. 웹·사용자 확인·운영 로그와 대조. 기존 앱이 정지 상태라면 위 HTTP 실패는 그 사실로 기록하고 포트·로그·기존 실행 절차부터 확인.

## 3. 변경 전 백업·복구 기준 확보

### 3.1 서버 운영 폴더 아래에 적용별 백업 생성

이 폴더는 Git 저장소 밖 서버 전용 위치. 전체 터미널 transcript·전체 환경변수 덤프는 키 노출 위험 때문에 만들지 않음.

```powershell
$X12Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$X12Backup = Join-Path $X12OpsDir "backups\x12-$X12Stamp"
New-Item -ItemType Directory -Path $X12Backup -Force | Out-Null
$X12PreviousCommit = (git -C $X12Repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw '기존 커밋 확인 실패' }
Set-Content -LiteralPath (Join-Path $X12Backup 'previous-commit.txt') -Value $X12PreviousCommit -Encoding UTF8
Copy-Item -LiteralPath $X12Bat -Destination (Join-Path $X12Backup 'run_app_task.bat')
if (Test-Path -LiteralPath $X12Manager) {
    Copy-Item -LiteralPath $X12Manager -Destination (Join-Path $X12Backup 'ops_app_autostart.ps1')
}
$X12LocalPaths = Join-Path $X12Repo 'engine/config/paths.local.yaml'
if (Test-Path -LiteralPath $X12LocalPaths) {
    Copy-Item -LiteralPath $X12LocalPaths -Destination (Join-Path $X12Backup 'paths.local.yaml')
}
Export-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath |
    Set-Content -LiteralPath (Join-Path $X12Backup 'scheduled-task.xml') -Encoding Unicode
$X12Task | Select-Object TaskName, TaskPath, State |
    Export-Clixml -LiteralPath (Join-Path $X12Backup 'scheduled-task-state.xml')
Get-NetFirewallProfile -PolicyStore ActiveStore |
    Select-Object Name, Enabled, DefaultInboundAction, DefaultOutboundAction |
    Export-Clixml -LiteralPath (Join-Path $X12Backup 'firewall-profiles.xml')
netsh advfirewall export (Join-Path $X12Backup 'firewall-before.wfw')
if ($LASTEXITCODE -ne 0) { throw '방화벽 백업 실패' }
```

- 백업 경로가 실제 `$X12Repo` 내부가 아닌지 확인 후 진행.
- 기존 시작 파일이 비밀값을 포함한다면 백업도 같은 민감도. 서버 운영 계정·관리자 범위로 접근 제한, 저장소·공개 채팅 업로드 제외.
- 원본·runs 전체를 복제하는 단계 아님. 기존 파일을 삭제·이동하지 않으며 서버의 기존 백업 정책 유지.
- 방화벽 전체 백업은 비상 참고용. 일반 복구에서 통째로 import하지 않음. 다른 서비스의 동시 변경을 덮어쓸 수 있기 때문.

### 3.2 로컬 운영 메모에 남길 값

| 항목 | 기록 위치·방식 |
|---|---|
| 기존 커밋·현재 브랜치·작업 시각 | 서버 운영 메모 |
| 저장소·BAT·관리 스크립트·로그의 실제 경로 | 서버 운영 메모 |
| 작업 이름·TaskPath·기존 Enabled 상태 | 서버 운영 메모·XML |
| 기존 HOST·PORT·SAMPLE·RUNS·뉴스 활성 여부 | 비밀값 제외한 서버 운영 메모 |
| 실제 IP·MAC·방화벽 주소 범위 | 서버 전용 설정·메모에만 기록 |
| 토큰·모델 API 키 | 기존 승인된 비밀 전달 방식 유지. 공개 메모·예시 파일에 추가 금지 |

## 4. 서버·팀원 IP 고정과 직접 접속 경로

### 4.1 서버와 팀원 A·B의 현재 주소 확인

서버·A·B 각각에서 `Get-NetIPConfiguration`, `Get-NetAdapter` 확인. 공유기 DHCP 예약에 사용할 MAC은 실제 사용하는 유선 또는 Wi-Fi 어댑터의 값.

1. 서버와 A·B가 사용하는 네트워크·공유기·VLAN 확인
2. 공유기 관리 화면의 DHCP 주소 예약/고정 할당 메뉴 진입
3. 서버 MAC ↔ 현재 유효 IPv4 예약
4. 팀원 A MAC ↔ A IPv4 예약
5. 팀원 B MAC ↔ B IPv4 예약
6. 추가 팀원도 기기별 예약 및 `/32` 추가 계획 기록
7. 예약 적용 후 실제 주소와 게이트웨이 재확인

주의 사항:

- 공유기 메뉴 이름·예약 적용 방식은 기종별 차이. 현재 관리자 화면 기준으로 확인.
- Wi-Fi 임의 MAC 기능이 네트워크 재연결 때 바뀌면 예약이 어긋날 수 있음. 해당 네트워크에서 예약 대상 MAC이 유지되는지 확인.
- 공유기 예약을 이미 쓰고 있다면 중복 고정 IP를 수동 지정하지 않음. 주소 충돌 방지.
- `ipconfig /release`나 어댑터 비활성화는 원격 관리 연결을 끊을 수 있음. 필요 시 직접 접근이 가능한 상태에서 계획된 시간에 수행.
- 게스트 Wi-Fi의 기기 격리·회사 VLAN 정책으로 직접 통신이 막히면 담당자와 경로 해결. 허용 목록을 넓히는 것으로 해결되지 않음.
- VPN·프록시·NAT로 A·B가 서버에서 같은 IP로 보이면 대기열의 동일 요청자 제한이 걸릴 수 있음. 실제 서버가 보는 접속 IP 확인.

### 4.2 접속 주소의 구분

| 값 | 사용하는 곳 |
|---|---|
| `0.0.0.0` | 서버의 IPv4 리슨 설정. 브라우저 접속 주소 아님 |
| `http://127.0.0.1:<앱포트>` | 서버 PC 자신의 브라우저·상태 점검·LLM 설정 |
| `http://<서버 IPv4>:<앱포트>` | 같은 망 팀원 브라우저 |
| `http://127.0.0.1:<앱포트>`를 팀원 PC에서 열기 | 팀원 PC 자신을 가리키므로 서버 접속 시험 아님 |

외부 공개·포트포워딩·터널은 이번 범위에서 제외. 팀원은 서버 앱 포트로만 접근하며 DB·데이터 API를 브라우저 사용을 위해 새로 열 필요 없음.

## 5. Windows 방화벽 사전 점검 — 아직 적용하지 않는 단계

### 5.1 현재 프로필·기본 정책

관리자 PowerShell:

```powershell
Get-NetConnectionProfile
Get-NetFirewallProfile -PolicyStore ActiveStore |
    Select-Object Name, Enabled, DefaultInboundAction, DefaultOutboundAction
```

- 서버가 사용하는 신뢰된 LAN 어댑터의 프로필과 실제 방화벽 활성 상태 확인.
- 아래 규칙은 `Private` 프로필 기준. 현재 `Public`이면 무조건 Private으로 바꾸거나 규칙을 Any로 넓히지 않음. 네트워크 성격·관리 정책부터 확인.
- `DomainAuthenticated` 또는 그룹 정책 환경이면 관리 담당 정책을 적용. 이 문서의 Private 규칙을 그대로 추가해도 적용되지 않을 수 있음.
- 기본 인바운드 정책이 Allow라면 좁은 Allow 규칙을 추가해도 제한 효과 없음. 담당자와 영향 확인 후 유효 정책을 정리해야 완료 가능.
- DefaultInboundAction의 `NotConfigured`만 보고 허용/차단을 단정하지 않음. 유효 정책·고급 방화벽 UI·실기기 검사로 확인.

### 5.2 앱 포트에 영향을 줄 수 있는 기존 규칙 조회

아래는 활성 인바운드 Allow 규칙과 관련 필터를 함께 확인하는 읽기 명령. 포트가 `Any`인 프로그램 허용도 확인 대상이므로 포트 8000 규칙만 검색하지 않음.

```powershell
$X12InboundRules = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound
$X12FirewallView = foreach ($X12Rule in $X12InboundRules) {
    $X12PortFilter = $X12Rule | Get-NetFirewallPortFilter
    $X12AddressFilter = $X12Rule | Get-NetFirewallAddressFilter
    $X12AppFilter = $X12Rule | Get-NetFirewallApplicationFilter
    [pscustomobject]@{
        Name = $X12Rule.Name
        DisplayName = $X12Rule.DisplayName
        Action = $X12Rule.Action
        Profile = $X12Rule.Profile
        Protocol = ($X12PortFilter.Protocol -join ',')
        LocalPort = ($X12PortFilter.LocalPort -join ',')
        RemoteAddress = ($X12AddressFilter.RemoteAddress -join ',')
        Program = ($X12AppFilter.Program -join ',')
        PolicySourceType = $X12Rule.PolicyStoreSourceType
    }
}
$X12FirewallView | Format-Table -AutoSize
$X12FirewallView | Export-Clixml -LiteralPath (Join-Path $X12Backup 'inbound-rules-before.xml')
```

`wf.msc`를 열어 **인바운드 규칙**에서 다음 항목 대조:

| 점검 대상 | 문제 조건 | 조치 원칙 |
|---|---|---|
| Python 프로그램 규칙 | 앱 Python을 모든 포트·Any 원격 주소에 허용 | 현재 다른 서비스 사용 여부 확인 후 해당 규칙만 비활성화 또는 범위 조정 |
| 앱 포트 규칙 | 앱 포트 또는 포함 범위에 Any·LocalSubnet·전체 `/24` 허용 | 팀원 `/32` 규칙과 겹치는 넓은 허용 제거 계획 |
| 프로필 | Public·Any에서도 동일 앱 광범위 허용 | 실제 사용하는 신뢰 LAN 범위와 정책 확인 |
| 명시 Block 규칙 | 앱 포트·실행 프로그램·팀원 주소와 겹침 | Allow보다 먼저 차단될 수 있으므로 규칙 목적 확인. 전체 Block 신규 생성 제외 |
| 정책 출처 | GroupPolicy 등 중앙 관리 | 로컬 수정 강행 대신 관리 담당 조정 |
| 다른 서비스 | 같은 Python이 데이터 API 등에도 사용 | Python 규칙 일괄 해제로 다른 포트 장애를 만들지 않음 |

규칙 우선순위·기본 차단과 예외의 관계는 Microsoft [Windows Firewall rules](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules) 기준. 좁은 Allow 추가만으로 기존 넓은 Allow가 사라지지 않으며, 명시 Block은 충돌하는 Allow를 막을 수 있음.

### 5.3 수정할 기존 규칙을 정확히 특정

- 표시 이름은 중복될 수 있으므로 실제 `Name`과 프로그램·포트·프로필 확인 후 선택.
- 기존 관련 규칙마다 원래 Enabled·RemoteAddress·Profile·Program·LocalPort를 서버 백업에 기록.
- 광범위 규칙이 다른 서비스에 필요하다면 해당 서비스에 필요한 좁은 규칙을 먼저 준비. 앱만을 이유로 DB·데이터 API 접근 정책을 재설계하지 않음.
- 이번 단계에서는 조회·변경 계획만 수행. 실제 X12 규칙 적용은 §10.

## 6. 유지보수 진입·기존 앱 정지

**앱 정지 후 Git 갱신·동기화 진행.** 엔진과 API가 editable 설치인 환경에서 실행 중 소스 교체·패키지 변경을 피하기 위한 순서.

1. 이용자에게 중단 시점·예상 복구 조건 공유
2. §2.4로 실행·탐색 진행 중 작업 확인
3. 작업 완료 대기. 취소가 필요하면 해당 작업 사용자와 합의
4. 기존 관리 스크립트의 종료 기능·작업 재시도·다른 감시 프로세스 확인
5. 자동 시작의 재기동을 잠시 막고 기존 앱 정지
6. 앱 포트 리슨이 사라졌는지 확인한 뒤 코드 갱신

기존 관리 스크립트가 종료 기능을 제공하면 원문으로 지원 인자·대상을 확인한 뒤 우선 사용. 지원 여부가 불명확하면 `-Stop` 같은 인자를 지어내지 않음.

작업 스케줄러가 해당 앱만 관리하는 것이 확인된 경우의 예:

```powershell
# 서버 작업의 정확한 TaskName·TaskPath는 §2.3에서 확인한 값 사용
Disable-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath | Out-Null
Stop-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath
Get-NetTCPConnection -State Listen -LocalPort $X12Port -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

- 작업 정지가 자식 Python 종료를 보장한다고 단정하지 않음. 리슨이 남으면 프로세스 경로·기존 관리 방식 확인.
- 콘솔 직접 실행이면 해당 콘솔에서 `Ctrl+C`로 종료.
- 자식 프로세스가 남고 정상 종료 방법이 없으면 정확한 PID·실행 파일·대상 앱을 확인한 뒤 담당자가 해당 프로세스만 종료. `taskkill /IM python.exe /F` 같은 일괄 종료 제외.
- 데이터 API·MariaDB·수집기·원격 관리 서비스 종료 제외.
- 작업이 다른 서비스까지 관리하거나 정지 직후 다시 켜지는 구조면 원인을 먼저 해결. 동일 포트에 두 번째 앱 기동 금지.
- 기존 작업이 원래 Disabled였다면 그 사실 기록. §15에서 자동 시작을 활성화할지는 기존 운영 의도에 맞춰 판단.

## 7. 서버 저장소에 develop 반영

### 7.1 작업 트리 확인·최신 코드 수신

서버 PC 저장소에서 일반 PowerShell 실행. 기존 운영 파일은 저장소 밖에서 유지.

```powershell
Set-Location -LiteralPath $X12Repo
git status --short --branch
# 미커밋 변경이 있다면 여기서 중단하고 출처·보존 확인
git switch develop
if ($LASTEXITCODE -ne 0) { throw 'develop 전환 실패' }
git fetch origin
if ($LASTEXITCODE -ne 0) { throw '원격 수신 실패' }
git pull --ff-only origin develop
if ($LASTEXITCODE -ne 0) { throw 'fast-forward 갱신 실패. 임의 merge/reset 금지' }
git log -5 --oneline
git merge-base --is-ancestor c092d82 HEAD
if ($LASTEXITCODE -ne 0) {
    throw 'X12 병합 커밋 포함 여부 확인 필요. shallow 저장소이면 이력 확보 후 재검사'
}
$X12AppliedCommit = (git rev-parse HEAD).Trim()
Set-Content -LiteralPath (Join-Path $X12Backup 'applied-commit.txt') -Value $X12AppliedCommit -Encoding UTF8
```

- develop이 이미 더 앞선 커밋이면 `HEAD=c092d82`일 필요 없음. X12 커밋을 포함하는지가 기준.
- 서버 로컬 develop이 별도 커밋으로 갈라져 있으면 `--ff-only` 실패가 정상. 강제 reset으로 맞추지 않고 차이 확인.
- shallow 이력 때문에 커밋을 못 찾는 경우 추가 이력을 받은 뒤 판정. 코드 일부를 수동 복사해 병합을 대신하지 않음.

### 7.2 X12 반영 파일 확인

```powershell
Select-String -LiteralPath (Join-Path $X12Repo 'api/src/regime_api/settings.py') -Pattern 'REGIME_ALLOWED_NETS', '_demo_allowed_nets'
Select-String -LiteralPath (Join-Path $X12Repo 'api/src/regime_api/main.py') -Pattern 'lan_allowlist', 'network_not_allowed', 'proxy_headers'
Test-Path -LiteralPath (Join-Path $X12Repo 'api/tests/test_lan_allowlist.py')
Test-Path -LiteralPath (Join-Path $X12Repo 'docs/X12_구현_요구사항_명세.md')
git status --short
```

문자열 존재 확인은 빠른 점검이며 동작 검증을 대체하지 않음. 서버 실제 환경 검증은 다음 단계.

## 8. 의존성·데이터 경로·실행 환경 검증

### 8.1 기존 lockfile 기준 환경 동기화

```powershell
Set-Location -LiteralPath $X12Repo
uv sync --locked --project engine
if ($LASTEXITCODE -ne 0) { throw 'engine 환경 동기화 실패' }
uv sync --locked --project api
if ($LASTEXITCODE -ne 0) { throw 'api 환경 동기화 실패' }
uv run --no-sync --project api python -c "import sys, regime_api, regime_lab; print(sys.version); print(regime_api.__file__); print(regime_lab.__file__)"
if ($LASTEXITCODE -ne 0) { throw '실행 환경 확인 실패' }
git status --short
```

- engine·API 요구 Python은 `>=3.12,<3.13`. 다른 저장소·오래된 설치 경로가 출력되면 환경 정리 후 진행.
- `--locked`는 lockfile 갱신 대신 불일치 오류 발생. 배포 중 임의 `uv lock --upgrade` 수행 제외.
- `--no-sync`는 이미 동기화한 환경을 사용하는 옵션. 환경 생성·설치를 대신하지 않음.
- 기존 실행 환경에 별도 비관리 패키지가 있다면 기본 `uv sync`의 정확 동기화 영향을 먼저 확인. X12 자체의 새 외부 의존성은 없음.
- ingest 환경 재설치·수집 실행은 X12 선행 조건 아님. 기존 수집 서비스 보존.

옵션 의미는 [uv 공식 문서](https://docs.astral.sh/uv/concepts/projects/sync/) 기준.

### 8.2 기존 경로 확인

```powershell
uv run --no-sync --project engine python -c "from regime_lab.config import load_paths; p=load_paths(); print('store_exists=',p.store.exists()); print('cache_exists=',p.cache.exists()); print('runs_exists=',p.runs.exists()); print('ext_store_exists=',p.ext_store.exists()); print('news_snapshots_exists=',p.news_snapshots.exists())"
```

| 확인 항목 | 정상 기준·조치 |
|---|---|
| `engine/config/paths.local.yaml` | 기존 서버 파일 유지. 없을 때만 서버 실제 경로로 예시를 참고해 작성 |
| 상대 경로 | 저장소 루트 기준. BAT가 다른 폴더를 현재 작업 폴더로 쓰더라도 엔진 경로 규칙과 혼동 금지 |
| `store` | 이미 준비된 서버 가격 Parquet 확인. X12만을 위해 원본 다시 다운로드하지 않음 |
| `cache`·`runs` | 실제 실행 계정의 읽기·쓰기 권한 및 기존 경로 유지 |
| `REGIME_RUNS_DIR` | 설정되어 있으면 paths의 runs보다 우선. 빈 결과 목록을 새 폴더로 잘못 바꾼 결과인지 확인 |
| `REGIME_SAMPLE` | 기존 sample30 검증 운영인지 전체 데이터 운영인지 유지. sample30 파일이 없는데 1로 설정하지 않음 |
| `ext_store`·`news_snapshots` | 기존 서버 정책 유지. 새 HTTP 사본 경로는 저장소 밖 |
| `data.as_of_date` | 현재 확정값 `2026-09-18` 유지. X12 적용을 이유로 최신 날짜로 변경하지 않음 |

`prepare`는 기존 준비 상태가 유효하면 별도 실행 필요 없음. 캐시 부재·준비 환경 확인이 필요한 경우에만 서버 담당자가 원본을 변경하지 않는 기존 명령 `uv run --project engine regime-lab prepare` 실행. API도 기동 시 준비를 수행하므로 같은 시점에 중복 준비하지 않음.

### 8.3 현재 터미널에 X12 값 설정·시작 전 검증

```powershell
$env:REGIME_AUTH_MODE = 'demo'
$env:REGIME_HOST = '0.0.0.0'
$env:REGIME_PORT = [string]$X12Port
$env:REGIME_ALLOWED_NETS = Read-Host '허용 팀원 IPv4/32 목록을 쉼표로 입력'

# 운영 정책은 항목마다 /32. 코드의 넓은 CIDR 지원과 운영 정책을 구분
$X12TeamNets = @($env:REGIME_ALLOWED_NETS.Split(',') | ForEach-Object { $_.Trim() })
if ($X12TeamNets.Count -lt 2 -or @($X12TeamNets | Where-Object { $_ -notmatch '^\d{1,3}(\.\d{1,3}){3}/32$' }).Count -gt 0) {
    throw '두 PC 수용용으로 A·B의 IPv4/32 항목을 확인. 빈 항목·/24 입력 제외'
}
uv run --no-sync --project api python -c "from regime_api.settings import Settings; s=Settings.from_env(); s.validate_auth(); assert s.auth_mode=='demo'; assert s.allowed_nets; print('X12 settings OK; auth=demo; allowed_entries=',len(s.allowed_nets))"
if ($LASTEXITCODE -ne 0) { throw 'X12 환경 검증 실패. 앱 시작 전 수정 필요' }
$X12TeamIps = @($X12TeamNets | ForEach-Object { $_.Split('/')[0] })
```

- 위 PS 정규식은 `/32` 운영 형식 확인용. 실제 IPv4 범위·RFC1918 여부는 코드의 Settings 검증으로 판정.
- 최소 2개 항목은 이번 두 PC 수용 준비 기준이며 앱 자체의 최소 허용 개수 요구사항 아님. 실제 A·B가 서로 다른 주소인지도 확인.
- 기존 뉴스 관련 환경이 이 터미널에 없으면 최종 BAT와 동일하게 보존·입력 후 다시 검증. 키 값을 명령 결과로 출력하지 않음.
- `REGIME_AUTH_MODE=demo` 명시로 기존 Supabase 환경변수 자동 감지를 피함. 기존 Supabase 키를 지우거나 설정 파일에 복사하는 단계 아님.

### 8.4 원본 없는 서버 환경 점검용 검사

해당 환경의 테스트 실행 확인이 필요한 경우, 앱을 정지한 유지보수 중 다음 신규 94건만 먼저 실행 가능. 원본 데이터를 만들거나 외부 모델을 호출하는 검사 아님.

```powershell
Set-Location -LiteralPath $X12Repo
New-Item -ItemType Directory -Path (Join-Path $X12Repo 'cache') -Force | Out-Null
$X12QaId = Get-Date -Format 'MMddHHmmss'
uv run --no-sync --project api python -B -m pytest api/tests/test_lan_allowlist.py -q --basetemp="cache/x12s-$X12QaId" -o "cache_dir=cache/x12sc-$X12QaId"
if ($LASTEXITCODE -ne 0) { throw 'X12 서버 환경 검사 실패' }
```

현재 병합 코드의 기준은 94 passed. 이후 develop에서 검사가 추가되면 건수가 달라질 수 있으며 실행 커밋·실제 결과를 함께 기록. 구현 PC의 과거 전체 회귀 1102 passed·8 skipped를 서버에서 다시 실행한 결과처럼 쓰지 않음.

## 9. 서버 전용 시작 BAT 수정 — 저장소 밖

### 9.1 기존 파일에서 바꿀 값

`$X12Bat`를 편집기로 열고 **기존 파일의 앱 실행 전 환경변수 부분만** 수정. 아래 예시를 파일 전체로 덮어쓰지 않음.

```bat
rem X12: direct LAN demo; approved team IPv4 /32 only
set "REGIME_AUTH_MODE=demo"
set "REGIME_HOST=0.0.0.0"
set "REGIME_PORT=8000"
set "REGIME_ALLOWED_NETS=<TEAM_A_IPV4>/32,<TEAM_B_IPV4>/32"
```

- `<TEAM_A_IPV4>`·`<TEAM_B_IPV4>`는 서버에서 확인한 실제 값으로 교체. 자리표시자를 그대로 저장하면 시작 검증 실패가 정상.
- 포트를 바꿨다면 BAT의 값도 `$X12Port`와 일치시킴.
- `set "NAME=value"`의 바깥 따옴표는 cmd 구문. 값에 따옴표 문자를 넣는 `set NAME="value"` 형식과 구분.
- `REGIME_ALLOWED_NETS`의 `/32` 목록은 앱 변수에는 쉼표로 연결된 문자열 하나, 방화벽에는 배열로 전달.
- 기존 `REGIME_HOST=127.0.0.1` 줄을 수정하고 뒤쪽 중복 설정도 확인. 주석의 “X12 병합 전 loopback 전용” 문구는 실제 반영 상태에 맞춰 수정.

### 9.2 유지할 내용

| 기존 설정 | 처리 |
|---|---|
| 저장소로 이동하는 `cd /d`·작업 폴더 | 현재 실제 저장소 기준 유지 |
| `uv` 절대 경로·실행 계정의 PATH 대응 | 기존 자동 시작이 정상인 방식을 유지 |
| 로그 폴더·표준 출력/오류 리디렉션 | 기존 위치·인코딩·보관 방식 유지 |
| SAMPLE·RUNS_DIR·데이터 경로 | 기존 운영 범위 보존 |
| NEWS_REFRESH·DATA_API_URL·DATA_API_TOKEN | 기존 승인된 서버 환경 보존. 새 토큰을 BAT·로그에 추가 기록하지 않음 |
| 종료 코드·중복 실행 방지·재시도 | 기존 관리 구조 보존 |
| 상태 점검 주소 | `http://127.0.0.1:<앱포트>` 유지. 팀원 주소로 바꾸지 않음 |
| 앱 진입점 | 공식 `regime-api` 유지. 여러 worker·reload·프록시 추가 제외 |

### 9.3 저장 후 확인

1. 같은 이름의 `.bat.txt`가 생성되지 않았는지 확인
2. 기존 문자 인코딩·줄바꿈 유지. 한글 주석을 추가했다면 cmd에서 깨지지 않는지 확인
3. 환경변수 설정이 최종 Python 프로세스에 전달되는 호출 흐름인지 확인
4. 배치 파일이 내부에서 `setlocal`을 써도 그 범위 안에서 앱을 실행하면 전달 가능. 앱 실행 뒤 환경 설정 또는 다른 창의 `$env:`만 바꾼 경우 전달 불가
5. 작업 실행 계정은 일반 터미널의 세션 환경을 자동 상속하지 않음. 수동 터미널에서 성공한 값만으로 BAT 적용 완료 판정 금지
6. 전체 파일·키·실제 IP를 채팅에 붙여 넣지 말고, 서버에서 수정 전후 비교

## 10. Windows 방화벽 실제 적용

### 10.1 팀원 `/32` 앱 포트 허용 규칙

관리자 PowerShell. §8.3과 다른 창이면 확인된 변수·팀원 목록을 같은 방식으로 다시 설정. 기본 인바운드 차단·겹치는 넓은 Allow 정리 계획이 준비된 상태에서 진행.

```powershell
$X12RuleName = 'regime-lab-x12-app-team'
$X12RuleDisplayName = 'regime-lab X12 app team IPv4'
$X12ExistingRule = Get-NetFirewallRule -Name $X12RuleName -ErrorAction SilentlyContinue
if ($null -ne $X12ExistingRule) {
    if ($X12ExistingRule.DisplayName -ne $X12RuleDisplayName) {
        throw '동일 Name의 다른 규칙 발견. 덮어쓰기 중단'
    }
    $X12OldPorts = $X12ExistingRule | Get-NetFirewallPortFilter
    $X12OldAddresses = $X12ExistingRule | Get-NetFirewallAddressFilter
    [pscustomobject]@{
        Name = $X12ExistingRule.Name
        Enabled = [string]$X12ExistingRule.Enabled
        Direction = [string]$X12ExistingRule.Direction
        Action = [string]$X12ExistingRule.Action
        Profile = [string]$X12ExistingRule.Profile
        Protocol = $X12OldPorts.Protocol
        LocalPort = $X12OldPorts.LocalPort
        RemoteAddress = $X12OldAddresses.RemoteAddress
        EdgeTraversalPolicy = [string]$X12ExistingRule.EdgeTraversalPolicy
    } | Export-Clixml -LiteralPath (Join-Path $X12Backup 'x12-rule-before.xml')
} else {
    'new-rule' | Set-Content -LiteralPath (Join-Path $X12Backup 'x12-rule-created.txt') -Encoding UTF8
}
if ($null -eq $X12ExistingRule) {
    New-NetFirewallRule -Name $X12RuleName -DisplayName $X12RuleDisplayName -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $X12Port -RemoteAddress $X12TeamNets -EdgeTraversalPolicy Block
} else {
    # 같은 이름이 이 문서의 X12 규칙인지 먼저 기존 필터 확인 후 실행
    $X12ExistingRule | Format-List Name, DisplayName, Direction, Action, Profile
    Set-NetFirewallRule -Name $X12RuleName -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $X12Port -RemoteAddress $X12TeamNets -EdgeTraversalPolicy Block
}
Get-NetFirewallRule -Name $X12RuleName | Format-List Name, Enabled, Direction, Action, Profile
Get-NetFirewallRule -Name $X12RuleName | Get-NetFirewallPortFilter
Get-NetFirewallRule -Name $X12RuleName | Get-NetFirewallAddressFilter
```

**동일 Name이 다른 운영 규칙이면 위 Set 명령 실행 중단**. 다른 규칙을 덮어쓰지 말고 전용 이름 확보 후 §17 복구 기록도 같은 이름으로 맞춤.

파라미터는 Microsoft [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule), [Set-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/set-netfirewallrule) 기준. 최종 RemoteAddress가 실제 A·B 각각의 `/32`인지 서버에서 확인.

### 10.2 겹치는 기존 광범위 허용 정리

§5에서 검토·백업한 정확한 규칙만 조정. 아래는 실행 형태 예시이며 대상 규칙 선택을 대신하지 않음.

```powershell
$X12BroadRuleName = Read-Host '검토 완료한 광범위 허용 규칙의 정확한 Name'
Get-NetFirewallRule -Name $X12BroadRuleName |
    Format-List Name, DisplayName, Enabled, Direction, Action, Profile
Get-NetFirewallRule -Name $X12BroadRuleName | Get-NetFirewallApplicationFilter
Get-NetFirewallRule -Name $X12BroadRuleName | Get-NetFirewallPortFilter
Get-NetFirewallRule -Name $X12BroadRuleName | Get-NetFirewallAddressFilter

# 다른 서비스 영향·백업·대체 허용 확인 후, 불필요한 해당 규칙만 비활성화
Disable-NetFirewallRule -Name $X12BroadRuleName
```

- 규칙 삭제 대신 비활성화를 우선하여 복구 가능성 확보.
- 모든 Python 규칙을 한 번에 선택·비활성화하지 않음.
- 넓은 Python 규칙을 유지해야 하는 사유가 있으면 앱 포트에서도 실제로 허용되는지 확인. 최종 방화벽 제한이 미충족이면 그 상태를 잔여로 기록하고 완료 처리 금지.
- “모두 차단” 명시 Block 규칙을 추가해 `/32` Allow로 예외를 만들려 하지 않음. 명시 Block이 허용 기기도 막을 수 있음.
- 방화벽을 끄거나 Public 전체 허용으로 접속 장애를 우회하지 않음.
- 기존 8100·3306 정책은 X12 앱 포트 변경과 분리. 이번 작업에서 임의 축소·개방하지 않음.

## 11. 앱 기동·서버 자체 정상 확인

### 11.1 수동 1회 확인 또는 기존 관리 경로 중 하나 선택

| 경로 | 실행 방식 | 조건 |
|---|---|---|
| 수동 콘솔 점검 | §8.3 환경을 설정한 일반 PowerShell에서 공식 명령 실행 | 작업이 멈춰 있고 같은 포트 리슨 없음. 콘솔 종료 후 §15에서 자동 시작 인계 |
| 기존 관리 경로 | 검토한 관리 스크립트 또는 기존 작업으로 실행 | 수정 BAT·정확한 저장소·환경 전달·중복 실행 방지 확인 완료 |

수동 점검 명령:

```powershell
Set-Location -LiteralPath $X12Repo
uv run --project api regime-api
```

이 창은 서버 실행용으로 유지. 나머지 확인은 별도 PowerShell에서 수행. 방화벽 팝업이 뜨면 모든 네트워크 허용을 추가로 승인하지 말고, §10의 지정 규칙을 사용.

관리 작업용 창을 계속 사용하려면 별도 실행 콘솔에서 `$X12Repo`·포트·§8.3의 X12 환경과 기존 뉴스/결과 설정을 먼저 동일하게 준비한 뒤 실행. 실행 콘솔을 열기만 하면 이전 창의 `$env:`와 `$X12...` 변수가 자동 복사되는 것으로 가정하지 않음.

기존 관리 스크립트에서 `-Restart` 지원과 정확한 대상이 확인된 경우에만 다음 형태 사용 가능:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $X12Manager -Restart
if ($LASTEXITCODE -ne 0) { throw '기존 관리 스크립트 재시작 실패' }
```

`Bypass`는 해당 검토된 스크립트 프로세스에만 적용하는 형태. 전역 실행 정책 완화·다운로드한 미검토 스크립트 실행 지시 아님. 관리 스크립트가 작업을 재등록·활성화하는지 원문 확인 후 사용. 수동 앱과 동시 실행 금지.

### 11.2 리슨·인증·준비 상태

별도 서버 PowerShell:

```powershell
$X12Port = 8000  # 실제 적용 포트
$X12LocalBase = "http://127.0.0.1:$X12Port"
Get-NetTCPConnection -State Listen -LocalPort $X12Port |
    Select-Object LocalAddress, LocalPort, OwningProcess
Invoke-RestMethod -Uri "$X12LocalBase/api/auth/config" -TimeoutSec 10
Invoke-RestMethod -Uri "$X12LocalBase/api/health" -TimeoutSec 10
Invoke-RestMethod -Uri "$X12LocalBase/api/meta" -TimeoutSec 10 |
    Select-Object status, scope, data_as_of, backtest_start
```

| 관측값 | 해석·조치 |
|---|---|
| `0.0.0.0:<앱포트>` 리슨 | IPv4 LAN 접속을 받는 상태. 이것만으로 허용 목록·방화벽 정상 증명 불가 |
| `127.0.0.1:<앱포트>`만 리슨 | 오래된 BAT·다른 작업·HOST 덮어쓰기 확인 |
| `/api/auth/config`의 `enabled=false` | 이번 데모 운영 모드 |
| `/api/health` HTTP 200·`status=warming_up` | 웹은 켜졌으나 분석 준비 중. 수용 작업 제출 전 대기 |
| HTTP 200·`status=ready`·`error=null` | 가격 분석 준비 완료 |
| HTTP 200·`status=failed` | 앱 프로세스는 살아 있으나 준비 실패. 경로·자료·권한·로그 확인 |
| `/api/meta`의 `scope` | 기존 계획한 `all` 또는 `sample30`인지 확인 |

가격 준비 완료에는 데이터 크기·캐시에 따라 시간이 필요. 200 응답만 보고 완료 처리하지 않고 상태 필드 확인. 실패 상태를 시간 초과로 간주해 무한 재시작하지 않음.

### 11.3 서버 브라우저

1. `http://127.0.0.1:<앱포트>` 열기
2. 데모의 테스트용 게스트 로그인 진입
3. 전략 화면·기록·기존 결과 조회
4. 새로고침 후 개발자 도구 Console 오류 확인
5. 강력 새로고침으로 오래된 정적 파일 캐시 영향 배제

LLM 설정 경계 검사는 §13에서 먼저 수행. 화면 입력 키는 재시작 시 지워지므로 마지막 자동 시작 인계·재시작을 마친 뒤 필요한 경우에만 서버 담당자가 직접 입력.

## 12. 허용·비허용 기기의 접속 수용

### 12.1 허용 PC A·B 각각 확인

각 팀원 PC에서 실행. 실제 서버 주소는 로컬 입력하며 공개 채팅에 기록하지 않음.

```powershell
$X12ServerIp = Read-Host '서버 IPv4'
$X12Port = 8000  # 실제 포트
$X12RemoteBase = "http://${X12ServerIp}:$X12Port"
Test-NetConnection -ComputerName $X12ServerIp -Port $X12Port
curl.exe --noproxy "*" --connect-timeout 5 --max-time 15 -i "$X12RemoteBase/api/health"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 15 -i "$X12RemoteBase/api/auth/config"
```

1. `TcpTestSucceeded=True` 확인
2. `/api/health` HTTP 200·`ready`, 인증 설정 `enabled=false` 확인
3. 브라우저에서 `$X12RemoteBase`와 같은 주소를 직접 열기
4. 테스트용 게스트 로그인 → 전략 화면 → 기존 실행 결과 조회
5. 정적 JS·CSS가 정상 로드되고 Console 오류가 없는지 확인
6. A·B 각각 현재 IPv4가 예약·앱 허용 목록·방화벽 목록과 일치하는지 서버에서 대조

`curl.exe --noproxy "*"`는 HTTP 프록시 환경의 영향을 피하기 위한 시험 옵션. 브라우저가 별도 프록시·VPN을 쓰면 서버가 보는 주소가 달라질 수 있으므로 브라우저 경로도 확인.

### 12.2 최종 방화벽 상태에서 비허용 기기 C 확인

기기 C는 A·B와 직접 통신 가능한 같은 시험 LAN의 기기이며 앱 허용 목록·최종 방화벽 목록 모두에 없는 주소 사용.

```powershell
$X12ServerIp = Read-Host '서버 IPv4'
$X12Port = 8000
$X12RemoteBase = "http://${X12ServerIp}:$X12Port"
Test-NetConnection -ComputerName $X12ServerIp -Port $X12Port
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i "$X12RemoteBase/api/health"
```

최종 방화벽 제한이 먼저 동작하면 TCP 연결 실패·시간 초과가 정상. HTTP 403이 보이지 않는다는 이유로 실패 처리하지 않음. 반대로 200·웹 화면이 보이면 즉시 원인 확인·공유 보류.

**연결 실패만으로 앱 필터 통과 판정은 불가.** 아래에서 C의 앱 도달을 해당 기기 한 대에 한해 일시 허용해 앱 계층을 따로 확인.

### 12.3 C 한 대의 임시 방화벽 예외로 앱 403 확인

조건: 운영자 입회·A/B 정상 확인·§10 최종 규칙 확보·C가 신뢰 가능한 시험 기기·테스트 시간 제한. `/24` 전체를 임시로 열지 않음. 앱 허용 목록은 A·B 그대로 유지하므로 앱 재시작 필요 없음.

서버 관리자 PowerShell에서 C 실제 IPv4 입력. `$X12TeamIps`·`$X12Port`는 기존 운영값 재확인.

```powershell
$X12DeniedIp = Read-Host '앱 허용 목록에 없는 시험 기기 C의 IPv4'
$X12ParsedC = [System.Net.IPAddress]::Parse($X12DeniedIp)
$X12CBytes = $X12ParsedC.GetAddressBytes()
$X12CPrivate = $X12ParsedC.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and (
    $X12CBytes[0] -eq 10 -or
    ($X12CBytes[0] -eq 172 -and $X12CBytes[1] -ge 16 -and $X12CBytes[1] -le 31) -or
    ($X12CBytes[0] -eq 192 -and $X12CBytes[1] -eq 168)
)
if (-not $X12CPrivate -or $X12TeamIps -contains $X12DeniedIp) {
    throw 'C는 허용 목록 밖 RFC1918 IPv4여야 함'
}
if ($X12ParsedC.ToString() -ne $X12DeniedIp -or $X12TeamIps.Count -lt 2) {
    throw '정규 IPv4 표기·현재 A/B 허용 목록 확인 필요'
}
$X12TempRuleName = 'regime-lab-x12-deny-test-' + [guid]::NewGuid().ToString('N')
try {
    New-NetFirewallRule -Name $X12TempRuleName -DisplayName 'regime-lab X12 temporary C test' -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $X12Port -RemoteAddress "$X12DeniedIp/32" -EdgeTraversalPolicy Block | Out-Null
    Read-Host '이 창을 유지하고 C에서 아래 HTTP 검사를 완료한 뒤 Enter'
} finally {
    Remove-NetFirewallRule -Name $X12TempRuleName -ErrorAction SilentlyContinue
}
```

예외 적용 중 C에서 다음 실행:

```powershell
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i "$X12RemoteBase/"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i "$X12RemoteBase/index.html"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i "$X12RemoteBase/api/health"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i "$X12RemoteBase/x12-does-not-exist"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i -X OPTIONS "$X12RemoteBase/api/runs"
```

5개 모두 HTTP 403·공통 JSON의 `code=network_not_allowed` 확인. 본문의 `detail=null`·`retryable=false`도 확인. 정적 HTML·404·OPTIONS 응답으로 필터를 우회하지 않아야 정상.

전달 헤더 우회 검사도 C에서 실행. 허용 PC의 실제 IP를 위조 헤더로 넣어도 실제 C 주소로 거절되어야 정상.

```powershell
$X12AllowedIpForHeader = Read-Host '헤더 우회 검사에 사용할 허용 PC IPv4'
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i -H "X-Forwarded-For: $X12AllowedIpForHeader" -H "X-Real-IP: $X12AllowedIpForHeader" -H "Forwarded: for=$X12AllowedIpForHeader" "$X12RemoteBase/api/health"
```

기대값: `403 network_not_allowed`. 어떤 헤더로든 200으로 바뀌면 공식 진입점·프록시·기동 프로세스 확인 후 완료 판정 보류.

검사 종료:

1. 서버의 대기 중인 Read-Host에서 Enter → finally의 임시 규칙 제거
2. 서버에서 `Get-NetFirewallRule -Name 'regime-lab-x12-deny-test-*' -ErrorAction SilentlyContinue`로 잔여 확인
3. 창 강제 종료 등으로 잔여가 생겼다면 해당 시험의 정확한 Name 확인 후 그 규칙만 제거
4. C의 TCP/HTTP 접속 실패 재확인
5. A·B의 HTTP 200 재확인
6. 최종 방화벽 목록이 A·B의 `/32`만 허용하는 상태인지 기록

명시 Block·중앙 정책·네트워크 격리가 C의 시험 요청을 먼저 막으면 임시 Allow로도 403 확인 불가. 전체 방화벽을 끄지 말고 통제된 시험 경로를 확보하거나 앱 계층 검증 미확인으로 기록.

## 13. 원격 LLM 설정 제한 수용

### 13.1 검사 순서·전제

- 서버 재시작 직후, UI에 API 키를 입력하기 전 검사를 우선 수행.
- 실제 키·모델 호출 불필요. 허용 PC A·B에서 각각 경계만 확인.
- GET은 기존대로 설정 조회 가능. GET 성공을 변경 권한으로 해석하지 않음. 응답의 키 힌트도 공개 결과에서 제거.
- PUT 검사가 403이 아니면 DELETE를 이어 실행하지 않고 원인 확인. DELETE는 경계 오류 시 UI 설정을 초기화할 수 있으므로 초기 상태·서버 담당 입회 조건 유지.

### 13.2 A·B의 실제 원격 주소에서 검사

```powershell
# 허용 PC A 또는 B. $X12RemoteBase는 해당 PC에서 설정한 서버 주소
$X12LlmProbe = @{ model = 'x12-boundary-check' } | ConvertTo-Json -Compress
$X12LlmProbe | curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i -X PUT -H "Content-Type: application/json" --data-binary '@-' "$X12RemoteBase/api/llm/config"
```

기대값: HTTP 403·`code=local_only`. 422·200·500은 통과로 보지 않음. 실제 API 키를 보내지 않음. JSON은 표준 입력으로 전달하여 Windows PowerShell의 네이티브 명령 따옴표 전달 차이를 피함.

PUT이 정확히 `403 local_only`인 것을 확인한 뒤:

```powershell
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 -i -X DELETE "$X12RemoteBase/api/llm/config"
```

기대값: HTTP 403·`code=local_only`. `network_not_allowed`이면 해당 PC 자체가 허용 목록 밖인 것이므로 LLM 경계 검증 성공으로 집계하지 않음.

서버 PC의 loopback에서 `GET /api/llm/config`가 정상이며 설정 화면이 열리는지 확인. 실제 모델 사용이 필요할 때만 §15의 마지막 재시작 뒤 사용자가 서버 PC의 `127.0.0.1` 설정 화면에 키 직접 입력. 이 문서의 네트워크 수용만을 위해 유료 모델을 호출할 필요 없음.

## 14. 두 PC 동시 작업·X13 대기→완료 수용

### 14.1 시험 성립 조건

| 조건 | 이유 |
|---|---|
| 실제 A·B 두 기기·서로 다른 서버 관측 IPv4 | 데모 요청자 키가 IP. 같은 PC의 두 창·프록시 경유 동일 주소는 독립 요청자 아님 |
| 가격 분석 `ready` | 작업이 실제 실행·완료되어야 수용 가능 |
| 다른 이용자 작업 없는 시간 | 시험의 FIFO·순번·시간 근거 명확화 |
| 첫 작업이 실행 중일 때 둘째 제출 | 첫 작업이 이미 끝나면 대기열을 확인한 것이 아님 |
| 공용 FIFO·동시 계산 1 유지 | 포트·프로세스·worker를 늘려 동시 실행하는 방식 제외 |
| 제한 수치 유지 | 현재 설정 `jobs.queue_max=5`, `per_user=1`. 시험 때문에 값 변경 제외 |

### 14.2 UI 기준 필수 시나리오

**시험 Q1 — A 실행 → B 실행**

1. A에서 전략 만들기 진입, 기존 정상 데이터·기간·패턴으로 시험용 이름 설정
2. A 실행 시작, 진행 화면에서 실제 계산 중 상태 확인
3. A가 끝나기 전에 B에서 별도 시험용 이름으로 실행 제출
4. B에서 대기 상태·대기 순번 표시 확인
5. A 완료 후 B가 실행 상태로 전환되는지 확인
6. B 완료 및 결과 화면 진입 확인
7. A/B 작업 ID·상태 전환·시간을 기록

**시험 Q2 — A 실행 → B 역방향 탐색**

1. Q1의 모든 작업 종료 확인
2. A에서 다시 실행 시작
3. A 계산 중 B에서 “목표로 전략 찾기”로 탐색 제출
4. B의 후보 수를 미리 확인하고 작은 유효 조합으로 제한. 필요 없는 대규모 탐색 금지
5. B 탐색이 같은 대기열에서 대기하는지 확인
6. A 완료 후 B 탐색 실행·완료·결과 또는 정상 빈 결과 확인

**시험 Q3 — 같은 PC 중복 제출 제한**

1. A 작업이 queued 또는 running인 동안 A에서 추가 실행 또는 탐색 제출
2. HTTP 409·`code=user_busy` 또는 이에 대응하는 화면 안내 확인
3. 두 번째 작업이 실제로 추가되지 않았는지 확인
4. 첫 작업 완료 뒤 A의 다음 제출은 정상 접수되는지 확인

빈 후보·거래 없음은 정상 완료일 수 있음. 최종 상태가 `completed`인지 확인. `failed`·`cancelled`를 대기→완료 통과로 집계하지 않음.

### 14.3 API로 ID·상태 근거 확보 — UI와 병행 가능

기본 예시는 유효 패턴 하나·기존 기본 기간. 첫 작업이 너무 빨리 끝나면 정상 전략 비교 등 실제로 필요한 범위에서 작업 시간을 확보하고 두 사람이 준비해 재시험. 서버 코드에 sleep 삽입·테스트 대역 연결·전 종목 설정 강제 변경 제외.

PC A:

```powershell
$X12RunBodyA = @{
    strategies = @(@{
        name = 'x12_a_' + (Get-Date -Format 'HHmmss')
        patterns = @('breakout_20d')
    })
} | ConvertTo-Json -Depth 8
$X12RunA = Invoke-RestMethod -Uri "$X12RemoteBase/api/runs" -Method Post -ContentType 'application/json' -Body $X12RunBodyA
$X12RunA | Select-Object run_id, status, queue_position
$X12RunIdA = $X12RunA.run_id
Invoke-RestMethod -Uri "$X12RemoteBase/api/runs/$X12RunIdA" |
    Select-Object run_id, status, queue_position, created_at, finished_at, elapsed_sec
```

A가 running임을 확인한 직후 PC B에서 Q1용 실행 제출:

```powershell
$X12RunBodyB = @{
    strategies = @(@{
        name = 'x12_b_' + (Get-Date -Format 'HHmmss')
        patterns = @('ma_cross_5_20')
    })
} | ConvertTo-Json -Depth 8
$X12RunB = Invoke-RestMethod -Uri "$X12RemoteBase/api/runs" -Method Post -ContentType 'application/json' -Body $X12RunBodyB
$X12RunB | Select-Object run_id, status, queue_position
$X12RunIdB = $X12RunB.run_id
```

**접수 기대값**: HTTP 202·`status=queued`. 실제 대기를 확인하려면 B의 `queue_position>=1` 또는 A running과 같은 시점의 B queued 관측 필요. 순번 0은 계산 슬롯 확보·시작 중이므로 그것만으로 대기 증거 불충분.

PC B에서 2초 간격·최대 10분 상태 관측 예시:

```powershell
$X12Deadline = (Get-Date).AddMinutes(10)
do {
    $X12SnapB = Invoke-RestMethod -Uri "$X12RemoteBase/api/runs/$X12RunIdB" -TimeoutSec 15
    [pscustomobject]@{
        ObservedKst = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Korea Standard Time').ToString('yyyy-MM-dd HH:mm:ss')
        Id = $X12SnapB.run_id
        Status = $X12SnapB.status
        QueuePosition = $X12SnapB.queue_position
        ElapsedSec = $X12SnapB.elapsed_sec
    }
    if ($X12SnapB.status -in @('completed', 'failed', 'cancelled')) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $X12Deadline)
```

10분은 관측 예시의 중단 시점이며 제품 실행 제한 아님. 시간 초과 시 작업이 자동 취소되는 것도 아님. 현재 상태·원인을 확인하고 관측 연장 또는 시험 작업 취소를 운영자가 결정.

Q2용 B의 최소 탐색 예시. 기존 Q1 종료 후 A의 새 작업 실행 중에 제출:

```powershell
$X12SearchBody = @{
    name = 'x12_search_b_' + (Get-Date -Format 'HHmmss')
    target_win_rate = 0.55
    axes = @{
        patterns = @('breakout_20d')
        combine = @('or')
        stop_loss_pct = @(-8.0)
        take_profit_pct = @(20.0)
        max_hold_days = @(20)
    }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri "$X12RemoteBase/api/searches/preview" -Method Post -ContentType 'application/json' -Body $X12SearchBody
# within_limit=true·의도한 작은 후보 수 확인 후 다음 실행
$X12SearchB = Invoke-RestMethod -Uri "$X12RemoteBase/api/searches" -Method Post -ContentType 'application/json' -Body $X12SearchBody
$X12SearchIdB = $X12SearchB.search_id
$X12SearchB | Select-Object search_id, status, queue_position, candidates
Invoke-RestMethod -Uri "$X12RemoteBase/api/searches/$X12SearchIdB" |
    Select-Object search_id, status, queue_position, created_at, finished_at, elapsed_sec
```

후속 상태 조회는 `/api/searches/<search_id>`로 반복, 완료 결과는 `/api/searches/<search_id>/result`. API 시나리오 실행은 서버 `runs`에 시험 기록을 만들므로 식별 가능한 이름 사용. 원본 수정 없음.

### 14.4 실패·재시험 판정

| 관측 | 판정 | 확인 사항 |
|---|---|---|
| A 완료 뒤 B 제출·곧바로 실행 | 대기 수용 미확인 | 제출 시점 맞춰 재시험 |
| B에서 `409 user_busy` | 서로 다른 요청자로 인식되지 않았거나 B 기존 작업 존재 | A/B 실제 접속 IP·프록시·VPN·기존 작업 확인 |
| `429 queue_full` | 시험 외 대기열 포화 가능 | 기존 이용자 작업 확인, 상한을 늘리지 말고 비운 뒤 재시험 |
| A/B 실제 계산이 동시에 수행 | 공용 단일 계산 위반 의심 | 중복 서버·여러 worker·별도 인스턴스 확인 |
| `failed(server_restarted)` | 시험 중 재시작 발생 | 자동 재기동·수동 서버 충돌·오류 로그 확인 |
| B queued 관측 후 running→completed | 대기 수용 통과 후보 | A 완료와 B 시작 순서·결과 확인까지 기록 |

### 14.5 대기 취소·시험 기록 정리 — 보조 확인

필수 Q1/Q2 완료 증거를 먼저 확보한 뒤 수행. 다른 사용자 작업은 취소하지 않음.

- 실행 취소: `POST /api/runs/<시험 run_id>/cancel`
- 탐색 취소: `POST /api/searches/<시험 search_id>/cancel`
- 대기 중 취소는 `cancelled`·`cancelled_before_start`, 계산 미수행 확인
- 실행 중 취소와 대기 취소는 다른 경로. 취소된 시험을 완료 증거로 사용하지 않음
- 완료된 시험 실행 기록은 운영자가 기존 화면 삭제 기능으로 정리 가능. 실행 결과는 `runs/.deleted/` 이동 정책 유지
- 탐색 기록은 기존 제품이 제공하는 기능 범위에서만 관리. 폴더 직접 삭제·원본 정리·전체 runs 초기화 제외

## 15. 자동 시작 인계·재시작 후 지속성 확인

### 15.1 수동 점검 앱에서 기존 작업으로 인계

1. Q1/Q2와 모든 실제 작업이 종료되었는지 확인
2. 수동 콘솔로 실행했다면 해당 창에서 `Ctrl+C`
3. 앱 포트 리슨 없음 확인
4. 수정 BAT·실행 계정·작업 경로가 맞는지 재확인
5. 원래 자동 시작 운영 대상으로 확인된 작업만 활성화·기동

```powershell
Enable-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath | Out-Null
Start-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath
Get-ScheduledTask -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath |
    Select-Object TaskName, TaskPath, State
Get-ScheduledTaskInfo -TaskName $X12Task.TaskName -TaskPath $X12Task.TaskPath
```

기존 관리 스크립트가 작업 등록·기동까지 담당한다면 검토한 그 경로만 사용. 위 Start와 관리 스크립트를 중복 실행하지 않음.

### 15.2 작업 실행 환경에서 핵심 검사 반복

- [ ] 포트 리슨·정확한 앱 프로세스 1개 확인
- [ ] 서버 loopback health `ready`, 데모 인증 `enabled=false`
- [ ] A·B 웹/API 정상
- [ ] C의 최종 방화벽 차단 유지
- [ ] 허용 PC의 LLM PUT·DELETE `403 local_only` 유지
- [ ] 수정 BAT의 값이 적용되었는지 실제 결과로 확인
- [ ] 로그에 반복 기동·포트 충돌·권한 오류 없음
- [ ] 임시 C 방화벽 규칙 없음

필요한 경우 §12.3의 C 앱 403 검사도 최종 작업 프로세스에서 재수행. 수동 환경과 작업 환경의 서로 다른 BAT·변수·프록시 경로를 배제하기 위한 확인.

### 15.3 계획된 재부팅 확인

- 재부팅은 DB·데이터 API·수집 작업에도 영향. 서버 담당이 별도 운영 시간에 수행.
- 재부팅 전 실제 작업 종료 확인. 이 문서 작성이나 X12 병합만으로 지금 재부팅할 필요 없음.
- 재부팅 뒤 직접 로그인하지 않아도 기존 정책대로 작업이 켜지는지 확인. 사용자 로그온 트리거와 부팅 트리거는 다른 조건.
- 작업의 시작 지연·재시도 횟수는 실제 등록값 확인. 과거 문서의 “60초·3회”를 현재 사실로 단정하지 않음.
- 서버·A·B 주소가 DHCP 예약대로 유지되는지 확인.
- §15.2 핵심 검사를 다시 수행하고 부팅 자동 시작 결과를 별도 기록.
- 재부팅 검사를 미뤘다면 자동 시작의 부팅 지속성은 미확인으로 남김. 실제 LAN 수용 AC12와 운영 부팅 검사 상태를 구분.

### 15.4 LLM·뉴스·기존 운영 기능 보존

- UI 입력 모델 API 키는 재시작 시 사라질 수 있음. 마지막 재시작 뒤 사용자가 서버 PC loopback 설정 화면에 직접 입력.
- 키를 BAT·WBS·문서·브라우저 저장소·서버 로그에 새로 기록하지 않음.
- 기존에 승인되어 사용 중인 뉴스 갱신만 보존. 서버 앱의 `REGIME_NEWS_REFRESH=1`·내부 DATA_API 설정을 작업 프로세스도 받는지 확인.
- 활성 출처 대장·권한 범위를 확인하기 전 새 출처를 켜지 않음. 기사 사본을 팀원 PC로 복사하지 않음.
- 뉴스 사본 `status.json`의 정상/지연 상태·현재 검색 결과를 기존 운영 방식으로 확인. 새 스냅샷·장애 복구 전체 실험은 별도 P3-11 운영 검사와 구분.
- 뉴스 기능 장애를 X12 네트워크 코드 성공으로 숨기지 않되, 네트워크 수용 결과와 뉴스 연동 결과는 항목을 나누어 기록.
- 새벽 자동 갱신·경로 전환·prepare·재시작 스크립트가 BAT를 덮어쓰면 X12 값이 사라질 수 있으므로 관련 호출 파일도 확인. X14 구현 완료로 간주하지 않음.

## 16. 최종 수용표·완료 판정·WBS 갱신

### 16.1 X12 필수 수용표

현재 표는 작성 시점 기준 **미실행**. 실제 서버에서 수행한 결과만 채움.

| ID | 검사 | 기대값 | 작성 시점 결과 |
|---|---|---|---|
| S01 | 서버 develop 반영 | `c092d82` 포함하는 실제 커밋 확인 | 미확인 |
| S02 | 공식 실행·프로세스 | demo·직접 Uvicorn·단일 앱 프로세스 | 미확인 |
| S03 | 실제 설정 | A·B별 RFC1918 IPv4 `/32`, 방화벽 동일 범위 | 미확인 |
| S04 | 서버 loopback | 웹 정상·health ready·게스트 진입 | 미실행 |
| S05 | 허용 PC A | 웹/API·기존 결과 정상 | 미실행 |
| S06 | 허용 PC B | 웹/API·기존 결과 정상 | 미실행 |
| S07 | 비허용 C·앱 도달 시험 | 5개 경로/메서드 모두 `403 network_not_allowed` | 미실행 |
| S08 | 위조 전달 헤더 | C에서 위조해도 `403 network_not_allowed` | 미실행 |
| S09 | 최종 방화벽 | C 연결 실패, A·B 정상, 넓은 허용·임시 예외 없음 | 미실행 |
| S10 | 허용 원격 LLM PUT·DELETE | 각각 `403 local_only` | 미실행 |
| S11 | A 실행→B 실행 | B queued·순번 확인→A 완료→B running→completed | 미실행 |
| S12 | A 실행→B 탐색 | 같은 대기열에서 순서 보존·둘 다 completed | 미실행 |
| S13 | 같은 PC 중복 제출 | `409 user_busy`, 추가 작업 생성 없음 | 미실행 |
| S14 | 최종 자동 시작 작업 환경 | 수정 BAT로 실제 기동·핵심 접속 경계 재확인 | 미실행 |
| S15 | 결과·설정 보존 | 기존 경로·확정 기준일·원본·다른 서비스 보존 | 미확인 |

### 16.2 운영 보조 검사

| 항목 | 판정 원칙 |
|---|---|
| 신규 94건 서버 환경 검사 | 실행했다면 커밋·건수·skip·실패 기록. 미실행이면 구현 PC 결과와 구분 |
| 부팅 자동 시작 | 재부팅 후 확인 여부 별도 기록. 미실행 상태를 완료로 표시하지 않음 |
| 기존 뉴스 자동 갱신 | 기존 승인 운영에 포함되어 있다면 보존 결과 기록. 별도 출처 활성·장애 실험 상태와 구분 |
| 대기 취소 | 실제 수행 여부·시험 ID 기록. 완료 시험을 취소로 대체하지 않음 |
| 429 상한 | 운영 서버를 일부러 포화시킬 필요 없음. 기존 계약 테스트 근거와 구분 |

### 16.3 WBS 갱신 원칙

| 작업 | 현재 문서상 상태 | 갱신 조건 |
|---|---|---|
| X12.1 | 완료100% | 구현 완료 상태 유지 |
| X12.2 | 진행 중 | 로컬 테스트·문서 외 실제 서버 수용까지 통과한 뒤 완료100% |
| 부모 X12 | 진행 중60% | 완료 확정 작업량 0.3/0.5. X12.2의 0.2까지 인정하면 100% |
| X13.4·부모 X13 | 기존 상태 유지 | 실제 두 PC 대기→완료 결과가 해당 수용 조건을 충족하는지 대조한 뒤 별도 갱신 |
| X11·X14·예측 S8 연계 | 자동 완료 처리 제외 | 각각 고유한 설치·갱신·연계 완료 조건을 따로 확인 |

- 병합만으로 X12 100% 처리하지 않음. S01~S15의 필수 수용을 근거로 전체 완료 판정.
- 현재 문서 작성은 실제 서버 시험이 아니므로 이 문서 저장과 동시에 WBS 진행률을 올리지 않음.
- 실제 완료일은 서버 수용 통과일, 구현 PC 테스트일로 소급하지 않음.
- WBS CSV는 대상 행만 수정하고 BOM·CRLF·비대상 행 원문 보존. 담당·작업량·선행·일정은 이번 실적 기록 때문에 임의 변경하지 않음.
- 실제 IP·MAC·서버 주소·키·원문 로그는 공개 WBS·진행 기록에 포함하지 않음.
- 결과 기록은 `PEANUTBUTTER1001`, 리뷰는 `hongsungmin0315`. 서버 운영 확인에 참여한 계정과 수행 범위를 함께 기록하되 리뷰 승인·미수행을 구분.

## 17. 장애 대응·안전한 복구

### 17.1 원인별 점검 순서

| 증상 | 우선 확인 | 조치 |
|---|---|---|
| non-loopback demo 설정 오류 | MODE·HOST·ALLOWED_NETS가 같은 프로세스에 전달되는지 | BAT 중복/누락/자리표시자 수정 후 검증 |
| `Invalid REGIME_ALLOWED_NETS entry` | 공인/특수 주소·IPv6·잘못된 `/32`·host bits | 실제 RFC1918 IPv4/32로 수정. 값을 오류 채팅에 붙여 넣지 않음 |
| 서버 local 정상·원격 TCP 실패 | 리슨 주소·Private 프로필·포트·유효 방화벽·VLAN | `0.0.0.0` 리슨과 실제 정책 대조 |
| 허용 PC가 `network_not_allowed` | 현재 DHCP 주소·BAT 허용 목록·프록시/VPN | 예약·실제 접속자 주소 일치 확인 |
| C가 정상 화면·API 200 | MODE=Supabase·다른 앱·필터 없음·프록시 | 즉시 공유 보류, 공식 데모 기동 구성 확인 |
| 원격 LLM 200 | 서버가 원격을 loopback으로 보는 프록시 등 | 공유 보류·프록시 제거 계획·공식 진입점 확인 |
| 503 warming_up | 가격 준비 또는 검색 인덱스 준비 중 | health와 해당 기능 상태 구분, 준비 완료 대기 |
| health failed | 가격 경로·권한·캐시·자료 부재 | 로그 확인·원인 수정. 원본 덮어쓰기 제외 |
| 기록이 사라져 보임 | 다른 RUNS_DIR·다른 저장소·다른 실행 계정 | 기존 결과 경로로 복귀, 삭제로 단정하지 않음 |
| 409 user_busy가 B에도 발생 | 같은 IP·프록시·B 기존 작업 | 직접 접속·작업 종료 상태 확인 |
| 자동 시작만 실패 | 계정·WorkingDirectory·uv 경로·환경변수·로그 권한 | 수동 성공 환경과 차이 대조 |
| 재시작 반복·포트 충돌 | 수동 앱+작업 동시 실행·감시 스크립트 | 정확한 앱 인스턴스 한 개로 정리 |
| 뉴스만 실패 | 기존 NEWS_REFRESH·내부 API·경로·활성 출처 | P3-11 운영 문서 기준 확인, 팀원 사본 배포로 우회하지 않음 |

### 17.2 긴급히 LAN 공유만 닫기

기본 대응은 X12 관련 앱 허용을 닫고 loopback으로 복귀. 방화벽 전체 초기화·데이터 삭제는 필요x.

1. 담당자에게 장애·잠정 공유 중단 통보
2. 정확한 X12 규칙이 이번에 신규 생성된 규칙이면 비활성화. 기존 규칙을 수정한 경우에는 백업 정책에 따라 원복
3. C 임시 시험 규칙이 남았으면 해당 Name만 제거
4. 진행 작업 확인 후 기존 앱 정지
5. 서버 전용 BAT의 해당 환경을 아래처럼 변경

```bat
set "REGIME_AUTH_MODE=demo"
set "REGIME_HOST=127.0.0.1"
set "REGIME_ALLOWED_NETS="
```

6. 공식 진입점으로 기존 단일 앱 기동
7. 리슨이 `127.0.0.1:<앱포트>`인지 확인
8. 서버 loopback 정상·A/B 원격 연결 불가 확인
9. 원인 해결 후 §8.3 이후를 다시 적용·수용

신규 X12 방화벽 규칙 비활성화 명령:

```powershell
# 이번에 새로 만든 이 규칙이 맞는지 Name·범위 확인 후 수행
Get-NetFirewallRule -Name 'regime-lab-x12-app-team' |
    Format-List Name, DisplayName, Enabled, Direction, Action, Profile
Disable-NetFirewallRule -Name 'regime-lab-x12-app-team'
```

기존 광범위 Python Allow가 남으면 위 규칙만 꺼도 LAN이 닫힌다고 보장할 수 없음. **loopback 바인딩 복귀와 실제 원격 실패까지 확인**.

### 17.3 BAT·작업·방화벽 원복

- §3 백업의 정확한 시작 파일을 원래 `$X12Bat` 경로에 복원. 복원 전 현재 실패 구성을 서버 로컬 별도 이름으로 보존 가능.
- 현재 원인과 무관한 관리 스크립트를 새로 덮어쓰지 않음. 이번에 실제 수정한 파일만 복원.
- 비활성화한 기존 규칙은 백업의 원래 Enabled 상태에 맞춰 해당 Name만 복원.
- 이번에 수정한 기존 X12 규칙은 원래 주소·포트·프로필·활성 상태로 복원. 새 규칙을 기존 규칙처럼 취급하지 않음.
- 작업 XML을 다시 등록하면 계정·암호·트리거·동작을 재확인해야 할 수 있음. 이번에 작업 정의를 바꾸지 않았다면 XML 재등록보다 기존 작업의 원래 활성 상태 복원이 우선.
- 전체 `.wfw` import는 다른 서비스·동시 운영 변경까지 덮어쓸 수 있어 일반 복구 경로에서 제외.
- 서버가 Supabase였던 별도 환경의 복구를 데모 BAT 예시로 대체하지 않음. 이 문서 §1.2에서 구분한 기존 모드 기준 복구.

앱 정지·정확한 백업 선택 후 BAT 복원 예시:

```powershell
$X12BackupBat = Join-Path $X12Backup 'run_app_task.bat'
if (-not (Test-Path -LiteralPath $X12BackupBat)) { throw '시작 파일 백업 없음' }
Copy-Item -LiteralPath $X12BackupBat -Destination $X12Bat -Force
# 복원 파일의 모드·HOST·포트를 서버 화면에서 확인한 뒤 기동
```

기존 X12 규칙을 수정했던 경우에만 해당 백업을 검토한 뒤 복원:

```powershell
$X12RuleBackup = Join-Path $X12Backup 'x12-rule-before.xml'
if (-not (Test-Path -LiteralPath $X12RuleBackup)) { throw '기존 X12 규칙 백업 없음. 신규 규칙과 구분 필요' }
$X12OldRule = Import-Clixml -LiteralPath $X12RuleBackup
$X12OldRule | Format-List Name, Enabled, Direction, Action, Profile, Protocol, LocalPort, RemoteAddress, EdgeTraversalPolicy
if ($X12OldRule.Name -ne 'regime-lab-x12-app-team') { throw '복원 대상 규칙 Name 확인 필요' }
Set-NetFirewallRule -Name $X12OldRule.Name -Enabled $X12OldRule.Enabled -Direction $X12OldRule.Direction -Action $X12OldRule.Action -Profile $X12OldRule.Profile -Protocol $X12OldRule.Protocol -LocalPort $X12OldRule.LocalPort -RemoteAddress $X12OldRule.RemoteAddress -EdgeTraversalPolicy $X12OldRule.EdgeTraversalPolicy
```

기존에 비활성화한 광범위 허용은 다른 서비스 필요 여부와 원래 상태를 확인한 뒤 해당 Name에만 `Enable-NetFirewallRule -Name <정확한 Name>` 적용. 보안을 위해 제거한 불필요한 광범위 허용을 복구라는 이유만으로 다시 켜지 않음. loopback 복귀 상태와 실제 다른 서비스 정상 여부로 판단.

### 17.4 소스 코드도 되돌려야 하는 경우

LAN 공유를 닫아도 앱 기동 실패 등 코드 원인이 남을 때만 검토. 서버 담당이 §3에서 기록한 기존 정상 커밋을 확인하고, 앱 정지·미커밋 변경 보존 후 임시 복귀.

```powershell
Set-Location -LiteralPath $X12Repo
git status --short --branch
# 변경이 있으면 여기서 중단. 아래는 작업 트리가 깨끗한 상태에서만 수행
$X12PreviousCommit = (Get-Content -LiteralPath (Join-Path $X12Backup 'previous-commit.txt') -Raw).Trim()
git show --no-patch --oneline $X12PreviousCommit
if ($LASTEXITCODE -ne 0) { throw '복귀 커밋 확인 실패' }
git switch --detach $X12PreviousCommit
if ($LASTEXITCODE -ne 0) { throw '임시 복귀 실패' }
uv sync --locked --project engine
if ($LASTEXITCODE -ne 0) { throw '복귀 engine 환경 실패' }
uv sync --locked --project api
if ($LASTEXITCODE -ne 0) { throw '복귀 api 환경 실패' }
```

- detached HEAD는 서버의 임시 코드 복귀 상태. 새 작업·커밋·develop 강제 갱신을 위한 지시 아님.
- 이전 코드가 X12를 포함하지 않으면 LAN 바인딩이 불가능할 수 있으므로 loopback BAT로 복귀.
- 소스 복귀가 데이터·결과 폴더를 과거 상태로 복구하는 것은 아님. 이번 X12는 형식 마이그레이션을 수행하지 않으며 결과·원본은 그대로 보존.
- 문제 해결 후 재적용 시 앱 정지·작업 트리 확인 → `git switch develop` → `git pull --ff-only origin develop` → 동기화 → 환경 검증 → 수용 순서.
- `git reset --hard`, `git clean -fdx`, 전체 runs/cache 삭제는 복구 명령에 포함하지 않음.

## 18. 이후 운영·주소 변경·팀원 추가

### 18.1 일상 확인

- 서버 전원·절전·네트워크 연결과 자동 시작 상태 확인. 전원 설정 변경은 다른 서비스 운영 요구와 함께 결정.
- 앱 health ready, 프로세스·리슨·대기열 상태, 디스크·로그 용량 확인.
- 실패한 가격 준비·뉴스 갱신을 빈 정상 데이터로 간주하지 않음.
- 키·토큰·전체 환경변수·주소 목록을 일반 로그로 출력하지 않음.
- 관리자용 상태 점검 URL은 loopback 유지. 팀원은 LAN URL 사용.

### 18.2 새 팀원 추가

1. 서버 운영 담당과 대상 기기·권한 범위 확인
2. 해당 기기의 DHCP 예약·실제 IPv4 고정 확인
3. 서버 BAT의 `REGIME_ALLOWED_NETS`에 해당 IPv4 `/32` 추가
4. X12 방화벽 규칙의 RemoteAddress에도 동일 `/32` 추가
5. 기동 중 앱은 허용 목록을 메모리에 보유하므로 BAT만 수정해 즉시 반영되는 것이 아님
6. 진행 작업 종료·유지보수 합의 후 앱 재시작
7. 기존 A/B·새 팀원 정상, 비허용 기기 차단·LLM 제한 확인
8. 실제 주소는 서버에만 기록, 공개 문서에는 변경 시각·인원 수·통과 항목만 기록

### 18.3 팀원 제거·분실·IP 변경

- 제거할 대상은 방화벽 허용에서 먼저 제외하고 앱 허용 목록에서도 제거 후 계획된 재시작.
- IP가 다른 사람에게 재할당되면 `/32`만으로 그 사람이 기존 기기와 구분되지 않음. DHCP 예약·기기 대장 유지 필요.
- IP 변경은 “앱 BAT 목록·방화벽 목록·공유기 예약” 세 곳을 일치시킴.
- IP 기반 데모는 인증된 개인 계정 권한을 제공하지 않음. 외부 공개·기기 신뢰 범위 변경이 필요하면 별도 설계·승인 대상.

### 18.4 포트·도메인·HTTPS 변경

- 이번 X12에서는 기존 앱 포트 유지가 기본.
- 포트 변경 시 BAT·방화벽·작업 설명·관리 스크립트 상태 URL·팀원 URL·문서 모두 일치 필요.
- 이름 주소가 필요하면 승인된 내부 DNS/hosts 운영 별도 결정. 이름이 IPv6를 우선 선택하는 상황과 실제 IPv4 대상 확인.
- 포트 없는 주소를 만들기 위해 로컬 역방향 프록시를 추가하지 않음. 현재 LLM loopback 경계와 충돌 가능.
- HTTPS·외부 공개·포트포워딩·터널은 이번 절차의 후속 선택 항목으로 몰래 포함하지 않음. 별도 요구·접근 설계가 필요한 범위.

## 19. 서버 담당에게 넘길 실행 체크리스트·결과 기록 양식

### 19.1 순서대로 체크

- [ ] §2 현재 경로·작업·프로세스·모드·진행 작업 확인
- [ ] §3 서버 로컬 백업·기존 커밋·복구 기준 확보
- [ ] §4 서버·A·B 주소 예약 및 직접 접속 경로 확인
- [ ] §5 유효 방화벽·광범위 허용·중앙 정책 확인
- [ ] §6 유지보수 시점 합의·앱 정지·포트 해제
- [ ] §7 develop 수신·X12 커밋 포함 확인
- [ ] §8 lockfile 동기화·경로·설정 검증·필요 시 94건 검사
- [ ] §9 서버 전용 BAT에 demo·HOST·PORT·팀원 `/32` 반영
- [ ] §10 최종 방화벽 앱 규칙·기존 겹침 정리
- [ ] §11 서버 기동·loopback·health ready·화면 확인
- [ ] §12 A/B 허용·C 앱403·헤더 우회 불가·최종 방화벽 차단
- [ ] §13 원격 LLM PUT/DELETE local_only
- [ ] §14 두 PC 실행/탐색 대기→완료·동일 PC 제한
- [ ] §15 자동 시작 작업으로 최종 인계·임시 예외 제거·핵심 재검사
- [ ] §16 실제 통과 항목·잔여·완료일 기록
- [ ] §17 복구 파일·규칙 원래 상태·재적용 경로 확인
- [ ] 재부팅 확인은 실제 수행 여부를 별도로 표시

### 19.2 공개 진행 기록용 양식 — 실제 주소·키 제외

```markdown
## YYYY-MM-DD HH:mm:ss KST — X12 서버 적용·실기기 수용

- 수행 계정:
- 검토 계정:
- 적용 커밋:
- 운영 모드: demo / 공식 regime-api / 단일 앱 프로세스 / 직접 접속
- 허용 정책: 팀원별 IPv4 /32, 실제 값은 서버 설정에만 보관
- 서버 loopback·health ready:
- 허용 PC A·B 웹/API:
- 비허용 C 앱 도달 시험: 정적·API·없는 경로·OPTIONS 403 network_not_allowed
- C 위조 전달 헤더: 403 network_not_allowed
- 최종 방화벽: C 연결 차단·A/B 정상·광범위 예외 없음
- 원격 LLM PUT·DELETE: 403 local_only
- Q1 A 실행→B 실행: 작업 ID·queued 순번·A 완료·B 시작/완료 시각
- Q2 A 실행→B 탐색: 작업 ID·queued 순번·A 완료·B 시작/완료 시각
- Q3 같은 PC 중복: 409 user_busy
- 자동 시작 작업 환경 재검사:
- 재부팅 후 자동 시작: 수행/미수행 및 결과
- 기존 데이터·결과·뉴스 운영 보존:
- 서버 환경 테스트: 명령·통과/skip/실패 수 또는 미실행
- 임시 시험 방화벽 예외 제거:
- 잔여/실패 항목:
- X12 판정: 완료/진행 중 및 근거
- WBS 갱신: 대상 행·실제 완료일·비대상 보존 확인
```

캡처에는 브라우저 주소창·개발자 도구의 요청 주소·클라이언트 주소·사용자 개인정보·키 힌트가 포함될 수 있으므로 공개 공유 전 가림. 원문 로그·서버 전용 백업은 공개 저장소 첨부 제외.

## 20. 근거 문서·검증 범위

### 20.1 저장소 기준

- [X12 구현 요구사항 명세](X12_구현_요구사항_명세.md): 1A·2B·3B·FR·AC12·구현 상태
- [F1 데이터 서버 API](F1_데이터_서버_API.md) §6: 서버 단일 실행·운영 파일과 팀 저장소 분리·수용 순서
- [X3 로그인 설계](X3_로그인_설계.md): 데모·Supabase·소유자 경계
- [P3-P4 로드맵](P3-P4_외부자료_브리핑_로드맵.md) B22: 구현 담당·리뷰·운영 연계
- [구현 계획](구현_계획.md) §6.6: 실행/탐색 공용 FIFO·동일 요청자 제한·수용 조건
- [공시·뉴스 자동 갱신](P3_DB_검색_자동갱신.md): 서버 PC 내부 수신·경로·기존 정상 사본 보존
- [README](../README.md), [AGENTS.md](../AGENTS.md): 실행·설정·금지·완료 기준
- [WBS](plan/regime-lab-wbs-20261001.csv), [진행 기록](../progress/PROGRESS.md): 실제 실적 기록 위치
- 코드 기준: `api/src/regime_api/settings.py`, `main.py`, `routes/meta.py`, `routes/auth.py`, `routes/llm.py`, `routes/runs.py`, `routes/searches.py`, `schemas.py`, `jobs.py`, 엔진 설정·경로 로더·각 pyproject

### 20.2 외부 명령 근거

- [Microsoft — Get-NetTCPConnection](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-nettcpconnection): 리슨·소유 프로세스 조회
- [Microsoft — Get-NetConnectionProfile](https://learn.microsoft.com/en-us/powershell/module/netconnection/get-netconnectionprofile): 실제 네트워크 프로필 조회
- [Microsoft — Get-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/get-netfirewallrule): 유효 규칙·관련 필터 조회
- [Microsoft — New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule), [Set-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/set-netfirewallrule): 주소·포트·프로필 범위 설정
- [Microsoft — Windows Firewall rules](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules): 기본 차단·기존 허용·명시 차단 관계
- [Microsoft — Get-ScheduledTaskInfo](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtaskinfo), [Export-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/export-scheduledtask): 기존 작업 상태·정의 보존
- [Astral — uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/): locked·no-sync·editable 동작

외부 문서는 2026-10-15 확인. 서버 전용 BAT·관리 스크립트·공유기 화면·실제 방화벽·실기기는 문서 작성 PC에서 직접 확인하지 못했으므로 해당 값·지원 인자·현재 동작을 단정하지 않음. 이 문서의 명령은 현장 실행용이며, 문서 검토 과정에서 서버 변경 명령을 실행하지 않음.
