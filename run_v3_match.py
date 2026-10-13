#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RECOMMEND] 스키마 v3 공고(시험 추출 결과)를 학생 정보와 판정해 본다. DB·AI 를 쓰지 않는다 (크레딧 0).

  python run_v3_match.py --profile-text sample_student.txt                 # v3_trial\\ 의 공고 전체를 판정 (공고별 한 줄)
  python run_v3_match.py --profile-text sample_student.txt --detail 9096   # 한 공고의 모든 판정 항목과 이유
  python run_v3_match.py --profile student.json --trial-dir v3_trial --json
  python run_v3_match.py --profile-text sample_student.txt --ext01-dir out_v3 --follow-up      # 외부01(out_v3) + 외부02(v3_trial)를 함께 판정
  python run_v3_match.py --profile-text sample_student.txt --ext01-dir out_v3 --trial-dir "" --open-only --today 2026-10-13   # 외부01 만, 마감 전 공고만

run_v3_trial.py 가 만든 *_v3.json (외부02)과, run_ext01.py --dump-dir 가 만든 ext01_글번호_ai_result.json (외부01)의 정규화된 결과를 그대로 판정 엔진 v3 에 넣는다. 판정 규칙이 맞는지 눈으로 확인하는 용도다.
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from scholarship.common.config import Settings, bootstrap, parse_categories
from scholarship.common.secret_store import SecretFileError
from scholarship.recommend import matcher
from scholarship.recommend.profile import ProfileError, build_profile
from scholarship.recommend.service import VERDICT_LABEL, Recommender, detail_table, evaluation_fields, format_table, format_text, side_lines
from scholarship.recommend.student_text import format_parse_report, parse_student_text
from scholarship.common.ai import schema
from scholarship.sources.ext02_trial import dreamspon_view_url


def load_trial(folder: Path) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(folder.glob("*_v3.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if rec.get("ok") and isinstance(rec.get("data"), dict):
            rec.setdefault("src_cd", "EXT02")
            out.append(rec)
    return out


_EXT01_NAME = re.compile(r"^(ext0[12])_(\d+)_ai_result\.json$", re.IGNORECASE)      # run_ext01.py / run_ext02.py 의 --dump-dir 결과 파일


def ext01_view_url(sid: str, base: Optional[str] = None, setup_seq: Optional[str] = None) -> str:
    """외부01 공고 원문 주소 (학교 홈페이지 게시판). run_ext01 이 저장하는 주소와 같은 규칙이다."""
    base = (base or os.environ.get("EXT01_DOWN_BASE") or "https://www.tukorea.ac.kr").rstrip("/")
    return "{}/bbs/tukorea/{}/{}/artclView.do".format(base, setup_seq or os.environ.get("EXT01_BBS_SETUP_SEQ") or "374", sid)


def load_ext01(folder: Path) -> List[Dict[str, Any]]:
    """run_ext01.py / run_ext02.py --dump-dir 폴더의 ext01_·ext02_글번호_ai_result.json 을 load_trial 과 같은 모양으로 읽는다. 읽을 수 없는 파일과 v3 가 아닌 결과는 건너뛴다."""
    out = []
    for f in sorted(folder.glob("*_ai_result.json")):
        m = _EXT01_NAME.match(f.name)
        if not m:
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            elig = raw["elig"]
            if (elig.get("schema_version") or 1) < 3:
                continue
            data, _ = schema.normalize(elig, None, "", version=3)
        except (ValueError, KeyError, TypeError, OSError):
            continue
        data["schema_version"] = 3
        out.append({"idx": m.group(2), "src_cd": m.group(1).upper(), "title": data.get("scholarship_name") or f.stem, "ok": True, "data": data})
    return out


def origin_url(rec: Dict[str, Any]) -> str:
    if rec.get("url"):
        return rec["url"]
    if (rec.get("src_cd") or "EXT02") == "EXT01":
        return ext01_view_url(str(rec["idx"]))
    return dreamspon_view_url(rec["idx"], os.environ.get("EXT02_BASE_URL") or None)


def _closed(rec: Dict[str, Any], today: date) -> bool:
    """마감일이 기준일보다 앞이면 True. 마감일을 모르거나 읽을 수 없으면 False (빼지 않는다)."""
    try:
        end = date.fromisoformat(rec["data"].get("apply_end") or "")
    except ValueError:
        return False
    return end < today


def _brief(ev: matcher.Evaluation) -> str:
    if ev.verdict == matcher.NO_MATCH:
        return ev.fail_reasons[0] if ev.fail_reasons else ""
    items = list(ev.to_confirm)
    for v in ev.matched_variants:
        items += v.to_confirm
    return "확인 {}개: {}".format(len(items), " / ".join(m.split(" (")[0][:28] for m in items[:2])) if items else ""


def notice_category(rec) -> str:
    """판정 대상 구분. 학교 공지(외부01)는 AI 가 매긴 구분을 쓰고, 외부02(드림스폰)는 전부 교외 장학 공고라 OUT 으로 본다
    (시험 추출 파일의 category 는 기본값이 ETC 라서 그대로 쓰면 전부 빠진다)."""
    if rec.get("src_cd") == "EXT01":
        return (rec.get("data") or {}).get("category") or "OUT"
    return "OUT"


class _TrialSource:
    """시험 추출 결과(v3_trial)를 Recommender 가 읽는 공고 원본으로. 마감 필터는 쓰지 않는다 (시험 추출 공고는 마감이 지난 것도 있다)."""

    def __init__(self, notices, today):
        self.n, self.today = notices, today

    def list_candidates(self, today):
        out = []
        for rec in self.n:
            d = dict(rec["data"], schema_version=3)
            try:
                end = date.fromisoformat(d["apply_end"]) if d.get("apply_end") else None
            except ValueError:
                end = None
            out.append({"src_cd": rec.get("src_cd") or "EXT02", "src_id": rec["idx"], "title": rec.get("title") or "", "origin_url": origin_url(rec),
                        "category": notice_category(rec), "elig": d, "status": None, "apply_start": None, "apply_end": end or date(self.today.year + 1, 12, 31), "org_deadline": None,
                        "summary": d.get("summary")})
        return out


def _follow_up(a, settings, data, notices) -> int:
    try:
        today = date.fromisoformat(a.today) if a.today else date.today()
    except ValueError:
        print("오류: --today 는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    settings.recommend_min_days_left = -36500
    answers_text = None
    if a.answers:
        try:
            answers_text = open(a.answers, encoding="utf-8-sig").read()
        except OSError as e:
            print("오류: 답변 파일을 읽을 수 없습니다: {}".format(e), file=sys.stderr)
            return 2
    try:
        result = Recommender(_TrialSource(notices, today), settings).recommend_step(
            data, answers_text=answers_text, declined=[x.strip() for x in a.declined.split(",") if x.strip()], round_no=a.round, today=today, max_questions=a.max_questions)
    except ProfileError as e:
        print("프로필 오류: {}".format(e), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif a.format == "list":
        print(format_text(result, True))
    else:
        print(format_table(result, True, style="md" if a.format == "md" else "ascii", width=a.width or None))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v3 공고(시험 추출 결과)를 학생 정보와 판정해 보기")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile", help="학생 프로필 JSON 파일")
    src.add_argument("--profile-text", help="학생 정보 텍스트 파일 (`라벨: 값`)")
    ap.add_argument("--trial-dir", default="v3_trial", help="run_v3_trial.py 결과 폴더 (기본: v3_trial)")
    ap.add_argument("--ext01-dir", help="외부01 결과 폴더 (run_ext01.py --dump-dir 의 ext01_글번호_ai_result.json). 주면 --trial-dir 의 외부02 공고와 함께 판정한다")
    ap.add_argument("--categories", help="추천할 공고 구분을 쉼표로 (OUT 교외 / IN 교내 / NATIONAL 국가). 기본: .env 의 RECOMMEND_CATEGORIES (없으면 OUT). 안내(INFO)·대출(LOAN)·기타(ETC)는 항상 뺀다")
    ap.add_argument("--open-only", action="store_true", help="기준일(--today)에 마감이 지나지 않은 공고만 판정한다 (마감일을 모르는 공고는 포함)")
    ap.add_argument("--detail", nargs="+", metavar="IDX", help="지정한 공고의 모든 판정 항목과 이유를 출력")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD (유형별 마감이 지났는지 표시. 기본: 오늘)")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    ap.add_argument("--format", choices=("table", "md", "list"), default="table", help="출력 모양: table(표, 기본) / md(마크다운 표) / list(목록)")
    ap.add_argument("--width", type=int, default=0, help="표의 최대 폭 (기본: 터미널 폭, 알 수 없으면 120)")
    ap.add_argument("--follow-up", action="store_true", help="후속 질문(풀어 주는 공고 수 순)을 함께 출력 (run_recommend.py 와 같은 5단계 기능, 마감 필터 없음)")
    ap.add_argument("--answers", help="후속 질문에 답한 파일 (`라벨: 값` 줄). 답을 넣기 전·후를 비교해 변화를 출력 (--profile-text 는 지금까지의 입력)")
    ap.add_argument("--declined", default="", help="건너뛴 항목 코드를 쉼표로 (예: SPECIAL,AGE)")
    ap.add_argument("--round", type=int, default=1, help="몇 번째 질문 회차인지 (최대 3회)")
    ap.add_argument("--max-questions", type=int, default=5, help="한 번에 묻는 최대 질문 수")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    a = ap.parse_args(argv)

    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    settings = Settings.from_env()
    report = None
    try:
        if a.profile_text:
            parsed = parse_student_text(open(a.profile_text, encoding="utf-8-sig").read())
            data, report = parsed.data, parsed.report
        else:
            data = json.load(open(a.profile, encoding="utf-8"))
        built = build_profile(data, settings.default_gpa_scale)
    except ProfileError as e:
        print("프로필 오류: {}".format(e), file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print("오류: 입력을 읽을 수 없습니다: {}".format(e), file=sys.stderr)
        return 2
    p = built.profile
    Recommender(None, settings)._fill_school_defaults(p)               # 학교 고정 항목(소재지·종류)은 서버 설정에서
    if p.school_sido or p.school_sigungu:
        print("학교 설정: {} ({}){}".format(" ".join(x for x in (p.school_sido, p.school_sigungu) if x), p.school_type or "종류 미설정",
                                         " | 참여 사업: " + ", ".join(p.school_programs) if p.school_programs else ""), file=sys.stderr)
    else:
        print("※ 학교 소재지 설정이 없습니다: 학교 소재지 조건(예: 비수도권 소재 대학, 서울 소재 대학)은 '확인 필요'로 남습니다. "
              ".env 에 RECOMMEND_SCHOOL_SIDO / RECOMMEND_SCHOOL_SIGUNGU / RECOMMEND_SCHOOL_TYPE 를 설정하세요.", file=sys.stderr)

    try:
        today0 = date.fromisoformat(a.today) if a.today else date.today()
    except ValueError:
        print("오류: --today 는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    notices = []
    if a.trial_dir:
        folder = Path(a.trial_dir)
        notices += load_trial(folder) if folder.is_dir() else []
    ext01_n = 0
    if a.ext01_dir:
        folder1 = Path(a.ext01_dir)
        if not folder1.is_dir():
            print("오류: --ext01-dir 폴더가 없습니다: {}".format(folder1), file=sys.stderr)
            return 2
        e1 = load_ext01(folder1)
        ext01_n = len(e1)
        notices += e1
    try:
        if a.categories:
            settings.recommend_categories = parse_categories(a.categories)
    except ValueError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    allowed = set(settings.recommend_categories)
    kept = [r for r in notices if notice_category(r) in allowed]
    if len(kept) != len(notices):
        dropped: Dict[str, int] = {}
        for r in notices:
            c = notice_category(r)
            if c not in allowed:
                dropped[c] = dropped.get(c, 0) + 1
        print("※ 추천 대상 구분({})이 아니라서 뺀 공고 {}건: {}  (--categories OUT,NATIONAL,IN 으로 바꿀 수 있습니다)".format(
            ",".join(settings.recommend_categories), len(notices) - len(kept), ", ".join("{} {}건".format(k, v) for k, v in sorted(dropped.items()))), file=sys.stderr)
        notices = kept
    if a.open_only:
        before = len(notices)
        notices = [r for r in notices if not _closed(r, today0)]
        print("※ --open-only: 기준일 {} 에 마감이 지난 공고 {}건을 뺐습니다".format(today0, before - len(notices)), file=sys.stderr)
    if not notices:
        print("판정할 v3 공고가 없습니다: {} (python run_v3_trial.py 로 만든 *_v3.json, 또는 --ext01-dir 의 ext01_*_ai_result.json 이 필요합니다)".format(
            " + ".join(x for x in (a.trial_dir, a.ext01_dir) if x) or "(폴더 없음)"), file=sys.stderr)
        return 2
    if a.ext01_dir:
        print("※ 공고 {}건: 외부01 {}건 + 외부02 {}건".format(len(notices), sum(1 for r in notices if r.get("src_cd") == "EXT01"), sum(1 for r in notices if r.get("src_cd") != "EXT01")), file=sys.stderr)
    if report is not None:
        print(format_parse_report(report), file=sys.stderr)
    if a.follow_up or a.answers or a.declined or a.round > 1:
        return _follow_up(a, settings, data, notices)
    for note in built.assumptions:
        print("※ 가정: " + note, file=sys.stderr)

    try:
        today = date.fromisoformat(a.today) if a.today else date.today()
    except ValueError:
        print("오류: --today 는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2
    rows = []
    for rec in notices:
        elig = dict(rec["data"], schema_version=3)
        ev = matcher.evaluate(elig, p, regular_terms=settings.recommend_regular_terms)
        rows.append((rec, ev))
    order = {matcher.MATCH: 0, matcher.CONDITIONAL: 1, matcher.NO_MATCH: 2}
    rows.sort(key=lambda r: (order[r[1].verdict], str(r[0]["idx"])))

    if a.json:
        out = [{"idx": rec["idx"], "src_cd": rec.get("src_cd") or "EXT02", "url": origin_url(rec), "title": rec.get("title"), "verdict": ev.verdict, "assumptions": ev.assumptions, "fail_reasons": ev.fail_reasons,
                "to_confirm": ev.to_confirm, "passed": ev.passed,
                "prerequisites": ev.prerequisites, "preferences": ev.preferences, "school_programs": ev.school_programs, "notes": ev.notes,
                "variants": [{"name": v.name, "verdict": v.verdict, "to_confirm": v.to_confirm, "fails": v.fails, "apply_start": v.apply_start, "apply_end": v.apply_end,
                              "preferences": v.preferences} for v in ev.variants]} for rec, ev in rows]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    counts = {k: sum(1 for _, ev in rows if ev.verdict == k) for k in order}
    head = "=== v3 공고 {}건 판정: 충족 {} / 조건부 {} / 미달 {} ===".format(len(rows), counts[matcher.MATCH], counts[matcher.CONDITIONAL], counts[matcher.NO_MATCH])
    want = set(a.detail or [])
    if a.format != "list":
        items = []
        for rec, ev in rows:
            d = {"src_id": rec["idx"], "title": rec.get("title") or "", "url": origin_url(rec),
                 "apply_end": None, "days_left": None, "warnings": []}
            d.update(evaluation_fields(ev, today))
            items.append((d, ev))
        result = {"counts": {}, "profile_notes": {},
                  "recommended": [d for d, ev in items if ev.verdict == matcher.MATCH], "conditional": [d for d, ev in items if ev.verdict == matcher.CONDITIONAL],
                  "excluded": [{"src_id": d["src_id"], "title": d["title"], "url": d["url"], "reasons": ev.fail_reasons[:3]} for d, ev in items if ev.verdict == matcher.NO_MATCH]}
        style = "md" if a.format == "md" else "ascii"
        print(format_table(result, show_excluded=True, style=style, width=a.width or None, header=[head]))
        for d, ev in items:
            if d["src_id"] in want:
                print("\n" + detail_table(d, ev, style=style, width=a.width or None))
        missing = sorted(want - {rec["idx"] for rec, _ in rows})
        if missing:
            print("\n(없는 공고: {})".format(", ".join(missing)), file=sys.stderr)
        if not want:
            print("\n상세 판정 항목: --detail 글번호 (예: --detail {})".format(rows[0][0]["idx"]))
        return 0
    print(head)
    for rec, ev in rows:
        print("[{}] {} {}".format(VERDICT_LABEL[ev.verdict], rec["idx"], (rec.get("title") or "")[:34]))
        brief = _brief(ev)
        if brief and rec["idx"] not in want:
            print("      " + brief[:150])
        if rec["idx"] in want:
            for c in ev.common:
                print("      {:<7} {:<10} {}".format(c.status, c.key, c.message))
            for v in ev.variants:
                print("      -- 유형 {} -> {}{}".format(v.name, VERDICT_LABEL[v.verdict], " (~{})".format(v.apply_end[5:]) if v.apply_end else ""))
                for c in v.checks:
                    print("         {:<7} {:<10} {}".format(c.status, c.key, c.message))
            for line in side_lines(ev.prerequisites, ev.preferences, ev.school_programs, ev.notes, max_notes=99):
                print("      [별도] " + line)
            for note in ev.assumptions:
                print("      ※ 가정: " + note)
    missing = sorted(want - {rec["idx"] for rec, _ in rows})
    if missing:
        print("\n(없는 공고: {})".format(", ".join(missing)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
