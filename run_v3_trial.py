#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[EXT02] 스키마 v3 시험 추출. 조사한 공고 본문(survey_ext02\\ext02_survey_notices.txt)을 v3 프롬프트로 실제 AI 에 보내고,
제가 읽고 분류한 기대 결과(data/ext02_survey_expected.json)와 항목별로 대조해 보고서를 만든다. DB 에는 저장하지 않는다.
AI 크레딧이 든다 (본문만 보내므로 첨부는 없음). 이미 성공한 공고는 기본으로 건너뛴다.

  python run_v3_trial.py --dry-run          # AI 호출 없이 입력 크기와 예상 크레딧만 확인
  python run_v3_trial.py --first 5          # 1차: 표본 앞 5건만 (응답 길이·형식 확인)
  python run_v3_trial.py                    # 2차: 표본 20건 중 아직 안 한 것
  python run_v3_trial.py --only 9195 9096   # 지정한 공고만 (표본이 아니어도 됨)
  python run_v3_trial.py --force --only 9195   # 이미 한 공고를 다시
  python run_v3_trial.py --report-only      # AI 호출 없이 저장된 결과로 보고서만 다시 만들기
  python run_v3_trial.py --renormalize      # AI 호출 없이 저장된 AI 원문(*_ai_raw.txt)을 현재 코드로 다시 정리하고 보고서 다시 만들기
결과: v3_trial\\ 폴더 (공고별 *_v3.json, *_ai_raw.txt, v3_trial_report.txt, v3_trial_results.json, credits_log.json)
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scholarship.common import registry as R
from scholarship.common.ai.client import AIError, make_client
from scholarship.common.ai.prompt import build_payload
from scholarship.common.ai.runner import parse_json, run_extraction
from scholarship.common.ai.schema import normalize
from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError
from scholarship.sources import ext02_trial as T

log = logging.getLogger("run_v3_trial")
EST_CHARS_PER_TOKEN = 1.7          # 한글 위주 글의 대략적인 값 (추정)
EST_OUT_TOKENS = (3000, 9000)      # v3 응답 길이 추정 범위
EST_CREDITS_PER_1K = (4.0, 7.0)    # 지금까지 측정한 토큰 1,000개당 크레딧 범위 (추정)


def _paths(out: Path, idx: str):
    return out / "{}_v3.json".format(idx), out / "{}_ai_raw.txt".format(idx)


def _load_results(out: Path) -> List[Dict[str, Any]]:
    res = []
    for f in sorted(out.glob("*_v3.json")):
        try:
            res.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            log.warning("읽을 수 없는 결과 파일 무시: %s", f.name)
    return res


def _credit_total(out: Path) -> List[str]:
    """실행마다 기록한 크레딧 변화를 항목별로 합산한다."""
    f = out / "credits_log.json"
    if not f.is_file():
        return []
    total: Dict[str, float] = {}
    for run in json.loads(f.read_text(encoding="utf-8")):
        for k, v in run.get("delta", {}).items():
            total[k] = total.get(k, 0.0) + v
    return ["{}: {:+.2f}".format(k, v) for k, v in sorted(total.items())]


def ai_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    """AI 가 쓴 결과 중 원인 분석에 필요한 부분(면제, 유형, 검수 표시, 신분, 제외 조건 등)만 모은 요약."""
    return {"variants": [{"name": v.get("name"), "apply_end": v.get("apply_end"), "special": [s.get("code") for s in v.get("special_groups") or []]}
                         for v in d.get("variants") or []],
            "waivers": d.get("waivers") or [], "special_groups": d.get("special_groups") or [], "exclusion_items": d.get("exclusion_items") or [],
            "prerequisites": d.get("prerequisites") or [], "enrollment": d.get("enrollment"), "nationality": d.get("nationality"),
            "apply": {k: d.get(k) for k in ("apply_type", "apply_start", "apply_end", "org_deadline", "deadline_kind")},
            "region": {"mode": (d.get("region") or {}).get("mode"), "notes": (d.get("region") or {}).get("notes")},
            "notes": {k: (d.get(k) or {}).get("notes") for k in ("gpa", "credits", "income") if (d.get(k) or {}).get("notes")},
            "uncertain": d.get("uncertain") or []}


def build_report(out: Path, expected, names, registry) -> str:
    results = []
    for r in _load_results(out):
        d = r.get("data") or {}
        r["compare"] = T.compare_one(expected.get(r["idx"], set()), d, registry) if r.get("ok") else {}
        r["unmodeled"] = len(d.get("unmodeled_conditions") or [])
        r["region_mode_unknown"] = (d.get("region") or {}).get("mode") == "UNKNOWN"
        r["uncertain_items"] = [u for u in (d.get("uncertain") or []) if u.get("level") == "verify"]
        r["uncertain_verify"] = len(r["uncertain_items"])
        results.append(r)
    results.sort(key=lambda r: r["idx"], reverse=True)
    text = T.summarize(results, names, registry, _credit_total(out))
    (out / "v3_trial_report.txt").write_text(text, encoding="utf-8")
    compact = [{"idx": r["idx"], "title": r.get("title"), "ok": r.get("ok"), "error": r.get("error"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "attempts": r.get("attempts"), "issues": r.get("issues"),
                "compare": r.get("compare"), "unmodeled_conditions": (r.get("data") or {}).get("unmodeled_conditions"),
                "prompt_sha": r.get("prompt_sha"), "ai": ai_summary(r.get("data") or {})} for r in results]
    (out / "v3_trial_results.json").write_text(json.dumps(compact, ensure_ascii=False, indent=1), encoding="utf-8")
    return text


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="스키마 v3 시험 추출 (조사한 공고 본문 -> AI -> 기대 결과와 대조)")
    ap.add_argument("--notices", default="survey_ext02/ext02_survey_notices.txt", help="조사 도구가 만든 공고 본문 파일")
    ap.add_argument("--out", default="v3_trial", help="결과 폴더")
    ap.add_argument("--first", type=int, help="표본 앞에서 N건만")
    ap.add_argument("--only", nargs="+", help="지정한 공고(idx)만 (표본이 아니어도 됨)")
    ap.add_argument("--force", action="store_true", help="이미 성공한 공고도 다시 (크레딧이 다시 듦)")
    ap.add_argument("--dry-run", action="store_true", help="AI 를 호출하지 않고 입력 크기와 예상 크레딧만 보여줌")
    ap.add_argument("--report-only", action="store_true", help="AI 호출 없이 저장된 결과로 보고서만 만듦")
    ap.add_argument("--renormalize", action="store_true", help="AI 호출 없이 저장된 AI 원문을 현재 코드로 다시 정리하고 보고서를 다시 만듦")
    ap.add_argument("--registry", default="", help="항목 사전 파일 (기본: ITEM_REGISTRY_FILE 또는 config/item_registry.json)")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    ap.add_argument("--log-file")
    a = ap.parse_args(argv)

    handlers = [logging.StreamHandler()]
    if a.log_file:
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers, force=True)

    try:
        bootstrap(a.secret_file, a.env)
        settings = Settings.from_env()
        registry = R.load_registry(a.registry or settings.item_registry_file or None)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    except R.RegistryError as e:
        log.error("항목 사전 오류: %s", e)
        return 2
    registry_file = a.registry or settings.item_registry_file
    expected, names = T.load_expected()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def payload_for(n: T.TrialNotice):
        try:
            ref = date.fromisoformat(n.period_start) if n.period_start else date.today()     # 신청 시작일을 [기준일]로 (지난 공고의 마감을 '이미 지남'으로 오해하지 않게)
        except ValueError:
            ref = date.today()
        return build_payload(ref, n.title, None, n.org, n.body, [], [], [], settings.max_chars_per_file, settings.max_total_chars,
                             schema_version=3, registry_file=registry_file)

    if a.renormalize:
        nf0 = Path(a.notices)
        known = {n.idx: n for n in T.parse_notice_file(nf0.read_text(encoding="utf-8-sig"))} if nf0.is_file() else {}
        redone = 0
        for jf in sorted(out.glob("*_v3.json")):
            rec = json.loads(jf.read_text(encoding="utf-8"))
            rawf = out / "{}_ai_raw.txt".format(rec.get("idx"))
            if not rec.get("ok") or not rawf.is_file():
                continue
            obj = parse_json(rawf.read_text(encoding="utf-8"))
            if obj is None:
                continue
            try:
                data, issues = normalize(obj, None, payload_for(known[rec["idx"]]).source_text if rec["idx"] in known else "", version=3)
            except ValueError:
                continue
            rec.update(data=data, issues=issues, renormalized=True)
            jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            redone += 1
        print("{}건을 저장된 AI 원문에서 다시 정리했습니다 (AI 호출·크레딧 없음).\n".format(redone))
        print(build_report(out, expected, names, registry))
        return 0 if redone else 1

    if a.report_only:
        if not _load_results(out):
            print("{} 에 저장된 결과가 없습니다.".format(out), file=sys.stderr)
            return 1
        print(build_report(out, expected, names, registry))
        print("보고서: {}".format(out / "v3_trial_report.txt"))
        return 0

    nf = Path(a.notices)
    if not nf.is_file():
        print("공고 본문 파일을 찾을 수 없습니다: {} (python run_ext02_survey.py 로 만든 파일)".format(nf), file=sys.stderr)
        return 2
    notices = {n.idx: n for n in T.parse_notice_file(nf.read_text(encoding="utf-8-sig"))}
    order = list(a.only) if a.only else list(T.DEFAULT_SAMPLE)
    missing = [i for i in order if i not in notices]
    if missing:
        log.warning("공고 본문 파일에 없는 idx 는 제외: %s", ", ".join(missing))
    order = [i for i in order if i in notices]
    if a.first:
        order = order[:a.first]
    done = set()
    if not a.force:
        for i in order:
            p, _ = _paths(out, i)
            if p.is_file():
                try:
                    if json.loads(p.read_text(encoding="utf-8")).get("ok"):
                        done.add(i)
                except ValueError:
                    pass
    todo = [i for i in order if i not in done]
    log.info("대상 %d건 중 이미 성공 %d건 건너뜀, 이번에 처리 %d건", len(order), len(done), len(todo))

    if a.dry_run:
        tin_sum = 0
        print("\n=== dry-run: AI 를 호출하지 않았습니다 (아래는 추정치) ===")
        print("  {:<6}{:<34}{:>8}{:>12}".format("idx", "제목", "본문(자)", "입력 토큰"))
        for i in todo:
            p = payload_for(notices[i])
            tin = int((len(p.system) + len(p.user_text)) / EST_CHARS_PER_TOKEN)
            tin_sum += tin
            print("  {:<6}{:<34}{:>8,}{:>12,}".format(i, notices[i].title[:16], len(notices[i].body), tin))
        n = len(todo)
        if todo:
            sys_chars = len(payload_for(notices[todo[0]]).system)
            body_avg = sum(len(notices[i].body) for i in todo) / n
            print("\n  시스템 프롬프트 {:,}자 vs 공고 본문 평균 {:,.0f}자 -> 건당 입력의 약 {:.0f}% 가 매번 같은 프롬프트입니다.".format(
                sys_chars, body_avg, 100.0 * sys_chars / (sys_chars + body_avg)))
            print("  (이 클라이언트는 프롬프트 캐시를 요청하므로 게이트웨이가 지원하면 실제 소모는 아래 추정보다 낮을 수 있습니다. 1차 5건으로 실측하세요.)")
        lo = (tin_sum + EST_OUT_TOKENS[0] * n) / 1000 * EST_CREDITS_PER_1K[0]
        hi = (tin_sum + EST_OUT_TOKENS[1] * n) / 1000 * EST_CREDITS_PER_1K[1]
        print("\n  처리할 공고 {}건 | 입력 약 {:,} 토큰 + 출력 약 {:,}~{:,} 토큰".format(n, tin_sum, EST_OUT_TOKENS[0] * n, EST_OUT_TOKENS[1] * n))
        print("  예상 크레딧 약 {:,.0f} ~ {:,.0f} (지금까지 측정한 토큰당 소모율 기준의 추정이며, 실제 값은 실행 후 보고서에 나옵니다)".format(lo, hi))
        print("  예상 소요: 공고당 1~3분 -> 약 {}~{}분".format(n, n * 3))
        return 0

    if not todo:
        print("처리할 공고가 없습니다 (모두 성공). 보고서만 다시 만듭니다.")
        print(build_report(out, expected, names, registry))
        return 0
    if client is None:
        if not settings.ai_api_key:
            print("오류: AI_API_KEY 가 없습니다. python set_secret.py AI_API_KEY 로 저장하세요.", file=sys.stderr)
            return 2
        try:
            client = make_client(settings)
        except (AIError, ValueError) as e:
            print("오류: AI 클라이언트를 만들 수 없습니다: {}".format(e), file=sys.stderr)
            return 2
        before = T.credit_snapshot(settings)
    else:
        before = None

    fail = 0
    for k, i in enumerate(todo, 1):
        n = notices[i]
        p = payload_for(n)
        t0 = time.time()
        res = run_extraction(client, p, None, tokens_limit=settings.ai_max_tokens_limit)
        el = time.time() - t0
        jf, rf = _paths(out, i)
        rf.write_text(res.raw_text or "", encoding="utf-8")
        rec = {"idx": i, "title": n.title, "state": n.state, "org": n.org, "url": T.dreamspon_view_url(i, os.environ.get("EXT02_BASE_URL") or None), "ok": bool(res.ok), "error": res.error, "model": res.model,
               "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "attempts": res.attempts, "stop_reason": res.stop_reason,
               "elapsed_sec": round(el, 1), "issues": res.issues if res.ok else [], "data": res.data if res.ok else None,
               "prompt_sha": hashlib.sha1(p.system.encode("utf-8")).hexdigest()[:8]}
        jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        if not res.ok:
            fail += 1
        log.info("[%d/%d] %s %s | %s | 토큰 %s/%s | 시도 %d회 | 이슈 %d | %.0f초", k, len(todo), i, n.title[:20], "성공" if res.ok else "실패: " + str(res.error)[:50],
                 res.input_tokens, res.output_tokens, res.attempts, len(res.issues or []), el)

    if before is not None:
        after = T.credit_snapshot(settings)
        delta = {}
        if before and after:
            delta = {k: after[k] - before[k] for k in after if k in before and abs(after[k] - before[k]) > 1e-9}
            for line in T.credit_diff(before, after):
                log.info("크레딧 변화: %s", line)
        logf = out / "credits_log.json"
        runs = json.loads(logf.read_text(encoding="utf-8")) if logf.is_file() else []
        runs.append({"when": datetime.now().isoformat(timespec="seconds"), "count": len(todo), "delta": delta})
        logf.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + build_report(out, expected, names, registry))
    remaining = [i for i in (T.DEFAULT_SAMPLE if not a.only else a.only) if i in notices and not _paths(out, i)[0].is_file()]
    if remaining and not a.only:
        print("다음: 아직 안 한 표본 {}건이 있습니다. python run_v3_trial.py 로 이어서 실행하세요.".format(len(remaining)))
    else:
        print("올려주실 파일: {}  와  {}".format(out / "v3_trial_report.txt", out / "v3_trial_results.json"))
    return 1 if fail == len(todo) else 0


if __name__ == "__main__":
    sys.exit(main())
