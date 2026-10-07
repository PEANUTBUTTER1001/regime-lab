"""MariaDB stock_minutes → 월별 Parquet 사본 (P2-4.1, 인프라). 원본 DB 는 읽기만 한다.

mysql 클라이언트의 탭 구분 출력(--batch --skip-column-names)을 표준입력으로 받아
<출력 폴더>/stock_minutes_YYYYMM.parquet 과 minutes_manifest.json 을 만든다. 비밀번호는 mysql 클라이언트가
직접 묻고 이 스크립트는 받지 않는다. 결과는 원본 사본이므로 저장소 밖(data/minutes, Git 제외)에 둔다.

사용 (저장소 루트, Windows PowerShell — 파이프의 바이트를 그대로 넘기려고 cmd 로 감싼다):
  cmd /c "mysql -u root -p --batch --quick --skip-column-names --default-character-set=utf8mb4 -e ""SELECT code, dt, open_p, high_p, low_p, close_p, volume, value FROM stock_minutes"" regime_lab | uv run --project engine python engine/scripts/export_minutes.py data/minutes"
macOS·Linux:
  mysql -u root -p --batch --quick --skip-column-names -e "SELECT code, dt, open_p, high_p, low_p, close_p, volume, value FROM stock_minutes" regime_lab | uv run --project engine python engine/scripts/export_minutes.py data/minutes

2026-10-01 실측: 237,308,896행 → 월별 19개 파일 약 3.1GB, 약 8분.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

COLS = ["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"]
SCHEMA = pa.schema([
    ("code", pa.string()), ("dt", pa.timestamp("s")),
    ("open_p", pa.int32()), ("high_p", pa.int32()), ("low_p", pa.int32()), ("close_p", pa.int32()),
    ("volume", pa.int64()), ("value", pa.int64()),
])
REPORT_EVERY = 5_000_000


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    # 이전 내보내기 결과와 섞이지 않게, 결과물이 하나라도 있는 폴더에는 쓰지 않는다 (삭제는 사람이 직접)
    leftovers = sorted(p.name for p in out_dir.glob("stock_minutes_*.parquet*")) + \
        (["minutes_manifest.json"] if (out_dir / "minutes_manifest.json").exists() else [])
    if leftovers:
        print(f"[stop] {out_dir} 에 이전 결과가 있습니다 ({', '.join(leftovers[:3])} 등 {len(leftovers)}개). "
              "빈 새 폴더를 지정하거나, 기존 폴더를 직접 옮긴 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    started = datetime.now()
    t0 = time.time()
    try:
        reader = pcsv.open_csv(
            sys.stdin.buffer,
            read_options=pcsv.ReadOptions(column_names=COLS, block_size=64 << 20),
            parse_options=pcsv.ParseOptions(delimiter="\t", quote_char=False),
            convert_options=pcsv.ConvertOptions(column_types={f.name: f.type for f in SCHEMA},
                                                timestamp_parsers=["%Y-%m-%d %H:%M:%S"]),
        )
    except pa.ArrowInvalid as e:  # 빈 입력 = mysql 이 아무것도 보내지 않음 (비밀번호 오류 등)
        print(f"[stop] 받은 데이터가 없습니다. 위의 mysql 메시지(비밀번호 오류 등)를 확인하세요. ({e})", file=sys.stderr)
        return 1
    writers: dict[int, pq.ParquetWriter] = {}
    stats: dict[int, dict] = {}
    total, next_report = 0, REPORT_EVERY
    try:
        for batch in reader:
            tbl = pa.Table.from_batches([batch]).cast(SCHEMA)
            ym = pc.add(pc.multiply(pc.year(tbl["dt"]), 100), pc.month(tbl["dt"]))
            for key in pc.unique(ym).to_pylist():
                part = tbl.filter(pc.equal(ym, key))
                if key not in writers:  # 끝까지 쓰기 전에는 .part 이름으로 둔다 (중단 시 완성본과 구분)
                    writers[key] = pq.ParquetWriter(out_dir / f"stock_minutes_{key}.parquet.part", SCHEMA,
                                                    compression="zstd")
                    stats[key] = {"rows": 0, "min_dt": None, "max_dt": None}
                writers[key].write_table(part)
                s = stats[key]
                s["rows"] += part.num_rows
                lo, hi = pc.min(part["dt"]).as_py(), pc.max(part["dt"]).as_py()
                s["min_dt"] = lo if s["min_dt"] is None else min(s["min_dt"], lo)
                s["max_dt"] = hi if s["max_dt"] is None else max(s["max_dt"], hi)
            total += tbl.num_rows
            if total >= next_report:
                rate = total / max(time.time() - t0, 1e-9)
                print(f"  {total:,} rows  ({rate:,.0f} rows/s, {time.time() - t0:,.0f}s)", file=sys.stderr, flush=True)
                next_report += REPORT_EVERY
    finally:
        for w in writers.values():
            w.close()

    if total == 0:
        print("[stop] 받은 데이터가 없습니다. 위의 mysql 메시지(비밀번호 오류 등)를 확인하세요.", file=sys.stderr)
        return 1
    for key in writers:
        (out_dir / f"stock_minutes_{key}.parquet.part").replace(out_dir / f"stock_minutes_{key}.parquet")
    manifest = {
        "source": "MariaDB regime_lab.stock_minutes (read-only export)",
        "columns": COLS,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - t0, 1),
        "total_rows": total,
        "months": {str(k): {"rows": v["rows"], "min_dt": str(v["min_dt"]), "max_dt": str(v["max_dt"])}
                   for k, v in sorted(stats.items())},
    }
    (out_dir / "minutes_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    print(f"[done] {total:,} rows, {len(writers)} files -> {out_dir} ({time.time() - t0:,.0f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용: ... | python engine/scripts/export_minutes.py <출력 폴더>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
