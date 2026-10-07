"""재현성 입력 지문 (NFR-10) — 원본 데이터 없이 실행."""


def test_warmup_file_hashes(tmp_path):
    """워밍업 파일이 없으면 n=0, 있으면 파일별 크기·수정시각. parquet 가 아닌 파일은 무시."""
    from regime_lab.data.loader import warmup_file_hashes

    assert warmup_file_hashes(tmp_path) == {"cache/warmup": "n=0"}
    (tmp_path / "warmup").mkdir()
    (tmp_path / "warmup" / "a.parquet").write_bytes(b"12")
    (tmp_path / "warmup" / "note.txt").write_text("무시")
    h = warmup_file_hashes(tmp_path)
    assert list(h) == ["cache/warmup/a.parquet"] and h["cache/warmup/a.parquet"].startswith("size=2;")
