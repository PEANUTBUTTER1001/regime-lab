"""P3-11 토큰화: 한글 문자 2-gram, 영문·숫자 단어 그대로."""

from regime_lab.rag.text import doc_text, tokenize


def test_particle_attached_word_still_overlaps():
    a, b = tokenize("한빛반도체가"), tokenize("한빛반도체")
    assert a == ["한빛", "빛반", "반도", "도체", "체가"]
    assert len(set(a) & set(b)) == 4


def test_ascii_words_kept_and_punctuation_split():
    assert tokenize("LNG·2분기 a, HBM3E!") == ["lng", "2분", "분기", "a", "hbm3e"]


def test_single_hangul_char_kept_and_empty():
    assert tokenize("주 가") == ["주", "가"]
    assert tokenize(None) == [] and tokenize("  ··  ") == []


def test_doc_text_uses_only_given_fields():
    d = {"title": "자기주식취득결정", "corp_name": "한빛반도체", "summary": "요약은 색인하지 않음"}
    assert doc_text(d, ["title", "corp_name"]) == "자기주식취득결정 한빛반도체"
    assert doc_text({"title": None}, ["title", "corp_name"]) == " "
