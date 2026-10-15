"""Local search fixture from the existing n8n bridge's raw Parquet history.

Reads metadata only. Does not open SQLite, run n8n, call LLMs, or overwrite
source data. This is a local preview, not an activated production source.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs

import pyarrow as pa
import pyarrow.parquet as pq

INPUT_COLUMNS = ["kind", "title", "source_url", "published_at", "entity_key",
                 "version", "imported_at", "available_at"]


def aware(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Timestamp has no timezone")
    return result.astimezone(timezone.utc)


def convert(rows):
    docs, seen = [], set()
    for row in rows:
        if row["kind"] != "raw" or not isinstance(row["title"], str) or not row["title"].strip():
            raise ValueError("Expected a raw row with a title")
        url = urlsplit(row["source_url"])
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            raise ValueError("Invalid source URL")
        if not re.fullmatch(r"[a-f0-9]{64}", row["entity_key"]):
            raise ValueError("Invalid source identity")
        version = row["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("Invalid version")
        first = aware(row["imported_at"])
        available = max(first, aware(row["available_at"]))
        published = aware(row["published_at"]) if row["published_at"] else None
        disclosure = url.hostname.lower() == "dart.fss.or.kr"
        source = "n8n_disclosure" if disclosure else "n8n_news"
        doc_id = source + ":" + row["entity_key"]
        if (doc_id, version) in seen:
            raise ValueError("Duplicate document version")
        seen.add((doc_id, version))
        docs.append({
            "doc_id": doc_id, "version": version, "source": source,
            "source_type": "disclosure" if disclosure else "news",
            "title": row["title"].strip(), "corp_name": None, "url": row["source_url"],
            "published_at": (published.astimezone(timezone(timedelta(hours=9))).date().isoformat()
                             if disclosure else published.isoformat()) if published else None,
            "published_at_basis": "receipt_date" if disclosure else "upstream_metadata",
            "time_precision": "date_only" if disclosure else "datetime",
            "first_seen_at": first.isoformat(), "available_at": available.isoformat(),
            "backfilled": False, "tickers": [], "ticker_status": "none",
            "content_hash": hashlib.sha256(json.dumps(
                [row["title"], row["source_url"], row["published_at"]],
                ensure_ascii=False).encode()).hexdigest(),
            "license_scope": "metadata_only", "summary": None, "body_ref": None,
            "rcept_no": (parse_qs(url.query).get("rcpNo") or [None])[0] if disclosure else None,
            "status": "active", "ingest_run_id": "local_n8n_search_preview",
        })
    return sorted(docs, key=lambda d: (d["doc_id"], d["version"]))


def export(input_path, output_root):
    # One immutable read avoids mixing two concurrently replaced exports.
    raw = input_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = pq.ParquetFile(io.BytesIO(raw)).read(columns=INPUT_COLUMNS).to_pylist()
    docs = convert(rows)
    if not docs:
        raise ValueError("No raw documents; refusing an empty normal snapshot")
    target = output_root / ("snapshot_" + digest[:16])
    report = {"input_sha256": digest, "versions": len(docs),
              "documents": len({d["doc_id"] for d in docs}),
              "current_by_type": dict(Counter({d["doc_id"]: d["source_type"] for d in docs}.values())),
              "observation_basis": "bridge version imported_at; not the unverified sheet first-seen field",
              "scope": "local_fixture_metadata_only", "automatic_updates": False}
    if target.exists():
        if not (target / "report.json").is_file():
            raise ValueError("Incomplete snapshot exists; choose a new output folder")
        if json.loads((target / "report.json").read_text(encoding="utf-8")) != report:
            raise ValueError("Existing snapshot report mismatch")
        return target, report
    target.mkdir(parents=True)
    for source in sorted({d["source"] for d in docs}):
        folder = target / "docs" / ("source=" + source)
        selected = [d for d in docs if d["source"] == source]
        days = sorted({aware(d["first_seen_at"]).date().isoformat() for d in selected})
        for day in days:
            part = folder / ("date=" + day)
            part.mkdir(parents=True)
            daily = [d for d in selected if aware(d["first_seen_at"]).date().isoformat() == day]
            pq.write_table(pa.Table.from_pylist(daily), part / "part-00000.parquet")
        manifest = {"schema_version": 1, "source": source, "local_fixture": True,
                    "license_scope": "metadata_only", "input_sha256": digest,
                    "time_policy": "bridge_version_observation", "coverage": "unknown"}
        (folder / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (target / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target, report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target, report = export(args.input, args.output)
    print(json.dumps({"ext_store": str(target), **report}, ensure_ascii=False))
