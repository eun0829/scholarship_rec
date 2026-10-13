# -*- coding: utf-8 -*-
"""run_v3_match.py: 시험 추출 결과(v3_trial)를 학생 정보와 판정해 보는 확인용 스크립트."""
import json

import pytest

import run_v3_match
from scholarship.common.ai.schema import normalize
from tests.test_ext02_trial import IDEAL, NATIONAL_LIKE
from tests.test_student_text import SAMPLE

NOENV = ["--env", "/nonexistent/.env"]
ALLCAT = "OUT,IN,NATIONAL"
LIST = ["--format", "list"]


def write_trial(folder, notices):
    folder.mkdir(parents=True, exist_ok=True)
    for idx, (title, obj) in notices.items():
        data, _ = normalize(dict(obj, summary="x"), None, "", version=3)
        (folder / "{}_v3.json".format(idx)).write_text(json.dumps({"idx": idx, "title": title, "ok": True, "data": data}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def trial(tmp_path):
    d = tmp_path / "v3_trial"
    write_trial(d, {"9096": ("거창군 지역출신 대학생 등록금 지원", IDEAL["9096"]), "9212": ("문주장학재단 AI·ICT 인재장학", IDEAL["9212"]),
                    "9195": ("국가장학금", dict(NATIONAL_LIKE, apply_end="2026-10-08")), "9001": ("조건 없는 장학", {"category": "OUT", "apply_end": "2026-10-08"})})
    (d / "9100_v3.json").write_text(json.dumps({"idx": "9100", "ok": False, "data": None}), encoding="utf-8")        # 실패한 추출은 건너뜀
    (d / "9101_v3.json").write_text("{깨진 json", encoding="utf-8")                                                  # 읽을 수 없는 파일도 건너뜀
    (d / "9195_ai_raw.txt").write_text("원문", encoding="utf-8")
    return d


@pytest.fixture
def student(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text(SAMPLE, encoding="utf-8")
    return str(f)


def test_summary_lists_every_notice_with_a_verdict_and_reason(trial, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial)] + LIST + NOENV) == 0
    cap = capsys.readouterr()
    out = cap.out
    assert "=== v3 공고 4건 판정: 충족 1 / 조건부 1 / 미달 2 ===" in out                                             # 실패·깨진 파일은 세지 않음
    assert out.index("[충족] 9001") < out.index("[조건부]") < out.index("[미달]")                                    # 충족, 조건부, 미달 순서
    assert "[충족] 9001 조건 없는 장학" in out and "[조건부] 9195 국가장학금" in out
    assert "지역 조건 미달 (필요: [본인 주민등록 경상남도 거창군 3년 이상]" in out and "지역 조건 미달 (필요: [본인 주민등록 서울특별시 서초구 1년 이상]" in out
    assert "[학생 정보 텍스트] 읽은 항목 21개, 경고 0개" in cap.err


def test_detail_prints_all_checks_and_assumptions(trial, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--detail", "9195"] + LIST + NOENV) == 0
    out = capsys.readouterr().out
    section = out.split("[조건부] 9195")[1].split("[미달]")[0]
    assert "PASS    income" in section and "-- 유형 국가장학금Ⅰ·다자녀유형 -> 조건부" in section and "100점제 기준: 100점 환산 점수(직전학기 백분위)가 필요함" in section
    assert "신분 조건" in section                                                                                    # 안전망 항목도 이유와 함께 보임
    assert "PASS    levels" not in out.split("[미달] 9096")[1]                                                        # 상세는 지정한 공고에만 (다른 공고는 한 줄 요약)


def test_json_output_and_json_profile(trial, tmp_path, capsys):
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({"level": "UNIV", "gpa_last": 3.9, "gpa_scale": 4.5, "semesters_completed": 7, "residence_sido": "경상남도", "residence_sigungu": "거창군",
                                "residence_years": 5}), encoding="utf-8")
    assert run_v3_match.main(["--profile", str(prof), "--trial-dir", str(trial), "--json"] + NOENV) == 0
    data = json.loads(capsys.readouterr().out)
    by = {d["idx"]: d for d in data}
    assert by["9096"]["verdict"] == "CONDITIONAL" and any("지역 조건 충족" in m for m in by["9096"]["passed"])      # 거창군 거주 5년: 지역·성적·학점 충족, 나이 등은 확인
    assert by["9212"]["verdict"] == "CONDITIONAL"                                                                  # 본인은 서초구가 아니지만 부모 주민등록 정보가 없어 미달로 단정하지 않음
    assert any("부모 주민등록 정보 없음" in m for m in by["9212"]["to_confirm"])
    assert by["9001"]["verdict"] == "MATCH" and by["9195"]["variants"]


def test_input_errors_return_2(trial, student, tmp_path, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(tmp_path / "none")] + NOENV) == 2
    assert "판정할 v3 공고가 없습니다" in capsys.readouterr().err
    assert run_v3_match.main(["--profile-text", str(tmp_path / "x.txt"), "--trial-dir", str(trial)] + NOENV) == 2
    big = tmp_path / "big.txt"
    big.write_text("# 가" * 20000, encoding="utf-8")
    assert run_v3_match.main(["--profile-text", str(big), "--trial-dir", str(trial)] + NOENV) == 2
    assert "프로필 오류" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run_v3_match.main(["--trial-dir", str(trial)] + NOENV)                                                      # 프로필이 필요함


def test_missing_notice_in_detail_is_reported(trial, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--detail", "1234"] + NOENV) == 0
    assert "(없는 공고: 1234)" in capsys.readouterr().err


# ------------------------------------------------------------------ 학교 소재지 설정 (20건 실제 판정에서 설정이 없어 3건이 '확인 필요'로 남았다)
def school_trial(tmp_path):
    d = tmp_path / "school_trial"
    write_trial(d, {"9088": ("한국장학재단 지역인재장학금", {"category": "OUT", "apply_end": "2026-10-08",
                                                          "region": {"mode": "ANY", "requirements": [{"type": "SCHOOL_LOCATION", "areas": ["비수도권"]}]}}),
                    "9141": ("한중인재양성장학재단 장학", {"category": "OUT", "apply_end": "2026-10-08",
                                                       "region": {"mode": "ANY", "requirements": [{"type": "SCHOOL_LOCATION", "areas": ["중국"]}]},
                                                       "enrollment": {"required_status": ["재학", "입학예정"]}}),
                    "9058": ("서울미래인재재단 해외교환학생장학", {"category": "OUT", "apply_end": "2026-10-08",
                                                              "region": {"mode": "ANY", "requirements": [{"type": "SCHOOL_LOCATION", "areas": ["서울특별시"]}]},
                                                              "enrollment": {"required_status": ["재학", "휴학"]}})})
    return d


def test_without_school_settings_the_tool_warns_and_leaves_school_conditions_open(tmp_path, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(school_trial(tmp_path))] + NOENV) == 0
    cap = capsys.readouterr()
    assert "=== v3 공고 3건 판정: 충족 0 / 조건부 3 / 미달 0 ===" in cap.out
    assert "※ 학교 소재지 설정이 없습니다" in cap.err and "RECOMMEND_SCHOOL_SIDO / RECOMMEND_SCHOOL_SIGUNGU / RECOMMEND_SCHOOL_TYPE" in cap.err
    assert "학교 설정:" not in cap.err


def test_with_school_settings_school_location_conditions_are_decided(tmp_path, student, capsys, monkeypatch):
    for k, v in (("RECOMMEND_SCHOOL_SIDO", "경기도"), ("RECOMMEND_SCHOOL_SIGUNGU", "시흥시"), ("RECOMMEND_SCHOOL_TYPE", "GENERAL")):
        monkeypatch.setenv(k, v)
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(school_trial(tmp_path))] + NOENV) == 0
    cap = capsys.readouterr()
    assert "=== v3 공고 3건 판정: 충족 0 / 조건부 0 / 미달 3 ===" in cap.out                                       # 비수도권, 중국, 서울: 경기 시흥의 학교는 모두 해당하지 않음
    assert "학교 설정: 경기도 시흥시 (GENERAL)" in cap.err and "※ 학교 소재지 설정이 없습니다" not in cap.err
    assert "지역 조건 미달 (필요: [학교 소재지 비수도권])" in cap.out and "[학교 소재지 중국]" in cap.out and "[학교 소재지 서울특별시]" in cap.out


# ------------------------------------------------------------------ 외부01(--ext01-dir) + 외부02(--trial-dir) 함께 판정
from pathlib import Path
import shutil

EXT01_FIX = Path(__file__).parent / "fixtures" / "ext01_v3"


@pytest.fixture
def ext01(tmp_path):
    """실제 학교 공지 3건(국가근로장학금 153930, 대산장학생 153808, 은평구민 153907)의 v3 추출 결과 + 읽으면 안 되는 파일들."""
    d = tmp_path / "out_v3"
    shutil.copytree(EXT01_FIX, d)
    (d / "ext01_153381_ai_result.json").write_text(json.dumps({"elig": {"schema_version": 2, "scholarship_name": "옛 형식", "apply_end": "2026-12-31"}}), encoding="utf-8")   # v3 가 아니면 건너뜀
    (d / "ext01_153999_ai_result.json").write_text("{깨진 json", encoding="utf-8")                                                                                          # 읽을 수 없는 파일도 건너뜀
    (d / "ext01_153000_ai_input.txt").write_text("입력", encoding="utf-8")                                                                                                  # 결과 파일이 아님
    return d


def test_ext01_only_judges_school_notices_and_builds_homepage_urls(ext01, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--json", "--categories", ALLCAT] + NOENV) == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    by = {d["idx"]: d for d in data}
    assert set(by) == {"153930", "153808", "153907"}                                                                 # 옛 형식·깨진 파일·입력 파일은 세지 않음
    assert all(d["src_cd"] == "EXT01" for d in data)
    assert by["153930"]["url"] == "https://www.tukorea.ac.kr/bbs/tukorea/374/153930/artclView.do"
    assert "공고 3건: 외부01 3건 + 외부02 0건" in cap.err
    assert by["153930"]["verdict"] == "MATCH"                                                                        # 소득 2구간: 국가근로장학금 0~9구간 안
    assert by["153808"]["verdict"] == "CONDITIONAL"                                                                  # 평점·학기는 충족, '농업 종사 의지'는 학생이 판단
    assert by["153907"]["verdict"] == "NO_MATCH" and any("지역 조건 미달" in m for m in by["153907"]["fail_reasons"])      # 수원 거주: 은평구 2년 이상이 아님


def test_both_sources_are_judged_together_with_their_own_source_and_url(trial, ext01, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--ext01-dir", str(ext01), "--json", "--categories", ALLCAT] + NOENV) == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    by = {d["idx"]: d for d in data}
    assert len(data) == 7 and "공고 7건: 외부01 3건 + 외부02 4건" in cap.err
    assert by["9096"]["src_cd"] == "EXT02" and by["9096"]["url"].endswith("/scholarship/view.html?idx=9096")
    assert by["153808"]["src_cd"] == "EXT01" and by["153808"]["url"].endswith("/153808/artclView.do")


def test_follow_up_flow_works_with_both_sources_and_keeps_source_ids(trial, ext01, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--ext01-dir", str(ext01), "--follow-up", "--json", "--categories", ALLCAT] + NOENV) == 0
    res = json.loads(capsys.readouterr().out)
    ids = {n["id"] for k in ("recommended", "conditional") for n in res[k]} | {n["id"] for n in res["excluded"]}
    assert {"EXT01:153930", "EXT01:153808", "EXT01:153907", "EXT02:9096"} <= ids
    urls = {n["id"]: n["url"] for k in ("recommended", "conditional") for n in res[k]}
    assert urls["EXT01:153930"].startswith("https://www.tukorea.ac.kr/bbs/tukorea/374/")


def test_open_only_drops_notices_closed_on_the_reference_date(ext01, student, capsys):
    base = ["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--json", "--categories", ALLCAT] + NOENV
    assert run_v3_match.main(base + ["--today", "2026-10-13"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3                                                             # --open-only 가 없으면 기준일과 상관없이 모두 판정
    assert run_v3_match.main(["--open-only"] + base + ["--today", "2026-10-30"]) == 0                              # 은평구민(10/28)·국가근로(10/29)는 닫힘, 대산(11/11)만 남음
    cap = capsys.readouterr()
    assert [d["idx"] for d in json.loads(cap.out)] == ["153808"] and "마감이 지난 공고 2건을 뺐습니다" in cap.err
    assert run_v3_match.main(["--open-only"] + base + ["--today", "2026-12-01"]) == 2                              # 모두 닫혔으면 판정할 공고가 없음
    assert "판정할 v3 공고가 없습니다" in capsys.readouterr().err


def test_open_only_keeps_notices_without_a_deadline(trial, student, capsys):
    (trial / "9300_v3.json").write_text(json.dumps({"idx": "9300", "title": "마감 미상", "ok": True, "data": normalize({"category": "OUT", "summary": "x"}, None, "", version=3)[0]}), encoding="utf-8")
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--open-only", "--today", "2026-10-30", "--json"] + NOENV) == 0
    assert [d["idx"] for d in json.loads(capsys.readouterr().out)] == ["9300"]                                       # 마감 지난 4건은 빠지고, 마감일을 모르는 공고는 남음


def test_missing_ext01_dir_returns_2(trial, student, tmp_path, capsys):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial), "--ext01-dir", str(tmp_path / "none")] + NOENV) == 2
    assert "--ext01-dir 폴더가 없습니다" in capsys.readouterr().err


def test_default_recommends_only_out_and_drops_info_and_national(ext01, student, capsys):
    base = ["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--json"] + NOENV
    assert run_v3_match.main(base) == 0
    cap = capsys.readouterr()
    assert "추천 대상 구분(OUT)" in cap.err and "NATIONAL 1건" in cap.err and "INFO 1건" in cap.err           # 국가근로(NATIONAL)는 기본에서 빠지고 이유가 표시됨


def test_categories_option_rejects_unknown_values(ext01, student, capsys):
    assert run_v3_match.main(["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--categories", "OUT,INFO"] + NOENV) == 2


def test_info_notice_is_never_judged_even_when_all_recommendable_categories_are_on(ext01, student, capsys):
    """학자금 지원구간 개편 안내(153545, INFO)는 장학금이 아니라서 어떤 설정에서도 판정 대상이 아니다."""
    assert run_v3_match.main(["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--json", "--categories", ALLCAT] + NOENV) == 0
    cap = capsys.readouterr()
    assert "153545" not in cap.out and "INFO 1건" in cap.err


def test_ext02_dump_files_in_the_same_folder_are_read_as_external_notices(ext01, student, capsys):
    """run_ext02.py --dump-dir 로 받은 ext02_글번호_ai_result.json 도 같은 폴더에서 함께 판정한다 (원문 주소는 드림스폰)."""
    shutil.copy(ext01 / "ext01_153808_ai_result.json", ext01 / "ext02_9999_ai_result.json")
    assert run_v3_match.main(["--profile-text", student, "--ext01-dir", str(ext01), "--trial-dir", "", "--json", "--categories", ALLCAT] + NOENV) == 0
    by = {r["idx"]: r for r in json.loads(capsys.readouterr().out)}
    assert by["9999"]["src_cd"] == "EXT02" and "dreamspon.com" in by["9999"]["url"] and by["153808"]["src_cd"] == "EXT01"
