#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RECOMMEND] 추천 단계 시험/점검용 실행 파일. 학생 프로필(JSON, 개인정보 제외)로 추천 결과를 출력한다.

  python run_recommend.py --profile student.json --results-dir out      # DB 없이 --dump-dir 의 결과 파일로 시험
  python run_recommend.py --profile student.json                        # 저장 DB 의 공고로 (config/recommend_query.txt)
  python run_recommend.py --profile student.json --json > result.json   # 서비스 연동용 JSON
  python run_recommend.py --profile-text student.txt --results-dir out  # 학생 정보를 `라벨: 값` 텍스트로 (양식: python make_student_text.py --template)
옵션: --today 2026-10-06 (기준일 지정), --show-excluded (미달 목록과 사유)
"""
import argparse
import json
import sys
from datetime import date, datetime

from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError
from scholarship.recommend.file_source import FileSource
from scholarship.recommend.profile import ProfileError
from scholarship.recommend.service import Recommender, format_table, format_text
from scholarship.recommend.student_text import format_parse_report, parse_student_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="학생 프로필로 장학금 추천 (개인정보 제외)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile", help="학생 프로필 JSON 파일")
    src.add_argument("--profile-text", help="학생 정보 텍스트 파일 (`라벨: 값` 형식, 순서 무관)")
    ap.add_argument("--results-dir", help="DB 대신 사용할 폴더 (ext01_글번호_ai_result.json 들)")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    ap.add_argument("--show-excluded", action="store_true", help="미달 공고와 사유도 출력")
    ap.add_argument("--follow-up", action="store_true", help="후속 질문(풀어 주는 공고 수 순)을 함께 출력")
    ap.add_argument("--answers", help="후속 질문에 답한 파일 (`라벨: 값` 줄). 이 파일의 답을 넣기 전·후를 비교해 변화를 출력 (--profile-text/--profile 은 지금까지의 입력)")
    ap.add_argument("--declined", default="", help="건너뛴 항목 코드를 쉼표로 (예: SPECIAL,AGE). 다시 묻지 않는다")
    ap.add_argument("--round", type=int, default=1, help="몇 번째 질문 회차인지 (최대 3회까지 묻는다)")
    ap.add_argument("--max-questions", type=int, default=5, help="한 번에 묻는 최대 질문 수")
    ap.add_argument("--format", choices=("table", "md", "list"), default="table", help="출력 모양: table(표, 기본) / md(마크다운 표) / list(목록)")
    ap.add_argument("--width", type=int, default=0, help="표의 최대 폭 (기본: 터미널 폭, 알 수 없으면 120)")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    a = ap.parse_args(argv)

    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    parse_report = None
    try:
        if a.profile_text:
            parsed = parse_student_text(open(a.profile_text, encoding="utf-8-sig").read())
            data, parse_report = parsed.data, parsed.report
        else:
            data = json.load(open(a.profile, encoding="utf-8"))
        today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    except ProfileError as e:
        print("프로필 오류: {}".format(e), file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print("오류: 입력을 읽을 수 없습니다: {}".format(e), file=sys.stderr)
        return 2
    if parse_report is not None:
        print(format_parse_report(parse_report), file=sys.stderr)

    repo = None
    try:
        if a.results_dir:
            source = FileSource(a.results_dir)
        else:
            from scholarship.common import db
            from scholarship.common.repository import OracleRepository
            if not settings.db_password:
                print("필요한 비밀 값이 없습니다: DB_PASSWORD -> python set_secret.py DB_PASSWORD", file=sys.stderr)
                return 2
            repo = OracleRepository(db.connect(settings.db_user, settings.db_password, settings.db_dsn, settings.oracle_client_lib),
                                    candidate_sql_file=settings.recommend_query_file)
            source = repo
        rec = Recommender(source, settings)
        if a.follow_up or a.answers or a.declined or a.round > 1:
            answers_text = open(a.answers, encoding="utf-8-sig").read() if a.answers else None
            result = rec.recommend_step(data, answers_text=answers_text, declined=[x.strip() for x in a.declined.split(",") if x.strip()], round_no=a.round,
                                        today=today, max_questions=a.max_questions)
        else:
            result = rec.recommend(data, today=today)
    except ProfileError as e:
        print("프로필 오류: {}".format(e), file=sys.stderr)
        return 2
    except Exception as e:
        print("추천 실패: {}".format(e), file=sys.stderr)
        return 1
    finally:
        if repo:
            repo.close()

    if parse_report is not None:
        result["profile_parse"] = parse_report.as_dict()
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if a.format == "list":
            print(format_text(result, a.show_excluded))
        else:
            print(format_table(result, a.show_excluded, style="md" if a.format == "md" else "ascii", width=a.width or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
