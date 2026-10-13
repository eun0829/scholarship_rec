# -*- coding: utf-8 -*-
"""ZIP 내부 파일을 파일별로 분류·추출하는 extract_members."""
import io
import zipfile

import pytest

from scholarship.common import file_role
from scholarship.common.config import Settings
from scholarship.common.extractors.results import ExtractError
from scholarship.common.extractors.zip_ import extract_members
from tests.test_ext02_pipeline import mark_encrypted


def build(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def st(tmp_path):
    s = Settings()
    s.keywords_file = file_role.Path(__file__).resolve().parent.parent / "config" / "file_keywords.json" if hasattr(file_role, "Path") else s.keywords_file
    return s


def run(tmp_path, data, st, parent_role=file_role.UNKNOWN, name="t.zip"):
    p = tmp_path / name
    p.write_bytes(data)
    return extract_members(p, st, parent_role=parent_role, label=name)


def test_roles_statuses_and_text(tmp_path, st):
    out = run(tmp_path, build({"선발 공고문.txt": "요강 내용", "지원서 양식.txt": "양식 내용", "붙임.txt": "미분류 내용",
                               "합격자 명단.txt": "명단", "오래된.doc": "x", "중첩.zip": "PK", "이상한.xyz": "x"}), st)
    by = {m.name: m for m in out}
    assert [m.role for m in (by["선발 공고문.txt"], by["지원서 양식.txt"], by["붙임.txt"])] == ["GUIDE", "FORM", "UNKNOWN"]
    assert by["선발 공고문.txt"].status == "EXTRACTED" and by["선발 공고문.txt"].result.text == "요강 내용"
    assert by["합격자 명단.txt"].status == "SKIPPED" and by["합격자 명단.txt"].role == "EXCLUDED" and by["합격자 명단.txt"].result is None
    assert by["오래된.doc"].status == "SKIPPED" and by["중첩.zip"].err == "중첩 ZIP 은 처리하지 않음" and by["이상한.xyz"].status == "SKIPPED"


def test_parent_name_role_is_inherited_only_for_unclassified_members(tmp_path, st):
    data = build({"붙임.txt": "a", "공고문.txt": "b", "서식.txt": "c"})
    forms = {m.name: m.role for m in run(tmp_path, data, st, parent_role=file_role.FORM)}
    assert forms == {"붙임.txt": "FORM", "공고문.txt": "GUIDE", "서식.txt": "FORM"}                  # 이름에 단서가 있으면 그것을 우선
    aux = {m.name: m.role for m in run(tmp_path, data, st, parent_role=file_role.AUX)}
    assert aux["붙임.txt"] == "AUX"
    guide = {m.name: m.role for m in run(tmp_path, data, st, parent_role=file_role.GUIDE)}
    assert guide["붙임.txt"] == "UNKNOWN"                                                             # 요강 이름의 ZIP 이어도 확정하지는 않음


def test_legacy_korean_filenames_cp949_are_decoded(tmp_path, st):
    raw = bytearray(build({"AAAAAA.txt": "내용"}))                                                    # ASCII 이름 -> UTF-8 플래그 없음
    patched = bytes(raw).replace(b"AAAAAA.txt", "공고문.txt".encode("cp949"))                           # 옛 압축 프로그램이 cp949 로 저장한 이름
    assert len("공고문.txt".encode("cp949")) == len(b"AAAAAA.txt")
    [m] = run(tmp_path, patched, st)
    assert m.name == "공고문.txt" and m.role == "GUIDE" and m.status == "EXTRACTED"


def test_one_bad_member_does_not_stop_the_others(tmp_path, st):
    data = mark_encrypted(build({"공고문.txt": "정상", "비밀.txt": "secret", "깨진.pdf": b"%PDF-broken"}), "비밀.txt")
    by = {m.name: m for m in run(tmp_path, data, st)}
    assert by["공고문.txt"].status == "EXTRACTED" and by["비밀.txt"].status == "FAILED" and "암호" in by["비밀.txt"].err
    assert by["깨진.pdf"].status == "FAILED" and by["깨진.pdf"].err


def test_size_limits_and_member_count(tmp_path, st):
    st.max_file_bytes = 100
    by = {m.name: m for m in run(tmp_path, build({"작은 공고문.txt": "x" * 10, "큰 공고문.txt": "y" * 500}), st)}
    assert by["작은 공고문.txt"].status == "EXTRACTED" and by["큰 공고문.txt"].status == "SKIPPED" and "크기 초과" in by["큰 공고문.txt"].err
    st.max_zip_members = 2
    with pytest.raises(ExtractError, match="너무 많음"):
        run(tmp_path, build({"a.txt": "1", "b.txt": "2", "c.txt": "3"}), st)
    with pytest.raises(ExtractError, match="ZIP 구조 손상"):
        run(tmp_path, b"PK not really", st)
    assert run(tmp_path, build({}), st) == []
