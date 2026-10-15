"""P3-11 완전 조건·조사 변형·문자 구간별 토큰화. 원본·회사 사전 없는 공통 규칙."""

import pytest

from regime_lab.config import load_config
from regime_lab.rag.text import doc_text, matches_conditions, query_conditions, tokenize


def test_particle_attached_word_still_overlaps():
    a, b = tokenize("한빛반도체가"), tokenize("한빛반도체")
    assert a == ["한빛", "빛반", "반도", "도체", "체가"]
    assert len(set(a) & set(b)) == 4


def test_ascii_words_kept_and_punctuation_split():
    assert tokenize("LNG·2분기 a, HBM3E!") == ["lng", "2", "분기", "a", "hbm3e"]


def test_single_hangul_char_kept_and_empty():
    assert tokenize("주 가") == ["주", "가"]
    assert tokenize(None) == [] and tokenize("  ··  ") == []


def test_doc_text_uses_only_given_fields():
    d = {"title": "자기주식취득결정", "corp_name": "한빛반도체", "summary": "요약은 색인하지 않음"}
    assert doc_text(d, ["title", "corp_name"]) == "자기주식취득결정 한빛반도체"
    assert doc_text({"title": None}, ["title", "corp_name"]) == " "


def _conditions(q, **override):
    r = {**load_config()["rag"], **override}
    return query_conditions(q, r["query_particles"], r["particle_min_base_chars"])


@pytest.mark.parametrize("q,title", [
    ("삼성전자가", "삼성전자 자사주 매입"), ("삼성전자는", "삼성전자를 분석"),
    ("삼성전자를", "삼성전자"), ("삼성전자와", "삼성전자"),
    ("대한전선이", "대한전선 공시"), ("대한전선은", "대한전선"),
    ("대한전선을", "대한전선"), ("대한전선과", "대한전선"),
    ("현대차가", "현대차"), ("셀트리온은", "셀트리온"),
    ("SK하이닉스가", "sk하이닉스 양산"), ("SK하이닉스를", "SK하이닉스"),
    ("삼성전자가", "삼성 전자 공시"),
    ("국가", "국가 정책"), ("전문가", "전문가 분석"),
    ("대한전선", "대한 전선 계약"), ("자사주매입", "자사주 매입"),
    ("HBM3E", "hbm3e양산"), ("HBM3E양산", "HBM3E 양산"),
    ("2분기", "2 분기 실적"), ("삼성전자", "삼성전자가 발표"),
    ("삼성전자 자기주식", "삼성전자 자기주식취득결정"),
    ("ＨＢＭ３Ｅ", "HBM3E 양산"),
])
def test_complete_conditions_allow_supported_variants(q, title):
    assert matches_conditions([title], _conditions(q))


@pytest.mark.parametrize("q,title", [
    ("대한전선", "한전"), ("대한전선", "한전기술 전선 산업"),
    ("대한전선이", "한전기술"), ("삼성전자가", "삼성전자서비스"),
    ("국가", "국 정책"), ("전문가", "전문 분석"), ("주가", "주"),
    ("대한전선가", "대한전선"), ("삼성전자이", "삼성전자"),
    ("삼성전자에서", "삼성전자"), ("삼성전자에서는", "삼성전자"),
    ("HBM3E가", "HBM3E"), ("HBM3E", "HBM3E2 양산"),
    ("HBM3E", "XHBM3E 양산"), ("HBM3E", "HBM 3E 양산"),
    ("987", "98%"), ("987", "1987"), ("987", "9870"),
    ("화성기지", "화성 사업 기지"), ("화성기지", "화성 성기 기지"),
    ("자사주매입", "자사주 계획물량 매입"),
    ("없는회사검증ZXQ987", "없는 회사 검증 완료 98%"),
    ("삼성전자 자사주 매입 화성기지", "삼성전자 자사주 매입"),
    ("삼전", "삼성전자"), ("하닉", "SK하이닉스"),
])
def test_complete_conditions_reject_fragments_and_unsupported_forms(q, title):
    assert not matches_conditions([title], _conditions(q))


def test_conditions_do_not_stitch_fields_or_allow_empty_query():
    assert not matches_conditions(["삼성", "전자"], _conditions("삼성전자"))
    assert matches_conditions(["자사주 매입", "삼성전자"], _conditions("삼성전자 자사주 매입"))
    assert not matches_conditions(["임의 제목"], _conditions(" ·! "))
    assert not matches_conditions([None, ""], _conditions("삼성전자"))


def test_particle_settings_control_fallback_without_changing_original():
    assert not matches_conditions(["삼성전자"], _conditions("삼성전자가", query_particles=[]))
    assert not matches_conditions(["삼성전자"], _conditions("삼성전자가", particle_min_base_chars=5))
    assert matches_conditions(["삼성전자가"], _conditions("삼성전자가", query_particles=[]))


def test_mixed_runs_keep_identifier_and_never_create_numeric_fragments():
    assert tokenize("없는회사검증ZXQ987") == ["없는", "는회", "회사", "사검", "검증", "zxq987"]
    assert tokenize("HBM3E양산 2분기 SK하이닉스가") == ["hbm3e", "양산", "2", "분기", "sk", "하이", "이닉", "닉스", "스가"]
