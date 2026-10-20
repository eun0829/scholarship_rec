# -*- coding: utf-8 -*-
"""선택한 공고 한 건으로 범위 제한 + 출처의 원본 첨부파일 연결·다운로드."""
import json
import os

import pytest

pytest.importorskip("fastapi")
from tests.test_chat import BODY, FILE, FIX, ID, TODAY, FakeAI, ask, good, settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from scholarship.api.app import create_app  # noqa: E402
from scholarship.chat.files import safe_path  # noqa: E402
from scholarship.rag import docs as D  # noqa: E402
from scholarship.recommend.file_source import FileSource  # noqa: E402

OTHER = "EXT01:153907"
OTHER_BODY = "은평구민 장학생 선발 안내\n\n은평구에 주소를 둔 대학생으로 성적 B학점 이상. 제출서류: 주민등록등본 1부."
H = {"X-API-Key": "k1"}


class Src:
    def __init__(self, nas_file="", nas_root=None):
        self.inner = FileSource(FIX)
        self.nas_file, self.calls = nas_file, 0

    def list_candidates(self, today):
        return self.inner.list_candidates(today)

    def get_notice_texts(self, pairs):
        self.calls += 1
        out = {}
        for p in pairs:
            if tuple(p) == ("EXT01", "153808"):
                out[tuple(p)] = {"body": BODY, "files": [{"seq": "1", "name": "안내문.pdf", "role": "GUIDE", "text": FILE, "ext": "pdf", "size": 9, "nas_path": self.nas_file}]}
            elif tuple(p) == ("EXT01", "153907"):
                out[tuple(p)] = {"body": OTHER_BODY, "files": []}
        return out

    def close(self):
        pass


def build(tmp_path, ai=None, nas_file=None, **kw):
    root = tmp_path / "nas"
    root.mkdir(exist_ok=True)
    f = root / "ext01" / "153808" / "1.pdf"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"%PDF-1.4 test")
    s = settings(nas_root=root, **kw)
    src = Src(str(f) if nas_file is None else nas_file)
    app = create_app(s, lambda: src)
    if ai is not None:
        app.state.chat._client = ai
    return TestClient(app), app, src, f


# ================================================================== 선택한 공고에만 답한다
def test_focus_limits_the_sources_to_that_notice_only(tmp_path):
    ai = FakeAI(good())
    c, app, src, _ = build(tmp_path, ai, api_enable_demo=True)
    j = ask(c, notice_ids=[ID, OTHER], focus=ID).json()
    assert j["outcome"] == "answered" and [n["id"] for n in j["notices"]] == [ID]
    assert "은평구" not in ai.last_user and 'id="S1"' in ai.last_user and j["diag"]["chosen"] == 1 and j["diag"]["asked"] == 2


def test_without_focus_the_closest_notices_are_still_searched(tmp_path):
    ai = FakeAI(good())
    c, app, src, _ = build(tmp_path, ai)
    j = ask(c, notice_ids=[ID, OTHER]).json()
    assert {n["id"] for n in j["notices"]} == {ID, OTHER} and "은평구" in ai.last_user


def test_focus_outside_the_asked_list_is_rejected(tmp_path):
    ai = FakeAI(good())
    c, app, src, _ = build(tmp_path, ai)
    r = ask(c, notice_ids=[ID], focus="EXT01:999")
    assert r.status_code == 400 and r.json()["error"]["fields"][0]["field"] == "focus" and ai.calls == 0


# ================================================================== 출처의 첨부파일
def _cite_file(user):
    return json.dumps({"answer": "면접은 11월 말에 진행합니다. [1]", "citations": [{"ref": "S2", "quote": "면접은 서류 합격자에 한해 11월 말에 진행합니다"}], "followups": []}, ensure_ascii=False)


def test_citation_from_an_attachment_carries_file_info_without_the_path(tmp_path):
    c, app, src, f = build(tmp_path, FakeAI(_cite_file))
    r = ask(c, "면접은 언제 하나요?", focus=ID)
    j = r.json()
    assert j["outcome"] == "answered" and j["citations"][0]["file"] == {"seq": "1", "name": "안내문.pdf", "ext": "pdf", "size": f.stat().st_size}
    assert j["source_files"] == [{"notice_id": ID, "title": j["notices"][0]["title"], "file": j["citations"][0]["file"]}]
    assert "file_seq" not in j["citations"][0] and str(tmp_path) not in r.text and "nas_path" not in r.text


def test_no_file_info_when_the_original_is_not_on_the_server(tmp_path):
    c, app, src, f = build(tmp_path, FakeAI(_cite_file), nas_file=str(tmp_path / "없는파일.pdf"))
    j = ask(c, "면접은 언제 하나요?", focus=ID).json()
    assert j["outcome"] == "answered" and "file" not in j["citations"][0] and j["source_files"] == []


def test_demo_diag_says_why_the_download_is_missing(tmp_path):
    c, app, src, f = build(tmp_path, FakeAI(_cite_file), nas_file=str(tmp_path / "other" / "1.pdf"), api_enable_demo=True)
    d = ask(c, "면접은 언제 하나요?", focus=ID).json()["diag"]
    assert d["cited_files"] == 1 and d["linked_files"] == 0 and "기준 폴더" in d["file_why"] and str(tmp_path) not in json.dumps(d, ensure_ascii=False)
    c, app, src, f = build(tmp_path, FakeAI(_cite_file), nas_file="", api_enable_demo=True)
    assert "NAS_PATH" in ask(c, "면접은 언제 하나요?", focus=ID).json()["diag"]["file_why"]


def test_body_citation_has_no_file(tmp_path):
    c, app, src, f = build(tmp_path, FakeAI(good()))
    j = ask(c).json()
    assert j["outcome"] == "answered" and "file" not in j["citations"][0] and j["source_files"] == []


# ================================================================== 다운로드
def test_download_needs_the_key_and_returns_the_file_as_attachment(tmp_path):
    c, app, src, f = build(tmp_path)
    url = "/v1/notices/{}/files/1".format(ID)
    assert c.get(url).status_code == 401
    r = c.get(url, headers=H)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 test"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment") and "filename*=" in cd and r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/v1/notices/EXT01:153808/files/2", "/v1/notices/EXT01:999/files/1", "/v1/notices/EXT01:153808/files/..%2F..%2Fetc", "/v1/notices/bad id/files/1",
                                  "/v1/notices/EXT01:153907/files/1"])
def test_download_of_anything_not_linked_is_404(tmp_path, path):
    c, app, src, f = build(tmp_path)
    r = c.get(path, headers=H)
    assert r.status_code == 404 and src.calls <= 1
    if "error" in r.json():
        assert r.json()["error"]["code"] == "NOT_FOUND"


def test_download_refuses_paths_outside_the_nas_root_and_zip_members(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    for bad in (str(outside), str(tmp_path / "nas" / ".." / "secret.txt"), str(tmp_path / "nas" / "ext01" / "153808" / "1.pdf") + "!inner.pdf", "", "\x00"):
        c, app, src, f = build(tmp_path, nas_file=bad)
        assert c.get("/v1/notices/{}/files/1".format(ID), headers=H).status_code == 404, bad


def test_symlink_pointing_outside_the_root_is_refused(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    outside = tmp_path / "out.txt"
    outside.write_text("x")
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 를 만들 수 없는 환경")
    assert safe_path(root, str(link)) is None and safe_path(root, str(outside)) is None


def test_demo_download_works_without_key_only_when_demo_is_on(tmp_path):
    c, app, src, f = build(tmp_path, api_enable_demo=True)
    assert c.get("/demo/files/{}/1".format(ID)).status_code == 200
    c2, *_ = build(tmp_path)
    assert c2.get("/demo/files/{}/1".format(ID)).status_code == 404


def test_download_is_not_logged_with_file_name(tmp_path, caplog):
    import logging
    c, app, src, f = build(tmp_path)
    with caplog.at_level(logging.DEBUG):
        c.get("/v1/notices/{}/files/1".format(ID), headers=H)
    assert "안내문" not in caplog.text and "1.pdf" not in caplog.text


# ================================================================== 색인에 파일 번호
def test_attachment_docs_carry_the_file_seq_and_it_changes_the_hash():
    cand = {"src_cd": "EXT01", "src_id": "1", "title": "t", "apply_end": None}
    t = lambda seq: {"body": "", "files": [{"seq": seq, "name": "a.pdf", "role": "GUIDE", "text": "첨부 내용 " * 20}]}
    a, b = D.notice_docs("EXT01:1", cand, t("7")), D.notice_docs("EXT01:1", cand, t("8"))
    assert [d.file_seq for d in a if d.kind == "attach"] == ["7"] and all(d.file_seq == "" for d in a if d.kind != "attach")
    assert D.content_hash(a, "m", 8) != D.content_hash(b, "m", 8)
