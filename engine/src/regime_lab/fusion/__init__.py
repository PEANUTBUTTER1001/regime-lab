"""P4-2 시점별 자료 특징. 순수 계산만; 원문·네트워크·파일 접근 없음.

문서 단위의 유효 구간을 거래일 배열의 차분으로 집계한다. 검색 top-k나 미래 어휘에 의존하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd

from regime_lab.rag.select import AS_OF_MODES, aware

EVIDENCE_COLUMNS = ["doc_id", "version", "code", "source", "source_type", "group", "available_at",
                    "visible_at", "until", "linked_bar_start", "linked_as_of", "linked_ordinal", "content_hash"]


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _kst(t):
    return pd.Timestamp(aware(t)).tz_convert("Asia/Seoul").tz_localize(None)


def _group(d, cfg):
    text = d.get("report_base") or d.get("title") or ""
    return next((k for k, pattern in cfg["fusion"]["report_rules"].items() if re.search(pattern, text)), "other")


def feature_definitions(cfg) -> dict[str, dict]:
    out = {}
    for w in cfg["fusion"]["windows"]:
        if not isinstance(w, int) or isinstance(w, bool) or w <= 0:
            raise ValueError("fusion.windows 는 양의 거래일 수")
        for kind, groups in (("all", ["all"]), ("source_type", ["disclosure", "news", "other"]),
                             ("report_type", list(cfg["fusion"]["report_rules"]))):
            for g in groups:
                out[f"f_{kind}_{g}_{w}"] = {"group_type": kind, "group": g, "window": w}
    return out


def _intervals(docs, bars, cfg, mode):
    fc = cfg["fusion"]
    records, seen, rejected = {}, {}, {"unlinked": 0, "source_or_license": 0, "inactive": 0}
    for d in docs:
        key = (d["doc_id"], int(d["version"]))
        digest = fingerprint(d)
        if key in seen:
            if seen[key] != digest:
                raise ValueError("같은 문서·버전의 내용 충돌")
            continue
        seen[key] = digest
        if d.get("source") not in fc["sources"] or d.get("license_scope") != "metadata_only":
            rejected["source_or_license"] += 1
        if d.get("status", "active") in ("deleted", "expired"):
            rejected["inactive"] += 1
            continue
        if not d.get("tickers") or d.get("ticker_status", "resolved") != "resolved":
            rejected["unlinked"] += 1
        visible = max(_kst(d["available_at"]), _kst(d["first_seen_at"])) if mode == "observed" else _kst(d["available_at"])
        records.setdefault(d["doc_id"], []).append((visible, d))
    by_code = {str(c): b.sort_values("available_at") for c, b in bars.groupby("code", sort=False)}
    rows = []
    oh, om = map(int, cfg["minutes"]["session_open"].split(":"))
    for versions in records.values():
        versions.sort(key=lambda x: (x[0], int(x[1]["version"])))
        for visible, d in versions:
            # 더 높은 버전이 먼저 가시화되었다면 이 버전은 한 번도 선택되지 않는다.
            newer = [v for v, x in versions if int(x["version"]) > int(d["version"])]
            until = min(newer) if newer else pd.Timestamp.max
            if d.get("expires_at"):
                until = min(until, _kst(d["expires_at"]))
            if visible >= until:
                continue
            # 더 새 버전이 미연결/권한 제한이면 옛 연결을 계속 사용하는 일도 방지.
            if (d.get("source") not in fc["sources"] or d.get("license_scope") != "metadata_only" or
                    d.get("ticker_status", "resolved") != "resolved"):
                continue
            for code in sorted({str(x.get("code")) for x in d.get("tickers") or [] if re.fullmatch(r"[0-9A-Za-z]{6}", str(x.get("code", "")))}):
                b = by_code.get(code)
                if b is None:
                    continue
                starts = b["date"].dt.normalize() + pd.Timedelta(hours=oh, minutes=om)
                k = int(starts.searchsorted(visible, side="right"))
                if k >= len(b) or b["available_at"].iloc[k] >= until:
                    continue
                rows.append({"doc_id": d["doc_id"], "version": int(d["version"]), "code": code,
                             "source": d["source"], "source_type": d.get("source_type", "other"),
                             "group": _group(d, cfg), "available_at": d["available_at"], "visible_at": visible,
                             "until": until, "linked_bar_start": starts.iloc[k],
                             "linked_as_of": b["available_at"].iloc[k], "linked_ordinal": k, "content_hash": d.get("content_hash")})
    return pd.DataFrame(rows, columns=EVIDENCE_COLUMNS), rejected


def _coverage_mask(dates, times, coverage, cfg, mode):
    """집계 창에 걸친 달력 날짜의 Y/K 수집 상태. unknown은 정상0건으로 만들지 않는다."""
    rows = sorted(coverage, key=lambda r: (_kst(r["observed_at"]), int(r.get("seq", 0))))
    result = np.zeros(len(times), dtype=bool)
    fc = cfg["fusion"]
    maxw = max(fc["windows"])
    states, cursor = {}, 0
    observed = [_kst(r["observed_at"]) for r in rows]
    for i, t in enumerate(times):
        while cursor < len(rows) and (mode == "historical_assumed" or observed[cursor] <= t):
            r = rows[cursor]
            key = (r["source"], r["day"], r["corp_cls"])
            if key not in states or int(r.get("seq", 0)) >= int(states[key].get("seq", 0)):
                states[key] = r
            cursor += 1
        # 첫 창의 이전 달력 날짜까지 포함: 주말·휴장일 자료도 다음 실제 거래일에 연결된다.
        lo = dates[max(0, i - maxw)]
        days = pd.date_range(lo, dates[i], freq="D")
        complete = i >= maxw
        for src in fc["sources"]:
            for day in days:
                for market in fc["markets"]:
                    r = states.get((src, day.date().isoformat(), market))
                    ok = r is not None and r["state"] == "collected"
                    # 순방향 당일은 장 시작까지의 관측을 입증한 경우에만; 이전 날의 일부 수집은 불충분.
                    if r is not None and r["state"] == "forward" and day == dates[i]:
                        oh, om = map(int, cfg["minutes"]["session_open"].split(":"))
                        ok = _kst(r["observed_at"]) >= day + pd.Timedelta(hours=oh, minutes=om)
                    complete = complete and ok
        result[i] = complete
    return result


def build_doc_features(docs, bars, cfg, mode="observed", coverage=()):
    if mode not in AS_OF_MODES:
        raise ValueError("지원하지 않는 자료 시점 모드")
    if bars.empty or bars.duplicated(["code", "available_at"]).any():
        raise ValueError("중복 없는 일봉이 필요합니다")
    defs = feature_definitions(cfg)
    ev, rejected = _intervals(docs, bars, cfg, mode)
    out, coverage_cache = [], {}
    for code, b in bars.groupby("code", sort=True):
        b = b.sort_values("available_at")
        times = b["available_at"].to_numpy(dtype="datetime64[ns]")
        dates = pd.DatetimeIndex(b["date"].dt.normalize())
        changes = {k: np.zeros(len(b) + 1) for k in defs}
        for r in ev[ev["code"] == str(code)].to_dict("records"):
            start = int(np.searchsorted(times, np.datetime64(r["linked_as_of"])))
            stop = int(np.searchsorted(times, np.datetime64(r["until"])))
            for col, info in defs.items():
                matches = (info["group_type"] == "all" or
                           (info["group_type"] == "source_type" and info["group"] ==
                            (r["source_type"] if r["source_type"] in ("disclosure", "news") else "other")) or
                           (info["group_type"] == "report_type" and info["group"] == r["group"]))
                end = min(start + info["window"], stop, len(b))
                if matches and start < end:
                    changes[col][start] += 1
                    changes[col][end] -= 1
        calendar_key = times.tobytes()
        if calendar_key not in coverage_cache:
            coverage_cache[calendar_key] = _coverage_mask(dates, pd.DatetimeIndex(times), coverage, cfg, mode)
        covered = coverage_cache[calendar_key]
        f = pd.DataFrame({"code": str(code), "as_of": times,
                          **{k: np.cumsum(v[:-1]) for k, v in changes.items()}, "docs_missing": ~covered})
        f.loc[~covered, list(defs)] = np.nan
        out.append(f)
    features = pd.concat(out, ignore_index=True)
    splits = cfg["forecast"]["split"]
    labels = np.select([features["as_of"] < pd.Timestamp(splits["train_end"]) + pd.Timedelta(days=1),
                        features["as_of"] < pd.Timestamp(splits["valid_end"]) + pd.Timedelta(days=1)],
                       ["train", "valid"], "test")
    audit = {"versions": len(docs), "linked_versions": len(ev), "excluded": rejected,
             "coverage_records": len(coverage), "source_policy": cfg["fusion"]["sources"],
             "by_split": {s: {"rows": int((labels == s).sum()),
                                "usable_rows": int(((labels == s) & ~features["docs_missing"]).sum()),
                                "days": int(features.loc[(labels == s) & ~features["docs_missing"], "as_of"].nunique())}
                          for s in ("train", "valid", "test")}}
    return features, ev, audit


def doc_features(docs, bars, cfg, mode="observed", coverage=()):
    return build_doc_features(docs, bars, cfg, mode, coverage)[0]
