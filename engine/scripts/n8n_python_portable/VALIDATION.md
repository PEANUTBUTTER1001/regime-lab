# 검증 기록

검증일: 2026-10-12 KST. Windows 64비트, Python 3.14.6 및 3.12.15, 별도 가상환경.

- `python -m pytest portable_checks.py bridge_checks.py -q`: **51 passed**, 1.31초 (Python 3.14.6, 공개용 설정 검사 3개 포함). CI와 같은 uv 명령/Python 3.12.15에서도 **51 passed**, 2.65초.
- 원래 n8n JavaScript 검증 노드 5개를 독립 VM에서 실행하고 Python 결과와 비교: **8/8 일치**. 정상 기사, 근거 없는 숫자, 권유 문구, 입력에 없는 종목, 관련성 근거 불일치, 관련성 이유 충돌, 영향 근거 불일치, 공시 방향 판단 보류.
- `python runner.py demo`: 합성 기사 1건 처리 → 브리핑 1건 → 종목 집계 → SQLite/Parquet 저장 → HTML/JSON 생성 성공.
- Gemini 출력 스키마 실패 시 추가 호출 없이 중단, 시트 저장 실패 시 raw 상태 대기 유지, 두 번째 실행 모델 호출 0회, 인증 요청의 리디렉션 금지/오류 비밀값 비노출 검사 통과.
- 원래 `store.py`, `normalize.py` 코드 그대로 재사용. 원래 `bridge_checks.py`의 단일 Parquet 읽기만 `ParquetFile(...).read()`로 조정: 최신 PyArrow의 Hive 디렉터리 `kind` 자동 추론과 파일 내 열의 충돌을 피함. 저장 스키마 변경 없음.
- 이식된 프롬프트에서 n8n 표현식 잔여(`$json`, `$now`, `{{...}}`) 없음 확인.

미실행: 실제 Gemini/네이버/OpenDART 호출, 실제 Google Sheets 인증·읽기·쓰기, 원래 운영 DB 변경, 새 PC의 실제 자격 증명·예약 운영. 테스트 통과가 이 항목의 통과를 의미하지 않습니다. 모델·계정 권한·네트워크는 새 PC에서 설정 후 확인해야 합니다.

Node.js는 원본과의 비교 검사에서만 사용했습니다. 배포본 실행에는 Python만 필요합니다.

공개 배포 준비: 개인 스프레드시트 ID·워크플로 인스턴스 ID 제거, 로컬 설정 자동 선택, 시트 ID 미설정·탭 중복 검사 추가. 원본 워크플로 JSON·기사 원문·실행 결과·DB·인증 파일은 커밋 대상에서 제외했습니다.
