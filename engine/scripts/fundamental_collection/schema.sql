-- 사용자 승인 서버 전달 DDL. 대상 기존 DB를 선택한 뒤 실행. ALTER/DROP 없음.
CREATE TABLE IF NOT EXISTS fund_corp_raw (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source VARCHAR(64) NOT NULL,
  entity_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revision INT UNSIGNED NOT NULL,
  schema_version SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  corp_code VARCHAR(8) NULL,
  stock_code VARCHAR(32) NULL,
  rcept_no VARCHAR(14) NULL,
  identity_json LONGTEXT NOT NULL,
  request_json LONGTEXT NOT NULL,
  payload_json LONGTEXT NOT NULL,
  payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  quality_json LONGTEXT NOT NULL,
  source_date DATE NULL,
  source_published_at DATETIME(6) NULL,
  time_precision VARCHAR(16) NOT NULL DEFAULT 'unknown',
  first_seen_at DATETIME(6) NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  available_at DATETIME(6) NULL,
  availability_basis VARCHAR(32) NOT NULL DEFAULT 'unverified',
  batch_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_fund_revision (source, entity_key, revision),
  KEY ix_fund_corp_observed (corp_code, observed_at),
  KEY ix_fund_receipt_observed (rcept_no, observed_at),
  KEY ix_fund_source_date (source, source_date),
  KEY ix_fund_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS fund_company_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_disclosure_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_event_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_financial_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_ecos_meta_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_ecos_value_raw LIKE fund_corp_raw;
CREATE TABLE IF NOT EXISTS fund_collection_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source VARCHAR(64) NOT NULL,
  operation VARCHAR(24) NOT NULL,
  work_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  batch_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  status VARCHAR(24) NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  config_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  request_json LONGTEXT NOT NULL,
  report_json LONGTEXT NOT NULL,
  PRIMARY KEY (id),
  KEY ix_fund_log_work (source, operation, work_key, id),
  KEY ix_fund_log_batch (batch_id),
  KEY ix_fund_log_status (status, observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
