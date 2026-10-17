-- X4 Python 수집 전용. 선택한 기존 DB 안에만 생성. DROP/ALTER/UPDATE 없음.
-- 가격·큰 정수는 payload_json 안에서 원본 자릿수를 보존한다.
CREATE TABLE IF NOT EXISTS fsc_stock_daily_raw (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  bas_dt DATE NOT NULL,
  entity_key VARCHAR(191) NOT NULL,
  revision INT UNSIGNED NOT NULL,
  code VARCHAR(32) NULL,
  isin VARCHAR(12) NULL,
  label VARCHAR(240) NULL,
  market VARCHAR(32) NULL,
  payload_hash CHAR(64) NOT NULL,
  payload_json LONGTEXT NOT NULL,
  quality_json LONGTEXT NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  batch_id CHAR(32) NOT NULL,
  UNIQUE KEY uq_fsc_stock_revision (bas_dt, entity_key, revision),
  KEY ix_fsc_stock_date (bas_dt),
  KEY ix_fsc_stock_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS fsc_index_daily_raw (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  bas_dt DATE NOT NULL,
  entity_key VARCHAR(191) NOT NULL,
  revision INT UNSIGNED NOT NULL,
  code VARCHAR(32) NULL,
  isin VARCHAR(12) NULL,
  label VARCHAR(240) NULL,
  market VARCHAR(32) NULL,
  payload_hash CHAR(64) NOT NULL,
  payload_json LONGTEXT NOT NULL,
  quality_json LONGTEXT NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  batch_id CHAR(32) NOT NULL,
  UNIQUE KEY uq_fsc_index_revision (bas_dt, entity_key, revision),
  KEY ix_fsc_index_date (bas_dt),
  KEY ix_fsc_index_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS fsc_listed_daily_raw (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  bas_dt DATE NOT NULL,
  entity_key VARCHAR(191) NOT NULL,
  revision INT UNSIGNED NOT NULL,
  code VARCHAR(32) NULL,
  isin VARCHAR(12) NULL,
  label VARCHAR(240) NULL,
  market VARCHAR(32) NULL,
  payload_hash CHAR(64) NOT NULL,
  payload_json LONGTEXT NOT NULL,
  quality_json LONGTEXT NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  batch_id CHAR(32) NOT NULL,
  UNIQUE KEY uq_fsc_listed_revision (bas_dt, entity_key, revision),
  KEY ix_fsc_listed_date (bas_dt),
  KEY ix_fsc_listed_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS fsc_collection_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  source VARCHAR(16) NOT NULL,
  operation VARCHAR(16) NOT NULL,
  bas_dt DATE NOT NULL,
  batch_id CHAR(32) NOT NULL,
  status VARCHAR(24) NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  config_hash CHAR(64) NOT NULL,
  report_json LONGTEXT NOT NULL,
  KEY ix_fsc_log_day (source, operation, bas_dt, id),
  KEY ix_fsc_log_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
