# -*- coding: utf-8 -*-
"""표 출력 모듈: 한글 폭 계산, 줄바꿈, 정렬, 폭 제한, 마크다운."""
import random

import pytest

from scholarship.recommend.table import ELLIPSIS, display_width, pad, render_table, truncate, wrap


@pytest.mark.parametrize("text, width", [("abc", 3), ("가나다", 6), ("a가b", 4), ("", 0), ("2026-10-08", 10), ("Ⅰ", 1), ("ＡＢ", 4), ("e\u0301", 1), ("(주)한국", 8), ("·~※", 3)])
def test_display_width(text, width):
    assert display_width(text) == width


def test_pad_aligns_by_display_width():
    assert pad("가", 5) == "가   " and display_width(pad("가", 5)) == 5
    assert pad("가", 5, "r") == "   가" and pad("abcdef", 3) == "abcdef"                                   # 넘치면 그대로


@pytest.mark.parametrize("text, width, want", [("가나다라마바사", 9, "가나다..."), ("abc", 9, "abc"), ("가나다", 6, "가나다"), ("가나다라", 6, "가..."), ("abcdefghij", 6, "abc..."),
                                               ("가나다라마바사", 4, "..."), ("가나다라마바사", 1, "...")])
def test_truncate(text, width, want):
    assert truncate(text, width) == want
    assert display_width(truncate(text, width)) <= max(width, len(ELLIPSIS))


def test_wrap_basic_and_existing_newlines():
    assert wrap("국가장학금 신청 (확인 필요)", 16) == ["국가장학금 신청", "(확인 필요)"]
    assert wrap("가 나\n다 라", 20) == ["가 나", "다 라"]                                                     # 이미 있는 줄바꿈은 유지
    assert wrap("", 10) == [""] and wrap(None, 10) == [""]
    assert wrap("abc def", 100) == ["abc def"]


def test_wrap_breaks_a_word_longer_than_the_width_by_characters():
    lines = wrap("https://www.dreamspon.com/scholarship/view.html?idx=9096", 20)
    assert len(lines) >= 3 and all(display_width(l) <= 20 for l in lines) and "".join(lines) == "https://www.dreamspon.com/scholarship/view.html?idx=9096"
    korean = wrap("가나다라마바사아자차카타파하", 8)
    assert korean == ["가나다라", "마바사아", "자차카타", "파하"]


def test_wrap_never_exceeds_width_or_loses_text_on_random_input():
    rng = random.Random(7)
    alphabet = list("가나다라마바사아자차카타파하") + list("abcdefg") + list("0123456789") + [" ", " ", "·", "~", "(", ")", "/", ":", "\n"]
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        width = rng.randint(2, 20)
        lines = wrap(text, width)
        assert all(display_width(l) <= width for l in lines), (text, width, lines)
        assert "".join(text.split()) == "".join("".join(lines).split()), (text, width, lines)


# ================================================================== 표
def widths_of(table):
    return {display_width(line) for line in table.split("\n")}


def test_ascii_table_structure_and_alignment_with_korean():
    t = render_table(["#", "장학금", "마감"], [["1", "거창군 장학", "10-08"], ["2", "서울 장학", "09-30"]], max_width=80)
    lines = t.split("\n")
    assert lines[0].startswith("+---+") and lines[2].startswith("+===+") and lines[-1].startswith("+---+")
    assert len(widths_of(t)) == 1                                                                           # 모든 줄의 표시 폭이 같다
    assert "| 1 | 거창군 장학 | 10-08 |" in t and "| 2 | 서울 장학   | 09-30 |" in t                                  # 한글 폭을 계산해 열이 맞는다
    assert t.count("\n") == 5                                                                               # 한 줄짜리 행 사이에는 구분선이 없다


def test_multiline_cells_get_row_separators_and_stay_aligned():
    t = render_table(["구분", "내용"], [["선행", "국가장학금 신청\n지원구간 산정"], ["우대", "-"]], max_width=60)
    assert len(widths_of(t)) == 1 and sum(1 for l in t.split("\n") if l.startswith("+---")) == 3                  # 위, 첫 행 뒤, 둘째 행 뒤
    assert "| 선행 | 국가장학금 신청 |" in t and "|      | 지원구간 산정   |" in t


def test_wide_tables_shrink_flexible_columns_to_the_max_width():
    rows = [["1", "가" * 40, "나" * 40, "다" * 40]]
    t = render_table(["#", "a", "b", "c"], rows, max_width=70)
    assert max(widths_of(t)) <= 70 and len(widths_of(t)) == 1
    text = "".join(line.replace("|", "").replace(" ", "") for line in t.split("\n") if "가" in line or "나" in line or "다" in line)
    assert text.count("가") == 40 and text.count("나") == 40 and text.count("다") == 40                             # 줄여도 글자는 사라지지 않는다


def test_non_flex_column_keeps_a_long_url_whole_even_beyond_the_max_width():
    url = "https://www.dreamspon.com/scholarship/view.html?idx=9096"
    t = render_table(["판정", "글번호", "원문"], [["충족", "9096", url]], max_width=40, flex=[True, True, False])
    assert url in t and len(widths_of(t)) == 1                                                              # 주소는 줄바꿈하지 않아 그대로 열 수 있다


def test_right_alignment_empty_rows_and_ragged_rows():
    t = render_table(["n", "이름"], [["7", "가"], ["12345", "나"]], align=["r", "l"], max_width=60)
    assert "|     7 | 가   |" in t and "| 12345 | 나   |" in t
    empty = render_table(["a", "b"], [], max_width=60)
    assert empty.split("\n")[1] == "| a | b |" and len(empty.split("\n")) == 4
    ragged = render_table(["a", "b", "c"], [["1"]], max_width=60)
    assert "| 1 |   |   |" in ragged


def test_markdown_style():
    t = render_table(["#", "장학금", "내용"], [["1", "[거창군](https://x.kr/1)", "a|b\n두 줄"]], style="md")
    assert t.split("\n") == ["| # | 장학금 | 내용 |", "| --- | --- | --- |", "| 1 | [거창군](https://x.kr/1) | a\\|b<br>두 줄 |"]


def test_random_tables_always_have_equal_line_widths_within_the_limit():
    rng = random.Random(11)
    alphabet = list("가나다라마바사아자") + list("abcdefg") + list("0123456789") + [" ", " ", "·", "(", ")", "\n"]
    for _ in range(1000):
        n = rng.randint(1, 5)
        rows = [["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(n)] for _ in range(rng.randint(0, 6))]
        limit = rng.randint(30, 140)
        t = render_table(["헤더%d" % i for i in range(n)], rows, max_width=limit)
        ws = widths_of(t)
        assert len(ws) == 1, (limit, n)
        if n * 9 + 1 <= limit:
            assert max(ws) <= limit, (limit, n)


def test_long_headers_are_never_cut_or_pushed_past_the_width():
    """폭을 줄일 때 머리글 글자 수 아래로 열을 줄이면 머리글이 칸을 넘친다 (추천 결과 표에서 실제로 발견된 오류)."""
    headers = ["#", "글번호", "장학금", "해당 가능 유형", "확인 필요"]
    rows = [["1", "9096", "가" * 60, "나" * 60, "다" * 60]]
    for limit in (60, 75, 90):
        t = render_table(headers, rows, max_width=limit)
        head = t.split("\n")[1]
        assert all(h in head for h in headers), (limit, head)                                               # 머리글이 온전하다
        assert len(widths_of(t)) == 1 and max(widths_of(t)) <= limit, limit


def test_random_tables_with_long_headers_stay_aligned_and_within_the_limit_when_possible():
    rng = random.Random(23)
    alphabet = list("가나다라마바사") + list("abcdef") + [" ", "\n"]
    for _ in range(800):
        n = rng.randint(1, 5)
        headers = ["".join(rng.choice("가나다라마바사") for _ in range(rng.randint(1, 8))) for _ in range(n)]
        rows = [["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 50))) for _ in range(n)] for _ in range(rng.randint(1, 4))]
        limit = rng.randint(40, 140)
        t = render_table(headers, rows, max_width=limit)
        assert len(widths_of(t)) == 1
        if sum(max(6, display_width(h)) for h in headers) + 3 * n + 1 <= limit:
            assert max(widths_of(t)) <= limit, (headers, limit)
