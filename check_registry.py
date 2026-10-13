#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""항목 사전 점검: 오류 검사 + 요약. config/item_registry.json 을 고친 뒤 실행하세요 (DB·AI 를 쓰지 않습니다).

  python check_registry.py                    # 기본 파일(config/item_registry.json) 점검
  python check_registry.py --file 다른.json   # 다른 파일 점검
  python check_registry.py --show-prompt      # v3 공고 구조화 프롬프트의 '항목 사전' 부분과 전체 길이도 출력
"""
import argparse
import sys
from collections import Counter

from scholarship.common import registry as R
from scholarship.common.ai.prompt_v3 import build_system_prompt_v3

TIER_NAME = {1: "필수", 2: "권장", 3: "선택"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="항목 사전 점검")
    ap.add_argument("--file", help="점검할 사전 파일 (기본: ITEM_REGISTRY_FILE 또는 config/item_registry.json)")
    ap.add_argument("--show-prompt", action="store_true", help="v3 프롬프트의 '항목 사전' 부분도 보여줌")
    a = ap.parse_args(argv)
    try:
        reg = R.load_registry(a.file)
    except R.RegistryError as e:
        print("점검 실패: {}".format(e), file=sys.stderr)
        return 1
    items, on = reg.items, reg.enabled()
    tiers = Counter(i.tier for i in on)
    print("항목 사전 점검 통과 ({}) - 버전 {}".format(R.default_path(a.file or ""), reg.version))
    print("  항목 {}개 (사용 {}개, 끈 항목 {}개)".format(len(items), len(on), len(items) - len(on)))
    print("  등급: " + ", ".join("{} {}개".format(TIER_NAME[t], tiers.get(t, 0)) for t in (1, 2, 3)))
    print("  학생이 직접 입력(질문 문구 있음) {}개, 민감 항목 {}개, 공고 필드에 연결 {}개".format(
        sum(1 for i in on if i.source == "student"), sum(1 for i in on if i.sensitive), sum(1 for i in on if i.notice_path)))
    linked = [i for i in on if i.notice_path]
    generic = [i for i in linked if i.op != "custom"]
    shared = Counter(i.custom for i in linked if i.op == "custom")
    print("  비교 방식: 범용 비교(이상·이하·포함) {}개 항목 / 코드로 만든 비교 함수 {}종을 {}개 항목이 나눠 씀".format(
        len(generic), len(shared), sum(shared.values())))
    print("    함수별 항목 수: " + ", ".join("{} {}".format(k, v) for k, v in sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))))
    print("    -> 새 항목이 범용 비교이거나 위 함수 중 하나를 그대로 쓰면 코드 변경 없이 추가됩니다. 새로운 비교 방식이면 함수를 새로 만들어야 합니다.")
    if a.show_prompt:
        prompt = build_system_prompt_v3(reg)
        print("\n[v3 프롬프트] 전체 {}자".format(len(prompt)))
        print("\n".join(reg.prompt_lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
