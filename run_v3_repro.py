#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3 추출 재현성 시험: 같은 학교 공지를 N번 추출해서 결과가 얼마나 흔들리는지 본다.  (AI 호출 = 크레딧 소모)

  python run_v3_repro.py --ids 153930 153808 --runs 3 --student student_1st.txt   # 추출 3회 + 비교
  python run_v3_repro.py --ids 153930 --runs 3 --dry-plan                          # 호출 없이 계획(호출 횟수)만 출력
  python run_v3_repro.py --compare-only --out-dir out_repro                        # 이미 받은 결과만 비교 (AI 호출 없음)

각 회차는 `run_ext01.py --dry-run --only <글번호> --force --dump-dir <out-dir>/runN` 으로 돌린다 (DB 저장 없음, AI_SCHEMA_VERSION=3 은 이 도구가 환경변수로만 설정).
비교 항목: 구분(category) · 신청 마감 · 소득·학년·학점 등 핵심 조건 · other_conditions · unmodeled_conditions 의 kind · 유형 이름.
--student 를 주면 회차별 판정(충족/조건부/미달)까지 비교한다. 같은 공고에서 회차마다 판정이 달라지면 [판정 흔들림] 으로 표시한다.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

TOKENS_PER_NOTICE = 9000      # 11건 시험에서 공고 1건당 평균 약 12,800 토큰(입력 8.5k + 출력 4.3k)이었다. 첨부가 적은 공고는 이보다 작다.


def run_dirs(out_dir: Path) -> List[Path]:
    return sorted([d for d in out_dir.glob("run*") if d.is_dir()], key=lambda d: int(d.name[3:]) if d.name[3:].isdigit() else 0)


def load_result(folder: Path, nid: str) -> Optional[Dict[str, Any]]:
    f = folder / "ext01_{}_ai_result.json".format(nid)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("elig")
    except (OSError, ValueError):
        return None


def fingerprint(e: Dict[str, Any]) -> Dict[str, Any]:
    """판정에 영향을 주는 핵심 값만 뽑는다 (문구 하나하나가 아니라 구조)."""
    inc = e.get("income") or {}
    gpa = e.get("gpa") or {}
    cr = e.get("credits") or {}
    return {
        "category": e.get("category"),
        "apply_end": e.get("apply_end"),
        "grades": sorted(e.get("grades") or []),
        "income.max_bracket": inc.get("max_bracket"),
        "gpa.min": gpa.get("min"),
        "credits.min_last_semester": cr.get("min_last_semester"),
        "variants": sorted((v.get("name") or "") for v in e.get("variants") or []),
        "other_conditions": len(e.get("other_conditions") or []),
        "확인필요로 이어지는 문장(ELIGIBILITY)": sum(1 for c in e.get("unmodeled_conditions") or [] if c.get("kind") == "ELIGIBILITY"),
        "unmodeled.kinds": sorted(c.get("kind") or "" for c in e.get("unmodeled_conditions") or []),
        "prerequisites": sorted(c.get("type") or "" for c in e.get("prerequisites") or []),
    }


def compare(results: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """회차별 지문에서 값이 서로 다른 항목만 {항목: [회차별 값]} 으로 돌려준다."""
    prints = [fingerprint(e) for e in results]
    diff: Dict[str, List[Any]] = {}
    for k in prints[0] if prints else []:
        vals = [p[k] for p in prints]
        if any(v != vals[0] for v in vals[1:]):
            diff[k] = vals
    return diff


def verdicts(student: str, folder: Path, nid: str, env: str) -> Optional[str]:
    """한 회차 결과 폴더로 run_v3_match 를 돌려 해당 공고의 판정을 읽는다."""
    import contextlib
    import io
    import run_v3_match
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = run_v3_match.main(["--profile-text", student, "--ext01-dir", str(folder), "--trial-dir", "", "--json",
                                "--categories", "OUT,IN,NATIONAL", "--env", env])
    if rc != 0:
        return None
    try:
        data = json.loads(buf.getvalue())
    except ValueError:
        return None
    for r in data if isinstance(data, list) else []:
        if str(r.get("idx", r.get("src_id"))) == nid:
            return r.get("verdict_label") or r.get("verdict")
    return None


def report(out_dir: Path, ids: List[str], student: Optional[str], env: str) -> int:
    dirs = run_dirs(out_dir)
    if len(dirs) < 2:
        print("비교할 회차가 2개 이상 필요합니다: {}".format(out_dir), file=sys.stderr)
        return 2
    unstable = 0
    print("=== 재현성 결과: {}회 비교 ===".format(len(dirs)))
    for nid in ids:
        res = [load_result(d, nid) for d in dirs]
        ok = [e for e in res if e]
        if len(ok) < 2:
            print("\n[{}] 결과가 있는 회차가 {}개뿐이라 비교하지 못했습니다".format(nid, len(ok)))
            continue
        diff = compare(ok)
        vs = [verdicts(student, d, nid, env) for d in dirs] if student else []
        shaky = bool(diff) or (vs and len(set(vs)) > 1)
        unstable += 1 if shaky else 0
        print("\n[{}] {}".format(nid, "흔들림 있음" if shaky else "회차마다 같음"))
        for k, vals in diff.items():
            print("  - {}: {}".format(k, " | ".join(str(v) for v in vals)))
        if vs:
            print("  - 판정: {}{}".format(" | ".join(str(v) for v in vs), "   <-- [판정 흔들림]" if len(set(vs)) > 1 else ""))
    print("\n공고 {}건 중 흔들림 있음 {}건".format(len(ids), unstable))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v3 추출 재현성 시험 (AI 호출 = 크레딧 소모)")
    ap.add_argument("--ids", nargs="+", help="글번호 (외부01)")
    ap.add_argument("--runs", type=int, default=3, help="반복 횟수 (기본 3)")
    ap.add_argument("--student", help="학생 정보 텍스트 파일: 주면 회차별 판정도 비교")
    ap.add_argument("--out-dir", default="out_repro", help="회차별 결과 폴더의 부모 (기본 out_repro)")
    ap.add_argument("--dry-plan", action="store_true", help="AI 를 부르지 않고 호출 횟수·예상 토큰만 출력")
    ap.add_argument("--compare-only", action="store_true", help="이미 받은 회차 폴더만 비교 (AI 호출 없음)")
    ap.add_argument("--env", default=".env")
    a = ap.parse_args(argv)
    out_dir = Path(a.out_dir)

    if a.compare_only:
        ids = a.ids or sorted({f.name.split("_")[1] for d in run_dirs(out_dir) for f in d.glob("ext01_*_ai_result.json")})
        return report(out_dir, ids, a.student, a.env)
    if not a.ids:
        print("오류: --ids 로 글번호를 하나 이상 주세요.", file=sys.stderr)
        return 2
    if not 2 <= a.runs <= 10:
        print("오류: --runs 는 2~10 사이여야 합니다.", file=sys.stderr)
        return 2
    calls = len(a.ids) * a.runs
    print("AI 호출 {}건 (공고 {} x {}회), 예상 토큰 약 {:,}".format(calls, len(a.ids), a.runs, calls * TOKENS_PER_NOTICE + 0))
    if a.dry_plan:
        return 0
    base = dict(os.environ, AI_SCHEMA_VERSION="3", PYTHONUTF8="1")
    for r in range(1, a.runs + 1):
        folder = out_dir / "run{}".format(r)
        folder.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "run_ext01.py", "--dry-run", "--force", "--only"] + list(a.ids) + ["--dump-dir", str(folder), "--env", a.env]
        print("[{}/{}] {}".format(r, a.runs, " ".join(cmd)))
        rc = subprocess.call(cmd, env=base)
        if rc != 0:
            print("오류: {}회차 추출이 실패했습니다 (종료 코드 {})".format(r, rc), file=sys.stderr)
            return rc
    return report(out_dir, list(a.ids), a.student, a.env)


if __name__ == "__main__":
    sys.exit(main())
