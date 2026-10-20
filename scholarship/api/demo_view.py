# -*- coding: utf-8 -*-
"""[API] /demo 시험 화면의 응답: 추천 결과를 카드 구조로 바꿔 돌려준다 (터미널 card 출력과 같은 규칙, recommend.present). 판정에는 관여하지 않는다."""
from typing import Any, Dict

from ..recommend import present as P


def demo_payload(parse_text: str, result: Dict[str, Any], width: int = 100) -> Dict[str, Any]:
    cards = P.build_cards(result)
    cards["symbols"] = dict(P.SYMBOL)                         # 화면의 기호도 터미널과 같은 표를 쓴다
    cards["kind_label"] = dict(P.KIND_LABEL)
    cards["verdict_title"] = dict(P.VERDICT_TITLE)
    kinds = (("recommended", "충족"), ("conditional", "조건부"), ("excluded", "미달"))
    notices = [{"id": it["id"], "title": it.get("title") or "", "verdict": label} for key, label in kinds for it in result.get(key) or [] if it.get("id")]      # 챗봇에 물어볼 수 있는 공고 (미달 포함, 화면의 공고 선택 칸)
    ids = [n["id"] for n in notices]
    return {"ok": True, "parse": parse_text, "text": P.format_cards(result, show_excluded=True, width=width), "cards": cards, "notice_ids": ids, "notices": notices}
