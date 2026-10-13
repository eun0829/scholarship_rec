# -*- coding: utf-8 -*-
"""외부02 가 공통 파이프라인과 맞물리는 동작: guide_only 첨부 정책, ZIP 내부 파일 분류, 신청기간 교차 검증, D-day 보조,
신청기간만 바뀐 글의 갱신, 상세 미조회(stub) 처리, 사라진 글 정리 범위."""
import io
import json
import zipfile
from datetime import date

import pytest

from scholarship.common.config import Settings
from scholarship.common.models import SourceNotice
from scholarship.common.repository import MemoryRepository
from scholarship.sources import ext02_parse as P
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config
from tests.fake_dreamspon import DEFAULT_SECTIONS, detail_html
from tests.test_pipeline import FakeAdapter, FakeAI, GOOD, TODAY, make_pipe

AI_OK = dict(GOOD, apply_type="DIRECT", apply_start="2026-10-01", apply_end="2026-10-31", org_deadline=None, summary="요약")
GUIDE_TEXT, FORM_TEXT, UNKNOWN_TEXT = "공고문본문-자격요건-평점3.0", "지원서양식-제출서류체크리스트", "붙임자료-미분류내용"


class Ext02Fake(FakeAdapter):
    src_cd = "EXT02"
    full_scope = True
    deactivation_safe = True


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


def make_notice(tmp_path, files=(), idx="9218", sections=None, dday=3, **detail_kw):
    """실제 파서/변환(Ext02Adapter._build_notice)으로 SourceNotice 를 만들고, 첨부 파일은 로컬 파일로 연결한다."""
    names = [(name, "/webData/x/{}mod{}".format(i, name)) for i, (name, _) in enumerate(files, 1)]
    html = detail_html(idx, files=names, sections=sections, **detail_kw)
    ad = Ext02Adapter(Settings(), Ext02Config(), today=TODAY)
    row = P.ListRow(idx=idx, title="t", org="o", tags=["장학금", "대학생"], dday=dday, state="모집중")
    n = ad._build_notice(row, P.parse_detail(html))
    paths = {}
    for att, (name, data) in zip(n.attachments, files):
        p = tmp_path / "src_{}".format(att.file_seq)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        paths[att.file_seq] = p
    return n, paths


def run(settings, n, paths, ai=None, repo=None, adapter_cls=Ext02Fake, **kw):
    ai = ai or FakeAI(AI_OK)
    ad = adapter_cls([n], files=paths)
    pipe, repo = make_pipe(settings, ad, ai, repo=repo, **kw)
    summ = pipe.run()
    return summ, repo, ai, ad


def rec(repo, idx="9218"):
    return repo.notices[("EXT02", idx)]


# ================================================================== guide_only: 공고문만 AI 에, 전부 저장
def test_guide_only_sends_guides_but_stores_every_attachment(settings, tmp_path):
    files = [("2026 장학 선발 공고문.txt", GUIDE_TEXT), ("장학금 지원서.txt", FORM_TEXT), ("붙임자료.txt", UNKNOWN_TEXT)]
    n, paths = make_notice(tmp_path, files)
    assert settings.ext02_attach_to_ai == "guide_only"
    summ, repo, ai, _ = run(settings, n, paths)
    text = ai.calls[0][0]
    assert GUIDE_TEXT in text and UNKNOWN_TEXT in text and FORM_TEXT not in text and "참고 첨부" not in text   # 양식은 AI 에 보내지 않음
    rows = {r["org_file_nm"]: r for (c, i, s), r in repo.files.items() if c == "EXT02"}
    assert rows["2026 장학 선발 공고문.txt"]["role_cd"] == "GUIDE" and rows["장학금 지원서.txt"]["role_cd"] == "FORM"
    assert all(r["status_cd"] == "EXTRACTED" and r["extract_text"] for r in rows.values())                  # 양식 텍스트도 DB 에 저장
    assert rows["장학금 지원서.txt"]["extract_text"] == FORM_TEXT and rows["붙임자료.txt"]["role_cd"] == "UNKNOWN"
    assert summ.att_ok == 3


def test_attach_modes(settings, tmp_path):
    files = [("공고문.txt", GUIDE_TEXT), ("지원서.txt", FORM_TEXT)]
    n, paths = make_notice(tmp_path, files)
    settings.ext02_attach_to_ai = "guide_and_form"
    text = run(settings, n, paths)[2].calls[0][0]
    assert GUIDE_TEXT in text and FORM_TEXT in text and "[참고 첨부(양식/보조)" in text
    settings.ext02_attach_to_ai = "none"
    summ, repo, ai, _ = run(settings, n, paths)
    assert GUIDE_TEXT not in ai.calls[0][0] and FORM_TEXT not in ai.calls[0][0]                          # 첨부는 AI 에 보내지 않음
    assert {r["status_cd"] for (c, i, s), r in repo.files.items()} == {"EXTRACTED"}                         # 첨부는 AI 에 안 보내도 받아서 저장은 함
    assert "신청자격" in ai.calls[0][0] and rec(repo)["status_cd"] == "EXTRACTED"                          # 본문 7개 항목만으로 추출 진행


def test_ext01_keeps_guide_and_form_behavior(settings, tmp_path):
    from scholarship.common.models import SourceAttachment
    n = SourceNotice(src_cd="EXT01", src_id="1", title="외부01 공고", body_html="<p>본문입니다 " + "가" * 300 + "</p>",
                     attachments=[SourceAttachment("1", 1, "공고문.txt"), SourceAttachment("2", 2, "지원서.txt")])
    a = FakeAdapter([n], files={"1": _write(tmp_path, "g.txt", GUIDE_TEXT), "2": _write(tmp_path, "f.txt", FORM_TEXT)})
    pipe, repo = make_pipe(settings, a, FakeAI(GOOD))
    pipe.run()
    assert settings.ext02_attach_to_ai == "guide_only" and a.src_cd == "EXT01"
    assert FORM_TEXT in pipe.ai.calls[0][0]                                                                 # 외부01 은 기존대로 양식도 참고로 전달


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_form_failures_do_not_trigger_review_but_guide_failures_do(settings, tmp_path):
    files = [("지원서.docx", b"PK-not-a-real-docx"), ("공고문.txt", GUIDE_TEXT)]
    n, paths = make_notice(tmp_path, files)
    summ, repo, ai, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK)))
    rows = {r["org_file_nm"]: r for (c, i, s), r in repo.files.items()}
    assert rows["지원서.docx"]["status_cd"] == "FAILED" and summ.att_failed == 1
    assert rec(repo)["status_cd"] == "EXTRACTED"                                                           # 양식 실패는 검수 사유가 아님
    files = [("공고문.docx", b"PK-not-a-real-docx"), ("지원서.txt", FORM_TEXT)]
    n, paths = make_notice(tmp_path, files, idx="9219")
    summ, repo, ai, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK)))
    assert rec(repo, "9219")["status_cd"] == "NEED_REVIEW"
    assert any("처리 실패" in p for p in json.loads(rec(repo, "9219")["uncertain_json"])["attachment_problems"])


# ================================================================== ZIP: 내부 파일별 분류
def test_zip_members_are_classified_individually(settings, tmp_path):
    members = {"2026 윤영문화재단 장학 선발 공고문.txt": GUIDE_TEXT, "장학금 지원서 양식.txt": FORM_TEXT, "붙임1.txt": UNKNOWN_TEXT,
               "합격자 명단.txt": "홍길동 합격", "구형문서.doc": "x", "내부.zip": "PK", "메모.xyz": "x"}
    n, paths = make_notice(tmp_path, [("20261008 윤영문화재단 장학(지원서).zip", zip_bytes(members))])
    summ, repo, ai, _ = run(settings, n, paths)
    text = ai.calls[0][0]
    assert GUIDE_TEXT in text and FORM_TEXT not in text and UNKNOWN_TEXT not in text and "홍길동" not in text
    seq = n.attachments[0].file_seq
    rows = {s: r for (c, i, s), r in repo.files.items()}
    parent = rows[seq]
    assert parent["status_cd"] == "DOWNLOADED" and parent["extract_method"] == "ZIP" and "내부 파일 7개" in parent["err_msg"]
    members_rows = {r["org_file_nm"].split("/", 1)[1]: r for s, r in rows.items() if s.startswith(seq + ".")}
    assert set(members_rows) == set(members) and all(s.startswith(seq + ".") for s in rows if s != seq)
    g = members_rows["2026 윤영문화재단 장학 선발 공고문.txt"]
    assert (g["role_cd"], g["status_cd"], g["extract_text"]) == ("GUIDE", "EXTRACTED", GUIDE_TEXT)
    assert members_rows["붙임1.txt"]["role_cd"] == "FORM"                                                   # 이름으로 분류가 안 되면 ZIP 이름('지원서')을 따름
    assert members_rows["붙임1.txt"]["extract_text"] == UNKNOWN_TEXT                                        # 텍스트는 저장됨
    assert members_rows["합격자 명단.txt"]["status_cd"] == "SKIPPED" and "제외 대상" in members_rows["합격자 명단.txt"]["err_msg"]
    assert "DOC" in members_rows["구형문서.doc"]["err_msg"].upper() and members_rows["구형문서.doc"]["status_cd"] == "SKIPPED"
    assert "중첩 ZIP" in members_rows["내부.zip"]["err_msg"] and members_rows["메모.xyz"]["status_cd"] == "SKIPPED"
    assert "첨부1: 20261008 윤영문화재단 장학(지원서).zip/2026 윤영문화재단 장학 선발 공고문.txt" in text         # evidence 에서 파일을 구분할 수 있음


def test_zip_with_guide_name_keeps_unknown_members_as_guide_candidates(settings, tmp_path):
    n, paths = make_notice(tmp_path, [("공고문 모음.zip", zip_bytes({"붙임1.txt": UNKNOWN_TEXT, "안내 양식.txt": FORM_TEXT}))])
    ai = run(settings, n, paths)[2]
    assert UNKNOWN_TEXT in ai.calls[0][0]                                                                   # 미분류는 요강 후보로 AI 에 전달


def mark_encrypted(data: bytes, name: str) -> bytes:
    """ZIP 안의 한 파일에 '암호화됨' 비트(일반 목적 플래그 bit0)를 켠다 (로컬 헤더 + 중앙 디렉터리)."""
    raw = bytearray(data)
    nb = name.encode("utf-8")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        off = z.getinfo(name).header_offset
    raw[off + 6] |= 0x01
    pos = raw.find(b"PK\x01\x02")
    while pos != -1:
        if bytes(raw[pos + 46:pos + 46 + len(nb)]) == nb:
            raw[pos + 8] |= 0x01
        pos = raw.find(b"PK\x01\x02", pos + 4)
    return bytes(raw)


def test_broken_zip_and_zip_member_errors_are_isolated(settings, tmp_path):
    n, paths = make_notice(tmp_path, [("자료.zip", b"PK this is not a zip"), ("공고문.txt", GUIDE_TEXT)])
    summ, repo, ai, _ = run(settings, n, paths)
    rows = {r["org_file_nm"]: r for (c, i, s), r in repo.files.items()}
    assert rows["자료.zip"]["status_cd"] == "FAILED" and "ZIP" in rows["자료.zip"]["err_msg"] and GUIDE_TEXT in ai.calls[0][0]
    # 암호가 걸린 파일: 그 파일만 실패하고 같은 ZIP 의 다른 공고문은 정상 처리
    enc = mark_encrypted(zip_bytes({"공고문.txt": GUIDE_TEXT, "비밀 공고.txt": "secret"}), "비밀 공고.txt")
    n2, paths2 = make_notice(tmp_path, [("공고 모음.zip", enc)], idx="9300")
    summ, repo, ai, _ = run(settings, n2, paths2)
    mem = {r["org_file_nm"].split("/", 1)[1]: r for (c, i, s), r in repo.files.items() if "/" in r["org_file_nm"]}
    assert mem["비밀 공고.txt"]["status_cd"] == "FAILED" and "암호" in mem["비밀 공고.txt"]["err_msg"]
    assert mem["공고문.txt"]["status_cd"] == "EXTRACTED" and GUIDE_TEXT in ai.calls[0][0] and "secret" not in ai.calls[0][0]
    assert summ.att_ok == 1 and summ.att_failed == 1 and rec(repo, "9300")["status_cd"] == "NEED_REVIEW"       # 요강 후보의 실패는 검수 사유


def test_zip_member_limit_and_empty_zip(settings, tmp_path):
    settings.max_zip_members = 3
    members = {"공고{}.txt".format(i): "내용{}".format(i) for i in range(5)}
    n, paths = make_notice(tmp_path, [("많음.zip", zip_bytes(members))])
    summ, repo, ai, _ = run(settings, n, paths)
    [row] = repo.files.values()
    assert row["status_cd"] == "FAILED" and "너무 많음" in row["err_msg"] and summ.att_failed == 1
    n, paths = make_notice(tmp_path, [("빈.zip", zip_bytes({}))], idx="9301")
    summ, repo, ai, _ = run(settings, n, paths)
    assert [r["status_cd"] for r in repo.files.values()] == ["DOWNLOADED"] and summ.att_ok == 0


# ================================================================== 코드 확정 값(hints) 과 AI 교차 검증
def test_hints_override_ai_and_flag_mismatch(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    ai_result = dict(AI_OK, apply_end="2026-10-25", apply_start="2026-09-04", apply_type="SCHOOL")           # AI 가 잘못 읽은 마감일
    summ, repo, ai, _ = run(settings, n, paths, ai=FakeAI(ai_result))
    r = rec(repo)
    assert r["apply_end"] == date(2026, 10, 31) and r["apply_start"] == date(2026, 10, 1)                    # 신청기간(코드) 값 사용
    review = json.loads(r["uncertain_json"])
    reasons = " | ".join(i["reason"] for i in review["issues"])
    assert "AI 가 읽은 마감일(2026-10-25)" in reasons and "AI 가 읽은 시작일(2026-09-04)" in reasons
    assert r["status_cd"] == "NEED_REVIEW" and summ.need_review == 1
    elig = json.loads(r["elig_json"])
    assert elig["apply_end"] == "2026-10-31" and elig["recruit_count"] == "5명" and elig["amount"] == "[장학금지급] 최대 100 만원"
    assert elig["apply_how"].startswith("온라인 접수") and elig["target_levels"] == ["UNIV"]


def test_matching_ai_result_is_extracted_and_apply_type_conflict_is_flagged(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK)))
    assert rec(repo)["status_cd"] == "EXTRACTED" and summ.extracted == 1
    assert json.loads(rec(repo)["uncertain_json"])["issues"] == []
    n, paths = make_notice(tmp_path, idx="9400")
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK, apply_type="SCHOOL")))                  # 접수방법 키워드는 DIRECT
    r = rec(repo, "9400")
    assert r["apply_type_cd"] == "SCHOOL" and r["status_cd"] == "NEED_REVIEW"                                # AI 값 유지 + 검수 표시
    assert any(i["field"] == "apply_type" for i in json.loads(r["uncertain_json"])["issues"])
    n, paths = make_notice(tmp_path, idx="9401")
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK, apply_type="UNKNOWN")))
    assert rec(repo, "9401")["apply_type_cd"] == "DIRECT"                                                    # AI 가 모르면 코드 값 사용


def test_period_warnings_become_review_reasons(settings, tmp_path):
    sec = dict(DEFAULT_SECTIONS, 신청기간=["2026. 10. 01. (화) ~ 2026. 10. 31. (토)"])                          # 10/1 은 목요일
    n, paths = make_notice(tmp_path, sections=sec)
    summ, repo, _, _ = run(settings, n, paths)
    assert rec(repo)["status_cd"] == "NEED_REVIEW"
    assert any("요일 불일치" in i["reason"] for i in json.loads(rec(repo)["uncertain_json"])["issues"])


def test_dday_is_a_first_save_fallback_only(settings, tmp_path):
    sec = dict(DEFAULT_SECTIONS, 신청기간=["상시 모집"])
    n, paths = make_notice(tmp_path, sections=sec, dday=5, info_period="상시")
    assert n.hints["apply_end"] is None and n.hints["dday_end"] == "2026-10-11"
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK, apply_end=None, apply_start=None)))
    r = rec(repo)
    assert r["apply_end"] == date(2026, 10, 11) and r["status_cd"] == "NEED_REVIEW"                          # 최초 저장: D-day 로 추정 + 검수 표시
    assert any("D-day" in i["reason"] for i in json.loads(r["uncertain_json"])["issues"])
    pipe, _ = make_pipe(settings, Ext02Fake([n], files=paths), FakeAI(dict(AI_OK, apply_end=None, apply_start=None)), repo=repo, force=True)
    pipe.run()                                                                                               # 이미 저장된 글을 다시 처리할 때는 D-day 를 쓰지 않음
    assert rec(repo)["apply_end"] is None
    assert any("확인할 수 없음" in i["reason"] for i in json.loads(rec(repo)["uncertain_json"])["issues"])
    n2, paths2 = make_notice(tmp_path, sections=sec, dday=5, info_period="상시", idx="9500")
    summ, repo2, _, _ = run(settings, n2, paths2, ai=FakeAI(dict(AI_OK, apply_end="2026-10-20", apply_start=None)))
    assert rec(repo2, "9500")["apply_end"] == date(2026, 10, 20)                                              # 첨부 등에서 AI 가 찾은 마감일이 있으면 유지
    assert any("D-day 추정" in i["reason"] for i in json.loads(rec(repo2, "9500")["uncertain_json"])["issues"])


# ================================================================== 저장되는 값
def test_source_fields_are_saved_separately_and_source_note_reaches_ai(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    summ, repo, ai, _ = run(settings, n, paths)
    f = json.loads(rec(repo)["src_fields_json"])
    assert f["period"]["end"] == "2026-10-31" and f["headcount"]["count"] == 5 and f["method"]["headline"] == "온라인 접수"
    assert f["eligibility"]["gpa"]["lines"] == ["직전학기 평점 3.0/4.5 이상"] and f["original_url"] == "https://example.org/notice/1"
    assert "src_fields_json" not in json.loads(rec(repo)["elig_json"]) and "period" not in json.loads(rec(repo)["elig_json"])   # AI 해석과 분리
    assert "[출처 안내]" in ai.calls[0][0] and "드림스폰이 공고문을 요약" in ai.calls[0][0]
    assert rec(repo)["origin_url"] == "https://example.org/notice/1" and rec(repo)["dept_nm"] == "테스트재단"
    n1 = SourceNotice(src_cd="EXT01", src_id="1", title="외부01", body_html="<p>" + "본문" * 100 + "</p>")
    pipe, repo1 = make_pipe(settings, FakeAdapter([n1]), FakeAI(GOOD))
    pipe.run()
    assert "src_fields_json" not in repo1.notices[("EXT01", "1")] and "[출처 안내]" not in pipe.ai.calls[0][0]


def test_no_ai_run_still_stores_source_fields(settings, tmp_path):
    n, paths = make_notice(tmp_path, [("공고문.txt", GUIDE_TEXT)])
    ad = Ext02Fake([n], files=paths)
    pipe, repo = make_pipe(settings, ad, None)
    summ = pipe.run()
    r = rec(repo)
    assert r["status_cd"] == "COLLECTED" and summ.collected_only == 1 and json.loads(r["src_fields_json"])["target"]["levels"] == ["UNIV"]
    assert next(iter(repo.files.values()))["extract_text"] == GUIDE_TEXT


# ================================================================== 변경 처리: 기간만 바뀐 경우 / 다른 내용이 바뀐 경우
def test_period_only_change_updates_dates_without_ai(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    summ, repo, ai, _ = run(settings, n, paths)
    first_hash = rec(repo)["content_hash"]
    assert len(ai.calls) == 1
    sec = dict(DEFAULT_SECTIONS, 신청기간=["2026. 10. 01. ~ 2026. 11. 14."])                                   # 마감 연장
    n2, paths2 = make_notice(tmp_path, sections=sec, info_period="2026. 10. 01. (목) ~ 2026. 11. 14. (토)")
    assert n2.hash_basis == n.hash_basis
    ai2 = FakeAI(AI_OK)
    pipe, _ = make_pipe(settings, Ext02Fake([n2], files=paths2), ai2, repo=repo)
    summ = pipe.run()
    assert summ.period_updated == 1 and summ.unchanged == 0 and ai2.calls == []                              # AI 호출 없음
    r = rec(repo)
    assert r["apply_end"] == date(2026, 11, 14) and r["content_hash"] == first_hash and r["status_cd"] == "EXTRACTED"
    assert json.loads(r["src_fields_json"])["period"]["end"] == "2026-11-14" and json.loads(r["elig_json"])["apply_end"] == "2026-11-14"
    assert "기간갱신 1" in summ.line()
    pipe3, _ = make_pipe(settings, Ext02Fake([n2], files=paths2), FakeAI(AI_OK), repo=repo)
    assert pipe3.run().unchanged == 1                                                                         # 같은 기간이면 변경 없음


def test_unparsable_new_period_is_ignored_and_other_changes_follow_default_rules(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    summ, repo, _, _ = run(settings, n, paths)
    n2, paths2 = make_notice(tmp_path, sections=dict(DEFAULT_SECTIONS, 신청기간=["추후 공지"]), info_period="추후 공지")
    pipe, _ = make_pipe(settings, Ext02Fake([n2], files=paths2), FakeAI(AI_OK), repo=repo)
    summ = pipe.run()
    assert summ.unchanged == 1 and summ.period_updated == 0 and rec(repo)["apply_end"] == date(2026, 10, 31)  # 해석 불가한 기간으로 덮어쓰지 않음
    changed = dict(DEFAULT_SECTIONS, 신청자격=["[기타조건]", "- 새로 추가된 조건"])
    n3, paths3 = make_notice(tmp_path, sections=changed)
    ai3 = FakeAI(AI_OK)
    pipe, _ = make_pipe(settings, Ext02Fake([n3], files=paths3), ai3, repo=repo)
    summ = pipe.run()
    assert summ.skipped_changed == 1 and ai3.calls == []                                                      # 기본: 내용 변경은 건너뜀 (--update-changed 로 재처리)
    settings.update_changed = True
    pipe, _ = make_pipe(settings, Ext02Fake([n3], files=paths3), ai3, repo=repo)
    pipe.run()
    assert len(ai3.calls) == 1


def test_old_record_without_period_info_is_not_period_updated(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    summ, repo, _, _ = run(settings, n, paths)
    repo.notices[("EXT02", "9218")]["src_fields_json"] = None                                                  # 이전 형식으로 저장된 글
    n2, paths2 = make_notice(tmp_path, sections=dict(DEFAULT_SECTIONS, 신청기간=["2026. 10. 01. ~ 2026. 12. 31."]))
    pipe, _ = make_pipe(settings, Ext02Fake([n2], files=paths2), FakeAI(AI_OK), repo=repo)
    assert pipe.run().unchanged == 1


# ================================================================== stub (상세 미조회)
def test_stub_notices(settings):
    repo = MemoryRepository()
    repo.upsert_notice("EXT02", "1", {"status_cd": "EXTRACTED", "use_yn": "N", "content_hash": "h", "title": "저장됨"})
    stubs = [SourceNotice(src_cd="EXT02", src_id="1", title="저장됨", stub=True),
             SourceNotice(src_cd="EXT02", src_id="2", title="상세 실패", stub=True)]
    ai = FakeAI(AI_OK)
    pipe, _ = make_pipe(settings, Ext02Fake(stubs), ai, repo=repo)
    summ = pipe.run()
    assert summ.unchanged == 1 and summ.skipped_stub == 1 and ai.calls == [] and ("EXT02", "2") not in repo.notices
    assert repo.notices[("EXT02", "1")]["use_yn"] == "Y"                                                       # 목록에 다시 나타나면 복구
    assert "상세미조회 1" in summ.line()


# ================================================================== 사라진 글 정리 (full_scope)
def test_deactivation_uses_all_active_ids_and_respects_safety_flag(settings):
    def seeded():
        repo = MemoryRepository()
        for i, d in (("1", date(2026, 1, 1)), ("2", date(2026, 9, 20)), ("3", None)):
            repo.upsert_notice("EXT02", i, {"status_cd": "EXTRACTED", "use_yn": "Y", "posted_date": d, "content_hash": "h"})
        return repo
    seen = [SourceNotice(src_cd="EXT02", src_id="2", title="t", stub=True)]
    repo = seeded()
    pipe, _ = make_pipe(settings, Ext02Fake(seen), None, repo=repo)
    summ = pipe.run()
    assert summ.deactivated == 2 and {i for (c, i), r in repo.notices.items() if r["use_yn"] == "N"} == {"1", "3"}   # 게시일이 오래된 글도 포함
    repo = seeded()
    unsafe = Ext02Fake(seen)
    unsafe.deactivation_safe = False
    pipe, _ = make_pipe(settings, unsafe, None, repo=repo)
    assert pipe.run().deactivated == 0 and all(r["use_yn"] == "Y" for r in repo.notices.values())             # 목록이 비정상이면 정리하지 않음
    repo = seeded()
    pipe, _ = make_pipe(settings, Ext02Fake(seen), None, repo=repo)
    assert pipe.run(limit=1).deactivated == 0                                                                  # --limit/--only 에서는 정리하지 않음


def test_summary_line_contains_new_counters():
    from scholarship.common.pipeline import Summary
    line = Summary(period_updated=2, skipped_stub=1).line()
    assert "기간갱신 2" in line and "상세미조회 1" in line and "비활성화 0" in line


# ================================================================== 실제 글(9227, 9235)에서 나온 빈칸 표기가 AI 결과를 덮어쓰지 않는다
def test_placeholder_headcount_and_tag_only_benefit_do_not_override_ai(settings, tmp_path):
    sec = dict(DEFAULT_SECTIONS, 선발인원=["총 00명"], 장학혜택=["[기타혜택]", "- 어학 강좌 수강료, 숙소 지원"])
    n, paths = make_notice(tmp_path, sections=sec)
    assert n.hints["recruit_count"] is None and n.hints["amount"] is None
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK, recruit_count="약 30명", amount="어학 강좌 수강료와 숙소 지원")))
    elig = json.loads(rec(repo)["elig_json"])
    assert elig["recruit_count"] == "약 30명" and elig["amount"] == "어학 강좌 수강료와 숙소 지원"          # AI 가 읽은 값을 유지
    n, paths = make_notice(tmp_path, sections=sec, idx="9600")
    summ, repo, _, _ = run(settings, n, paths, ai=FakeAI(dict(AI_OK)))
    elig = json.loads(rec(repo, "9600")["elig_json"])
    assert elig["recruit_count"] is None and elig["amount"] is None                                          # 없으면 빈 값 (문구를 억지로 넣지 않음)


# ================================================================== 수집 단계(COLLECTED)에서 신청기간을 날짜 컬럼에 반영
def test_collected_stage_stores_code_confirmed_period_without_ai(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    pipe, repo = make_pipe(settings, Ext02Fake([n], files=paths), None)                                      # --no-ai
    pipe.run()
    r = rec(repo)
    assert r["status_cd"] == "COLLECTED" and r["apply_start"] == date(2026, 10, 1) and r["apply_end"] == date(2026, 10, 31)
    assert json.loads(r["src_fields_json"])["period"]["end"] == "2026-10-31"                                 # SRC_FIELDS_JSON 과 같은 값


def test_collected_stage_does_not_guess_dates(settings, tmp_path):
    n, paths = make_notice(tmp_path, sections=dict(DEFAULT_SECTIONS, 신청기간=["상시 모집"]), info_period="상시", dday=5)
    pipe, repo = make_pipe(settings, Ext02Fake([n], files=paths), None)
    pipe.run()
    r = rec(repo)
    assert r.get("apply_end") is None and r.get("apply_start") is None                                       # 읽을 수 없으면 비워 둠 (D-day 추정은 AI 단계에서 검수 표시와 함께)
    repo.notices[("EXT02", "9218")]["apply_end"] = date(2026, 12, 1)                                          # 이미 값이 있으면 None 으로 덮어쓰지 않음
    pipe2, _ = make_pipe(settings, Ext02Fake([n], files=paths), None, repo=repo)
    pipe2.run()
    assert rec(repo)["apply_end"] == date(2026, 12, 1)


def test_ai_stage_still_applies_period_and_ext01_has_no_dates_at_collection(settings, tmp_path):
    n, paths = make_notice(tmp_path)
    pipe, repo = make_pipe(settings, Ext02Fake([n], files=paths), None)
    pipe.run()
    pipe2, _ = make_pipe(settings, Ext02Fake([n], files=paths), FakeAI(dict(AI_OK, apply_end="2026-10-25")), repo=repo)
    pipe2.run()                                                                                              # COLLECTED -> AI 단계로 이어서 처리
    r = rec(repo)
    assert r["status_cd"] == "NEED_REVIEW" and r["apply_end"] == date(2026, 10, 31)                          # AI 가 다르게 읽어도 신청기간 값
    n1 = SourceNotice(src_cd="EXT01", src_id="1", title="외부01", body_html="<p>" + "본문" * 100 + "</p>")
    pipe3, repo1 = make_pipe(settings, FakeAdapter([n1]), None)
    pipe3.run()
    assert "apply_end" not in repo1.notices[("EXT01", "1")] and "apply_start" not in repo1.notices[("EXT01", "1")]


def test_log_line_for_full_scope_sources_has_no_day_window(settings, tmp_path, caplog):
    import logging
    n, paths = make_notice(tmp_path)
    with caplog.at_level(logging.INFO):
        make_pipe(settings, Ext02Fake([n], files=paths), None)[0].run()
        make_pipe(settings, FakeAdapter([]), None)[0].run()
    assert "[EXT02] 공고 1건 조회" in caplog.text and "최근 30일" in caplog.text and "[EXT01] 최근 30일 공고 0건 조회" in caplog.text
    assert "[EXT02] 최근" not in caplog.text


# ================================================================== 공고문 첨부 여부는 코드가 기록 (AI 검수 소음 방지)
def test_attachment_check_is_recorded_by_code(settings, tmp_path):
    n, paths = make_notice(tmp_path, [("2026 장학 공고문.txt", GUIDE_TEXT), ("지원서.txt", FORM_TEXT)])
    summ, repo, _, _ = run(settings, n, paths)
    chk = json.loads(rec(repo)["src_fields_json"])["attachment_check"]
    assert chk == {"guide_attached": True, "extracted_by_role": {"GUIDE": 1, "FORM": 1}, "total": 2}
    n, paths = make_notice(tmp_path, [("지원서.txt", FORM_TEXT)], idx="9700")                                  # 양식만 있는 글 (예: 9235)
    summ, repo, _, _ = run(settings, n, paths)
    chk = json.loads(rec(repo, "9700")["src_fields_json"])["attachment_check"]
    assert chk["guide_attached"] is False and chk["extracted_by_role"] == {"FORM": 1}
    n, paths = make_notice(tmp_path, idx="9701")                                                                # 첨부가 아예 없는 글
    summ, repo, _, _ = run(settings, n, paths)
    assert json.loads(rec(repo, "9701")["src_fields_json"])["attachment_check"] == {"guide_attached": False, "extracted_by_role": {}, "total": 0}


def test_attachment_check_also_saved_without_ai_and_survives_period_update(settings, tmp_path):
    n, paths = make_notice(tmp_path, [("공고문.txt", GUIDE_TEXT)])
    pipe, repo = make_pipe(settings, Ext02Fake([n], files=paths), None)
    pipe.run()
    assert json.loads(rec(repo)["src_fields_json"])["attachment_check"]["guide_attached"] is True
    pipe, repo = make_pipe(settings, Ext02Fake([n], files=paths), FakeAI(AI_OK), repo=repo)
    pipe.run()                                                                                                  # AI 단계로 완료
    sec = dict(DEFAULT_SECTIONS, 신청기간=["2026. 10. 01. ~ 2026. 12. 15."])
    n2, paths2 = make_notice(tmp_path, [("공고문.txt", GUIDE_TEXT)], sections=sec, info_period="2026. 10. 01. (목) ~ 2026. 12. 15. (화)")
    assert n2.hash_basis == n.hash_basis
    pipe2, _ = make_pipe(settings, Ext02Fake([n2], files=paths2), FakeAI(AI_OK), repo=repo)
    assert pipe2.run().period_updated == 1
    f = json.loads(rec(repo)["src_fields_json"])
    assert f["period"]["end"] == "2026-12-15" and f["attachment_check"]["guide_attached"] is True               # 기간 갱신이 첨부 기록을 지우지 않음


def test_source_note_tells_ai_not_to_flag_summary_only_or_missing_documents():
    from scholarship.sources.ext02 import SOURCE_NOTE
    for needle in ("요약본이라는 사실", "요강 첨부가 없다는 사실", "'공고문 안내 확인'으로만", "미정/미기재", "코드가 따로 기록"):
        assert needle in SOURCE_NOTE
    assert "충돌하면 첨부를 우선" in SOURCE_NOTE and "level=verify" in SOURCE_NOTE                              # 기존 규칙은 유지
