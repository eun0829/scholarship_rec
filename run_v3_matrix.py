# -*- coding: utf-8 -*-
"""[RECOMMEND] 4-B2 일치 시험 (크레딧 0): 가상 학생 × 공고 × 학교를 엔진으로 판정하고, 원문에서 만든 정답 규칙과 대조한다.

  python run_v3_matrix.py                         # 저장소의 고정 공고 20건 × 학생 30명 × 학교 5개 (data/v3_matrix/)
  python run_v3_matrix.py --notices v3_trial      # 내가 추출한 결과 폴더로 (공고를 다시 추출한 뒤 엔진·프롬프트 변화 확인)
  python run_v3_matrix.py --show all              # 알려진 한계·과소 확신까지 모두
  python run_v3_matrix.py --pair s14:9217         # 한 쌍의 엔진 판정 항목과 정답 근거 (학교는 :시흥 처럼 덧붙임)
  python run_v3_matrix.py --strict                # 심각한 불일치가 있으면 종료 코드 1

분류: 거짓 탈락 / 거짓 통과 / 과신 / 조건부로 통과 / 알려진 한계 / 과소 확신 (설명은 보고서 첫 표)
"""
from __future__ import annotations

import argparse
import sys

from scholarship.recommend import matrix as X
from scholarship.recommend.service import detail_table, evaluation_fields
from scholarship.recommend.table import render_table


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notices", help="공고 폴더 (기본: data/v3_matrix/notices). v3_trial 의 *_v3.json 도 읽는다")
    ap.add_argument("--students", help="학생 파일 (기본: data/v3_matrix/students.txt)")
    ap.add_argument("--schools", help="학교 이름을 쉼표로 (기본: 전체 {})".format(", ".join(X.SCHOOLS)))
    ap.add_argument("--show", choices=("problems", "all"), default="problems", help="problems: 거짓 탈락·통과·과신·조건부 통과 / all: 알려진 한계·과소 확신도")
    ap.add_argument("--pair", help="학생:공고[:학교] 한 쌍의 상세 (예: s14:9217)")
    ap.add_argument("--format", choices=("table", "md"), default="table")
    ap.add_argument("--width", type=int, default=0, help="표의 최대 폭 (기본: 터미널 폭, 알 수 없으면 120)")
    ap.add_argument("--strict", action="store_true", help="심각한 불일치(거짓 탈락·거짓 통과·과신·조건부로 통과)가 있으면 종료 코드 1")
    a = ap.parse_args(argv)
    import shutil
    width = max(60, a.width or shutil.get_terminal_size((120, 24)).columns)
    style = "md" if a.format == "md" else "ascii"
    try:
        notices = X.load_notices(a.notices)
        students = X.load_students(a.students)
    except (OSError, ValueError) as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    names = [s.strip() for s in a.schools.split(",")] if a.schools else None
    unknown = [n for n in names or [] if n not in X.SCHOOLS]
    if unknown:
        print("오류: 알 수 없는 학교 {} (가능: {})".format(", ".join(unknown), ", ".join(X.SCHOOLS)), file=sys.stderr)
        return 2
    if not notices:
        print("오류: 공고가 없습니다 ({})".format(a.notices or "data/v3_matrix/notices"), file=sys.stderr)
        return 2
    pairs = X.run(notices, students, names)
    if a.pair:
        parts = a.pair.split(":")
        sel = [p for p in pairs if p.student.id == parts[0] and p.idx == (parts[1] if len(parts) > 1 else "") and (len(parts) < 3 or p.school == parts[2])]
        if not sel:
            print("오류: 해당하는 쌍이 없습니다 ({}). 형식: 학생:공고[:학교]".format(a.pair), file=sys.stderr)
            return 2
        for p in sel[:1] if len(parts) < 3 else sel:
            print("=== {} × {} ({}) | 학교 {} ===".format(p.student.id, p.idx, p.title, p.school))
            print("학생: " + p.student.desc)
            print("분류: {} | 엔진 {} | 정답 {} {}".format(X.CLASS_LABEL[p.klass], X.VERDICT_KO[p.verdict], X.TRUTH_KO[p.truth.outcome], sorted(p.truth.tags) or ""))
            print(render_table(["정답 근거"], [[r] for r in p.truth.reasons] or [["(모든 조건 충족)"]], max_width=width, style=style))
            item = dict(evaluation_fields(p.evaluation), src_id=p.idx, title=p.title, url=notices[p.idx].get("url"))
            print(detail_table(item, p.evaluation, style=style, width=width))
            if len(parts) < 3 and len({q.school for q in pairs if q.student.id == p.student.id and q.idx == p.idx}) > 1:
                print("(다른 학교의 결과: --pair {}:{}:학교이름)".format(p.student.id, p.idx))
        return 0
    print(X.format_report(pairs, a.show, width, style, notices))
    severe = [p for p in pairs if p.klass in ("FALSE_REJECT", "FALSE_ACCEPT_MATCH", "OVERCONFIDENT", "FALSE_ACCEPT_COND")]
    return 1 if (a.strict and severe) else 0


if __name__ == "__main__":
    sys.exit(main())
