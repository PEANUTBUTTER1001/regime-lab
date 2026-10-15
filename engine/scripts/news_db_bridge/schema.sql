-- C3 v3 schema; contract accepted 2026-10-13. Manual server-owner setup only.
-- Existing tables must be compared, not recreated. Collector: SELECT, INSERT only.
-- All tables are append-only. No IF NOT EXISTS: reject name/contract collisions.
-- Full JSON contains metadata only, never credentials, article bodies or LLM output.
CREATE TABLE regime_lab.ext_document_manifests (
  snapshot_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  source VARCHAR(64) NOT NULL,
  schema_version INT UNSIGNED NOT NULL,
  normalization_policy_version VARCHAR(191) NULL,
  time_policy_version VARCHAR(191) NULL,
  manifest_json JSON NOT NULL,
  mapping_policy_json JSON NOT NULL COMMENT 'Derived mapping provenance; original manifest stays unchanged',
  arrow_schema_json JSON NOT NULL COMMENT 'Ordered Parquet fields and types for lossless export',
  coverage_state VARCHAR(16) NOT NULL COMMENT 'recorded or unknown; not collection success',
  document_versions BIGINT UNSIGNED NOT NULL,
  coverage_rows BIGINT UNSIGNED NOT NULL,
  imported_at_utc DATETIME(6) NOT NULL COMMENT 'DB import time; never first_seen_at',
  PRIMARY KEY (snapshot_id, source),
  CHECK (coverage_state IN ('recorded', 'unknown'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE regime_lab.ext_document_versions (
  doc_id VARCHAR(191) NOT NULL,
  version BIGINT UNSIGNED NOT NULL,
  source VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  license_scope VARCHAR(32) NOT NULL,
  first_seen_at_utc DATETIME(6) NOT NULL,
  available_at_utc DATETIME(6) NOT NULL,
  record_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  document_json JSON NOT NULL COMMENT 'Exact original metadata incl. ID/URL/title/company/tickers/status/license/content_hash and offset timestamps',
  PRIMARY KEY (doc_id, version),
  UNIQUE KEY uq_doc_source (doc_id, version, source),
  KEY ix_doc_observed (source, first_seen_at_utc, available_at_utc),
  CHECK (version >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE regime_lab.ext_document_snapshot_members (
  snapshot_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  source VARCHAR(64) NOT NULL,
  doc_id VARCHAR(191) NOT NULL,
  version BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (snapshot_id, source, doc_id, version),
  FOREIGN KEY (snapshot_id, source) REFERENCES regime_lab.ext_document_manifests(snapshot_id, source),
  FOREIGN KEY (doc_id, version, source) REFERENCES regime_lab.ext_document_versions(doc_id, version, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE regime_lab.ext_document_coverage (
  snapshot_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  source VARCHAR(64) NOT NULL,
  entry_no BIGINT UNSIGNED NOT NULL COMMENT 'Stable input order, preserves repeated observations',
  record_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  coverage_json JSON NOT NULL COMMENT 'Original seq/day/corp_cls/state/run_id/observed_at incl. timezone',
  PRIMARY KEY (snapshot_id, source, entry_no),
  FOREIGN KEY (snapshot_id, source) REFERENCES regime_lab.ext_document_manifests(snapshot_id, source),
  CHECK (entry_no >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
