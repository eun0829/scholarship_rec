# -*- coding: utf-8 -*-
"""[RECOMMEND] 결과를 사람이 읽기 좋게 보여 주는 공통 규칙 (터미널 card 출력과 /demo 화면이 같이 쓴다).

판정과 JSON 응답은 건드리지 않는다. 포털이 받는 `to_confirm` 문구("확인 필요: …", "제외 조건 확인: …")는 그대로 두고,
보여 줄 때만 앞머리를 해석해 기호·라벨로 바꾸고, 유형별(`[학업장려]` 등) 항목은 유형 소제목 아래로 묶는다.

기호는 구형 한국어 Windows 콘솔(cp949)에서도 깨지지 않는 KS X 1001 기호만 쓴다 (✔ ✖ ⚠ 와 이모지는 쓰지 않는다).
터미널 표는 기호 폭(콘솔마다 1칸/2칸) 때문에 오른쪽 테두리가 어긋나지 않도록, 카드 안은 오른쪽 테두리 없이 항목/내용으로 정렬한다.
"""
from __future__ import annotations

import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from .table import display_width, pad, render_table

CONFIRM, EXCLUDE, WARN, NOTE = "CONFIRM", "EXCLUDE", "WARN", "NOTE"
SYMBOL = {CONFIRM: "□", EXCLUDE: "▲", WARN: "△", NOTE: "※", "LINK": "▶", "VARIANT": "◇", "match": "●", "cond": "◎", "no": "○", "BENEFIT": "◆"}
KIND_LABEL = {CONFIRM: "확인 필요", EXCLUDE: "제외 조건", WARN: "주의", NOTE: "참고"}
VERDICT_TITLE = {"match": "충족", "cond": "조건부", "no": "미달"}
_EXCLUDE_HEAD, _CONFIRM_HEAD = "제외 조건 확인:", "확인 필요:"
MAX_NOTES_TERMINAL = 5
MAX_CONFIRM_TERMINAL = 12


def split_message(msg: str) -> Tuple[str, str]:
    """판정 문구의 앞머리를 해석한다: ('EXCLUDE'|'CONFIRM', 본문). 앞머리가 없으면 문구 전체를 확인 필요로 본다."""
    s = (msg or "").strip()
    if s.startswith(_EXCLUDE_HEAD):
        return EXCLUDE, s[len(_EXCLUDE_HEAD):].strip()
    if s.startswith(_CONFIRM_HEAD):
        return CONFIRM, s[len(_CONFIRM_HEAD):].strip()
    return CONFIRM, s


def _belongs_to(text: str, names: List[str], keep_names: List[str]) -> bool:
    """'유형이름: 조건' 꼴 문장이 names 중 한 유형의 것이고 keep_names 의 유형 것은 아닌가."""
    from ..common.variant_scope import GENERIC_PREFIXES, VARIANT_PREFIX, prefix_matches
    m = VARIANT_PREFIX.match(text or "")
    pre = re.sub(r"^제한:\s*", "", m.group(1).strip()) if m else ""
    if not m:
        m2 = re.match(r"^\s*제한:\s*([^:：]{1,30}?)\s*[:：]", text or "")
        pre = m2.group(1).strip() if m2 else ""
    if not pre or re.sub(r"\s+", "", pre) in GENERIC_PREFIXES:
        return False
    sq = lambda xs: {re.sub(r"\s+", "", x) for x in xs}
    npre = re.sub(r"\s+", "", pre)
    if npre in sq(names) and npre not in sq(keep_names):                                      # 이름이 정확히 같으면 괄호 앞 공통 이름 때문에 같은 유형으로 보지 않는다
        return True
    return any(prefix_matches(pre, {n}) for n in sq(names)) and not any(prefix_matches(pre, {n}) for n in sq(keep_names))


def _hidden_msg(text: Any, gone: List[str], keep: List[str]) -> bool:
    if not isinstance(text, str):
        return False
    if _belongs_to(text, gone, keep):
        return True
    m = re.match(r"^[^:：\[]{1,14}[:：]\s*(.+)$", text)
    return bool(m) and _belongs_to(m.group(1), gone, keep)


def hide_unmatched(it: Dict[str, Any]) -> Dict[str, Any]:
    """충족·조건부 카드에서 해당하지 않는 유형을 화면에서 숨긴다 (유형 표·조건 표·확인사항·혜택 제한).
    해당하는 유형이 하나도 없는 카드(미달)는 사유를 보여야 하므로 그대로 둔다. 결과 JSON 은 바꾸지 않고 카드용 사본만 만든다.
    variant_total 에 원래 유형 수를 남겨서 유형이 하나만 남아도 어느 유형인지 보여 줄 수 있게 한다."""
    ms, us = it.get("matched_variants") or [], it.get("unmatched_variants") or []
    if not ms or not us:
        return it
    from ..common.config import env_bool
    if env_bool("CARD_SHOW_ALL_VARIANTS", False):                       # 시험용: 해당하지 않는 유형도 모두 보인다 (.env 의 CARD_SHOW_ALL_VARIANTS=true)
        return it
    gone, keep = [v["name"] for v in us], [v["name"] for v in ms]
    out = dict(it, unmatched_variants=[], variant_total=len(ms) + len(us))
    out["notes"] = [n for n in it.get("notes") or [] if not _belongs_to(n.get("text") or "", gone, keep)]
    for key in ("to_confirm", "passed", "self_confirm", "fail_reasons"):                      # "확인 필요: 유형: 조건" 처럼 종류 머리말이 앞에 붙은 문장도 거른다
        if it.get(key):
            out[key] = [t for t in it[key] if not _hidden_msg(t, gone, keep)]
    ben = it.get("benefit")
    if isinstance(ben, dict):
        out["benefit"] = dict(ben, limits=[t for t in ben.get("limits") or [] if not _belongs_to(t, gone, keep)])
    return out


def confirm_groups(it: Dict[str, Any]) -> List[Dict[str, Any]]:
    """확인 필요 항목을 묶는다: 공고 전체(공통) 먼저, 그다음 해당 가능한 유형별. 유형 이름은 문장 앞 `[이름]` 이 아니라 소제목으로 올린다."""
    groups: List[Dict[str, Any]] = []
    common = [dict(zip(("kind", "text"), split_message(m))) for m in it.get("to_confirm") or []]
    if common:
        groups.append({"name": None, "verdict": None, "items": common})
    for v in it.get("matched_variants") or []:
        rows = [dict(zip(("kind", "text"), split_message(m))) for m in v.get("to_confirm") or []]
        groups.append({"name": v.get("name"), "verdict": v.get("verdict_label"), "items": rows})
    return groups


def _confirm_count(groups: List[Dict[str, Any]]) -> int:
    return sum(len(g["items"]) for g in groups)


def _side(it: Dict[str, Any]) -> List[Dict[str, str]]:
    """판정에 영향 없는 참고 줄: 선행 조건 · 우대 조건 · 학교 사업."""
    from . import service as S
    rows: List[Dict[str, str]] = []
    if it.get("prerequisites"):
        rows.append({"label": "선행 조건", "text": " / ".join("{} ({})".format(x["label"], S.PREREQ_STATUS[x["status"]]) for x in it["prerequisites"])})
    prefs = [("{}".format(x["label"]), x["applies"]) for x in it.get("preferences") or []]
    prefs += [("{} - {}".format(v["name"], x["label"]), x["applies"]) for v in it.get("matched_variants") or [] for x in v.get("preferences") or []]
    if prefs:
        rows.append({"label": "우대 조건", "text": " / ".join("{} ({})".format(lb, S.APPLIES_LABEL[ap]) for lb, ap in prefs)})
    if it.get("school_programs"):
        rows.append({"label": "학교 사업", "text": " / ".join("{} ({})".format(x["label"], S.PROGRAM_STATUS[x["school"]]) for x in it["school_programs"])})
    return rows


_MET = re.compile(r"^(.*?)\s*(?:충족|해당)\s*(?:\((.*)\))?$")
_PAREN = re.compile(r"^(.*?)\s*\((.*)\)\s*$")
_NEED = re.compile(r"^(.*?)\s*\((?:필요|공고 대상):\s*(.*)\)\s*$")
RESULT_LABEL = {"fail": "미달", "met": "충족", "confirm": "확인 필요", "exclude": "제외 조건", "": "-"}


def parse_passed(sentence: str) -> Tuple[str, str]:
    """충족 문장 -> (조건, 내 상태): '성적 충족 (평점 3.9/4.5, 기준 3.5 이상)' -> ('성적', '평점 3.9/4.5, 기준 3.5 이상'). 모양을 모르면 전체를 조건으로 둔다."""
    t = (sentence or "").strip()
    m = _MET.match(t)
    if m and m.group(1):
        return m.group(1).strip(), (m.group(2) or "").strip()
    m = _PAREN.match(t)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return t, ""


def parse_confirm(text: str) -> Tuple[str, str]:
    """확인 필요 문구 -> (조건, 내 상태): '학교 종류 설정이 없음 (공고 대상: 4년제 대학)' -> ('4년제 대학', '학교 종류 설정이 없음'). 모양을 모르면 전체를 조건으로 둔다."""
    t = (text or "").strip()
    m = _NEED.match(t)
    if m and m.group(2):
        return m.group(2).strip(), m.group(1).strip() or "정보 없음"
    return t, "직접 확인"


NO_VARIANT_HEAD = "해당하는 유형이 없음:"              # matcher 가 모든 유형이 미달일 때 붙이는 문구 앞머리


_SIDO = re.compile(r"(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)\s+(?=\S+[시군구])")


def compact_region(text: str) -> str:
    """지역 문장을 짧게: 대괄호를 풀고, 시·군·구가 이어지는 시·도 이름은 뺀다 ('강원특별자치도 춘천시' -> '춘천시')."""
    return _SIDO.sub("", re.sub(r"[\[\]]", "", text or ""))


def _group_fail_rows(variants: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """모든 유형이 미달일 때: 사유가 같은 유형은 한 줄로 묶는다 ('A, B 외 N개' + 사유 한 번). 사유 순서는 처음 나온 순서."""
    order: List[str] = []
    by: Dict[str, List[str]] = {}
    for v in variants:
        for reason in v.get("reasons") or [v.get("reason") or "미달"]:
            if reason not in by:
                by[reason] = []
                order.append(reason)
            if v["name"] not in by[reason]:
                by[reason].append(v["name"])
    rows = []
    for reason in order:
        names = by[reason]
        label = ", ".join(names) if len(names) <= 2 else "{} 외 {}개".format(", ".join(names[:2]), len(names) - 2)
        rows.append({"cond": "[{}] {}".format(label, reason), "state": "기준에 맞지 않음", "result": "fail", "variant": label})
    return rows


def condition_table(it: Dict[str, Any], groups: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """'조건' 표의 행: met(충족) / open(확인 필요·제외 조건) / info(해당 유형·선행·우대·학교 사업 같은 참고 줄)."""
    fail = []
    for r in it.get("fail_reasons") or []:
        if r.startswith(NO_VARIANT_HEAD) and it.get("unmatched_variants"):                 # 유형마다 탈락 사유를 보이되 사유가 같은 유형은 한 줄로 묶는다
            fail += _group_fail_rows(it["unmatched_variants"])
        else:
            fail.append({"cond": r, "state": "기준에 맞지 않음", "result": "fail"})
    met = []
    for sen in it.get("passed") or []:
        c, st = parse_passed(sen)
        met.append({"cond": c, "state": st, "result": "met"})
    opened = []
    for g in groups:
        for i in g["items"]:
            c, st = parse_confirm(i["text"])
            if g.get("name"):
                c = "[{}] {}".format(g["name"], c)
            opened.append({"cond": c, "state": st if i["kind"] != EXCLUDE else "해당 여부 확인", "result": "exclude" if i["kind"] == EXCLUDE else "confirm",
                           "variant": g.get("name") or "", "text": parse_confirm(i["text"])[0]})
    for row in fail + opened:
        if "지역" in row["cond"]:
            pre = "[{}] ".format(row.get("variant") or "")
            row["cond"] = pre + compact_region(row["cond"][len(pre):]) if row.get("variant") and row["cond"].startswith(pre) else compact_region(row["cond"])
    merged: List[Dict[str, str]] = []                                                      # 같은 유형의 미달 사유 여러 개는 한 줄로 묶는다
    for row in fail:
        prev = next((m for m in merged if row.get("variant") and m.get("variant") == row["variant"] and m["result"] == "fail"), None)
        pre = "[{}] ".format(row.get("variant") or "")
        if prev is not None and row["cond"].startswith(pre):
            prev["cond"] += "; " + row["cond"][len(pre):]
        else:
            merged.append(dict(row))
    fail = merged
    rows_open = fail + opened
    rows_open = [r for r in rows_open if not r.get("variant")] + [r for r in rows_open if r.get("variant")]     # 공통 조건이 맨 위, 유형별은 그 아래
    info = []
    ms, us = it.get("matched_variants") or [], it.get("unmatched_variants") or []
    if ms or us:
        vs = ["{}({}{})".format(v["name"], v.get("verdict_label") or "", ", ~" + v["apply_end"][5:] if v.get("apply_end") else "") for v in ms]
        txt = " / ".join(vs)
        if us:
            txt += (" · " if txt else "") + "미달 유형: " + ", ".join(v["name"] for v in us)
        info.append({"cond": "해당 유형", "state": txt, "result": ""})                     # 터미널 카드용 요약 한 줄
    if len(ms) >= 2 or (ms and us):                                                        # 웹 카드: 유형이 둘 이상 남았으면 유형마다 결과 줄 (유형 표에는 결과·사유 열이 없다). web=True 줄은 터미널에서 건너뜀
        listed = {r["variant"] for r in fail if r.get("variant")}
        for v in ms:
            n = len(v.get("to_confirm") or [])
            state = (v.get("verdict_label") or "") + (" (확인 {}개)".format(n) if v.get("verdict") != "MATCH" and n else "")
            info.append({"cond": "[{}] 해당 여부".format(v["name"]), "state": state, "result": "met" if v.get("verdict") == "MATCH" else "confirm", "variant": v["name"], "web": True})
        for v in us:
            if v["name"] not in listed:
                for reason in v.get("reasons") or [v.get("reason") or "미달"]:
                    info.append({"cond": "[{}] {}".format(v["name"], compact_region(reason)), "state": "기준에 맞지 않음", "result": "fail", "variant": v["name"], "web": True})
    info += [{"cond": r["label"], "state": r["text"], "result": ""} for r in _side(it)]
    return {"met": met, "open": rows_open, "info": info}


def _label_for(row: Dict[str, Any], it: Dict[str, Any]) -> str:
    """조건 행의 문장과 같은 문장에 추출 때 붙은 이름(condition_labels)이 있으면 그 이름. 없으면 빈 문자열."""
    text = re.sub(r"\s+", "", row.get("text") or row.get("cond") or "")
    if not text:
        return ""
    for x in it.get("condition_labels") or []:
        t = re.sub(r"\s+", "", x.get("text") or "")
        if t and (t == text or t in text or text in t):
            return x.get("label") or ""
    return ""


def summary_groups(it: Dict[str, Any], rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """service 가 붙인 condition_summary 를 화면용 그룹으로. 행 인덱스가 현재 조건 행과 맞지 않는 그룹은 건너뛴다."""
    from . import feasibility_rules as FR
    rules = FR.current()
    out: List[Dict[str, Any]] = []
    for g in (it.get("condition_summary") or {}).get("groups") or []:
        idx = [i for i in g.get("rows") or [] if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(rows)]
        if not idx:
            continue
        lv = g.get("level") or ""
        title = g.get("title") or "조건"
        names = {_label_for(rows[i], it) for i in idx}
        if len(names) == 1 and next(iter(names)):                       # 행이 모두 같은 이름이면 추출 때 AI 가 붙인 이름을 쓴다 (코드가 고른 주제 이름보다 정확하다)
            title = next(iter(names))
        out.append({"title": title, "summary": g.get("summary") or "", "quote": g.get("quote") or "", "level": lv,
                    "level_label": rules.label(lv) if lv in ("HARD", "VERY_HARD") else "",
                    "result": "exclude" if any(rows[i]["result"] == "exclude" for i in idx) else "confirm", "count": len(idx)})
    return out


def row_groups(it: Dict[str, Any], rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """AI 요약이 없는 카드(미달)의 조건 표: 조건 행마다 한 줄씩 조건 / 결과 / 내용 / 원문. 미달 행은 '지역 조건 미달 (필요: …)' 을 이름과 내용으로 나눈다."""
    from . import condition_summary as CS
    out: List[Dict[str, Any]] = []
    for r in rows:
        text = (r.get("cond") or "").strip()
        if not text:
            continue
        title, summary, res = _label_for(r, it), text, r.get("result") or "confirm"
        m = re.match(r"^(.*?)\s*미달\s*\((.*)\)\s*(.*)$", text) if res == "fail" else None
        if m:
            title = title or re.sub(r"\s*조건$", "", m.group(1)).strip()
            summary = re.sub(r"^필요:\s*", "", m.group(2)).strip()
        out.append({"title": title or CS._topic(text), "summary": summary, "quote": text if text != summary else "", "level": "", "level_label": "", "result": res, "count": 1})
    return out


MAX_CHECKS = 3


_SENTENCE_HINTS = ("않", "해야", "예정", "하여", "경우", "에 한해", "에한해", "때문", "바랍니다", "하며", "하고", "시 ", "접수", "우편", "방문", "이메일", "온라인", "신청방법", "신청 방법")
_SENTENCE_ENDS = ("다", "음", "함", "요", "됨", "예정", "업로드", "제출", "요청")


def is_document_name(text: str) -> bool:
    """서류 이름(예: '장학원서', '한국장학재단 소득분위확인서')인가, 아니면 문장(예: '구비서류 누락시 선발되지 않음')인가.
    문장으로 보이면 합치지 않고 그대로 둔다 (잘못 합쳐 의미가 사라지는 것보다 길어도 그대로 두는 쪽이 안전)."""
    name = re.split(r"\s*[\(（]", (text or "").strip(), maxsplit=1)[0].strip()
    if not name or len(name) > 24 or "," in name or "，" in name:
        return False
    if any(h in name for h in _SENTENCE_HINTS) or name.endswith(_SENTENCE_ENDS):
        return False
    return True


def merge_documents(docs: List[str], label: str = "") -> List[str]:
    """서류 이름들을 한 줄로: '장학원서, 소득분위확인서' (label 을 주면 '유형 - 서류: …' 처럼 앞에 붙인다). 없으면 빈 목록. 같은 이름은 한 번만."""
    seen, names = set(), []
    for d in docs:
        key = re.sub(r"\s+", "", d)
        if key and key not in seen:
            seen.add(key)
            names.append(d.strip())
    return [("{}: {}".format(label, ", ".join(names)) if label else ", ".join(names))] if names else []


def _common_documents_and_checks(it: Dict[str, Any], rules: Any = None) -> Tuple[List[str], List[str]]:
    """공통 노트를 (서류 이름 목록, 확인사항 문장 목록)으로 나눈다. 흔한 서류는 뺀다. 추천서가 이미 '추천 필요'로 나오면 서류 이름에서 추천서는 뺀다."""
    if rules is None:
        from . import feasibility_rules as FR
        rules = FR.current()
    notes = it.get("notes") or []
    has_rec = any(n.get("kind") == "RECOMMENDATION" for n in notes)
    docs: List[str] = []
    checks: List[str] = []
    for n in notes:
        if n.get("kind") == "RECOMMENDATION":
            checks.append(n["text"])
        elif n.get("kind") == "PROCEDURE":
            t = n["text"]
            name = re.split(r"\s*[\(（]", t, maxsplit=1)[0]                              # 서류 이름만 본다 ('재학증명서 (2차 제출(면접 시))' 의 괄호 설명은 제외)
            if any(w in name for w in rules.doc_skip) and not any(w in name for w in rules.doc_keep):
                continue
            if is_document_name(t):
                if not (has_rec and "추천서" in name):
                    docs.append(t)
            else:
                checks.append(t)
    return docs, checks


def compact_notes(it: Dict[str, Any], rules: Any = None) -> List[str]:
    """확인사항: 추천·절차·안내 문장과 본인 확인. 서류 이름은 document_lines 로 따로 보인다. 기준은 config/feasibility_rules.json."""
    _, checks = _common_documents_and_checks(it, rules)
    return checks + ["본인 확인: " + t for t in it.get("self_confirm") or []]            # 신청할 때 학생이 스스로 확인하는 조건 (판정에는 영향 없음)


def document_lines(it: Dict[str, Any], rules: Any = None, per_variant: bool = True) -> List[str]:
    """'제출 서류' 행: 모든 유형이 내는 서류 한 줄 + (per_variant 이면) 유형마다 내는 서류 '유형 - 서류 이름들' 한 줄씩."""
    docs, _ = _common_documents_and_checks(it, rules)
    out = merge_documents(docs)
    if per_variant:
        for v in it.get("matched_variants") or []:
            names = _variant_doc_names(v, rules)
            out += ["{} - {}".format(v["name"], ", ".join(names))] if names else []
    return out


def _variant_doc_names(v: Dict[str, Any], rules: Any = None) -> List[str]:
    if rules is None:
        from . import feasibility_rules as FR
        rules = FR.current()
    out: List[str] = []
    for d in v.get("documents") or []:
        name = re.split(r"\s*[\(（]", d, maxsplit=1)[0]
        if any(w in name for w in rules.doc_skip) and not any(w in name for w in rules.doc_keep):
            continue
        if is_document_name(d) and d.strip() not in out:
            out.append(d.strip())
    return out


def _criteria_text(v: Dict[str, Any]) -> str:
    """유형의 선발 기준 칸: 추천자와 선발 기준을 한 칸에 ('추천: 총장 · 성적 우수자 우선')."""
    parts = (["추천: " + v["recommender"]] if v.get("recommender") else []) + [c for c in v.get("criteria") or [] if isinstance(c, str) and c]
    return " · ".join(parts)


def variant_rows(it: Dict[str, Any]) -> List[Dict[str, str]]:
    """유형이 둘 이상인 공고의 유형별 결과 줄: 해당 가능/확인 필요/미달과 금액·사유. 유형이 하나뿐이면 보여 줄 것이 없다."""
    ms, us = it.get("matched_variants") or [], it.get("unmatched_variants") or []
    if (it.get("variant_total") or len(ms) + len(us)) < 2:                       # 숨긴 유형이 있어도 원래 유형이 둘 이상이면 남은 유형을 보여 준다
        return []
    out: List[Dict[str, str]] = []
    for v in ms:
        n = len(v.get("to_confirm") or [])
        state = v.get("verdict_label") or ""
        if v.get("verdict") != "MATCH" and n:
            state += " (확인 {}개)".format(n)
        parts = [state] + ([str(v["amount"])] if v.get("amount") else []) + (["~" + v["apply_end"][5:]] if v.get("apply_end") else [])
        out.append({"name": v["name"], "result": "met" if v.get("verdict") == "MATCH" else "confirm", "text": " · ".join(parts),
                    "state": v.get("verdict_label") or "", "amount": str(v.get("amount") or ""), "end": (v.get("apply_end") or "")[5:], "note": "확인 {}개".format(n) if v.get("verdict") != "MATCH" and n else "",
                    "evaluation": _eval_text(v.get("evaluation")), "criteria": _criteria_text(v), "docs": ", ".join(_variant_doc_names(v))})
    for v in us:
        out.append({"name": v["name"], "result": "fail", "text": "미달" + (" - " + v["reason"] if v.get("reason") else ""),
                    "state": "미달", "amount": str(v.get("amount") or ""), "end": "", "note": v.get("reason") or "",
                    "evaluation": _eval_text(v.get("evaluation")), "criteria": _criteria_text(v), "docs": ""})
    return out


MAX_METHOD_LINES = 3                                         # 선발 방식 줄은 최대 3묶음까지 (나머지는 한 줄로 접음)


def _eval_text(ev: List[Dict[str, Any]]) -> str:
    return " · ".join("{} {}%".format(e.get("item"), e.get("percent")) for e in ev or [] if isinstance(e, dict) and e.get("item"))


def selection_rows(it: Dict[str, Any], per_variant: bool = True) -> Dict[str, Any]:
    """선발 방식(평가 비율·추천자)과 진행 절차를 핵심만 한 줄씩 요약한다. 값이 있는 것만 내고, 하나도 없으면 빈 dict.
    해당 가능한 유형(matched_variants)만 보며, 유형 값이 모두 같으면 이름 없이 한 줄로 합친다."""
    sel = it.get("selection_info") or {}
    ms = [v for v in it.get("matched_variants") or [] if v.get("evaluation") or v.get("recommender")] if per_variant else []      # 유형별 값은 per_variant=False 면 유형 표의 칸으로 보여 준다
    entries: List[Tuple[str, str]] = []                         # (유형 이름 또는 "", 요약)
    def line(ev, rc):
        parts = [t for t in (_eval_text(ev), ("추천: " + rc) if rc else "") if t]
        return "  |  ".join(parts)
    if ms:
        groups: Dict[str, List[str]] = {}
        for v in ms:
            groups.setdefault(line(v.get("evaluation"), v.get("recommender")), []).append(v["name"])
        total = len(it.get("matched_variants") or [])
        if len(groups) == 1 and len(ms) == total:
            entries.append(("", next(iter(groups))))
        else:
            ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))                       # 같은 값을 쓰는 유형이 많은 묶음부터
            entries += [(", ".join(names), text) for text, names in ranked[:MAX_METHOD_LINES]]
            if len(ranked) > MAX_METHOD_LINES:                                                 # 핵심만: 나머지는 한 줄로 접는다
                rest = sum(len(n) for _, n in ranked[MAX_METHOD_LINES:])
                entries.append(("그 밖의 {}개 유형".format(rest), "유형마다 다름 (공고문 확인)"))
    common = line(sel.get("evaluation"), sel.get("recommender"))
    if common and not any(not n for n, _ in entries):
        entries.insert(0, ("", common))
    out: Dict[str, Any] = {}
    if entries:
        out["method"] = [("{}: {}".format(n, t) if n else t) for n, t in entries]
    steps = ["{}{}".format(p.get("name"), "({})".format(p["when"]) if p.get("when") else "") for p in sel.get("procedure") or [] if p.get("name")]
    if steps:
        out["procedure"] = " → ".join(steps)
    if sel.get("criteria"):
        out["criteria"] = " · ".join(sel["criteria"])
    return out


def variant_doc_notes(it: Dict[str, Any], rules: Any = None) -> List[str]:
    """해당 가능한 유형의 서류 칸에 문장으로 적힌 안내 ('유형 - 문장'). 서류 이름은 document_lines 가 보여 준다."""
    if rules is None:
        from . import feasibility_rules as FR
        rules = FR.current()
    out: List[str] = []
    for v in it.get("matched_variants") or []:
        for d in v.get("documents") or []:
            name = re.split(r"\s*[\(（]", d, maxsplit=1)[0]
            if any(w in name for w in rules.doc_skip) and not any(w in name for w in rules.doc_keep):
                continue
            if not is_document_name(d):
                out.append("{} - {}".format(v["name"], d))
    return out


def checks_groups(it: Dict[str, Any], checks: List[str]) -> List[Dict[str, Any]]:
    """확인사항 문장을 성격별 행으로 나눈다: 확인사항(제출 방법·안내 포함) / 추천서 / 본인 확인. 빈 행은 만들지 않는다."""
    rec = {re.sub(r"\s+", "", n.get("text") or "") for n in it.get("notes") or [] if n.get("kind") == "RECOMMENDATION"}
    buckets: Dict[str, List[str]] = {"확인사항": [], "추천서": [], "본인 확인": []}
    for t in checks:
        if t.startswith("본인 확인: "):
            buckets["본인 확인"].append(t[len("본인 확인: "):])
        elif re.sub(r"\s+", "", t) in rec or "추천서" in t or "추천" in t[:6]:
            buckets["추천서"].append(t)
        else:
            buckets["확인사항"].append(t)
    return [{"title": k, "items": v} for k, v in buckets.items() if v]


def benefit_rows(it: Dict[str, Any]) -> List[str]:
    """혜택 구역: 지원 금액·내용, 유형별 금액, 혜택 제한. 없으면 빈 목록 (구역 생략)."""
    b = it.get("benefit") or {}
    rows: List[str] = []
    if b.get("amount"):
        rows.append(str(b["amount"]))
    amt = str(b.get("amount") or "")
    rows += ["{}: {}".format(v["type"], v["amount"]) for v in b.get("variants") or [] if v.get("amount") and str(v["amount"]) not in amt]
    rows += ["제한: {}".format(t) for t in b.get("limits") or []]
    return rows


def _period(it: Dict[str, Any]) -> str:
    """모집기간 '시작일 ~ 마감일'. 시작일을 모르면 빈 문자열 (마감일은 카드 머리에 있다)."""
    st, end = it.get("apply_start"), it.get("apply_end")
    return "{} ~ {}".format(st, end) if st and end else ""


def _deadline(it: Dict[str, Any]) -> str:
    end, left = it.get("apply_end"), it.get("days_left")
    if not end:
        return "-"
    if left is None:
        return end
    return "{} ({})".format(end, "마감 지남" if left < 0 else "D-{}".format(left))


def _has_variant_selection(rows: List[Dict[str, str]]) -> bool:
    return any(r.get("evaluation") or r.get("criteria") for r in rows)


def build_cards(result: Dict[str, Any]) -> Dict[str, Any]:
    """결과(JSON)를 카드 구조로 바꾼다. 충족·조건부·미달 순, 각 구역 안에서 `index/total` 번호를 붙인다."""
    c = result["counts"]
    summary = "기준일 {} · 후보 {}건 → 필터 {}건 → 묶은 뒤 {}건 · 충족 {} / 조건부 {} / 미달 {}".format(
        result["today"], c["candidates"], c["after_filter"], c["after_dedupe"], c["match"], c["conditional"], c["no_match"])
    pn = result.get("profile_notes") or {}
    notes = ["가정: " + a for a in pn.get("assumptions") or []]
    if pn.get("dropped_sensitive_keys"):
        notes.append("개인정보로 보여 받지 않은 항목: " + ", ".join(pn["dropped_sensitive_keys"]))
    items: List[Dict[str, Any]] = []
    for key, verdict in (("recommended", "match"), ("conditional", "cond")):
        rows = result.get(key) or []
        for i, it in enumerate(rows, 1):
            it = hide_unmatched(it)                                       # 해당하지 않는 유형은 카드에 보이지 않는다
            groups = confirm_groups(it)
            items.append({
                "verdict": verdict, "index": i, "total": len(rows), "title": it.get("title") or it.get("scholarship_name") or "(제목 없음)",
                "src_id": str(it.get("src_id") or ""), "url": it.get("url") or "", "deadline": _deadline(it), "summary": it.get("summary") or "",
                "variants": [{"name": v["name"], "verdict": v.get("verdict_label"), "end": v.get("apply_end"), "expired": bool(v.get("expired"))} for v in it.get("matched_variants") or []],
                "unmatched": [v["name"] for v in it.get("unmatched_variants") or []],
                "confirm_groups": groups, "confirm_count": _confirm_count(groups), "conditions": condition_table(it, groups),
                "condition_groups": summary_groups(it, condition_table(it, groups)["open"]), "condition_source": (it.get("condition_summary") or {}).get("source") or "",
                "benefit": benefit_rows(it), "period": _period(it),
                "feasibility": (it.get("feasibility") or {}).get("level"), "feasibility_label": "", "feasibility_reason": "",                       # 실현 가능성은 카드에 보이지 않는다 (정렬에만 쓰고 결과 JSON 에는 남는다)
                "variant_rows": variant_rows(it),
                **({"selection": selection_rows(it, per_variant=not _has_variant_selection(variant_rows(it)))} if selection_rows(it, per_variant=not _has_variant_selection(variant_rows(it))) else {}),   # 선발 방식·절차·기준 (있을 때만)
                "review": [n["text"] for n in it.get("notes") or [] if n.get("kind") == "REVIEW"],                  # 인재상·서술형 심사 기준 (판정에는 쓰지 않는다)
                "documents": document_lines(it, per_variant=False), "variant_documents": document_lines(it)[len(document_lines(it, per_variant=False)):],   # 제출 서류 행 (공통) / 유형별 서류 (웹은 유형 표 칸, 터미널은 같은 행)
                "checks": compact_notes(it) + variant_doc_notes(it),          # 확인사항: 추천·절차·안내 문장 (서류 이름은 제출 서류 행)
                "checks_groups": checks_groups(it, compact_notes(it) + variant_doc_notes(it)),
                "warnings": [w.get("message") or "" for w in it.get("warnings") or [] if w.get("code") != "NEED_REVIEW"],   # 검수 대기 안내는 카드에 보이지 않는다
                "assumptions": list(it.get("assumptions") or [])})
    ex = result.get("excluded") or []
    for i, e in enumerate(ex, 1):
        reasons = list(e.get("reasons") or [])
        card = {"verdict": "no", "index": i, "total": len(ex), "title": e.get("title") or "(제목 없음)", "src_id": str(e.get("src_id") or ""),
                "url": e.get("url") or "", "reasons": reasons, "summary": e.get("summary") or "", "deadline": _deadline(e) if e.get("apply_end") else "",
                "benefit": benefit_rows(e), "period": _period(e), "variant_rows": variant_rows(e), "documents": document_lines(e, per_variant=False), "variant_documents": [], "checks": compact_notes(e), "checks_groups": checks_groups(e, compact_notes(e)), "warnings": [w.get("message") or "" for w in e.get("warnings") or [] if w.get("code") != "NEED_REVIEW"],
                "review": [n["text"] for n in e.get("notes") or [] if n.get("kind") == "REVIEW"],
                "assumptions": [], "feasibility_label": "", "condition_groups": [], "condition_source": "", "confirm_count": 0}
        sel = selection_rows(e, per_variant=False)                       # 미달 카드에도 선발 방식·기준·진행 절차를 보인다 (공고를 이해하는 데 도움)
        if sel:
            card["selection"] = sel
        if "fail_reasons" in e:                                          # 상세 재료가 있으면 조건표를 만든다 (없는 옛 결과는 사유만)
            groups = confirm_groups(e)
            card["conditions"] = condition_table(e, groups)
            card["condition_groups"] = row_groups(e, card["conditions"]["open"])            # 미달 카드도 조건 / 결과 / 내용 / 원문 표
            card["confirm_count"] = _confirm_count(groups)
        else:
            card["conditions"] = {"met": [], "open": [], "info": []}
        items.append(card)
    return {"summary": summary, "notes": notes, "items": items, "counts": {"match": c["match"], "cond": c["conditional"], "no": c["no_match"]}}


# ------------------------------------------------------------------ 터미널 출력
def _wrap(text: str, width: int) -> List[str]:
    """화면 폭(한글 2칸) 기준으로 줄바꿈한다. 주소(공백 없는 긴 문자열)는 자르지 않는다."""
    if display_width(text) <= width or " " not in text.strip():
        return [text]
    lines, cur = [], ""
    for ch in text:
        if display_width(cur + ch) > width and cur:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def _rows(label: str, lines: List[str], lw: int, width: int) -> List[str]:
    """항목 이름 칸(lw)과 내용 칸. 내용이 여러 줄이면 이름은 첫 줄에만 쓴다."""
    out: List[str] = []
    first = True
    for ln in lines:
        for part in _wrap(ln, max(20, width - lw - 2)):
            out.append("  {}{}".format(pad(label if first else "", lw), part))
            first = False
    return out


def _condition_lines(cd: Dict[str, List[Dict[str, str]]], lw: int, width: int, show_met: bool, groups: Optional[List[Dict[str, Any]]] = None, show_raw: bool = False) -> List[str]:
    """'조건' 구역: 충족은 한 줄로 접고(--show-met 이면 표에 포함), 확인 필요·제외 조건·참고는 표로 보여 준다. 표 안은 기호 없이 글자만 (기호 폭 때문에 테두리가 어긋나지 않게)."""
    L: List[str] = []
    if groups and not show_raw:                                   # AI·코드 요약: 그룹마다 요약 + 원문 발췌, 전체 원문 조건은 --show-raw
        lines: List[str] = []
        for g in groups:
            tag = RESULT_LABEL[g["result"]] + (" · " + g["level_label"] if g["level_label"] else "")
            lines.append("▷ {} [{}]  {}".format(g["title"], tag, g["summary"]))
            if g["quote"] and g["quote"] != g["summary"]:
                lines.append("    원문: “{}”".format(g["quote"]))
        met = cd["met"]
        if met and not show_met:
            lines.append("충족 {}개  {}".format(len(met), " · ".join(m["cond"] for m in met)))
        lines.append("전체 원문 조건 {}개는 --show-raw 로 펼침".format(len(cd["open"])))
        return _rows("조건", lines, lw, width)
    met, rows = cd["met"], []
    if met and not show_met:
        L += _rows("조건", ["충족 {}개  {}".format(len(met), " · ".join(m["cond"] for m in met))], lw, width)
        label = ""
    else:
        label = "조건"
    rows = [[r["cond"], r["state"], RESULT_LABEL[r["result"]]] for r in (met if show_met else []) + cd["open"] + [i for i in cd["info"] if not i.get("web")]]
    if rows:
        tbl = render_table(["조건", "내 상태", "결과"], rows, max_width=max(40, width - lw - 2)).split("\n")
        if label:
            L += ["  " + pad(label, lw) + tbl[0]] + ["  " + " " * lw + t for t in tbl[1:]]
        else:
            L += ["  " + " " * lw + t for t in tbl]
    elif not met:
        L += _rows("조건", ["확인할 조건이 없음"], lw, width)
    return L


def format_cards(result: Dict[str, Any], show_excluded: bool = False, width: Optional[int] = None, show_met: bool = False, show_raw: bool = False) -> str:
    width = max(60, width or shutil.get_terminal_size((100, 24)).columns)
    cards = build_cards(result)
    L: List[str] = ["=== 장학금 추천 결과 (기준일 {}) ===".format(result["today"]), cards["summary"]]
    L += ["{} {}".format(SYMBOL[NOTE], n) for n in cards["notes"]]
    items = cards["items"] if show_excluded else [x for x in cards["items"] if x["verdict"] != "no"]
    if not items:
        L += ["", "추천할 장학금이 없습니다."]
    else:
        L += ["", "[한눈에 보기]"]
        rows = [["{} {}/{}".format(VERDICT_TITLE[x["verdict"]], x["index"], x["total"]), x["src_id"], x["title"], x.get("deadline", "-"),
                 (str(x["confirm_count"]) + "개") if x["verdict"] != "no" else "-"] for x in items]
        L.append(render_table(["구분", "글번호", "장학금", "마감", "확인 필요"], rows, max_width=width))
    heavy, light = "=" * min(width, 100), "-" * min(width, 100)
    lw = 10
    for x in items:
        L += ["", heavy, "{} {} {}/{}  |  글번호 {}".format(SYMBOL[x["verdict"]], VERDICT_TITLE[x["verdict"]], x["index"], x["total"], x["src_id"]) +
              ("  |  마감 " + x["deadline"] if x.get("deadline") else ""), "  " + x["title"], light]
        cd = x["conditions"]
        has_cond = bool(cd["open"] or cd["met"] or cd["info"])
        if x["verdict"] == "no" and not has_cond:                         # 상세 재료가 없는 옛 결과: 사유만
            L += _rows("미달 사유", ["{} {}".format(SYMBOL[EXCLUDE], r) for r in x["reasons"]] or ["-"], lw, width)
        if x.get("period"):
            L += _rows("모집기간", [x["period"]], lw, width)
        if x["summary"]:
            L += _rows("요약", [x["summary"]], lw, width)
        if x.get("benefit"):
            L += _rows("혜택", ["{} {}".format(SYMBOL["BENEFIT"], b) for b in x["benefit"]], lw, width)
        if x.get("variant_rows"):
            L += _rows("유형", ["{} {} - {}".format(SYMBOL[EXCLUDE] if r["result"] == "fail" else SYMBOL["VARIANT"], r["name"], r["text"]) for r in x["variant_rows"]], lw, width)
        if (x.get("selection") or {}).get("method"):
            L += _rows("선발 방식", x["selection"]["method"], lw, width)
        if (x.get("selection") or {}).get("criteria"):
            L += _rows("선발 기준", [x["selection"]["criteria"]], lw, width)
        if x.get("review"):
            L += _rows("심사 기준", x["review"], lw, width)
        if x.get("feasibility_label"):
            L += _rows("실현성", ["{}{}".format(x["feasibility_label"], " - " + x["feasibility_reason"] if x["feasibility_reason"] else "")], lw, width)
        if x["verdict"] != "no" or has_cond:
            L += _condition_lines(cd, lw, width, show_met, x.get("condition_groups"), show_raw)
        if (x.get("selection") or {}).get("procedure"):                     # 순서: 선발 방식·기준 → 조건 → 진행 절차
            L += _rows("진행 절차", [x["selection"]["procedure"]], lw, width)
        if x.get("documents") or x.get("variant_documents"):
            L += _rows("제출 서류", ["- " + d for d in (x.get("documents") or []) + (x.get("variant_documents") or [])], lw, width)
        groups = x.get("checks_groups")
        if groups is None:
            groups = [{"title": "확인사항", "items": x["checks"]}] if x["checks"] else []
        n_shown = 0
        for g in groups:
            items = g["items"][: max(0, MAX_CHECKS - n_shown)] if g["title"] == "확인사항" else g["items"]
            n_shown += len(items)
            if items:
                L += _rows(g["title"], ["- " + t for t in items], lw, width)
        if len(x["checks"]) > MAX_CHECKS and n_shown < len(x["checks"]):
            L += _rows("", ["… 외 {}개 (--json 으로 전체 확인)".format(len(x["checks"]) - n_shown)], lw, width)
        if x["verdict"] != "no" and not groups:
            L += _rows("확인사항", ["특이 사항 없음"], lw, width)
        for a in x["assumptions"]:
            L += _rows("가정", ["{} {}".format(SYMBOL[NOTE], a)], lw, width)
        L.append("  {}{} {}".format(pad("원문", lw), SYMBOL["LINK"], x["url"] or "(주소 없음)"))      # 주소는 줄바꿈·생략 없이 그대로
    if items:
        L.append(heavy)
    from . import service as S
    L += S.follow_up_blocks(result, "ascii", width)
    return "\n".join(L)
