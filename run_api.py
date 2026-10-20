#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[API] 추천 API 서버 실행.  (필요: pip install fastapi uvicorn)

  python run_api.py --results-dir out_v3 --no-key --demo      # 내 PC 시험: 파일의 공고로, 키 없이, 시험 화면(/demo) 켜고
  python run_api.py                                           # 운영: 저장 DB 의 공고로, API_KEY 필수 (python set_secret.py API_KEY)
  python run_api.py --host 0.0.0.0 --port 8000                # 다른 서버에서 부를 때 (내부망에서만, 포털 서버 IP만 허용)
설정(.env): API_KEY / API_REQUIRE_KEY / API_MAX_BODY_BYTES / API_CACHE_SECONDS / API_STALE_SECONDS / API_ENABLE_DEMO, 추천 설정은 RECOMMEND_* 그대로.
프로세스 하나로 실행한다 (공고 목록 캐시와 DB 연결이 프로세스마다 하나씩이다).
"""
import argparse
import logging
import sys

from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError


def build_source_factory(settings: Settings, results_dir=None):
    if results_dir:
        from scholarship.recommend.file_source import FileSource
        return lambda: FileSource(results_dir)

    def factory():
        from scholarship.common import db
        from scholarship.common.repository import OracleRepository
        if not settings.db_password:
            raise RuntimeError("DB_PASSWORD 가 없습니다")
        return OracleRepository(db.connect(settings.db_user, settings.db_password, settings.db_dsn, settings.oracle_client_lib),
                                candidate_sql_file=settings.recommend_query_file)
    return factory


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="장학금 추천 API 서버")
    ap.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (이 PC에서만). 서버에서 다른 호스트가 부르게 하려면 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--results-dir", help="DB 대신 사용할 폴더 (ext01_글번호_ai_result.json 들). 시험용")
    ap.add_argument("--include-closed", action="store_true", help="시험용: 마감이 지난 공고까지 DB 전체를 대상으로 판정한다 (운영에서는 쓰지 않는다)")
    ap.add_argument("--no-key", action="store_true", help="API 키 검사를 끈다 (개발용)")
    ap.add_argument("--demo", action="store_true", help="/demo 시험 화면을 켠다 (개발용)")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    a = ap.parse_args(argv)
    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if a.no_key:
        settings.api_require_key = False
    if a.demo:
        settings.api_enable_demo = True
    if a.include_closed:
        settings.recommend_include_closed = True
        print("주의: 시험용 전체 조회 — 마감이 지난 공고까지 판정합니다 (운영에서는 쓰지 않는다)", file=sys.stderr)
    if settings.api_require_key and not settings.api_key:
        print("필요한 비밀 값이 없습니다: API_KEY -> python set_secret.py API_KEY   (개발 시험이면 --no-key)", file=sys.stderr)
        return 2
    if settings.api_enable_demo and a.host not in ("127.0.0.1", "localhost"):
        print("오류: 시험 화면(/demo)은 인증이 없어 127.0.0.1 에서만 켤 수 있습니다.", file=sys.stderr)
        return 2
    try:
        import uvicorn
        from scholarship.api.app import create_app
    except ImportError as e:
        print("필요한 패키지가 없습니다 ({}) -> pip install fastapi uvicorn".format(e.name), file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    app = create_app(settings, build_source_factory(settings, a.results_dir))
    print("추천 API 시작: http://{}:{}  (키 검사 {}, 시험 화면 {})".format(a.host, a.port, "끔" if not settings.api_require_key else "켬", "켬 /demo" if settings.api_enable_demo else "끔"))
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", access_log=False)       # 접속 로그는 쓰지 않는다 (요청 로그는 앱이 입력값 없이 남긴다)
    return 0


if __name__ == "__main__":
    sys.exit(main())
