#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[EXT01] 외부01(학교 장학공지) 수집 -> 추출 -> AI 구조화 -> 저장.  일 1회 배치 실행용.

사용 예
  python run_ext01.py                         # 최근 30일, 전체 처리
  python run_ext01.py --no-ai --dump-dir out  # AI 없이 수집/추출까지만 (서버 환경 점검용)
  python run_ext01.py --days 365 --new 20     # 시험용: 저장된 글은 빼고 새 글 20건
  python run_ext01.py --only 153666 --force   # 특정 글만 다시 처리
  python run_ext01.py --dry-run --dump-dir out  # DB 저장 없이 (AI 호출은 함) 결과 확인
"""
import argparse
import logging
import sys
from pathlib import Path

from scholarship.common.ai.client import make_client
from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError
from scholarship.common.pipeline import Pipeline
from scholarship.common.repository import MemoryRepository, OracleRepository
from scholarship.sources.ext01 import Ext01Adapter, Ext01Config


def main() -> int:
    ap = argparse.ArgumentParser(description="외부01 장학공지 수집/추출/저장")
    ap.add_argument("--days", type=int, default=30, help="최근 N일 (기본 30)")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 처리 (테스트용)")
    ap.add_argument("--new", type=int, help="시험용: 이미 저장된 글은 빼고 새 글 N건만 처리 (--days 를 넓혀 함께 사용. 예: --days 365 --new 20)")
    ap.add_argument("--only", nargs="+", help="지정한 글번호만 처리")
    ap.add_argument("--force", action="store_true", help="이미 저장된 글도 다시 처리 (글번호/제목 건너뛰기 무시)")
    ap.add_argument("--update-changed", action="store_true", help="이미 완료된 글의 내용이 바뀌었으면 다시 처리 (기본: 글번호가 있으면 건너뜀)")
    ap.add_argument("--no-skip-title", action="store_true", help="제목이 같은 다른 글도 처리 (기본: 건너뜀)")
    ap.add_argument("--no-ai", action="store_true", help="AI 호출 없이 수집/추출까지만")
    ap.add_argument("--dry-run", action="store_true", help="DB 에 저장하지 않음 (메모리 저장소 사용)")
    ap.add_argument("--show-sql", action="store_true", help="실제로 실행될 SQL(쿼리 파일 + {DATE_COND} 치환 결과)을 출력하고 종료 (DB 접속 안 함)")
    ap.add_argument("--dump-dir", help="AI 입력/결과를 파일로 남길 폴더 (점검용)")
    ap.add_argument("--env", default=".env", help=".env 경로 (비밀번호/키는 넣지 말고 set_secret.py 사용)")
    ap.add_argument("--secret-file", help="비밀 파일 경로 (기본: ~/.scholarship/secrets.env)")
    ap.add_argument("--log-file", help="로그 파일")
    a = ap.parse_args()

    handlers = [logging.StreamHandler()]
    if a.log_file:
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)

    try:
        bootstrap(a.secret_file, a.env)          # 환경변수 > 비밀 파일 > .env
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if a.update_changed:
        settings.update_changed = True
    if a.no_skip_title:
        settings.skip_same_title = False
    cfg = Ext01Config.from_env(settings)

    if a.show_sql:
        from scholarship.common.sql_file import SqlFileError
        try:
            q1, q2 = Ext01Adapter(settings, cfg).render_queries("DATE")
        except SqlFileError as e:
            print("오류: {}".format(e), file=sys.stderr)
            return 2
        print("-- ===== 쿼리01 ({}) =====\n{}\n\n-- ===== 쿼리02 ({}) =====\n{}".format(cfg.query01_file, q1, cfg.query02_file, q2))
        print("\n-- 바인드: :setup = {!r}, :days = {}".format(cfg.setup_seq, a.days))
        print("-- {DATE_COND} 는 DATE 타입으로 가정한 결과입니다. 실행 시에는 RGSDE 컬럼 타입(DATE/문자열)에 맞춰 자동 선택됩니다.")
        return 0

    missing = []
    if not cfg.db_password:
        missing.append("DB_PASSWORD (또는 홈페이지 DB 가 다르면 EXT01_DB_PASSWORD)")
    if not a.dry_run and not settings.db_password:
        missing.append("DB_PASSWORD")
    if not a.no_ai and not settings.ai_api_key:
        missing.append("AI_API_KEY")
    if missing:
        print("필요한 비밀 값이 없습니다: {}\n  -> python set_secret.py <항목>  으로 한 번 저장하세요.".format(", ".join(dict.fromkeys(missing))),
              file=sys.stderr)
        return 2

    from scholarship.common.extractors import hwp as hwp_extractor
    hwp_ok, hwp_msg = hwp_extractor.check_environment()
    if not hwp_ok:
        logging.getLogger("run_ext01").warning(
            "HWP 변환 환경 문제: %s\n  이 상태에서는 HWP 첨부가 변환되지 않고 미리보기(앞부분 약 1,000자)로만 처리됩니다.", hwp_msg)

    adapter = Ext01Adapter(settings, cfg)

    repo = None
    try:
        ai = None if a.no_ai else make_client(settings)      # 설정 오류(AI_BASE_URL 등)를 DB 접속 전에 발견
        if a.dry_run:
            repo = MemoryRepository()
        else:
            from scholarship.common import db
            repo = OracleRepository(db.connect(settings.db_user, settings.db_password, settings.db_dsn,
                                               settings.oracle_client_lib))
        pipe = Pipeline(settings, adapter, repo, ai, force=a.force,
                        dump_dir=Path(a.dump_dir) if a.dump_dir else None)
        summ = pipe.run(days=a.days, limit=a.limit, only=a.only, new=a.new)
        return 1 if summ.failed and summ.failed == summ.total else 0
    except Exception as e:                       # 접속 실패 등 시작 단계 오류는 원인 안내만 출력 (비밀 값은 가림)
        msg = str(e)
        for secret in (settings.db_password, cfg.db_password, settings.ai_api_key):
            if secret:
                msg = msg.replace(secret, "****")
        logging.getLogger("run_ext01").error("실행 실패: %s", msg)
        if "항목 사전" in msg:
            logging.getLogger("run_ext01").error("  -> config/item_registry.json (또는 ITEM_REGISTRY_FILE)을 고친 뒤 python check_registry.py 로 점검하세요.")
        if "DPI-1047" in msg or "DPI-1072" in msg:
            logging.getLogger("run_ext01").error("  -> Oracle Instant Client 를 찾지 못했습니다. .env 의 ORACLE_CLIENT_LIB 경로를 확인하세요 (11g 는 thick 모드 필요).")
        elif "ORA-01017" in msg:
            logging.getLogger("run_ext01").error("  -> 사용자/비밀번호가 맞지 않습니다. python set_secret.py DB_PASSWORD 로 다시 저장 후 python set_secret.py --test-db 로 확인하세요.")
        return 1
    finally:
        adapter.close()
        if repo:
            repo.close()


if __name__ == "__main__":
    sys.exit(main())
