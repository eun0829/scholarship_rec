# -*- coding: utf-8 -*-
"""실현 가능성 규칙 파일(config/feasibility_rules.json) 검증과 시험.

  python check_feasibility_rules.py                       파일 검증 + 규칙 요약
  python check_feasibility_rules.py "조건 문장"            그 문장이 어떤 등급·이유가 되는지 (규칙으로 못 정하면 'AI 보조 대상')
  옵션: --file 경로  --major ENGINEERING  (비교할 학교·학생 계열, 기본은 파일의 학교_계열)
"""
import argparse
import sys

from scholarship.recommend import feasibility as F
from scholarship.recommend import feasibility_rules as R


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="실현 가능성 규칙 파일 검증·시험")
    ap.add_argument("text", nargs="*", help="시험할 조건 문장 (여러 개 가능)")
    ap.add_argument("--file", default=None, help="규칙 파일 (기본 config/feasibility_rules.json)")
    ap.add_argument("--major", default="", help="비교할 계열 코드 (쉼표 구분, 기본은 파일의 학교_계열)")
    a = ap.parse_args(argv)
    try:
        rules = R.load_rules(a.file)
    except R.RulesError as e:
        print(e)
        return 1
    majors = tuple(m.strip().upper() for m in a.major.split(",") if m.strip()) or rules.school_majors
    print("규칙 파일 정상: {} (버전 {})".format(rules.path, rules.version))
    print("학교 계열: {}  |  기본 등급: {}  |  계열 불일치: {}".format("·".join(rules.major_label(m) for m in majors) or "(없음: 계열 규칙 꺼짐)", rules.label(rules.default_level), rules.label(rules.mismatch_level)))
    if not a.text:
        print("규칙 {}개 (위에서부터 먼저 맞는 것이 적용):".format(len(rules.rules)))
        for r in rules.rules:
            print("  - {} -> {} : {}".format(r.name, rules.label(r.level), r.reason))
        print("계열 키워드: " + ", ".join("{}({}개)".format(f["label"], len(f["keywords"])) for f in rules.families.values()))
        return 0
    for t in a.text:
        r = F.classify_text("확인 필요: " + t, rules, majors)
        if r:
            print("[{}] {}\n    {}".format(rules.label(r[0]), t, r[1]))
        else:
            print("[AI 보조 대상 / 기본 {}] {}".format(rules.label(rules.default_level), t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
