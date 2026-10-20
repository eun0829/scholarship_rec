#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RECOMMEND] 카드 시험용 공고 6건 고르기 (유형 1개 3건 + 유형 여러 개 3건, AI 호출 없음).

  python export_notices.py --only-open       # 먼저 DB 공고를 내려받는다
  python run_pick_samples.py                  # 6건을 골라 tests/fixtures/cards/ 에 복사하고 목록을 보여 준다
  python run_pick_samples.py --single 3 --multi 3 --dir data/accuracy/notices --out tests/fixtures/cards
"""
import argparse
import shutil
import sys
from pathlib import Path

from scholarship.recommend import audit as A
from scholarship.recommend import pick_samples as P


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="카드 시험용 공고 고르기")
    ap.add_argument("--dir", default="data/accuracy/notices")
    ap.add_argument("--out", default="tests/fixtures/cards")
    ap.add_argument("--single", type=int, default=3, help="유형이 하나뿐인 공고 수")
    ap.add_argument("--multi", type=int, default=3, help="유형이 여러 개인 공고 수")
    a = ap.parse_args(argv)
    notices = A.load_notices(a.dir)
    if not notices:
        print("공고가 없습니다: {} (먼저 python export_notices.py --only-open)".format(a.dir), file=sys.stderr)
        return 2
    ones, many = P.pick(notices, a.single, a.multi)
    print("공고 {}건 중 유형 1개 이하 {}건 / 유형 여러 개 {}건".format(len(notices), sum(P.variant_count(r) < 2 for r in notices.values()), sum(P.variant_count(r) >= 2 for r in notices.values())))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for label, keys in (("유형 1개", ones), ("유형 여러 개", many)):
        print("\n[{}] {}건".format(label, len(keys)))
        for k in keys:
            r = notices[k]
            print("  {}  유형 {}개  {}".format(k, P.variant_count(r), (r.get("title") or "")[:50]))
            shutil.copyfile(Path(a.dir) / "{}.json".format(k.replace(":", "_")), out / "{}.json".format(k.replace(":", "_")))
    if len(ones) < a.single or len(many) < a.multi:
        print("\n주의: 요청한 수보다 적습니다 (마감이 지난 공고까지 보려면 export_notices.py 를 --only-open 없이 실행)")
    print("\n{} 에 복사했습니다. 이 폴더의 파일을 알려 주시면 카드 시험을 만듭니다.".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
