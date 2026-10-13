#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[EXT02] 외부02(드림스폰) 수집 -> 추출 -> AI 구조화 -> 저장.  일 1회 배치 실행용.

사용 예
  python run_ext02.py --list-only                    # 목록만 확인 (로그인/DB/AI 불필요) - 가장 먼저 해보세요
  python run_ext02.py --no-ai --limit 3 --dump-dir out   # 로그인 + 상세/첨부 수집까지 (AI 비용 없음)
  python run_ext02.py --limit 3 --dump-dir out       # AI 포함 3건
  python run_ext02.py                                # 전체 (이미 저장된 글은 상세를 조회하지 않음 = 로그인도 안 함)
  python run_ext02.py --refresh                      # 저장된 글도 상세를 다시 읽어 신청기간 변경을 반영 (AI 호출 없이 날짜만 갱신)
  python run_ext02.py --only 9218 --force            # 특정 글만 다시 처리
"""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from scholarship.common.ai.client import make_client
from scholarship.common.config import Settings, bootstrap
from scholarship.common.pipeline import Pipeline
from scholarship.common.repository import MemoryRepository, OracleRepository
from scholarship.common.secret_store import SecretFileError
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config, LoginError


def format_list(rows, selected, cfg) -> str:
    """--list-only 출력: 후보 목록과 제외 사유별 건수."""
    sel_ids = {r.idx for r in selected}
    why = Counter()
    for r in rows:
        if r.idx in sel_ids:
            continue
        if cfg.include_kinds and r.kind not in cfg.include_kinds:
            why["종류가 {} 아님".format("/".join(cfg.include_kinds))] += 1
        elif cfg.include_tags and not any(t in r.audiences for t in cfg.include_tags):
            why["대상이 {} 아님".format("/".join(cfg.include_tags))] += 1
        else:
            why["마감/모집완료"] += 1
    lines = ["=== 드림스폰 목록 (로그인 없이 조회) ===",
             "조회 {}건 -> 후보 {}건 (종류 {}, 대상 {}, 제외 상태 {})".format(
                 len(rows), len(selected), "/".join(cfg.include_kinds) or "전체", "/".join(cfg.include_tags) or "전체",
                 "/".join(cfg.exclude_states) or "없음")]
    if why:
        lines.append("제외: " + ", ".join("{} {}건".format(k, v) for k, v in why.most_common()))
    lines.append("")
    for r in selected:
        lines.append("  idx {} | {} | {} | {} | {} | {}".format(
            r.idx, "D-{}".format(r.dday) if r.dday is not None else "D-?", r.state or "-", r.org[:16], " ".join("#" + t for t in r.tags), r.title[:40]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="외부02 드림스폰 수집/추출/저장")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 처리 (마감 임박순, 테스트용)")
    ap.add_argument("--only", nargs="+", help="지정한 글번호(idx)만 처리")
    ap.add_argument("--force", action="store_true", help="이미 저장된 글도 다시 처리")
    ap.add_argument("--refresh", action="store_true", help="저장된 글도 상세를 다시 읽어 변경(신청기간)을 확인 (로그인 필요)")
    ap.add_argument("--update-changed", action="store_true", help="이미 완료된 글의 내용이 바뀌었으면 다시 처리 (기본: 건너뜀)")
    ap.add_argument("--no-skip-title", action="store_true", help="제목이 같은 다른 글도 처리")
    ap.add_argument("--list-only", action="store_true", help="목록만 확인하고 종료 (로그인/DB/AI 불필요)")
    ap.add_argument("--no-ai", action="store_true", help="AI 호출 없이 수집/추출까지만")
    ap.add_argument("--dry-run", action="store_true", help="DB 에 저장하지 않음 (메모리 저장소 사용)")
    ap.add_argument("--dump-dir", help="AI 입력/결과를 파일로 남길 폴더 (점검용)")
    ap.add_argument("--env", default=".env", help=".env 경로 (비밀번호/키는 넣지 말고 set_secret.py 사용)")
    ap.add_argument("--secret-file", help="비밀 파일 경로 (기본: ~/.scholarship/secrets.env)")
    ap.add_argument("--log-file", help="로그 파일")
    a = ap.parse_args(argv)

    handlers = [logging.StreamHandler()]
    if a.log_file:
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers, force=True)
    log = logging.getLogger("run_ext02")

    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if a.update_changed:
        settings.update_changed = True
    if a.no_skip_title:
        settings.skip_same_title = False
    cfg = Ext02Config.load()

    if a.list_only:
        adapter = Ext02Adapter(settings, cfg)
        try:
            rows = adapter.crawl_list()
            print(format_list(rows, adapter.select(rows), cfg))
            return 0 if rows else 1
        finally:
            adapter.close()

    missing = []
    if not a.dry_run and not settings.db_password:
        missing.append("DB_PASSWORD")
    if not a.no_ai and not settings.ai_api_key:
        missing.append("AI_API_KEY")
    if missing:
        print("필요한 비밀 값이 없습니다: {}\n  -> python set_secret.py <항목>  으로 한 번 저장하세요.".format(", ".join(missing)), file=sys.stderr)
        return 2

    from scholarship.common.extractors import hwp as hwp_extractor
    hwp_ok, hwp_msg = hwp_extractor.check_environment()
    if not hwp_ok:
        log.warning("HWP 변환 환경 문제: %s\n  이 상태에서는 HWP 첨부가 변환되지 않고 미리보기(앞부분 약 1,000자)로만 처리됩니다.", hwp_msg)

    repo = adapter = None
    try:
        ai = None if a.no_ai else make_client(settings)
        if a.dry_run:
            repo = MemoryRepository()
        else:
            from scholarship.common import db
            repo = OracleRepository(db.connect(settings.db_user, settings.db_password, settings.db_dsn, settings.oracle_client_lib))
        adapter = Ext02Adapter(settings, cfg, known_state=repo.get_notice_state, refresh=a.refresh, force=a.force,
                               limit=a.limit, only=a.only)
        pipe = Pipeline(settings, adapter, repo, ai, force=a.force, dump_dir=Path(a.dump_dir) if a.dump_dir else None)
        summ = pipe.run(limit=a.limit, only=a.only)
        return 1 if summ.failed and summ.failed == summ.total else 0
    except LoginError as e:
        msg = str(e)
        if settings.dreamspon_password:
            msg = msg.replace(settings.dreamspon_password, "****")
        log.error("드림스폰 로그인 문제: %s", msg)
        log.error("  -> python set_secret.py --test-dreamspon 으로 확인하세요. 로그인 실패는 계정 보호를 위해 자동 재시도하지 않습니다.")
        return 2
    except Exception as e:
        msg = str(e)
        for secret in (settings.db_password, settings.ai_api_key, settings.dreamspon_password):
            if secret:
                msg = msg.replace(secret, "****")
        log.error("실행 실패: %s", msg)
        if "항목 사전" in msg:
            log.error("  -> config/item_registry.json (또는 ITEM_REGISTRY_FILE)을 고친 뒤 python check_registry.py 로 점검하세요.")
        if "ORA-01017" in msg:
            log.error("  -> 사용자/비밀번호가 맞지 않습니다. python set_secret.py DB_PASSWORD 로 다시 저장 후 --test-db 로 확인하세요.")
        return 1
    finally:
        if adapter:
            adapter.close()
        if repo:
            repo.close()


if __name__ == "__main__":
    sys.exit(main())
