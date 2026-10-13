# -*- coding: utf-8 -*-
"""4-B1 지원 기능: 항목 사전의 enum_codes, 학과 계열 입력, 본교 참여 사업 설정, 별도 표기 출력, AI 프롬프트가 바뀌지 않았는지."""
import copy
import json

import pytest

import run_v3_match
from scholarship.common import registry as R
from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
from scholarship.common.config import Settings
from scholarship.recommend import conditions_v3 as C
from scholarship.recommend.profile import ProfileError, StudentProfile, build_profile
from scholarship.recommend.service import Recommender, format_text, side_lines
from scholarship.recommend.student_text import build_template, parse_student_text, profile_from_text, profile_to_text
from tests.test_matcher_v3 import SAMPLE, cand, run3, v3cand
from tests.test_run_v3_match import NOENV, write_trial

DEFAULT = json.loads(open(R.default_path(), encoding="utf-8").read())


def registry_with(**changes_by_code):
    d = copy.deepcopy(DEFAULT)
    for it in d["items"]:
        it.update(changes_by_code.get(it["code"], {}))
    return d


# ================================================================== 항목 사전: enum_codes
def test_major_group_item_is_a_student_only_item_that_never_reaches_the_ai_prompt():
    reg = R.load_registry()
    it = reg.get("MAJG")
    assert (it.label, it.source, it.type, it.profile_field, it.notice_path, it.tier) == ("학과 계열", "school_db", "enum", "major_group", "", 2)
    assert it.enum == ("인문", "사회", "자연", "공학", "의약", "예체능", "교육", "농림")
    assert it.enum_codes == ("HUMANITIES", "SOCIAL", "NATURAL", "ENGINEERING", "MEDICAL", "ARTS_PE", "EDUCATION", "AGRICULTURE")
    prompt = build_system_prompt_v3(reg)
    assert "MAJG" not in prompt and "학과 계열" not in prompt and "MAJG" not in "\n".join(reg.prompt_lines())            # 공고 경로가 없는 항목은 프롬프트에 들어가지 않음
    assert build_system_prompt_v3(reg.with_items([])) == prompt
    without = copy.deepcopy(DEFAULT)
    without["items"] = [x for x in without["items"] if x["code"] != "MAJG"]
    assert build_system_prompt_v3(R.from_data(without)) == prompt                                                   # MAJG 가 있든 없든 AI 프롬프트는 같다


def test_enum_codes_lookup_and_round_trip():
    it = R.load_registry().get("MAJG")
    assert it.code_for("공학") == "ENGINEERING" and it.word_for("MEDICAL") == "의약" and it.code_for("기계") is None and it.word_for("X") is None
    assert R.load_registry().get("LV").code_for("재학") is None                                                         # enum_codes 가 없는 항목
    again = R.from_data({"version": 1, "items": [R._to_dict(x) for x in R.load_registry().items]})
    assert again.get("MAJG").enum_codes == it.enum_codes


@pytest.mark.parametrize("change, message", [
    ({"enum_codes": ["A", "B"]}, "enum 과 같은 개수"),
    ({"enum_codes": ["HUMANITIES", "SOCIAL", "NATURAL", "ENGINEERING", "MEDICAL", "ARTS_PE", "EDUCATION", "EDUCATION"]}, "서로 다른 코드"),
    ({"enum_codes": ["humanities", "SOCIAL", "NATURAL", "ENGINEERING", "MEDICAL", "ARTS_PE", "EDUCATION", "AGRICULTURE"]}, "영문 대문자"),
    ({"enum_codes": "HUMANITIES"}, "enum 과 같은 개수"),
])
def test_invalid_enum_codes_are_reported(change, message):
    with pytest.raises(R.RegistryError, match=message):
        R.from_data(registry_with(MAJG=change))


def test_enum_codes_need_an_enum():
    with pytest.raises(R.RegistryError, match="enum_codes 는 enum 이 있는 항목에서만"):
        R.from_data(registry_with(MAJ={"enum_codes": ["X"]}))


def test_the_schools_own_labels_can_replace_the_defaults_without_code_changes():
    own = registry_with(MAJG={"enum": ["인문사회", "자연과학", "공학", "의학", "예체능"], "enum_codes": ["HUMANITIES", "NATURAL", "ENGINEERING", "MEDICAL", "ARTS_PE"]})
    reg = R.from_data(own)
    assert parse_student_text("- 학과 계열: 자연과학", reg).data == {"major_group": "NATURAL"}
    assert parse_student_text("- 계열: 인문사회계열", reg).data == {"major_group": "HUMANITIES"}
    assert parse_student_text("- 학과 계열: 사회", reg).report.warnings[0].startswith("학과 계열: 허용 값이 아닙니다")
    assert "허용 값: 인문사회 / 자연과학 / 공학 / 의학 / 예체능" in build_template(reg)
    p, _ = profile_from_text("- 학과 계열: 공학", reg)
    assert "- 학과 계열: 공학" in profile_to_text(p.profile, reg)


def test_guard_special_codes_the_engine_can_judge_match_the_registry():
    assert set(C.ANSWERABLE_SPECIAL) == {i.special_code for i in R.load_registry().enabled() if i.special_code}


# ================================================================== 학생 입력: 학과 계열
@pytest.mark.parametrize("value, code", [("공학", "ENGINEERING"), ("공학계열", "ENGINEERING"), ("인문", "HUMANITIES"), ("사회", "SOCIAL"), ("자연", "NATURAL"), ("의약", "MEDICAL"),
                                         ("예체능", "ARTS_PE"), ("교육", "EDUCATION"), ("농림", "AGRICULTURE"), (" 공 학 ", "ENGINEERING")])
def test_major_group_text_input(value, code):
    ps = parse_student_text("- 학과 계열: " + value)
    assert ps.data == {"major_group": code} and ps.report.warnings == []
    assert parse_student_text("- 계열: " + value).data == {"major_group": code} and parse_student_text("- 전공 계열: " + value).data == {"major_group": code}


def test_major_group_input_errors_keep_the_rest():
    ps = parse_student_text("- 학과 계열: 기계\n- 이수학기: 7")
    assert ps.data == {"semesters_completed": 7} and "허용 값이 아닙니다" in ps.report.warnings[0] and "MAJG" not in ps.report.recognized
    assert "MAJG" in [m["code"] for m in ps.report.missing]
    assert parse_student_text("- 학과 계열: 답하지 않음").report.declined == ["MAJG"]


@pytest.mark.parametrize("value, code", [("ENGINEERING", "ENGINEERING"), ("engineering", "ENGINEERING"), ("공학", "ENGINEERING"), ("공학계열", "ENGINEERING"), ("의학", "MEDICAL"), (None, None)])
def test_major_group_json_input(value, code):
    assert build_profile({"major_group": value}).profile.major_group == code


def test_major_group_json_rejects_unknown_values():
    with pytest.raises(ProfileError, match="major_group"):
        build_profile({"major_group": "기계"})


# ================================================================== 본교 참여 사업 설정
def test_school_programs_setting_fills_the_profile_only_when_the_student_gave_none(monkeypatch):
    monkeypatch.setenv("RECOMMEND_SCHOOL_PROGRAMS", "NATIONAL_SCHOLARSHIP, 지역인재")
    s = Settings.from_env()
    assert s.recommend_school_programs == ("NATIONAL_SCHOLARSHIP", "지역인재")
    p = StudentProfile()
    Recommender(None, s)._fill_school_defaults(p)
    assert p.school_programs == ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"]
    own = StudentProfile(school_programs=["HOUSING_STABILITY"])
    Recommender(None, s)._fill_school_defaults(own)
    assert own.school_programs == ["HOUSING_STABILITY"]
    monkeypatch.delenv("RECOMMEND_SCHOOL_PROGRAMS")
    unset = StudentProfile()
    Recommender(None, Settings.from_env())._fill_school_defaults(unset)
    assert Settings.from_env().recommend_school_programs == () and unset.school_programs is None                     # 설정이 없으면 '설정 없음'


def test_invalid_school_programs_setting_is_reported():
    s = Settings()
    s.recommend_school_programs = ("없는사업",)
    with pytest.raises(ProfileError, match="환경설정 오류: RECOMMEND_SCHOOL_PROGRAMS"):
        Recommender(None, s)._fill_school_defaults(StudentProfile())


def test_service_shows_whether_the_school_participates_using_the_setting():
    n = v3cand(1, school={"programs": ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"]})
    s = Settings()
    s.recommend_school_programs = ("NATIONAL_SCHOLARSHIP",)
    item = run3([n], settings=s)["recommended"][0]
    assert [(x["label"], x["school"]) for x in item["school_programs"]] == [("국가장학금 참여대학", "PARTICIPATES"), ("지역인재장학금 참여대학", "NOT")]
    assert run3([n])["recommended"][0]["school_programs"][0]["school"] == "UNKNOWN"


# ================================================================== 서비스 출력: 별도 표기
RICH = dict(prerequisites=[{"type": "NATIONAL_SCHOLARSHIP_APPLIED", "text": "해당 학기 신청자"}],
            special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"}], school={"programs": ["NATIONAL_SCHOLARSHIP"]},
            obligations=["장학증서 수여식 필참"], review_criteria=["자기소개서"], recommendation={"required": True, "from": ["PROFESSOR"]},
            exclusion_items=[{"type": "CONCURRENT_ANY", "text": "타 장학금 중복 수혜 불가"}],
            variants=[{"name": "대학생", "target_levels": ["UNIV"], "apply_end": "2026-10-07", "special_groups": [{"code": "NEAR_POOR", "mode": "PREFERRED"}]},
                      {"name": "2차 접수", "target_levels": ["UNIV"], "apply_end": "2026-09-23"}])


def test_service_item_carries_the_separate_sections_and_variant_dates():
    res = run3([v3cand(1, **RICH)])
    item = (res["recommended"] + res["conditional"])[0]
    assert [x["status"] for x in item["prerequisites"]] == ["UNKNOWN"] and [(x["label"], x["applies"]) for x in item["preferences"]] == [("기초생활수급자", False)]
    assert [x["kind"] for x in item["notes"]] == ["EXCLUSION", "OBLIGATION", "REVIEW", "RECOMMENDATION"]
    by = {v["name"]: v for v in item["matched_variants"]}
    assert (by["대학생"]["apply_end"], by["2차 접수"]["apply_end"]) == ("2026-10-07", "2026-09-23") and by["대학생"]["preferences"][0]["label"] == "차상위계층"


def test_text_output_lists_the_separate_sections_on_their_own_lines():
    text = format_text(run3([v3cand(1, **RICH)]))
    assert "    선행 조건: 국가장학금 신청 (확인 필요)" in text
    assert "    우대 조건: 기초생활수급자 (해당 없음) / 대학생 - 차상위계층 (해당 없음)" in text
    assert "    학교 사업: 국가장학금 참여대학 (본교 참여 여부 설정 없음)" in text
    assert "    확인 사항: 제외·중복수혜 제한: 타 장학금 중복 수혜 불가; 선발 후 의무: 장학증서 수여식 필참; 심사 기준: 자기소개서 외 1개 (--json 으로 전체 확인)" in text
    assert "대학생(충족, ~10-07)" in text and "2차 접수(충족, ~09-23 마감 지남)" in text                                                    # 유형별 마감


def test_v2_items_have_empty_separate_sections():
    item = run3([cand(5)])["recommended"][0]
    assert item["prerequisites"] == [] and item["preferences"] == [] and item["school_programs"] == [] and item["notes"] == []
    assert "선행 조건" not in format_text(run3([cand(5)])) and "확인 사항" not in format_text(run3([cand(5)]))


def test_side_lines_formatting():
    assert side_lines([], [], [], []) == []
    lines = side_lines([{"label": "학위 소지", "status": "UNMET"}, {"label": "국가장학금 신청", "status": "TODO"}], [{"label": "다자녀가정", "applies": True}, {"label": "한부모가정", "applies": None}],
                       [{"label": "국가장학금 참여대학", "school": "NOT"}], [{"label": "심사 기준", "text": "가" * 60}] * 5, max_notes=2)
    assert lines[0] == "선행 조건: 학위 소지 (미충족) / 국가장학금 신청 (신청 필요)" and lines[1] == "우대 조건: 다자녀가정 (해당) / 한부모가정 (모름)"
    assert lines[2] == "학교 사업: 국가장학금 참여대학 (본교 미참여)" and lines[3].count("가" * 40) == 2 and lines[3].endswith("외 3개 (--json 으로 전체 확인)")


# ================================================================== 확인 도구
def test_run_v3_match_shows_the_separate_sections(tmp_path, capsys, monkeypatch):
    d = tmp_path / "t"
    write_trial(d, {"9096": ("거창군 장학", dict(IDEAL_9096(), apply_end="2026-10-08"))})
    s = tmp_path / "s.txt"
    s.write_text(SAMPLE.replace("본인 주민등록: 경기도 수원시", "본인 주민등록: 경상남도 거창군"), encoding="utf-8")
    monkeypatch.setenv("RECOMMEND_SCHOOL_PROGRAMS", "NATIONAL_SCHOLARSHIP")
    monkeypatch.setenv("RECOMMEND_SCHOOL_SIDO", "경기도")
    assert run_v3_match.main(["--profile-text", str(s), "--trial-dir", str(d), "--detail", "9096", "--format", "list"] + NOENV) == 0
    cap = capsys.readouterr()
    assert "[충족] 9096 거창군 장학" in cap.out and "[별도] 선행 조건: 국가장학금 신청 (확인 필요)" in cap.out and "[별도] 학교 사업: 국가장학금 참여대학 (본교 참여)" in cap.out
    assert "[별도] 확인 사항: 제외·중복수혜 제한: 본인 부담 등록금이 1만원 미만인 자 선발 제외" in cap.out
    assert "학교 설정: 경기도 (종류 미설정) | 참여 사업: NATIONAL_SCHOLARSHIP" in cap.err
    assert run_v3_match.main(["--profile-text", str(s), "--trial-dir", str(d), "--json"] + NOENV) == 0
    item = json.loads(capsys.readouterr().out)[0]
    assert item["verdict"] == "MATCH" and item["prerequisites"][0]["status"] == "UNKNOWN" and item["school_programs"][0]["school"] == "PARTICIPATES" and item["notes"][0]["kind"] == "EXCLUSION"


def IDEAL_9096():
    from tests.test_ext02_trial import IDEAL
    return IDEAL["9096"]


# ================================================================== 유형별 마감이 지난 유형 표시 (9211 '성취 장학(대학생) ~09-08')
def deadline_notice():
    from tests.test_matcher_v3 import notice, prof
    n = notice(variants=[{"name": "성취 장학(대학생)", "target_levels": ["UNIV"], "apply_end": "2026-09-08"}, {"name": "과학 장학", "target_levels": ["UNIV"], "apply_end": "2026-10-07"},
                         {"name": "기한 없음", "target_levels": ["UNIV"]}, {"name": "오늘 마감", "target_levels": ["UNIV"], "apply_end": "2026-10-11"}])
    return n, M_evaluate(n)


def M_evaluate(n):
    from scholarship.recommend import matcher
    from tests.test_matcher_v3 import prof
    return matcher.evaluate(n, prof())


def test_variant_deadlines_that_have_passed_are_marked_expired():
    from datetime import date
    from scholarship.recommend.service import _variant_label, evaluation_fields
    n, ev = deadline_notice()
    f = evaluation_fields(ev, date(2026, 10, 11))
    assert [(v["name"], v["expired"]) for v in f["matched_variants"]] == [("성취 장학(대학생)", True), ("과학 장학", True), ("기한 없음", False), ("오늘 마감", False)]   # 마감 당일은 지나지 않음
    assert [_variant_label(v) for v in f["matched_variants"]][:2] == ["성취 장학(대학생)(충족, ~09-08 마감 지남)", "과학 장학(충족, ~10-07 마감 지남)"]
    assert _variant_label(f["matched_variants"][3]) == "오늘 마감(충족, ~10-11)"
    assert all(v["expired"] is False for v in evaluation_fields(ev)["matched_variants"])                          # 기준일이 없으면 표시하지 않음
    assert [v["expired"] for v in evaluation_fields(ev, date(2026, 9, 1))["matched_variants"]] == [False] * 4


def test_service_passes_the_reference_date_to_variant_deadlines_and_tables_show_it():
    from scholarship.recommend.service import format_table
    from tests.test_matcher_v3 import run3, v3cand
    res = run3([v3cand(1, variants=[{"name": "지난 유형", "target_levels": ["UNIV"], "apply_end": "2026-10-01"}, {"name": "남은 유형", "target_levels": ["UNIV"], "apply_end": "2026-10-17"}])])
    item = res["recommended"][0]                                                                                 # 기준일 2026-10-06
    assert [(v["name"], v["expired"]) for v in item["matched_variants"]] == [("지난 유형", True), ("남은 유형", False)]
    t = format_table(res, width=140)
    assert "지난 유형(충족, ~10-01 마감 지남)" in t and "남은 유형(충족, ~10-17)" in t


def test_run_v3_match_today_option(tmp_path, capsys):
    import run_v3_match
    from tests.test_run_v3_match import NOENV, write_trial
    from tests.test_student_text import SAMPLE
    d = tmp_path / "t"
    write_trial(d, {"9211": ("대전시 인재육성장학", {"category": "OUT", "apply_end": "2026-10-07", "variants": [
        {"name": "성취 장학(대학생)", "target_levels": ["UNIV"], "apply_end": "2026-09-08"}, {"name": "과학 장학", "target_levels": ["UNIV"], "apply_end": "2026-10-07"}]})})
    s = tmp_path / "s.txt"
    s.write_text(SAMPLE, encoding="utf-8")
    base = ["--profile-text", str(s), "--trial-dir", str(d)] + NOENV
    assert run_v3_match.main(base + ["--today", "2026-09-20"]) == 0
    out = capsys.readouterr().out
    assert "성취 장학(대학생)(충족, ~09-08 마감 지남)" in out and "과학 장학(충족, ~10-07)" in out
    assert run_v3_match.main(base + ["--today", "2026-09-01"]) == 0
    assert "마감 지남" not in capsys.readouterr().out
    assert run_v3_match.main(base + ["--today", "내일"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
