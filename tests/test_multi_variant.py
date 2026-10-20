# -*- coding: utf-8 -*-
"""한 공고에 장학금이 여러 개(유형)인 경우: 유형명 접두어 매칭, 고등학생 전용 문장 제외, 유형별 결과·서류 표시 (실제 공고 152953 형태)."""
import json
import pathlib

from scholarship.common.ai.schema import normalize
from scholarship.recommend import matcher_v3 as V
from scholarship.recommend.matcher import CONDITIONAL, NO_MATCH
from scholarship.recommend.profile import build_profile
from scholarship.recommend import present, service

FIX = pathlib.Path(__file__).parent / "fixtures" / "elig_incheon_152953.json"


def elig():
    return json.loads(FIX.read_text(encoding="utf-8"))


def prof(**kw):
    d = {"level": "UNIV", "gpa_scale": 4.5, "status": "ENROLLED", "grade": 3, "semesters_completed": 5, "gpa_last": 3.4, "credits_last": 18, "income_bracket": 5}
    d.update(kw)
    return build_profile(d).profile


def test_prefix_matches_name_with_school_level_suffix():
    assert V._prefix_matches("인천인희망드림 고등학생", {"인천인희망드림"})
    assert V._prefix_matches("iHUG 고등학생", {"iHUG인천도시공사"})
    assert not V._prefix_matches("공익인재", {"iHUG인천도시공사"})
    assert not V._prefix_matches("A", {"AB"})


def test_high_school_only_sentences():
    assert V.is_high_school_only("SK행복 고등학생: 가정형편 요건, 직전학기 내신 6등급 이내")
    assert V.is_high_school_only("직전 학기를 학점은행제 등 지원 제외 대상으로 이수한 경우 고3 2학기 내신 6등급 이내여야 함")
    assert not V.is_high_school_only("신입생은 고교 내신 3등급 이내")
    assert not V.is_high_school_only("공익인재: 최근 3년간 100시간 이상 봉사활동")
    assert not V.is_high_school_only("고등학교 졸업자")


def test_high_school_conditions_not_listed_for_university_student():
    ev = V.evaluate_v3(elig(), prof(residence_sido="경기도", residence_sigungu="안산시", parent_residence_sido="경기도", parent_residence_sigungu="안산시"))
    assert not any("고등학생" in t or "내신" in t for t in ev.to_confirm)


def test_other_region_student_fails_incheon_only_variants():
    ev = V.evaluate_v3(elig(), prof(residence_sido="경기도", residence_sigungu="안산시", parent_residence_sido="경기도", parent_residence_sigungu="안산시"))
    by = {v.name: v.verdict for v in ev.variants}
    assert by["관외대학 거주비 지원"] == NO_MATCH and by["공익인재"] == NO_MATCH and by["iHUG 인천도시공사"] == NO_MATCH


def test_incheon_student_matches_incheon_variants():
    ev = V.evaluate_v3(elig(), prof(residence_sido="인천광역시", residence_sigungu="부평구", parent_residence_sido="인천광역시", parent_residence_sigungu="부평구"))
    assert ev.verdict == CONDITIONAL or ev.verdict == "MATCH"
    assert any(v.name == "공익인재" for v in ev.matched_variants)


def test_variant_rows_and_docs_in_card():
    e = elig()
    e["variants"][2]["documents"] = ["봉사활동 실적 증명서 (1365/VMS)"]
    ev = V.evaluate_v3(e, prof(residence_sido="인천광역시", residence_sigungu="부평구", parent_residence_sido="인천광역시", parent_residence_sigungu="부평구"))
    it = service.evaluation_fields(ev)
    rows = present.variant_rows(it)
    assert len(rows) == 6 and {r["result"] for r in rows} <= {"met", "confirm", "fail"}
    assert any("봉사활동 실적 증명서" in t for t in present.document_lines(it))                       # 유형별 서류는 제출 서류 행에 ('유형 - 서류 이름들')
    assert any("봉사활동 실적 증명서" in r.get("docs", "") for r in rows)                                 # 웹은 유형 표의 '추가 서류' 칸


def test_variant_rows_empty_for_single_variant():
    assert present.variant_rows({"matched_variants": [{"name": "a", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": []}], "unmatched_variants": []}) == []


def test_schema_keeps_variant_documents():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "summary": "x",
           "variants": [{"name": "A", "documents": ["서류1", "서류1", "서류2"]}, {"name": "B"}]}
    out, _ = normalize(obj, None, "", version=3)
    assert out["variants"][0]["documents"] == ["서류1", "서류2"] and "documents" not in out["variants"][1]


def test_prompt_guides_multi_scholarship_notices():
    from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
    p = build_system_prompt_v3()
    assert "장학금 하나가 유형 하나" in p and "documents(서류 이름 목록)" in p and "시·도와 시·군·구를 알 수 있으면 앞에 붙" in p
    assert 8000 < len(p) < 22000


def test_majors_given_as_group_names_use_the_students_group():
    from scholarship.recommend import matcher as M
    p = prof(major="기계공학과", major_group="ENGINEERING")
    assert M.check_majors(["공학계열", "자연계열"], p, "공통")[0].status == M.PASS
    assert M.check_majors(["이공계"], p, "공통")[0].status == M.PASS
    q = prof(major="경영학과", major_group="SOCIAL")
    assert M.check_majors(["공학계열"], q, "공통")[0].status == M.UNKNOWN           # 아니라고 단정하지 않는다
    assert M.check_majors(["컴퓨터공학과"], prof(major="컴퓨터공학과"), "공통")[0].status == M.PASS
    assert M.check_majors(["공학계열"], prof(), "공통")[0].status == M.UNKNOWN        # 학과·계열 모두 없으면 모름


def test_self_attest_rules_classify_sentences():
    from scholarship.recommend import self_attest as S
    assert S.match("정학 이상 징계 처분자 제외")
    assert S.match("대학생 : 2개 학기 이상 성적증명서 제출이 가능한 자")
    assert S.match("타 장학금 중복 수혜자 제외")
    assert S.match("원덕고 재학생은 학교장 추천 (징계 이력 없음)") is None           # 학교가 추천하는 조건은 본인 확인이 아니다
    assert S.match("경제적 부담으로 학업에 어려움이 있는 자") is None               # 기본으로 꺼 둔 규칙
    assert S.match("부 or 모가 중소기업에 근무") is None
    assert S.match("정학 징계", "/nonexistent/rules.json") is None                  # 규칙 파일이 없으면 기존처럼 확인 필요


def test_self_attest_sentence_does_not_block_a_match():
    from scholarship.recommend import matcher as M
    cs = M.check_text_conditions([], ["정학 이상 징계 처분자 제외", "부 or 모가 중소기업에 근무"], [], prof(), "공통")
    assert [c.key for c in cs] == ["self", "other"] and cs[0].status == M.PASS and cs[1].status == M.UNKNOWN


def test_self_confirm_is_reported_but_not_in_passed_or_to_confirm():
    e = elig()
    e["other_conditions"] = list(e.get("other_conditions") or []) + ["정학 이상 징계 처분자 제외"]
    ev = V.evaluate_v3(e, prof(residence_sido="인천광역시", residence_sigungu="부평구", parent_residence_sido="인천광역시", parent_residence_sigungu="부평구"))
    assert any("징계" in t for t in ev.self_confirm)
    assert not any("징계" in t for t in ev.to_confirm) and not any("징계" in t for t in ev.passed)
    it = service.evaluation_fields(ev)
    assert any("본인 확인: " in t and "징계" in t for t in present.compact_notes(it))


def test_reextract_commands_group_by_source():
    import run_accuracy
    lint = {"EXT01:1": [{"code": "PREFIX_IN_COMMON", "detail": ""}], "EXT01:2": [{"code": "ONE_VARIANT", "detail": ""}],
            "EXT02:9": [{"code": "OLD_SCHEMA", "detail": ""}], "EXT02:10": [{"code": "OLD_SCHEMA", "detail": ""}]}
    out = run_accuracy.reextract_commands(lint, ["PREFIX_IN_COMMON", "OLD_SCHEMA"], chunk=1)
    assert "run_ext01.py --days 400 --only 1 --force" in out and "--only 2 " not in out
    assert "run_ext02.py --include-closed --only 10 --force" in out and "외부01 1건, 드림스폰 2건" in out
