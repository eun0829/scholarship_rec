#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[EXT02] 학생 정보 조건 조사 (1회성). 최근 공고 N건(마감 포함)의 상세를 읽어 '외부 장학 판단에 필요한 학생 정보'를 도출할 자료를 만든다.
DB 저장 없음, AI 호출 없음(크레딧 0). 드림스폰 계정은 set_secret.py 로 저장한 값을 쓴다.

사용 예
  python run_ext02_survey.py --list-only            # 조사 대상 목록만 확인 (로그인 불필요)
  python run_ext02_survey.py                        # 최근 100건 상세 수집 (로그인, 약 5~8분)
  python run_ext02_survey.py --attachments          # 공고문(요강) 첨부 텍스트까지 (시간이 더 걸림)
  python run_ext02_survey.py --resume               # 중단된 수집 이어서
결과는 survey_ext02 폴더: ext02_survey_notices.txt(전체 본문), ext02_survey_summary.txt(1차 통계), ext02_survey_notices.jsonl, (선택) ext02_survey_guides.txt
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError
from scholarship.sources import ext02_survey as S
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config, LoginError


def _csv(text: str):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="외부02 학생 정보 조건 조사 (최근 공고 N건)")
    ap.add_argument("--count", type=int, default=100, help="조사할 최근 공고 수 (기본 100)")
    ap.add_argument("--out", default="survey_ext02", help="결과 폴더 (기본 survey_ext02)")
    ap.add_argument("--kinds", default="장학금", help="조사할 종류(쉼표 구분). 비우면 전체 (기본: 장학금)")
    ap.add_argument("--audience", default="대학생", help="조사할 대상(쉼표 구분). 비우면 전체 (기본: 대학생)")
    ap.add_argument("--ordby", default="5,4,0", help="목록 정렬 방식을 훑을 순서 (5=업로드순, 4=모집완료, 0=기본). 대상이 충분히 모이면 멈춤")
    ap.add_argument("--max-pages", type=int, default=30, help="정렬 방식마다 훑을 최대 쪽수 (기본 30)")
    ap.add_argument("--list-only", action="store_true", help="조사 대상 목록만 보여주고 종료 (로그인 불필요)")
    ap.add_argument("--resume", action="store_true", help="이미 받은 공고는 건너뛰고 이어서")
    ap.add_argument("--attachments", action="store_true", help="공고문(요강) 첨부의 텍스트도 모음 (양식은 제외)")
    ap.add_argument("--env", default=".env", help=".env 경로")
    ap.add_argument("--secret-file", help="비밀 파일 경로 (기본: ~/.scholarship/secrets.env)")
    ap.add_argument("--log-file", help="로그 파일")
    a = ap.parse_args(argv)

    handlers = [logging.StreamHandler()]
    if a.log_file:
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers, force=True)
    log = logging.getLogger("run_ext02_survey")

    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    cfg = Ext02Config.load()
    kinds, audiences = _csv(a.kinds), _csv(a.audience)
    out_dir = Path(a.out)
    adapter = Ext02Adapter(settings, cfg)                      # DB 상태를 쓰지 않으므로 known_state 없음 (이미 저장된 글도 모두 읽음)
    try:
        rows, notes = S.collect_candidates(adapter, [int(x) for x in _csv(a.ordby)], a.count, kinds, audiences, a.max_pages)
        for n in notes:
            log.info("[목록] %s", n)
        selected = S.select_recent(rows, a.count, kinds, audiences)
        print(S.format_selection(selected))
        if not selected:
            print("\n조사 대상이 없습니다. --kinds, --audience 를 비우거나 --ordby 를 바꿔 보세요.", file=sys.stderr)
            return 1
        if len(selected) < a.count:
            log.warning("조사 대상이 요청한 %d건보다 적은 %d건입니다 (목록에서 찾을 수 있는 전부)", a.count, len(selected))
        if a.list_only:
            return 0

        guides = S.make_guide_collector(settings, out_dir) if a.attachments else None
        records, failures = S.run_survey(adapter, selected, out_dir, date.today(), resume=a.resume, guides=guides)
        paths = S.write_outputs(out_dir, records, failures)
        print("\n=== 완료: 상세 {}건 수집 (실패 {}건) ===".format(len(records), len(failures)))
        for k, p in paths.items():
            print("  {:<8} {}".format(k, p))
        print("\n분석을 맡기려면 ext02_survey_notices.txt 와 ext02_survey_summary.txt 를 올려주세요.")
        return 0 if records else 1
    except LoginError as e:
        msg = str(e)
        if settings.dreamspon_password:
            msg = msg.replace(settings.dreamspon_password, "****")
        log.error("드림스폰 로그인 문제: %s", msg)
        log.error("  -> python set_secret.py --test-dreamspon 으로 확인하세요. 로그인 실패는 자동 재시도하지 않습니다.")
        return 2
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
