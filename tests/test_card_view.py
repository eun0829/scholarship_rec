# -*- coding: utf-8 -*-
"""카드 표시 규칙: 해당하지 않는 유형 숨김, 제출 서류 행 분리, 유형 표의 금액·마감 열, 지역 판정 (대체 요건)."""
from pathlib import Path
import glob
import json
import pathlib
from datetime import date

import pytest

from scholarship.recommend import matcher as M
from scholarship.recommend import present as P
from scholarship.recommend import service as S
from scholarship.recommend.profile import build_profile

ROOT = pathlib.Path(__file__).parent.parent
TODAY = date(2026, 10, 16)


def prof(**kw):
    d = {"level": "UNIV", "gpa_scale": 4.5, "status": "ENROLLED", "grade": 2, "semesters_completed": 3, "gpa_last": 3.9, "credits_last": 18, "income_bracket": 1,
         "residence_sido": "충청남도", "residence_sigungu": "논산시", "parent_residence_sido": "충청남도", "parent_residence_sigungu": "논산시"}
    d.update(kw)
    return build_profile(d).profile


PROFILES = {"논산·상위": prof(), "수원·중간": prof(residence_sido="경기도", residence_sigungu="수원시", parent_residence_sido="경기도", parent_residence_sigungu="수원시", gpa_last=3.2, income_bracket=6),
            "춘천": prof(residence_sido="강원특별자치도", residence_sigungu="춘천시", parent_residence_sido="강원특별자치도", parent_residence_sigungu="춘천시", gpa_last=4.2, income_bracket=3)}


def _notices():
    out = []
    for f in sorted(glob.glob(str(ROOT / "data/v3_matrix/notices/*.json"))):
        d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        e = d.get("elig") or d.get("data") or d
        if isinstance(e, dict) and isinstance(e.get("variants"), list):
            out.append((pathlib.Path(f).stem, e))
    return out


def _cards(elig, p):
    ev = M.evaluate(elig, p)
    c = {"elig": elig, "title": "공고", "src_cd": "EXT01", "src_id": "1", "origin_url": "https://x/1", "summary": "", "apply_end": date(2026, 10, 30), "status": "OK"}
    it = S._item(c, ev, TODAY, 14, 3)
    res = {"today": TODAY.isoformat(), "counts": {"candidates": 1, "after_filter": 1, "after_dedupe": 1, "match": 0, "conditional": 0, "no_match": 0}, "profile_notes": {},
           "recommended": [it] if ev.verdict == M.MATCH else [], "conditional": [it] if ev.verdict == M.CONDITIONAL else [], "excluded": []}
    return ev, it, P.build_cards(res)["items"]


MULTI = _notices()


@pytest.mark.parametrize("name, elig", MULTI, ids=[n for n, _ in MULTI])
def test_unmatched_variants_never_appear_on_match_or_conditional_cards(name, elig):
    for label, p in PROFILES.items():
        ev, it, cards = _cards(elig, p)
        if not cards or not ev.matched_variants or not any(v.verdict == M.NO_MATCH for v in ev.variants):
            continue
        gone = [v.name for v in ev.variants if v.verdict == M.NO_MATCH]
        keep = [v.name for v in ev.matched_variants]
        text = json.dumps(cards[0], ensure_ascii=False)
        for g in gone:
            if any(g in k or k in g for k in keep) or len(g) < 3:                         # 이름이 겹치는 유형은 구분할 수 없어 제외
                continue
            assert g not in text, "{} / {}: 미달 유형 '{}' 이 카드에 보임".format(name, label, g)
        assert cards[0]["unmatched"] == []
        assert all(r["name"] in keep for r in cards[0]["variant_rows"])


@pytest.mark.parametrize("name, elig", MULTI, ids=[n for n, _ in MULTI])
def test_cards_are_deterministic_and_documents_are_not_in_checks(name, elig):
    for p in PROFILES.values():
        _, _, a = _cards(elig, p)
        _, _, b = _cards(elig, p)
        assert a == b
        for card in a:
            docs = ", ".join(card.get("documents") or [])
            for t in card["checks"]:
                assert not (t and docs and t in docs.split(", ")), "서류 이름이 확인사항에도 있음: {}".format(t)


def _it(**kw):
    base = {"matched_variants": [], "unmatched_variants": [], "notes": [], "to_confirm": [], "passed": [], "fail_reasons": [], "self_confirm": [], "benefit": {"amount": "최대 100만원", "variants": [], "limits": []}}
    base.update(kw)
    return base


def test_hide_unmatched_keeps_the_matched_variant_and_filters_prefixed_notes_and_limits():
    it = _it(matched_variants=[{"name": "성취(대학생)", "verdict": "CONDITIONAL", "verdict_label": "조건부", "to_confirm": ["확인 필요: x"]}],
             unmatched_variants=[{"name": "외국인 유학생", "reason": "국적 미해당"}, {"name": "과학", "reason": "지역 미달"}],
             notes=[{"kind": "PROCEDURE", "label": "", "text": "외국인 유학생: 개별 신청 불가"}, {"kind": "PROCEDURE", "label": "", "text": "성취(대학생): 학교 추천"}, {"kind": "PROCEDURE", "label": "", "text": "원본 PDF 제출"}],
             benefit={"amount": "70~200만원", "variants": [], "limits": ["과학: 고교 성적 기준", "1인 1년 1회"]})
    v = P.hide_unmatched(it)
    assert v["unmatched_variants"] == [] and v["variant_total"] == 3
    assert [n["text"] for n in v["notes"]] == ["성취(대학생): 학교 추천", "원본 PDF 제출"] and v["benefit"]["limits"] == ["1인 1년 1회"]
    assert P.hide_unmatched(_it(unmatched_variants=[{"name": "A"}])) ["unmatched_variants"] == [{"name": "A"}]          # 해당 유형이 없는 카드(미달)는 그대로
    rows = P.variant_rows(v)
    assert [r["name"] for r in rows] == ["성취(대학생)"]                                                                # 원래 유형이 여럿이면 남은 한 유형도 보여 준다


def test_document_row_is_separate_from_checks_and_variant_docs_go_to_the_table():
    notes = [{"kind": "PROCEDURE", "label": "", "text": t} for t in ("장학원서", "학습계획서", "구비서류 누락시 선발되지 않음")] + [{"kind": "RECOMMENDATION", "label": "", "text": "추천서 필요 (총장)"}]
    it = _it(notes=notes, matched_variants=[{"name": "A", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": [], "documents": ["봉사활동 확인서", "서류는 원본으로 제출한다"]},
                                            {"name": "B", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": []}],
             unmatched_variants=[{"name": "C", "reason": "x"}])
    v = P.hide_unmatched(it)
    assert P.document_lines(v, per_variant=False) == ["장학원서, 학습계획서"]
    assert P.document_lines(v)[1:] == ["A - 봉사활동 확인서"]
    assert sorted(P.compact_notes(v)) == sorted(["추천서 필요 (총장)", "구비서류 누락시 선발되지 않음"])
    rows = P.variant_rows(v)
    assert rows[0]["docs"] == "봉사활동 확인서" and rows[1]["docs"] == ""
    assert any("서류는 원본으로 제출한다" in t for t in P.variant_doc_notes(v))                                          # 서류 이름이 아닌 문장은 확인사항에 남는다


def test_variant_table_javascript_has_no_deadline_column_and_amount_column_needs_every_amount():
    from scholarship.api import app
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    a = src.index("function variantTable(")
    fn = src[a:src.index("function condBox(")]
    assert '"마감"' not in fn and "rows.every(r=>r.amount)" in fn and '"추가 서류"' in fn
    assert '"제출 서류"' in src


def test_condition_table_javascript_has_a_variant_column():
    from scholarship.api import app
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    fn = src[src.index("function condTable("):src.index("function variantTable(")]
    assert '"유형"' in fn and "공통" in fn


# ------------------------------------------------------------------ 지역: 대체 요건
def region(res, notes):
    return {"residence": res, "notes": notes}


def test_region_mismatch_with_alternative_route_is_a_miss_only_when_every_route_is_known_and_misses():
    rg = region(["춘천시"], "본인 또는 부모가 춘천시 주민등록 1년 이상")
    p = prof()                                                                         # 본인·부모 모두 논산
    assert M.check_region(rg, p, "x")[0].status == M.FAIL
    p2 = prof(parent_residence_sido="강원특별자치도", parent_residence_sigungu="춘천시")
    assert M.check_region(rg, p2, "x")[0].status == M.UNKNOWN                          # 부모가 맞으면 확인 필요 (자격 있는 학생을 숨기지 않는다)
    p3 = prof(parent_residence_sido=None, parent_residence_sigungu=None)
    assert M.check_region(rg, p3, "x")[0].status == M.UNKNOWN                          # 부모 거주지 정보가 없으면 확인 필요


def test_region_with_graduated_high_school_route_needs_that_input_before_it_can_be_a_miss():
    rg = region(["춘천시"], "본인 또는 출신 고교 소재지")
    assert M.check_region(rg, prof(), "x")[0].status == M.UNKNOWN                      # 졸업 고교 소재지를 입력하지 않음
    assert M.check_region(rg, prof(hs_sido="충청남도", hs_sigungu="논산시"), "x")[0].status == M.FAIL
    assert M.check_region(region(["춘천시"], "본인 또는 배우자"), prof(), "x")[0].status == M.UNKNOWN   # 입력받지 않는 대체 요건


def test_region_without_alternative_wording_is_still_a_plain_miss():
    assert M.check_region(region(["춘천시"], None), prof(), "x")[0].status == M.FAIL


# ------------------------------------------------------------------ 지역 문장 줄이기
def test_region_text_is_shortened_without_touching_the_variant_label():
    assert P.compact_region("지역 조건 미달 (필요: [본인 주민등록 강원특별자치도 춘천시 1년 이상] 또는 [부모 주민등록 강원특별자치도 춘천시 1년 이상])") == \
        "지역 조건 미달 (필요: 본인 주민등록 춘천시 1년 이상 또는 부모 주민등록 춘천시 1년 이상)"
    assert P.compact_region("지역 조건 (필요: 대전광역시)") == "지역 조건 (필요: 대전광역시)"                       # 시·군·구가 없으면 시·도는 그대로
    it = _it(fail_reasons=["지역 조건 미달 (필요: [경기도 수원시])"], matched_variants=[], unmatched_variants=[])
    assert P.condition_table(it, [])["open"][0]["cond"] == "지역 조건 미달 (필요: 수원시)"


def test_prompt_has_region_structure_and_representative_amount_rules():
    from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
    t = build_system_prompt_v3()
    assert "반드시 requirements 에 구조로" in t and "유형별 금액을 나열하지 않습니다" in t
    assert 8000 < len(t) < 22000


def test_prompt_requires_every_eligibility_bullet_including_narrative_ones():
    from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
    t = build_system_prompt_v3()
    assert "글머리표" in t and "의지와 실천 계획" in t and "kind REVIEW" in t
    assert len(t) < 22000


def test_review_criteria_notes_get_their_own_card_row():
    it = _it(notes=[{"kind": "REVIEW", "label": "심사 기준", "text": "농업리더로 성장하겠다는 의지와 실천 계획"}, {"kind": "PROCEDURE", "label": "", "text": "장학원서"}])
    assert [n["text"] for n in it["notes"] if n["kind"] == "REVIEW"] == ["농업리더로 성장하겠다는 의지와 실천 계획"]
    assert P.compact_notes(it) == []                                  # 확인사항에는 섞이지 않는다
    from scholarship.api import app
    assert '"심사 기준"' in pathlib.Path(app.__file__).read_text(encoding="utf-8")


def test_condition_table_puts_result_before_state_and_marks_each_condition_without_difficulty_chip():
    from scholarship.api import app
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    fn = src[src.index("function condTable("):src.index("function variantTable(")]
    assert '["조건","결과","내 상태"]' in fn and "MK[" in fn
    box = src[src.index("function condBox("):src.index("function render(")]
    assert "level_label" not in box and "MK[" in box


def test_condition_summary_groups_are_a_table_with_separate_title_and_result_cells():
    from scholarship.api import app
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    box = src[src.index("function condBox("):src.index("function render(")]
    assert '["조건","결과","내용","원문"]' in box and 'el("ul","sg")' not in box


def test_excluded_card_shows_selection_and_review_and_hides_review_pending_warning():
    res = {"today": TODAY.isoformat(), "counts": {"candidates": 1, "after_filter": 1, "after_dedupe": 1, "match": 0, "conditional": 0, "no_match": 1}, "profile_notes": {},
           "recommended": [], "conditional": [],
           "excluded": [{"id": "x", "src_cd": "EXT01", "src_id": "1", "title": "공고", "url": "u", "reasons": ["성적 미달"], "fail_reasons": ["성적 미달"], "to_confirm": [], "passed": [],
                         "matched_variants": [], "unmatched_variants": [], "notes": [{"kind": "REVIEW", "label": "", "text": "의지와 실천 계획"}],
                         "warnings": [{"code": "NEED_REVIEW", "message": "담당자 검수 대기"}, {"code": "LETTER", "message": "추천서 필요"}],
                         "selection_info": {"evaluation": None, "recommender": None, "criteria": ["영농 우대"], "procedure": [{"name": "서류심사", "when": None}, {"name": "면접심사", "when": "11/27"}]}}]}
    card = P.build_cards(res)["items"][0]
    assert card["review"] == ["의지와 실천 계획"] and card["warnings"] == ["추천서 필요"]
    assert card["selection"]["procedure"] and card["selection"]["criteria"]
    text = P.format_cards(res, show_excluded=True, width=100)
    assert text.index("선발 기준") < text.index("진행 절차") and "검수 대기" not in text


def test_card_row_order_is_selection_then_condition_then_procedure():
    from scholarship.api import app
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    fn = src[src.index("function render("):]
    assert fn.index('"선발 기준"') < fn.index('"조건"') < fn.index('"진행 절차"') < fn.index('"제출 서류"')


def test_show_all_variants_switch_keeps_unmatched_variants_for_testing(monkeypatch):
    it = _it(matched_variants=[{"name": "A", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": []}], unmatched_variants=[{"name": "B", "reason": "x"}])
    assert P.hide_unmatched(it)["unmatched_variants"] == []
    monkeypatch.setenv("CARD_SHOW_ALL_VARIANTS", "true")
    assert P.hide_unmatched(it)["unmatched_variants"] == [{"name": "B", "reason": "x"}]
    assert [r["name"] for r in P.variant_rows(P.hide_unmatched(it))] == ["A", "B"]


def test_school_type_sentence_with_year_range_is_redundant_when_school_types_are_structured():
    from scholarship.recommend.redundant import is_redundant
    sc = {"target_levels": ["UNIV"], "school": {"types": ["UNIV4", "COLLEGE"]}}
    assert is_redundant("국내 정규 4년제 대학 또는 2~3년제 전문대학 재학생", [sc])
    assert not is_redundant("국내 정규 4년제 대학 재학생으로 장애인", [sc])                # 모르는 단어가 남으면 그대로 확인 필요
    assert not is_redundant("국내 정규 4년제 대학 또는 2~3년제 전문대학 재학생", [{"target_levels": ["UNIV"]}])   # 학교 종류 값이 없으면 판단하지 않는다


def test_unmodeled_condition_label_is_kept_and_used_as_the_group_title():
    from scholarship.common.ai import schema_v3
    um = schema_v3.unmodeled([{"text": "승무경력 총 3년 이상으로 하선 후 1년 이내", "section": "지원자격", "source": "본문", "kind": "ELIGIBILITY", "label": "승무경력"},
                              {"text": "한 가정당 1명만 신청", "kind": "EXCLUSION"}], [], lambda *a: None)
    assert um[0]["label"] == "승무경력" and "label" not in um[1]
    it = {"condition_labels": [{"text": um[0]["text"], "label": "승무경력"}],
          "condition_summary": {"groups": [{"title": "조건", "summary": "s", "quote": "q", "level": "NORMAL", "rows": [0]}]}}
    rows = [{"cond": "승무경력 총 3년 이상으로 하선 후 1년 이내", "text": "승무경력 총 3년 이상으로 하선 후 1년 이내", "result": "confirm"}]
    assert P.summary_groups(it, rows)[0]["title"] == "승무경력"
    assert P.summary_groups({"condition_summary": it["condition_summary"]}, rows)[0]["title"] == "조건"      # 이름이 없으면 기존 그대로


def test_fallback_topic_names_cover_career_and_family_conditions():
    from scholarship.recommend.condition_summary import _topic
    assert _topic("승무경력 총 3년 이상") == "경력" and _topic("손자·손녀를 직접 부양할 경우") == "가족·부양"


def test_prompt_asks_for_a_short_label_on_each_unmodeled_condition():
    from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
    t = build_system_prompt_v3()
    assert "label" in t and "승무경력" in t and len(t) < 22000


def test_checks_are_split_by_kind_and_use_hyphens():
    it = _it(notes=[{"kind": "RECOMMENDATION", "text": "학과장 추천서 필요"}, {"kind": "PROCEDURE", "text": "서류는 우편으로 제출"}, {"kind": "PROCEDURE", "text": "선발 결과는 개별 통보"}],
             self_confirm=["타 장학금 중복 수혜 여부"])
    g = {x["title"]: x["items"] for x in P.checks_groups(it, P.compact_notes(it) + ["선발 결과는 개별 통보"])}
    assert g["추천서"] == ["학과장 추천서 필요"] and g["확인사항"] == ["서류는 우편으로 제출", "선발 결과는 개별 통보"] and "제출 방법·안내" not in g
    assert g["본인 확인"] == ["타 장학금 중복 수혜 여부"]
    assert P.checks_groups(_it(), []) == []                                              # 빈 행은 만들지 않는다


def test_check_rows_have_no_repeated_chip_or_asterisk():
    import inspect
    from scholarship.api import app
    src = inspect.getsource(app)
    assert "서류·절차" not in src and 'textNode("- "+t)' in src


def test_cards_do_not_show_a_warning_row():
    import inspect
    from scholarship.api import app
    assert 'row(tb,"주의"' not in inspect.getsource(app)
    import re
    assert not re.search(r'_rows\("주의"', Path(P.__file__).read_text(encoding="utf-8"))


def test_excluded_card_conditions_use_the_four_column_table():
    it = _it(title="t", src_id="1", reasons=["지역 조건 미달"], fail_reasons=["지역 조건 미달 (필요: 본인 주민등록 거창군 3년 이상)"], to_confirm=["확인 필요: 국가장학금 신청"])
    res = {"result_version": 1, "today": "2026-10-20", "profile_notes": {"dropped_sensitive_keys": [], "ignored_keys": [], "assumptions": []},
           "counts": {"candidates": 1, "after_filter": 1, "after_dedupe": 1, "match": 0, "conditional": 0, "no_match": 1}, "recommended": [], "conditional": [], "excluded": [it]}
    card = P.build_cards(res)["items"][0]
    g = card["condition_groups"]
    assert g and g[0]["result"] == "fail" and g[0]["title"] == "지역" and g[0]["summary"] == "본인 주민등록 거창군 3년 이상" and "미달" in g[0]["quote"]


def test_card_shows_application_period_when_start_is_known():
    assert P._period({"apply_start": "2026-10-01", "apply_end": "2026-10-23"}) == "2026-10-01 ~ 2026-10-23"
    assert P._period({"apply_end": "2026-10-23"}) == ""
    import inspect
    from scholarship.api import app
    assert 'row(tb,"모집기간"' in inspect.getsource(app)
