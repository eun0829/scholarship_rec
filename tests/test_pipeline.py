# -*- coding: utf-8 -*-
"""공통 파이프라인 통합 테스트 (가짜 어댑터 + 가짜 AI + 메모리 저장소). 외부01 실제 DB/AI 없이 전체 흐름을 검증한다."""
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from scholarship.common.ai.client import AIClient, AIError, AIResponse
from scholarship.common.extractors.results import ImagePart
from scholarship.common.models import SourceAttachment, SourceNotice
from scholarship.common.pipeline import Pipeline
from scholarship.common.repository import MemoryRepository
from scholarship.sources.base import SourceAdapter
from tests.test_extractors import make_docx, _png

TODAY = date(2026, 10, 6)
IN_WINDOW = date(2026, 9, 20)          # 최근 30일 안에 게시된 날짜

GOOD = {"category": "OUT", "scholarship_name": "인송장학생", "foundation": "인송문화재단", "apply_type": "SCHOOL",
        "apply_start": "2026-09-04", "apply_end": "2026-10-25", "org_deadline": "2026-11-06",
        "target_levels": ["UNIV"], "gpa": {"min": 80, "scale": 100, "basis": "EITHER"},
        "credits": {"min_last_semester": 12}, "income": {"max_bracket": 8},
        "summary": "인송문화재단 인송장학생 추천 모집 (3명, 200만원)"}


class FakeAdapter(SourceAdapter):
    src_cd = "EXT01"

    def __init__(self, notices, files=None, images=None):
        self.notices, self.files, self.images = notices, files or {}, images or {}
        self.downloads, self.image_downloads = [], []

    def fetch_notices(self, days):
        return list(self.notices)

    def _copy(self, src, dest):
        if isinstance(src, Exception):
            raise src
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return dest.stat().st_size

    def download_attachment(self, att, dest, max_bytes):
        self.downloads.append(att.file_seq)
        return self._copy(self.files[att.file_seq], dest)

    def download_body_image(self, src, dest, max_bytes):
        self.image_downloads.append(src)
        return self._copy(self.images[src], dest)


class FakeAI(AIClient):
    def __init__(self, *responses, max_tokens=4000):
        self.responses, self.calls, self.max_tokens_seen, self.max_tokens = list(responses), [], [], max_tokens

    def complete(self, system, user_text, images, max_tokens=None):
        self.calls.append((user_text, images))
        self.max_tokens_seen.append(max_tokens)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, AIResponse):
            return r
        return AIResponse(text=r if isinstance(r, str) else json.dumps(r, ensure_ascii=False),
                          model="fake-model", input_tokens=100, output_tokens=50)


def notice(fixture_dir, kind, sid="1001", atts=(), title="테스트 공고", posted=date(2026, 9, 4)):
    html = (fixture_dir / ("clob_text_type.html" if kind == "text" else "clob_image_only.html")).read_text(encoding="utf-8")
    return SourceNotice("EXT01", sid, title, "학생지원팀", posted, "https://x/{}".format(sid), html, list(atts))


def att(seq, name, sort=1):
    return SourceAttachment(seq, sort, name, "https://x/{}".format(seq))


def make_pipe(settings, adapter, ai, repo=None, **kw):
    repo = repo or MemoryRepository()
    return Pipeline(settings, adapter, repo, ai, today=TODAY, **kw), repo


# ------------------------------------------------------------------ 1. 텍스트형 + 양식 첨부
def test_text_notice_with_form_attachment(settings, fixture_dir, tmp_path):
    form = tmp_path / "form.docx"
    make_docx(form)
    n = notice(fixture_dir, "text", atts=[att("9001", "지원서 양식.docx")])
    ad = FakeAdapter([n], files={"9001": form})
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, ad, ai)
    s = pipe.run(days=30)

    rec = repo.notices[("EXT01", "1001")]
    assert rec["status_cd"] == "EXTRACTED" and rec["category_cd"] == "OUT" and rec["apply_type_cd"] == "SCHOOL"
    assert rec["apply_end"] == date(2026, 10, 25) and rec["org_deadline"] == date(2026, 11, 6)
    elig = json.loads(rec["elig_json"])
    assert elig["gpa"] == {"min": 80.0, "scale": 100.0, "basis": "EITHER", "alternatives": [], "notes": None}
    assert "evidence" not in elig and "uncertain" not in elig
    assert rec["extract_model"] == "fake-model" and rec["posted_date"] == date(2026, 9, 4)
    f = repo.files[("EXT01", "1001", "9001")]
    assert f["role_cd"] == "FORM" and f["status_cd"] == "EXTRACTED" and f["text_len"] > 0
    assert Path(f["nas_path"]).exists() and Path(f["nas_path"]).parts[-3:] == ("ext01", "1001", "9001.docx")
    prompt, images = ai.calls[0]
    assert "[참고 첨부(양식/보조)" in prompt and "소득분위 8분위 이내" in prompt and images == []   # 본문 충분 -> 이미지 없음
    assert s.extracted == 1 and s.input_tokens == 100


# ------------------------------------------------------------------ 2. 이미지형 + 요강 첨부 (HWP 실샘플)
def test_image_notice_uses_guide_attachment_not_poster(settings, fixture_dir, sample_hwps):
    guide = next(f for f in sample_hwps if "인천" in f.name)
    n = notice(fixture_dir, "image", sid="2002", atts=[att("9100", "붙임2-1 인천인희망드림 장학생 공고문.hwp")])
    ad = FakeAdapter([n], files={"9100": guide}, images={})
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, ad, ai)
    pipe.run()
    prompt, images = ai.calls[0]
    assert "4.5 만점 기준 2.8 이상" in prompt                   # 표 안의 조건이 AI 입력에 포함
    assert images == [] and ad.image_downloads == []             # 텍스트가 충분하면 포스터를 내려받지 않음 (토큰/시간 절약)
    assert repo.files[("EXT01", "2002", "9100")]["role_cd"] == "GUIDE"


# ------------------------------------------------------------------ 3. 첨부 실패 -> 포스터 이미지로 대체
def test_attachment_failure_falls_back_to_poster(settings, fixture_dir, tmp_path):
    poster = tmp_path / "p.png"
    _png(poster, 1300, 1839)
    src = "/CrossEditor/binary/images/000342/포스터.jpg"
    n = notice(fixture_dir, "image", sid="3003", atts=[att("9200", "공고문.hwp")])
    ad = FakeAdapter([n], files={"9200": RuntimeError("HTTP 500")}, images={src: poster})
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, ad, ai)
    pipe.run()
    prompt, images = ai.calls[0]
    assert len(images) == 1 and "이미지 1장이 함께 전달됨" in prompt and ad.image_downloads == [src]
    rec = repo.notices[("EXT01", "3003")]
    assert rec["status_cd"] == "NEED_REVIEW"                     # 첨부 실패는 사람이 확인하도록 표시
    review = json.loads(rec["uncertain_json"])
    assert "첨부1" in review["attachment_problems"][0]
    assert repo.files[("EXT01", "3003", "9200")]["status_cd"] == "FAILED"


# ------------------------------------------------------------------ 4. 제외/미지원 첨부는 내려받지 않음
def test_excluded_and_unsupported_not_downloaded(settings, fixture_dir):
    n = notice(fixture_dir, "text", sid="4004", atts=[att("1", "합격자 명단.xlsx", 1), att("2", "안내.doc", 2)])
    ad = FakeAdapter([n])
    pipe, repo = make_pipe(settings, ad, FakeAI(GOOD))
    pipe.run()
    assert ad.downloads == []
    assert repo.files[("EXT01", "4004", "1")]["status_cd"] == "SKIPPED"
    assert "DOC" in repo.files[("EXT01", "4004", "2")]["err_msg"]


# ------------------------------------------------------------------ 5. 변경 없으면 AI 재호출 없음 / 변경되면 재처리
def test_unchanged_is_skipped_and_changed_is_reprocessed_only_when_asked(settings, fixture_dir):
    n = notice(fixture_dir, "text")
    ad = FakeAdapter([n])
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, ad, ai)
    pipe.run()
    assert len(ai.calls) == 1
    s2 = pipe.run()
    assert len(ai.calls) == 1 and s2.unchanged == 1
    n.body_html += "<p>기간 연장 안내</p>"
    s3 = pipe.run()                                                          # 기본: 글번호가 이미 있으면 내용이 바뀌어도 건너뜀
    assert len(ai.calls) == 1 and s3.skipped_changed == 1 and s3.unchanged == 0
    settings.update_changed = True                                           # --update-changed
    pipe.run()
    assert len(ai.calls) == 2
    Pipeline(settings, ad, repo, ai, force=True, today=TODAY).run()          # --force
    assert len(ai.calls) == 3


# ------------------------------------------------------------------ 6. 원본에서 사라진 글 비활성화 / 다시 나타나면 복구
def test_missing_notice_deactivated_and_restored(settings, fixture_dir):
    a, b = notice(fixture_dir, "text", "1", posted=IN_WINDOW, title="공고 1"), notice(fixture_dir, "text", "2", posted=IN_WINDOW, title="공고 2")
    ad = FakeAdapter([a, b])
    pipe, repo = make_pipe(settings, ad, FakeAI(GOOD))
    pipe.run()
    ad.notices = [a]
    s = pipe.run()
    assert repo.notices[("EXT01", "2")]["use_yn"] == "N" and s.deactivated == 1
    ad.notices = [a, b]
    pipe.run()
    assert repo.notices[("EXT01", "2")]["use_yn"] == "Y"


def test_limit_or_only_never_deactivates(settings, fixture_dir):
    a, b = notice(fixture_dir, "text", "1", posted=IN_WINDOW, title="공고 1"), notice(fixture_dir, "text", "2", posted=IN_WINDOW, title="공고 2")
    ad = FakeAdapter([a, b])
    pipe, repo = make_pipe(settings, ad, FakeAI(GOOD))
    pipe.run()
    ad.notices = [a]                                   # b 가 원본에서 사라진 상황
    pipe.run(only=["1"])
    pipe.run(limit=1)
    assert repo.notices[("EXT01", "2")]["use_yn"] == "Y"


# ------------------------------------------------------------------ 7. AI 응답 처리
def test_ai_fenced_json_ok_and_garbage_retries_then_fails(settings, fixture_dir):
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]),
                           FakeAI("```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"))
    pipe.run()
    assert repo.notices[("EXT01", "1001")]["status_cd"] == "EXTRACTED"

    ai = FakeAI("죄송합니다. JSON 이 아닙니다")
    pipe2, repo2 = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), ai)
    s = pipe2.run()
    assert len(ai.calls) == 2 and s.failed == 1                  # 1회 재시도 후 실패
    assert repo2.notices[("EXT01", "1001")]["status_cd"] == "FAILED"
    assert "JSON" in json.loads(repo2.notices[("EXT01", "1001")]["uncertain_json"])["error"]
    assert "JSON 객체 하나만" in ai.calls[1][0]                   # 재시도 프롬프트에 경고 추가


def test_ai_error_and_unexpected_exception_do_not_stop_batch(settings, fixture_dir):
    a, b = notice(fixture_dir, "text", "1"), notice(fixture_dir, "text", "2", title="두번째")
    ai = FakeAI(AIError("HTTP 529"), GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([a, b]), ai)
    s = pipe.run()
    assert repo.notices[("EXT01", "1")]["status_cd"] == "FAILED" and repo.notices[("EXT01", "2")]["status_cd"] == "EXTRACTED"
    assert s.failed == 1 and s.extracted == 1

    pipe2, repo2 = make_pipe(settings, FakeAdapter([a, b]), FakeAI(RuntimeError("boom"), GOOD))
    s2 = pipe2.run()
    assert repo2.notices[("EXT01", "1")]["status_cd"] == "FAILED" and repo2.notices[("EXT01", "2")]["status_cd"] == "EXTRACTED"


# ------------------------------------------------------------------ 8. 코드 검증이 확인필요로 표시
def test_code_issues_mark_need_review(settings, fixture_dir):
    bad = dict(GOOD, apply_end="2026-01-01", gpa={"min": 3.0})        # 마감이 게시일보다 과거 + 만점 단위 없음
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(bad))
    s = pipe.run()
    rec = repo.notices[("EXT01", "1001")]
    fields = {i["field"] for i in json.loads(rec["uncertain_json"])["issues"]}
    assert rec["status_cd"] == "NEED_REVIEW" and {"apply_end", "gpa.scale"} <= fields and s.need_review == 1


# ------------------------------------------------------------------ 9. --no-ai 단계 실행
def test_no_ai_collects_then_ai_run_processes(settings, fixture_dir, tmp_path):
    form = tmp_path / "f.docx"
    make_docx(form)
    n = notice(fixture_dir, "text", atts=[att("9001", "공고문.docx")])
    ad = FakeAdapter([n], files={"9001": form})
    repo = MemoryRepository()
    Pipeline(settings, ad, repo, None, today=TODAY).run()
    assert repo.notices[("EXT01", "1001")]["status_cd"] == "COLLECTED" and ("EXT01", "1001", "9001") in repo.files
    ai = FakeAI(GOOD)
    Pipeline(settings, ad, repo, ai, today=TODAY).run()               # COLLECTED 는 '변경 없음'으로 건너뛰지 않음
    assert len(ai.calls) == 1 and repo.notices[("EXT01", "1001")]["status_cd"] == "EXTRACTED"
    assert ad.downloads == ["9001"]                                    # 이미 내려받은 파일은 재다운로드 안 함


# ------------------------------------------------------------------ 10. 근거가 전혀 없으면 AI 호출 안 함
def test_no_evidence_skips_ai(settings):
    n = SourceNotice("EXT01", "5005", "빈 공고", "", date(2026, 9, 4), "", "<p> </p>", [])
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([n]), ai)
    s = pipe.run()
    assert ai.calls == [] and repo.notices[("EXT01", "5005")]["status_cd"] == "FAILED" and s.failed == 1


# ------------------------------------------------------------------ 11. 점검용 덤프
def test_dump_files(settings, fixture_dir, tmp_path):
    pipe, _ = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(GOOD), dump_dir=tmp_path / "dump")
    pipe.run()
    assert (tmp_path / "dump" / "ext01_1001_ai_input.txt").exists() and (tmp_path / "dump" / "ext01_1001_ai_result.json").exists()


# ------------------------------------------------------------------ 12. 첨부 결과 로그/집계, AI 없이도 텍스트 덤프
def test_attachment_log_summary_and_text_dump_without_ai(settings, fixture_dir, tmp_path, caplog):
    import logging
    good = tmp_path / "g.docx"
    make_docx(good)
    n = notice(fixture_dir, "text", atts=[att("1", "공고문.docx", 1), att("2", "합격자 명단.xlsx", 2),
                                          att("3", "안내.hwp", 3), att("4", "신청서.doc", 4)])
    ad = FakeAdapter([n], files={"1": good, "3": RuntimeError("HTTP 404")})
    pipe, repo = make_pipe(settings, ad, None, dump_dir=tmp_path / "dump")
    with caplog.at_level(logging.INFO):
        s = pipe.run()
    assert (s.att_ok, s.att_failed, s.att_skipped) == (1, 1, 2)
    assert "첨부 성공 1 / 실패 1 / 건너뜀 2" in s.line()
    text = caplog.text
    assert "첨부1 공고문.docx | GUIDE | EXTRACTED | TEXT" in text            # 방식과 글자 수 표시
    assert "첨부3 안내.hwp" in text and "FAILED" in text and "다운로드 실패" in text
    assert "첨부2 합격자 명단.xlsx" in text and "SKIPPED" in text and "제외 대상" in text
    d = tmp_path / "dump"
    assert (d / "ext01_1001_body.txt").read_text(encoding="utf-8").count("소득분위") >= 1
    att1 = (d / "ext01_1001_att1_guide.txt").read_text(encoding="utf-8")
    assert "# 첨부1: 공고문.docx" in att1 and "장학생 모집 공고" in att1
    assert not (d / "ext01_1001_ai_input.txt").exists()                      # AI 입력은 AI 실행 시에만


# ------------------------------------------------------------------ 13. 부분 추출(HWP 미리보기만)은 확인필요 + AI 에 알림
def test_partial_attachment_marks_review_and_tells_ai(settings, fixture_dir, tmp_path, monkeypatch, caplog):
    import logging
    from scholarship.common.extractors import ExtractResult, hwp
    dummy = tmp_path / "d.hwp"
    dummy.write_bytes(b"x")
    monkeypatch.setattr(hwp, "extract", lambda path, settings=None, **k: ExtractResult(
        text="성취장학생 선발계획 공고 ... (앞부분 미리보기)" * 5, method="TEXT", partial=True,
        warnings=["HWP 변환 실패 (배포용 문서) -> 미리보기 텍스트만 추출됨"]))
    n = notice(fixture_dir, "text", atts=[att("7", "성취 장학생 선발계획 공고.hwp")])
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([n], files={"7": dummy}), ai)
    with caplog.at_level(logging.INFO):
        pipe.run()
    rec = repo.notices[("EXT01", "1001")]
    assert rec["status_cd"] == "NEED_REVIEW"                           # 일부만 추출 -> 사람이 확인
    assert "일부 내용만 추출됨" in json.loads(rec["uncertain_json"])["attachment_problems"][0]
    assert "일부만 추출됨: 원본 변환 실패" in ai.calls[0][0]               # AI 입력 머리말에 안내
    assert "(일부만)" in caplog.text
    f = repo.files[("EXT01", "1001", "7")]
    assert f["status_cd"] == "EXTRACTED" and "미리보기" in f["err_msg"]


# ------------------------------------------------------------------ 14. 참고(양식/보조) 문서는 더 짧게, 요강은 길게
def test_secondary_docs_capped_shorter_than_guides():
    from scholarship.common.ai.prompt import Section, build_payload
    long_text = "Ω" * 30000                                               # 프롬프트 문구에 없는 문자로 개수 확인
    p = build_payload(TODAY, "제목", date(2026, 9, 4), "팀", "본문",
                      [Section("첨부1: 요강.hwp", "요강", long_text)],
                      [Section("첨부2: 양식.hwp", "양식", long_text), Section("첨부3: faq.pdf", "보조", "Ψ" * 1000)],
                      [], max_chars_per_file=20000, max_total_chars=60000, max_chars_secondary=4000)
    assert p.user_text.count("Ω") == 20000 + 4000                       # 요강 20000 + 양식 4000
    assert "길어서 앞 20000자만" in p.user_text and "길어서 앞 4000자만" in p.user_text
    assert p.user_text.count("Ψ") == 1000                                # 짧은 보조 문서는 그대로


def test_char_limits_from_env(monkeypatch):
    from scholarship.common.config import Settings
    monkeypatch.setenv("MAX_CHARS_SECONDARY", "1500")
    monkeypatch.setenv("MAX_CHARS_PER_FILE", "abc")                      # 잘못된 값은 기본값
    s = Settings.from_env()
    assert s.max_chars_secondary == 1500 and s.max_chars_per_file == 20000


# ------------------------------------------------------------------ 15. AI_VISION=false (게이트웨이가 이미지를 못 받는 경우)
def test_vision_disabled_sends_no_images_and_flags_review(settings, fixture_dir, tmp_path):
    settings.ai_vision = False
    poster = tmp_path / "p.png"
    _png(poster, 1300, 1839)
    src = "/CrossEditor/binary/images/000342/포스터.jpg"
    n = notice(fixture_dir, "image", sid="6006")
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([n], images={src: poster}), ai)
    pipe.run()
    prompt, images = ai.calls[0]
    assert images == [] and "이미지" not in prompt.split("[본문]")[0]
    rec = repo.notices[("EXT01", "6006")]
    assert rec["status_cd"] == "NEED_REVIEW"
    assert "AI_VISION=false" in json.loads(rec["uncertain_json"])["attachment_problems"][0]


def test_vision_flag_from_env(monkeypatch):
    from scholarship.common.config import Settings
    for v, exp in (("false", False), ("0", False), ("No", False), ("true", True), ("", True)):
        monkeypatch.setenv("AI_VISION", v)
        assert Settings.from_env().ai_vision is exp


# ------------------------------------------------------------------ 16. 이미지 합계 용량 상한 (게이트웨이 요청 25MB 제한 대비)
def test_image_total_bytes_budget(settings, fixture_dir, tmp_path):
    from scholarship.common.extractors import ExtractResult, image_
    import scholarship.common.extractors.pdf_ as pdf_mod
    settings.max_image_bytes_total = 1000
    big = [ImagePart("image/png", b"x" * 600, "p{}".format(i)) for i in range(3)]
    dummy = tmp_path / "s.pdf"
    dummy.write_bytes(b"%PDF-")
    import unittest.mock as mock
    n = notice(fixture_dir, "image", sid="7007", atts=[att("1", "공고문.pdf")])
    ai = FakeAI(GOOD)
    with mock.patch.object(pdf_mod, "extract", lambda path, s, label="", **k: ExtractResult(text="", method="VISION", images=big)):
        pipe, repo = make_pipe(settings, FakeAdapter([n], files={"1": dummy}), ai)
        pipe.run()
    assert len(ai.calls[0][1]) == 1                                         # 600 + 600 > 1000 -> 1장만
    assert "이미지 용량 한도" in json.loads(repo.notices[("EXT01", "7007")]["uncertain_json"])["attachment_problems"][0]
    assert repo.notices[("EXT01", "7007")]["status_cd"] == "NEED_REVIEW"


def test_new_settings_from_env(monkeypatch):
    from scholarship.common.config import Settings
    monkeypatch.setenv("AI_ANTHROPIC_BETA", "prompt-caching-2024-07-31")
    assert Settings.from_env().ai_anthropic_beta == "prompt-caching-2024-07-31"
    assert Settings().max_image_bytes_total == 15 * 1024 * 1024


# ------------------------------------------------------------------ 17. 참고(note) 수준의 불확실 항목만 있으면 '추출완료', verify 가 있으면 '확인필요'
def test_only_verify_level_uncertainty_triggers_review(settings, fixture_dir):
    notes_only = dict(GOOD, uncertain=[{"field": "apply_type", "reason": "직접 신청으로 분류", "level": "note"}])
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(notes_only))
    s = pipe.run()
    assert repo.notices[("EXT01", "1001")]["status_cd"] == "EXTRACTED" and s.extracted == 1
    stored = json.loads(repo.notices[("EXT01", "1001")]["uncertain_json"])
    assert stored["uncertain"][0]["level"] == "note"                       # 참고 메모는 보관만 함

    verify = dict(GOOD, uncertain=[{"field": "gpa", "reason": "표가 깨짐", "level": "verify"}])
    pipe2, repo2 = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(verify))
    s2 = pipe2.run()
    assert repo2.notices[("EXT01", "1001")]["status_cd"] == "NEED_REVIEW" and s2.need_review == 1
    elig = json.loads(repo2.notices[("EXT01", "1001")]["elig_json"])
    assert "other_conditions" in elig and "uncertain" not in elig


# ------------------------------------------------------------------ 18. 기존 글번호/제목 건너뛰기
def test_incomplete_existing_notice_is_continued_not_skipped(settings, fixture_dir):
    n = notice(fixture_dir, "text")
    ad = FakeAdapter([n])
    repo = MemoryRepository()
    Pipeline(settings, ad, repo, None, today=TODAY).run()                     # --no-ai: COLLECTED 로 저장
    ai = FakeAI(GOOD)
    s = Pipeline(settings, ad, repo, ai, today=TODAY).run()                   # 미완료 글은 이어서 AI 처리
    assert len(ai.calls) == 1 and s.extracted == 1 and s.skipped_changed == 0
    repo.notices[("EXT01", "1001")]["status_cd"] = "FAILED"                   # 실패한 글도 재시도
    Pipeline(settings, ad, repo, ai, today=TODAY).run()
    assert len(ai.calls) == 2


def test_same_title_different_id_is_skipped(settings, fixture_dir):
    a = notice(fixture_dir, "text", "1", title="2026년 하나장학생 선발 안내")
    b = notice(fixture_dir, "text", "2", title="  2026년   하나장학생 선발 안내 ")          # 공백만 다름
    c = notice(fixture_dir, "text", "3", title="2026년 하나장학생 선발 안내 &amp; 추가")      # 다른 제목
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([a, b, c]), ai)
    s = pipe.run()
    assert ("EXT01", "1") in repo.notices and ("EXT01", "2") not in repo.notices and ("EXT01", "3") in repo.notices
    assert s.skipped_title == 1 and len(ai.calls) == 2                       # 중복은 AI 도 호출하지 않음
    assert repo.notices[("EXT01", "1")]["title"] == "2026년 하나장학생 선발 안내"     # 저장되는 제목도 공백 정리


def test_title_skip_ignores_inactive_rows_and_respects_switches(settings, fixture_dir):
    a = notice(fixture_dir, "text", "1", title="같은 제목")
    pipe, repo = make_pipe(settings, FakeAdapter([a]), FakeAI(GOOD))
    pipe.run()
    b = notice(fixture_dir, "text", "2", title="같은 제목")
    repo.notices[("EXT01", "1")]["use_yn"] = "N"                               # 원본에서 삭제된 글 (이번 조회에는 없음) -> 비교 대상 아님
    s = Pipeline(settings, FakeAdapter([b]), repo, FakeAI(GOOD), today=TODAY).run()
    assert ("EXT01", "2") in repo.notices and s.skipped_title == 0

    repo2 = MemoryRepository()
    Pipeline(settings, FakeAdapter([a]), repo2, FakeAI(GOOD), today=TODAY).run()
    settings.skip_same_title = False                                           # --no-skip-title
    Pipeline(settings, FakeAdapter([a, b]), repo2, FakeAI(GOOD), today=TODAY).run()
    assert ("EXT01", "2") in repo2.notices
    repo3 = MemoryRepository()
    settings.skip_same_title = True
    Pipeline(settings, FakeAdapter([a]), repo3, FakeAI(GOOD), today=TODAY).run()
    Pipeline(settings, FakeAdapter([a, b]), repo3, FakeAI(GOOD), force=True, today=TODAY).run()   # --force 는 건너뛰기 무시
    assert ("EXT01", "2") in repo3.notices


def test_skipped_notices_are_not_deactivated_and_summary_counts(settings, fixture_dir):
    a = notice(fixture_dir, "text", "1", posted=IN_WINDOW, title="A")
    b = notice(fixture_dir, "text", "2", posted=IN_WINDOW, title="A")          # 제목 중복 -> 저장 안 됨
    ad = FakeAdapter([a, b])
    pipe, repo = make_pipe(settings, ad, FakeAI(GOOD))
    s = pipe.run()
    assert s.skipped_title == 1 and ("EXT01", "2") not in repo.notices
    assert "건너뜀(제목중복) 1" in s.line() and "건너뜀(내용변경) 0" in s.line()
    assert repo.notices[("EXT01", "1")]["use_yn"] == "Y"


def test_skip_options_from_env_and_cli(monkeypatch):
    from scholarship.common.config import Settings
    assert Settings().skip_same_title is True and Settings().update_changed is False
    monkeypatch.setenv("SKIP_SAME_TITLE", "false")
    monkeypatch.setenv("UPDATE_CHANGED", "yes")
    s = Settings.from_env()
    assert s.skip_same_title is False and s.update_changed is True


# ------------------------------------------------------------------ 19. 저장되는 ELIG_JSON: variants / apply_how / schema_version
def test_elig_json_stores_variants_and_schema_version(settings, fixture_dir):
    data = dict(GOOD, apply_how="구글폼 + 우편 접수", variants=[
        {"name": "성적우수", "gpa": {"min": 4.0, "scale": 4.5}},
        {"name": "저소득", "income": {"eligible_types": ["기초생활수급자"]}}])
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(data))
    pipe.run()
    rec = repo.notices[("EXT01", "1001")]
    elig = json.loads(rec["elig_json"])
    assert elig["schema_version"] == 2 and elig["apply_how"] == "구글폼 + 우편 접수"
    assert [v["name"] for v in elig["variants"]] == ["성적우수", "저소득"]
    assert elig["variants"][1]["gpa"]["min"] is None


def test_single_variant_issue_sends_notice_to_review(settings, fixture_dir):
    data = dict(GOOD, variants=[{"name": "혼자"}])
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), FakeAI(data))
    pipe.run()
    rec = repo.notices[("EXT01", "1001")]
    assert rec["status_cd"] == "NEED_REVIEW"
    assert any(i["field"] == "variants" for i in json.loads(rec["uncertain_json"])["issues"])


def test_new_option_skips_saved_notices_and_takes_new_ones(settings, fixture_dir):
    ns = [notice(fixture_dir, "text", str(i), posted=IN_WINDOW, title="공고 {}".format(i)) for i in (1, 2, 3, 4)]
    ad = FakeAdapter(ns[:2])
    pipe, repo = make_pipe(settings, ad, FakeAI(GOOD))
    pipe.run()                                                  # 1, 2 는 이미 저장됨
    ad.notices = ns
    summ = pipe.run(new=1)                                      # 새 글 1건만 (저장된 1, 2 는 세지 않음)
    assert summ.total == 1 and ("EXT01", "3") in repo.notices and ("EXT01", "4") not in repo.notices
    ad.notices = ns[:3]                                         # 일부만 본 목록이라 사라진 글(2) 정리는 하지 않는다
    pipe.run(new=5)
    assert repo.notices[("EXT01", "2")]["use_yn"] == "Y"


# ------------------------------------------------------------------ 계열(농업·예체능) 전용 공고는 AI 를 부르지 않고 건너뜀
def test_major_only_notice_is_skipped_before_ai_and_kept_with_reason(settings, fixture_dir):
    skipped = notice(fixture_dir, "text", sid="3001", title="2026 농업계열 대학생 장학생 모집")
    kept = notice(fixture_dir, "text", sid="3002", title="이공계 새터민 장학")
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([skipped, kept]), ai)
    s = pipe.run(days=30)
    rec = repo.notices[("EXT01", "3001")]
    assert rec["status_cd"] == "SKIPPED_MAJOR" and rec["use_yn"] == "N" and "농업" in rec["uncertain_json"]
    assert repo.notices[("EXT01", "3002")]["status_cd"] == "EXTRACTED"
    assert len(ai.calls) == 1 and s.skipped_major == 1 and "건너뜀(계열) 1" in s.line()


def test_major_filter_can_be_turned_off(settings, fixture_dir):
    settings.major_exclude = False
    n = notice(fixture_dir, "text", sid="3003", title="2026 농업계열 대학생 장학생 모집")
    ai = FakeAI(GOOD)
    pipe, repo = make_pipe(settings, FakeAdapter([n]), ai)
    pipe.run(days=30)
    assert repo.notices[("EXT01", "3003")]["status_cd"] == "EXTRACTED" and len(ai.calls) == 1
