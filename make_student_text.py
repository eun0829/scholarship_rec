#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""학생 정보 텍스트(`라벨: 값`) 도구. DB·AI 를 쓰지 않는다. 라벨과 허용 값은 항목 사전(config/item_registry.json)에서 나온다.

  python make_student_text.py --template                       # 빈 양식 (값을 채워서 쓰세요)
  python make_student_text.py --template --out 양식.txt
  python make_student_text.py --from-json student.json         # 프로필 JSON 을 규격 텍스트로
  python make_student_text.py --check student.txt              # 텍스트를 읽어 보고서만 출력 (경고, 입력에 없는 항목)
"""
import argparse
import json
import sys

from scholarship.common.registry import RegistryError, load_registry
from scholarship.recommend.profile import ProfileError, build_profile
from scholarship.recommend.student_text import build_template, format_parse_report, parse_student_text, profile_to_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="학생 정보 텍스트 도구")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--template", action="store_true", help="빈 입력 양식 출력")
    g.add_argument("--from-json", help="프로필 JSON 을 규격 텍스트로 변환")
    g.add_argument("--check", help="학생 정보 텍스트를 읽고 보고서 출력")
    ap.add_argument("--out", help="결과를 파일로 저장 (기본: 화면 출력)")
    ap.add_argument("--registry", help="항목 사전 파일 (기본: ITEM_REGISTRY_FILE 또는 config/item_registry.json)")
    a = ap.parse_args(argv)
    try:
        reg = load_registry(a.registry)
        if a.template:
            text = build_template(reg)
        elif a.from_json:
            text = profile_to_text(build_profile(json.load(open(a.from_json, encoding="utf-8"))).profile, reg)
        else:
            parsed = parse_student_text(open(a.check, encoding="utf-8-sig").read(), reg)
            text = format_parse_report(parsed.report) + "\n"
    except (RegistryError, ProfileError) as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print("오류: 입력을 읽을 수 없습니다: {}".format(e), file=sys.stderr)
        return 2
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print("저장: {}".format(a.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
