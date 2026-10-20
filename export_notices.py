#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RECOMMEND] 정확도 점검용: 저장 DB 의 공고를 폴더로 내려받는다 (읽기 전용, SELECT 만 실행).

  python export_notices.py                         # data/accuracy/notices/ 에 공고마다 JSON 한 개
  python export_notices.py --out my_dump           # 폴더 지정
  python export_notices.py --only-open             # 마감이 지나지 않은 공고만 (기본: config/recommend_query.txt 조건에서 마감 조건만 뺀 전체)
추천과 같은 조회 파일(config/recommend_query.txt)을 쓰되 기준일을 아주 옛날로 두어 마감이 지난 공고도 포함한다.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DB 공고를 점검용 폴더로 내려받기 (읽기 전용)")
    ap.add_argument("--out", default="data/accuracy/notices")
    ap.add_argument("--only-open", action="store_true", help="마감이 지나지 않은 공고만")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    a = ap.parse_args(argv)
    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    s = Settings.from_env()
    if not s.db_password:
        print("필요한 비밀 값이 없습니다: DB_PASSWORD -> python set_secret.py DB_PASSWORD", file=sys.stderr)
        return 2
    from scholarship.common import db
    from scholarship.common.repository import OracleRepository
    repo = OracleRepository(db.connect(s.db_user, s.db_password, s.db_dsn, s.oracle_client_lib), candidate_sql_file=s.recommend_query_file)
    try:
        rows = repo.list_candidates(date.today() if a.only_open else date(2000, 1, 1))
    finally:
        repo.close()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()                                                  # 이전 내려받기 결과와 섞이지 않게
    for r in rows:
        rec = {"src_cd": r["src_cd"], "src_id": r["src_id"], "title": r["title"], "category": r["category"], "status": r["status"],
               "apply_end": r["apply_end"].isoformat() if r["apply_end"] else None, "summary": r["summary"], "url": r["origin_url"], "elig": r["elig"]}
        (out / "{}_{}.json".format(r["src_cd"], r["src_id"])).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("공고 {}건을 {} 에 저장했습니다.".format(len(rows), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
