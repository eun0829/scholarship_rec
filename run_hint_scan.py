#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RECOMMEND] 다시 추출하면 좋아질 공고 고르기 (AI 호출 없음, 크레딧 0).

  python export_notices.py                         # 먼저 DB 공고를 내려받는다 (data/accuracy/notices/)
  python run_hint_scan.py                          # 문장으로만 남은 조건 중 항목 사전 힌트에 걸리는 공고와 재추출 명령
  python run_hint_scan.py --codes SEM ENT          # 항목 코드를 골라서
  python run_hint_scan.py --dir my_dump
프롬프트·항목 사전을 고친 뒤에 어떤 공고를 --force 로 다시 추출할지 정하는 용도다. 문장을 자동으로 바꾸지는 않는다.
"""
import argparse
import sys

from scholarship.common.registry import load_registry
from scholarship.recommend import audit as A
from scholarship.recommend import hint_scan as H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="문장 조건 중 항목 사전으로 올릴 수 있는 공고 찾기")
    ap.add_argument("--dir", default="data/accuracy/notices")
    ap.add_argument("--codes", nargs="*", help="이 항목 코드만 (예: SEM ENT)")
    a = ap.parse_args(argv)
    notices = A.load_notices(a.dir)
    if not notices:
        print("공고가 없습니다: {} (먼저 python export_notices.py)".format(a.dir), file=sys.stderr)
        return 2
    res = H.scan_all(notices, load_registry(), a.codes)
    print("공고 {}건 중 {}건에서 구조로 올릴 수 있는 문장 조건을 찾았습니다.".format(len(notices), len(res)))
    for key, hits in sorted(res.items()):
        print("\n{}  {}".format(key, notices[key].get("title", "")))
        for h in hits:
            print("  - [{}] ({}) {}".format(h["label"], h["scope"], h["text"][:90]))
    if res:
        print("\n다시 추출하려면:")
        for c in H.reextract_commands(res):
            print("  " + c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
