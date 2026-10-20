# -*- coding: utf-8 -*-
"""결과 표시 규칙: 앞머리(확인 필요/제외 조건) 해석, 유형별 묶기, cp949 안전 기호, 터미널 card 출력."""
import pytest

from scholarship.recommend import present as P
from scholarship.recommend.service import format_text
from scholarship.recommend.table import display_width

URL = "https://www.tukorea.ac.kr/bbs/tukorea/374/153587/artclView.do?layout=unknown&very=long&query=" + "x" * 80


def item(**kw):
    base = {"id": "EXT01:153587", "src_id": "153587", "title": "논산시장학회 장학생", "url": URL, "summary": "논산시 거주 신입생 150만원", "apply_end": "2026-10-30",
            "days_left": 17, "warnings": [{"code": "NEED_REVIEW", "message": "담당자 검수 대기 중인 공고입니다. 신청 전 원문을 꼭 확인하세요"}],
            "to_confirm": ["제외 조건 확인: 생활비 지원 특수학교 학생", "확인 필요: 신청자는 본인 또는 부·모"],
            "matched_variants": [{"name": "학업장려", "verdict_label": "조건부", "apply_end": "2026-10-30", "to_confirm": ["확인 필요: 2003-01-01 이후 출생"]}],
            "unmatched_variants": [{"name": "취업장려"}], "prerequisites": [], "preferences": [], "school_programs": [],
            "notes": [{"kind": "PROCEDURE", "label": "절차·서류", "text": "방문 또는 등기우편 접수"}], "assumptions": ["공고에 평점 만점 표기가 없어 4.5 만점으로 가정함"]}
    base.update(kw)
    return base


def result(items=None, excluded=None):
    items = items if items is not None else [item()]
    excluded = excluded if excluded is not None else [{"id": "X", "src_id": "153907", "title": "은평구민 장학생", "url": "https://x/2", "reasons": ["지역 조건 미달"]}]
    return {"result_version": 1, "today": "2026-10-13", "profile_notes": {"dropped_sensitive_keys": [], "ignored_keys": [], "assumptions": []},
            "counts": {"candidates": 3, "after_filter": 3, "after_dedupe": 3, "match": 0, "conditional": len(items), "no_match": len(excluded)},
            "recommended": [], "conditional": items, "excluded": excluded}


@pytest.mark.parametrize("msg,want", [("제외 조건 확인: 특수학교 학생", (P.EXCLUDE, "특수학교 학생")), ("확인 필요: 출생 요건", (P.CONFIRM, "출생 요건")),
                                      ("어학 요건 확인 필요 (필요: TOEIC 700점 이상)", (P.CONFIRM, "어학 요건 확인 필요 (필요: TOEIC 700점 이상)")), ("", (P.CONFIRM, ""))])
def test_split_message(msg, want):
    assert P.split_message(msg) == want


def test_variant_items_become_a_subheading_not_a_bracket_prefix():
    g = P.confirm_groups(item())
    assert [x["name"] for x in g] == [None, "학업장려"] and g[0]["items"][0] == {"kind": P.EXCLUDE, "text": "생활비 지원 특수학교 학생"}
    assert g[1]["verdict"] == "조건부" and g[1]["items"] == [{"kind": P.CONFIRM, "text": "2003-01-01 이후 출생"}]
    assert P.confirm_groups(item(to_confirm=[], matched_variants=[])) == []


def test_symbols_are_safe_in_korean_windows_consoles():
    for sym in P.SYMBOL.values():
        sym.encode("cp949")                                                   # 구형 콘솔 인코딩에 있는 기호만 쓴다 (✔ ✖ ⚠ 이모지는 여기서 걸린다)
        assert len(sym) == 1
    assert "?" not in "".join(P.SYMBOL.values()) and "!" not in "".join(P.SYMBOL.values())


def test_cards_separate_each_item_and_keep_the_url_whole():
    out = P.format_cards(result([item(), item(src_id="153588", title="두 번째 장학")]), show_excluded=True, width=80)
    assert out.count("◎ 조건부 1/2") == 1 and out.count("◎ 조건부 2/2") == 1 and "○ 미달 1/1" in out
    assert out.count("=" * 80) >= 4                                            # 건마다 굵은 구분선
    assert "▶ " + URL in out                                                   # 원문 주소는 줄바꿈·생략 없이 그대로
    wide = P.format_cards(result([item()]), width=200)
    assert "[학업장려]" in wide and "생활비 지원 특수학교 학생" in wide and "제외 조건" in wide and "신청자는 본인 또는 부·모" in wide        # 조건 표: 유형은 조건 칸 앞에 붙는다
    assert "\n  ? " not in out and "확인 필요:" not in out
    assert "담당자 검수 대기" not in out and "- 방문 또는 등기우편 접수" in out and "취업장려" not in out and "◇ 취업장려" not in out               # 해당하지 않는 유형은 카드에 보이지 않는다
    table_part = out[out.index("[한눈에 보기]"):out.index("=" * 80)]
    assert max(display_width(l) for l in table_part.splitlines() if l.startswith(("+", "|"))) <= 80          # 한눈에 보기 표는 폭을 지킨다


def test_cards_hide_no_match_unless_asked_and_handle_empty():
    r = result()
    assert "미달 1/1" not in P.format_cards(r, show_excluded=False)
    empty = P.format_cards(result([], excluded=[]), show_excluded=True)
    assert "추천할 장학금이 없습니다" in empty


def test_list_format_uses_symbols_instead_of_question_and_exclamation_marks():
    r = result()
    r["conditional"][0].update(title="논산시장학회", src_id="1", apply_end="2026-10-30", days_left=17)
    text = format_text(r, show_excluded=True)
    assert "□ 확인 필요: 신청자는 본인 또는 부·모" in text and "▲ 제외 조건: 생활비 지원 특수학교 학생" in text and "□ [학업장려] 확인 필요: 2003-01-01 이후 출생" in text
    assert "\n    ? " not in text and "\n    ! " not in text and "△ 담당자 검수 대기" in text and "원문: " + URL in text


def test_cards_structure_for_the_demo_page():
    c = P.build_cards(result([item()]))
    x = c["items"][0]
    assert c["counts"] == {"match": 0, "cond": 1, "no": 1} and [i["verdict"] for i in c["items"]] == ["cond", "no"]
    assert x["index"] == 1 and x["total"] == 1 and x["url"] == URL and x["confirm_count"] == 3 and x["unmatched"] == []


def test_parse_passed_and_confirm_split_condition_and_state():
    assert P.parse_passed("성적 충족 (전체(누적) 평점 3.9/4.5, 기준 3.5/4.5 이상)") == ("성적", "전체(누적) 평점 3.9/4.5, 기준 3.5/4.5 이상")
    assert P.parse_passed("이수 학기 7학기 (기준 3학기 이상 충족)") == ("이수 학기 7학기", "기준 3학기 이상 충족")
    assert P.parse_passed("그냥 문장") == ("그냥 문장", "")
    assert P.parse_confirm("학교 종류 설정이 없음 (공고 대상: 4년제 대학)") == ("4년제 대학", "학교 종류 설정이 없음")
    assert P.parse_confirm("서술형 조건") == ("서술형 조건", "직접 확인")


def test_compact_notes_hides_common_documents_but_keeps_unusual_ones():
    notes = [{"kind": "PROCEDURE", "label": "절차·서류", "text": t} for t in (
        "대산 지원서 (소정양식)", "재학증명서 (2차 제출(면접 시))", "성적증명서", "영농현황 자료(본인 또는 보호자) (해당자에 한함)", "영농교육 수료증명서(본인)")]
    notes.append({"kind": "RECOMMENDATION", "label": "추천 필요", "text": "지도교수 추천서"})
    notes.append({"kind": "OBLIGATION", "label": "선발 후 의무", "text": "봉사활동 40시간"})
    got = P.compact_notes({"notes": notes})
    assert got == ["지도교수 추천서"]                                                              # 서류 이름은 확인사항이 아니라 제출 서류 행
    assert P.document_lines({"notes": notes}) == ["영농현황 자료(본인 또는 보호자) (해당자에 한함), 영농교육 수료증명서(본인)"]


def test_document_names_are_merged_into_one_line_and_sentences_stay_apart():
    mk = lambda k, t: {"kind": k, "label": "", "text": t}
    notes = [mk("PROCEDURE", "추천자로 최종선발된 학생에 한해 원본제출 요청 예정"), mk("PROCEDURE", "구비서류 누락시 선발되지 않음, 각 서류는 파일로 업로드"),
             mk("PROCEDURE", "장학원서"), mk("PROCEDURE", "총장 또는 학장 추천서"), mk("PROCEDURE", "한국장학재단 소득분위확인서"), mk("PROCEDURE", "학습계획서"),
             mk("RECOMMENDATION", "추천서 필요 (총장·학장·학과장)")]
    rules = type("R", (), {"doc_skip": (), "doc_keep": ()})()
    got = P.compact_notes({"notes": notes}, rules=rules)
    assert P.document_lines({"notes": notes}, rules=rules) == ["장학원서, 한국장학재단 소득분위확인서, 학습계획서"]      # 추천서는 '추천 필요' 줄이 있어 서류 줄에서 뺀다
    assert "추천서 필요 (총장·학장·학과장)" in got and not any("장학원서" in g or "소득분위" in g for g in got)
    assert "추천자로 최종선발된 학생에 한해 원본제출 요청 예정" in got and "구비서류 누락시 선발되지 않음, 각 서류는 파일로 업로드" in got
    assert P.is_document_name("장학원서") and not P.is_document_name("구비서류 누락시 선발되지 않음")


def test_met_conditions_are_folded_unless_asked():
    it = item(passed=["성적 충족 (3.9/4.5)", "학적 상태 해당 (필요: 재학)"])
    folded = P.format_cards(result([it]), width=120)
    assert "충족 2개  성적 · 학적 상태" in folded and "3.9/4.5" not in folded
    shown = P.format_cards(result([it]), width=120, show_met=True)
    assert "3.9/4.5" in shown and "충족 2개" not in shown
    shown.encode("cp949")


def test_card_has_conditions_and_checks_for_the_demo():
    card = P.build_cards(result([item(passed=["성적 충족 (3.9/4.5)"])]))["items"][0]
    assert len(card["conditions"]["met"]) == 1 and len(card["conditions"]["open"]) == 3 and card["conditions"]["info"][0]["cond"] == "해당 유형"
    assert card["checks"] == ["방문 또는 등기우편 접수"]


def test_excluded_card_shows_summary_benefit_and_conditions():
    ex = {"id": "X", "src_id": "9", "title": "탈락 장학", "url": "https://x/9", "reasons": ["성적 미달 (평점 2.0, 기준 3.0 이상)"], "summary": "요약 문장",
          "apply_end": "2026-10-30", "days_left": 17, "benefit": {"amount": "연 200만원", "variants": [], "limits": []},
          "passed": ["학적 상태 해당 (필요: 재학)"], "fail_reasons": ["성적 미달 (평점 2.0, 기준 3.0 이상)"], "to_confirm": ["확인 필요: 추천서 제출"],
          "matched_variants": [], "unmatched_variants": [], "prerequisites": [], "preferences": [], "school_programs": [], "notes": [], "warnings": [], "assumptions": []}
    card = P.build_cards(result([], [ex]))["items"][0]
    assert card["summary"] == "요약 문장" and card["benefit"] == ["연 200만원"] and card["deadline"].startswith("2026-10-30")
    assert [r["result"] for r in card["conditions"]["open"]] == ["fail", "confirm"] and len(card["conditions"]["met"]) == 1
    out = P.format_cards(result([], [ex]), show_excluded=True, width=120)
    for w in ("요약", "혜택", "연 200만원", "미달", "추천서 제출", "충족 1개"):
        assert w in out
    assert "미달 사유" not in out                                                  # 조건표에 미달 행이 있으니 사유 구역은 따로 안 둔다
    out.encode("cp949")
    old = P.format_cards(result([]), show_excluded=True, width=120)                # 상세 재료가 없는 옛 결과
    assert "미달 사유" in old and "지역 조건 미달" in old


def test_all_variants_failed_lists_each_variant_reason_on_its_own_row():
    it = {"fail_reasons": ["해당하는 유형이 없음: A (지역 미달); B (성적 미달)"], "matched_variants": [], "passed": [], "to_confirm": [],
          "unmatched_variants": [{"name": "A", "reason": "지역 미달"}, {"name": "B", "reason": "성적 미달", "reasons": ["성적 미달", "학점 미달"]}]}
    rows = P.condition_table(it, [])["open"]
    assert [r["cond"] for r in rows] == ["[A] 지역 미달", "[B] 성적 미달; 학점 미달"] and {r["result"] for r in rows} == {"fail"}
    it["unmatched_variants"] = [{"name": n, "reason": "지역 조건 미달 (필요: [본인 주민등록 강원특별자치도 춘천시 1년 이상])"} for n in "ABCD"]
    rows = P.condition_table(dict(it, fail_reasons=["해당하는 유형이 없음: …"]), [])["open"]
    assert [r["cond"] for r in rows] == ["[A, B 외 2개] 지역 조건 미달 (필요: 본인 주민등록 춘천시 1년 이상)"]          # 사유가 같은 유형은 한 줄, 지역 문장은 짧게
    it["unmatched_variants"] = [{"name": "A", "reason": "지역 미달"}, {"name": "B", "reason": "성적 미달", "reasons": ["성적 미달", "학점 미달"]}]
    it["unmatched_variants"] = []
    assert [r["cond"] for r in P.condition_table(it, [])["open"]] == ["해당하는 유형이 없음: A (지역 미달); B (성적 미달)"]       # 유형 목록이 없는 옛 결과는 그대로


def test_multi_variant_card_gets_one_result_row_per_variant_for_the_web_only():
    it = item(matched_variants=[{"name": "A", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": []}, {"name": "B", "verdict": "CONDITIONAL", "verdict_label": "조건부", "to_confirm": ["x", "y"]}],
              unmatched_variants=[{"name": "C", "reason": "지역 미달", "reasons": ["지역 미달", "성적 미달"]}], fail_reasons=[])
    info = P.condition_table(it, [])["info"]
    web = [r for r in info if r.get("web")]
    assert [(r["variant"], r["result"]) for r in web] == [("A", "met"), ("B", "confirm"), ("C", "fail"), ("C", "fail")] and web[1]["state"] == "조건부 (확인 2개)"
    assert info[0]["cond"] == "해당 유형"                                                          # 터미널용 요약 줄은 그대로
    text = P.format_cards(result([it]), width=120)
    assert "[A] 해당 여부" not in text and "미달 유형: C" not in text                                    # 터미널에는 web 줄이 나오지 않고, 미달 유형은 카드에서 숨긴다
