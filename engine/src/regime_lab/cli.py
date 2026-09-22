"""CLI: 전략 파일 실행 (구현_계획 §1-4, 엔진은 UI·API 와 독립).

  uv run regime-lab prepare                                  # 전종목 준비 프레임 캐시 생성
  uv run regime-lab run strategies/core_breakout_vol.yaml    # 전략 1개 (FDR m=1)
  uv run regime-lab run a.yaml b.yaml                        # 비교 실행 (한 run_id, m=전략 수)
  uv run regime-lab batch                                    # 핵심 5종을 한 요청으로 (m=5, S12)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from regime_lab.config import ENGINE_DIR, load_config, load_paths
from regime_lab.context import RecordingContext
from regime_lab.data.loader import load_sample_tickers
from regime_lab.pipeline import prepare
from regime_lab.runs import Strategy, StrategyError, run_and_save


class _PrintContext(RecordingContext):
    def stage(self, name, detail=None):
        super().stage(name, detail)
        suffix = f" ({detail})" if detail else ""
        print(f"  [{self.snapshot()['elapsed_sec']:6.1f}s] {name}{suffix}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="regime-lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "run", "batch"):
        sp = sub.add_parser(name)
        sp.add_argument("--sample", action="store_true", help="store/sample30 종목만 사용")
        sp.add_argument("--no-cache", action="store_true", help="준비 프레임 캐시를 쓰지 않음")
        if name == "run":
            sp.add_argument("strategy", type=Path, nargs="+", help="전략 YAML (여러 개면 비교 실행)")
    args = ap.parse_args(argv)

    cfg, paths = load_config(), load_paths()
    strategies: list[Strategy] = []
    try:
        if args.cmd == "run":
            strategies = [Strategy.load(p) for p in args.strategy]
        elif args.cmd == "batch":
            strategies = [Strategy.load(p) for p in sorted((ENGINE_DIR / "strategies").glob("core_*.yaml"))]
        for s in strategies:
            s.validate(cfg)
    except StrategyError as e:
        print(f"[error] 전략 입력 오류: {e.errors}", file=sys.stderr)
        return 2

    tickers = load_sample_tickers(paths.store) if args.sample else None
    t = time.time()
    prep = prepare(paths, cfg, tickers, use_cache=not args.sample and not args.no_cache)
    prep_sec = time.time() - t
    print(f"[prepare] rows={len(prep.frame):,} tickers={prep.frame['ticker'].nunique():,} {prep_sec:.1f}s",
          flush=True)
    if args.cmd == "prepare":
        return 0

    ctx = _PrintContext()
    out = run_and_save(strategies, prep, cfg, paths, tickers, ctx=ctx)
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    for sr in result["strategies"]:
        m, v = sr["summary"], sr["validation"]
        print(f"[run] {sr['strategy']}: trades={m['trades']:,} mean_ret={m['mean_ret']} "
              f"mean_excess={m['mean_excess']} | split={v['split_judgement']} fdr_p={v['p_value']} "
              f"random_pct={v['random_percentile']} -> analysis_target={v['analysis_target']}", flush=True)
    print(f"[done] m={result['fdr_family_size']} prepare {prep_sec:.1f}s + run {ctx.snapshot()['elapsed_sec']}s "
          f"-> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
