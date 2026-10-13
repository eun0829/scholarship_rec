# -*- coding: utf-8 -*-
"""항목 사전(item registry): 기본 파일의 유효성, 라벨 색인, 검증기가 실수를 잡는지, 항목 추가 시 자동으로 따라오는 것."""
import json
import re

import pytest

from scholarship.common import registry as R
from scholarship.common.ai import schema_v3


@pytest.fixture(scope="module")
def reg():
    return R.load_registry()


def item(**kw):
    d = {"code": "XTEST", "label": "시험 항목", "kind": "student", "source": "student", "type": "int", "tier": 3, "question": "시험 질문?"}
    d.update(kw)
    return d


def errors_of(extra):
    base = json.loads(R.default_path().read_text(encoding="utf-8"))
    return R.validate({"version": base["version"], "items": base["items"] + [extra]})


# ------------------------------------------------------------------ 기본 사전
def test_default_registry_is_valid_and_complete(reg):
    data = json.loads(R.default_path().read_text(encoding="utf-8"))
    assert R.validate(data) == []
    assert len(reg.items) >= 35 and len({i.code for i in reg.items}) == len(reg.items)
    assert reg.paths() <= schema_v3.V3_PATHS                                             # 연결된 공고 필드는 모두 실제 스키마에 있음
    assert {i.tier for i in reg.items} == {1, 2, 3}
    for code in ("GL", "CR", "GC", "GN", "RES", "RESP", "RESY", "HSL", "INC", "MAJ", "AGE", "GEN", "NSA", "SLOC", "SCH_TYPE", "SPART"):
        assert reg.get(code) is not None, code


def test_student_items_have_questions_and_sensitive_flags(reg):
    asked = [i for i in reg.items if i.source == "student"]
    assert asked and all(i.question for i in asked)
    for code in ("INC", "BASIC", "NEAR", "DIS", "SPAR", "DEF", "MUL", "VET", "MCH", "SPECIAL"):
        assert reg.get(code).sensitive, code                                           # 소득·신분은 민감 항목
    assert not reg.get("GL").sensitive and not reg.get("RES").sensitive


def test_special_code_items_map_to_schema_codes(reg):
    codes = {i.special_code for i in reg.items if i.special_code}
    assert codes == {"BASIC_LIVELIHOOD", "NEAR_POOR", "DISABLED", "SINGLE_PARENT", "DEFECTOR", "MULTICULTURAL", "VETERAN", "MULTI_CHILD"}
    assert codes <= set(schema_v3.SPECIAL_CODES)


def test_label_index_resolves_aliases_and_spacing(reg):
    idx = reg.label_index()
    n = R._norm
    for text, code in [("직전 학기 평점", "GL"), ("직전학기평점", "GL"), ("소득분위", "INC"), ("소득 구간", "INC"), ("학자금 지원구간", "INC"),
                       ("본인주민등록", "RES"), ("주민등록 기준지", "RES"), ("거주기간", "RESY"), ("새터민", "DEF"), ("나이", "AGE"), ("학과명", "MAJ")]:
        assert idx[n(text)].code == code, text
    assert n("성적") not in idx and n("평점") not in idx and n("주민등록") not in idx            # 모호한 라벨은 어느 항목에도 속하지 않음


def test_prompt_lines_cover_linked_items_sorted_by_tier(reg):
    lines = reg.prompt_lines()
    linked = [i for i in reg.enabled() if i.notice_path]
    assert len(lines) == len(linked) and all(l.startswith("- ") for l in lines)
    codes = [re.search(r"\(([A-Z][A-Z0-9_]*)\):", l).group(1) for l in lines]      # 라벨에 괄호가 있어도 '(코드):' 형태로 찾음
    tiers = [reg.get(c).tier for c in codes]
    assert tiers == sorted(tiers)                                                       # 필수 항목이 먼저
    assert any("직전학기 평점 (GL)" in l and "gpa.min" in l for l in lines)


def test_comment_keys_are_ignored_and_disabled_items_excluded():
    base = json.loads(R.default_path().read_text(encoding="utf-8"))
    base["_메모"] = "주석"
    base["items"][0]["_메모"] = "항목 주석"
    base["items"][1]["enabled"] = False
    r = R.from_data(base)
    code = base["items"][1]["code"]
    assert code not in r.codes() and code not in {i.code for i in r.label_index().values()}
    assert not any("({})".format(code) in l for l in r.prompt_lines())


# ------------------------------------------------------------------ 항목 추가 (코드 변경 없이)
def test_adding_one_entry_extends_labels_questions_and_prompt(reg):
    new = item(code="SCHOLAR_TERMS", label="장학 수혜 학기 수", aliases=["수혜 학기수", "장학 받은 학기 수"], type="int", range=[0, 16],
               question="지금까지 장학금을 받은 학기는 모두 몇 학기인가요?", notice_path="unmodeled_conditions", op="custom", custom="scholar_terms",
               description="장학 수혜 학기 수 한도")
    r2 = reg.with_items([new])
    assert len(r2.items) == len(reg.items) + 1
    assert r2.label_index()[R._norm("장학 받은 학기 수")].code == "SCHOLAR_TERMS"
    assert any("장학 수혜 학기 수 (SCHOLAR_TERMS): 장학 수혜 학기 수 한도" in l for l in r2.prompt_lines())
    assert "SCHOLAR_TERMS" in r2.codes() and "SCHOLAR_TERMS" not in reg.codes()          # 원래 사전은 그대로


def test_new_notice_field_must_exist_in_schema_first():
    errs = errors_of(item(notice_path="history.max_prior_terms", op="lte"))
    assert any("공고 스키마(v3)에 없는 경로" in e and "history.max_prior_terms" in e for e in errs)   # 새 공고 필드는 스키마에 먼저 추가해야 함


# ------------------------------------------------------------------ 검증기가 잡아야 하는 실수
@pytest.mark.parametrize("extra, needle", [
    (item(code="GL"), "중복됨"),
    (item(code="bad-code"), "code 는 영문 대문자"),
    (item(quesion="오타"), "알 수 없는 키"),
    (item(label="소득분위"), "겹침"),
    (item(aliases=["직전학기평점"]), "겹침"),
    (item(type="enum"), "enum(허용 값 목록)이 필요"),
    (item(type="int", enum=["a"]), "enum 은 type 이 enum/bucket"),
    (item(type="enum", enum=["a", "a"]), "enum 에 중복"),
    (item(range=[5, 1]), "range 는 [최소, 최대]"),
    (item(type="text", range=[0, 1]), "range 는 type 이 int/number/gpa"),
    (item(kind="x"), "kind 는"),
    (item(source="x"), "source 는"),
    (item(type="x"), "type 는"),
    (item(tier=4), "tier 는 1(필수)"),
    (item(question=""), "question"),
    (item(notice_path="gpa.min"), "op 는"),
    (item(op="gte"), "notice_path 가 있을 때만"),
    (item(notice_path="gpa.min", op="custom"), "custom(비교 함수 이름"),
    (item(notice_path="gpa.min", op="gte", custom="x"), "op=custom 일 때만"),
    (item(special_code="NOPE", type="bool"), "special_code"),
    (item(special_code="DISABLED", type="int"), "special_code"),
    (item(detect_hints=["(미완성"]), "정규식 오류"),
    (item(sensitive="yes"), "sensitive"),
])
def test_validator_catches_mistakes(extra, needle):
    errs = errors_of(extra)
    assert any(needle in e for e in errs), (needle, errs)


def test_validator_top_level_errors():
    assert R.validate([]) == ["최상위가 객체가 아님"]
    assert any("version" in e for e in R.validate({"items": [item()]}))
    assert any("비어 있음" in e for e in R.validate({"version": 1, "items": []}))
    with pytest.raises(R.RegistryError) as ei:
        R.from_data({"version": 1, "items": [item(code="a"), item(type="enum")]})
    msg = str(ei.value)                                                                      # 오류를 한꺼번에 알려 줌: 코드 형식 1건 + 라벨 겹침 1건 + enum 목록 없음 1건
    assert msg.startswith("항목 사전 오류 3건:") and msg.count("\n  - ") == 3
    assert "code 는 영문 대문자" in msg and "라벨/동의어" in msg and "type=enum 는 enum(허용 값 목록)이 필요함" in msg


# ------------------------------------------------------------------ 파일 읽기
def test_load_errors_are_clear(tmp_path):
    with pytest.raises(R.RegistryError, match="찾을 수 없음"):
        R.load_registry(str(tmp_path / "none.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{ 깨진 json", encoding="utf-8")
    with pytest.raises(R.RegistryError, match="올바른 JSON"):
        R.load_registry(str(bad))


def test_env_and_explicit_path_override(tmp_path, monkeypatch):
    data = json.loads(R.default_path().read_text(encoding="utf-8"))
    data["items"] = data["items"][:5]
    f = tmp_path / "mini.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert len(R.load_registry(str(f)).items) == 5
    monkeypatch.setenv("ITEM_REGISTRY_FILE", str(f))
    assert R.default_path() == f and len(R.load_registry().items) == 5
    f.write_text("\ufeff" + json.dumps(data, ensure_ascii=False), encoding="utf-8")        # BOM 이 있어도 읽음
    assert len(R.load_registry(str(f)).items) == 5
