#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[CHAT] 챗봇이 공고 원문을 못 찾는 이유 점검 (AI 를 부르지 않는다. 질문은 받지도 저장하지도 않는다).

  python run_chat_probe.py --title 인재육성 --words 대전 거주 주민등록
  python run_chat_probe.py --title 봄내 --words 유형 --include-closed          # 시험용
공고마다 본문 길이, 첨부 개수·글자 수, 찾을 말이 본문/첨부 어디에 몇 번 나오는지를 보여 준다.
"""
import argparse
import sys
from datetime import date

from scholarship.common.config import Settings, bootstrap
from scholarship.chat import text as T


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="챗봇 원문 점검")
    ap.add_argument("--title", required=True, help="공고 제목에 들어 있는 말")
    ap.add_argument("--words", nargs="*", default=[], help="원문에 있는지 볼 말들")
    ap.add_argument("--include-closed", action="store_true", help="시험용: 마감 공고 포함")
    ap.add_argument("--env", default=".env")
    a = ap.parse_args(argv)
    bootstrap(None, a.env)
    s = Settings.from_env()
    s.recommend_include_closed = bool(a.include_closed)
    from run_api import build_source_factory
    src = build_source_factory(s)()
    try:
        cands = [c for c in src.list_candidates(date(2000, 1, 1) if a.include_closed else date.today()) if a.title in (c.get("title") or "")]
        if not cands:
            print("후보 공고에 '{}' 가 들어간 제목이 없습니다 (마감/구분/상태 때문에 빠졌을 수 있음: --include-closed)".format(a.title))
            return 1
        texts = src.get_notice_texts([(c["src_cd"], c["src_id"]) for c in cands]) if hasattr(src, "get_notice_texts") else {}
        if not texts:
            print("원문을 읽는 기능이 이 공급원에 없거나 비어 있습니다 (--results-dir 파일 모드는 원문이 없어 챗봇이 항상 '확인되지 않음'으로 답합니다)")
        for c in cands:
            key = (c["src_cd"], c["src_id"])
            tx = texts.get(key)
            print("\n== {}:{}  {}".format(key[0], key[1], c.get("title")))
            if not tx:
                print("  원문 없음 (사용여부 N 이거나 본문이 비어 있음)")
                continue
            chunks = T.chunks_of("{}:{}".format(*key), tx)
            print("  본문 {}자, 첨부 {}개 ({}), 조각 {}개 / {}자".format(len(tx.get("body") or ""), len(tx.get("files") or []),
                  ", ".join("{}={}자".format(f.get("name"), len(f.get("text") or "")) for f in tx.get("files") or []) or "-", len(chunks), sum(len(x.text) for x in chunks)))
            for w in a.words:
                where = {}
                for x in chunks:
                    where[x.source] = where.get(x.source, 0) + x.text.count(w)
                print("  '{}': {}".format(w, ", ".join("{} {}번".format(k, v) for k, v in where.items() if v) or "없음"))
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
