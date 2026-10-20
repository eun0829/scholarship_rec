# -*- coding: utf-8 -*-
"""--only 와 --include-closed 를 함께 쓰면 마감·모집완료 글도 글번호로 찾는다 (기본 목록에는 마감 글이 없어 0건 조회였던 문제)."""
import logging
from types import SimpleNamespace

from scholarship.common.config import Settings
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config


def row(idx):
    return SimpleNamespace(idx=idx, title="t" + idx, org="o", kind="장학금", audiences=["대학생"], state="모집완료", dday=-3)


def adapter(only, closed_rows):
    ad = Ext02Adapter(Settings(), Ext02Config(), only=only, include_closed=True)
    ad.calls = []

    def crawl():
        ob = ad.cfg.ordby[0]
        ad.calls.append(ob)
        if ob in (1, 2, 3):
            return [row("9300")]                                    # 기본 목록: 진행 중인 글만
        return [row(i) for i in closed_rows] + [row("9300")]         # 최근순 목록: 마감 글 포함

    ad.crawl_list = crawl
    ad._notice_or_stub = lambda r: "N" + r.idx
    return ad


def test_closed_notice_is_found_by_its_number():
    ad = adapter(["9210"], ["9210", "9211"])
    assert ad.fetch_notices(400) == ["N9210"]
    assert ad.calls == [5]                                           # 찾았으면 더 넓히지 않는다
    assert ad.deactivation_safe is False                             # 일부만 본 목록이므로 사라진 글 정리를 하지 않는다


def test_list_settings_are_restored_after_the_search():
    ad = adapter(["9210"], ["9210"])
    before = (list(ad.cfg.ordby), ad.cfg.max_pages)
    ad.fetch_notices(400)
    assert (ad.cfg.ordby, ad.cfg.max_pages) == before


def test_several_numbers_and_missing_ones_are_reported(caplog):
    ad = adapter(["9210", "9999", "9211"], ["9210", "9211"])
    with caplog.at_level(logging.WARNING):
        out = ad.fetch_notices(400)
    assert out == ["N9210", "N9211"]
    assert ad.calls == [5, 4, 0]                                     # 하나가 없으면 다른 정렬 방식까지 찾아본다
    assert "9999" in caplog.text


def test_without_only_the_normal_path_is_unchanged():
    ad = adapter([], ["9210"])
    ad.only = set()
    ad.include_closed = True
    ad.crawl_list = lambda: [row("9210"), row("9211")]
    assert ad.fetch_notices(400) == ["N9211", "N9210"]               # select() 의 최근 등록순 그대로
