"""검증된 HTTP snapshot 교체 시 다른 출처와 coverage/지문을 보존한다."""
import json

import pandas as pd
import pytest

from regime_lab.data.docs import DocsStore


def source(root, name, docs):
    folder = root / "docs" / f"source={name}"
    folder.mkdir(parents=True)
    (folder / "_manifest.json").write_text(json.dumps({"schema_version": 1, "source": name, "license_scope": "metadata_only"}))
    pd.DataFrame(docs).to_parquet(folder / "part.parquet")


def test_source_level_replacement_retains_opendart_and_removes_stale_news(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    source(old, "opendart", [{"doc_id": "opendart:a", "version": 1, "title": "공시"}])
    source(old, "n8n_news", [{"doc_id": "n8n_news:old", "version": 1, "title": "옛 자료"}])
    source(new, "n8n_news", [{"doc_id": "n8n_news:new", "version": 1, "title": "새 자료"}])
    store = DocsStore(old, tmp_path / "cache", (new,))
    assert set(store.manifests()) == {"opendart", "n8n_news"}
    assert {d["doc_id"] for d in store.read(store.files())} == {"opendart:a", "n8n_news:new"}
    assert store.fingerprint(store.files()) == store.fingerprint(store.files())
    assert store.coverage() == []


def test_duplicate_conflict_is_not_silent(tmp_path):
    source(tmp_path, "opendart", [{"doc_id": "opendart:a", "version": 1, "title": "A"}])
    folder = tmp_path / "docs" / "source=opendart"
    pd.DataFrame([{"doc_id": "opendart:a", "version": 1, "title": "B"}]).to_parquet(folder / "part2.parquet")
    s = DocsStore(tmp_path, tmp_path / "cache")
    with pytest.raises(ValueError, match="충돌"):
        s.read(s.files())
