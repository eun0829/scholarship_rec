# -*- coding: utf-8 -*-
"""[CHAT] 질문 가리기, 원문 쪼개기, 질문과 관련된 부분 고르기. AI 를 부르지 않는 순수 함수."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

MASK = "[가림]"
_PII = [
    re.compile(r"\b\d{6}\s*-\s*[1-8]\d{6}\b"),                                # 주민등록번호 꼴
    re.compile(r"\b0\d{1,2}\s*-?\s*\d{3,4}\s*-?\s*\d{4}\b"),                  # 전화번호
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),            # 이메일
    re.compile(r"\b\d{3,6}(?:-\d{2,6}){2,4}\b"),                              # 계좌·카드 번호 꼴 (하이픈으로 이어진 긴 숫자)
    re.compile(r"\b\d{13,19}\b"),                                             # 하이픈 없는 긴 숫자
    re.compile(r"(학번|사번)\s*[:：]?\s*\d{5,12}"),
]


def mask_pii(text: str) -> str:
    """질문·대화 기록을 AI 로 보내기 전에 개인을 알 수 있는 숫자 꼴을 가린다 (완벽하지 않으므로 질문에 개인정보를 적지 않도록 화면에서도 안내한다)."""
    out = text or ""
    for p in _PII:
        out = p.sub(lambda m: (m.group(1) + " " if m.lastindex else "") + MASK, out)
    return out


def neutralize(text: str) -> str:
    """원문 안의 글이 자료 구분 표시를 흉내 내지 못하게 한다."""
    return (text or "").replace("<자료", "< 자료").replace("</자료", "< /자료")


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


_STOP_CHARS = re.compile(r"[^0-9A-Za-z가-힣]")
_FILLER = ("알려줘", "알려주세요", "알고싶어요", "궁금해요", "궁금합니다", "있나요", "인가요", "일까요", "할까요", "어떻게", "무엇", "뭐가", "뭔가요", "해주세요", "주세요")


def bigrams(text: str) -> set:
    t = _STOP_CHARS.sub("", text or "")
    for f in _FILLER:
        t = t.replace(f, "")
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


@dataclass
class Chunk:
    notice_id: str
    source: str          # '본문' / '첨부1: 안내문.pdf'
    index: int           # 같은 출처 안에서의 순서
    text: str
    file_seq: str = ""   # 첨부 조각이면 SCHOLARSHIP_NOTICE_FILE.FILE_SEQ (출처에서 원본 파일을 연결할 때 쓴다)


def split_text(text: str, size: int = 700) -> List[str]:
    """문단 단위로 모아 size 글자 안팎으로 자른다 (문단이 size 보다 길면 문장 끝에서 자른다)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "") if p.strip()]
    out: List[str] = []
    buf = ""
    for p in paras:
        pieces = [p]
        if len(p) > size:
            pieces = [x.strip() for x in re.split(r"(?<=[.!?。다요함음])\s+", p) if x.strip()]
            merged: List[str] = []
            cur = ""
            for x in pieces:
                while len(x) > size:                           # 문장 부호가 없는 아주 긴 줄
                    if cur:
                        merged.append(cur)
                        cur = ""
                    merged.append(x[:size])
                    x = x[size:]
                if len(cur) + len(x) + 1 > size and cur:
                    merged.append(cur)
                    cur = x
                else:
                    cur = (cur + " " + x).strip()
            if cur:
                merged.append(cur)
            pieces = merged
        for x in pieces:
            if len(buf) + len(x) + 1 > size and buf:
                out.append(buf)
                buf = x
            else:
                buf = (buf + "\n" + x).strip()
    if buf:
        out.append(buf)
    return out


def chunks_of(notice_id: str, texts: Dict) -> List[Chunk]:
    out: List[Chunk] = []
    for i, t in enumerate(split_text(texts.get("body") or "")):
        out.append(Chunk(notice_id, "본문", i, t))
    for n, f in enumerate(texts.get("files") or [], 1):
        label = "첨부{}: {}".format(n, f.get("name") or "")
        if f.get("role") == "FORM":
            label += " (양식)"
        for i, t in enumerate(split_text(f.get("text") or "")):
            out.append(Chunk(notice_id, label, i, t, str(f.get("seq") or "")))
    return out


def rank_notices(question: str, titles: Dict[str, str], bodies: Dict[str, str], focus: str = "") -> List[str]:
    """질문과 가까운 순서로 공고 id 를 늘어놓는다 (focus 가 있으면 맨 앞). 제목에 겹치는 말이 있으면 더 크게 본다."""
    q = bigrams(question)
    scores = {}
    for nid in titles:
        t, b = bigrams(titles[nid]), bigrams(bodies.get(nid, ""))
        scores[nid] = 3 * len(q & t) + len(q & b) / (1 + math.log(1 + len(b) / 200))
    order = sorted(titles, key=lambda n: (-scores[n], n))
    if focus in titles:
        order.remove(focus)
        order.insert(0, focus)
    return order


def rank_by_chunks(question: str, titles: Dict[str, str], chunks_by: Dict[str, List[Chunk]], focus: str = "") -> List[str]:
    """조각 단위로 공고 순서를 정한다. 모든 공고의 조각에서 질문과 겹치는 2글자 묶음 중 드문 것(예: '어학')일수록 큰 점수를 주고,
    공고마다 점수가 가장 높은 조각(+두 번째 조각의 일부)과 제목 겹침으로 순서를 매긴다. 긴 공고가 불리하지 않다. focus 가 있으면 맨 앞."""
    q = bigrams(question)
    sets = {n: [bigrams(c.text) for c in cs] for n, cs in chunks_by.items()}
    total = sum(len(v) for v in sets.values()) or 1
    df: Dict[str, int] = {}
    for v in sets.values():
        for s in v:
            for b in s:
                df[b] = df.get(b, 0) + 1
    scores = {}
    for nid in titles:
        per = sorted((sum(math.log(1 + total / df[b]) for b in q & s) for s in sets.get(nid, [])), reverse=True)
        best = (per[0] if per else 0.0) + (0.3 * per[1] if len(per) > 1 else 0.0)
        scores[nid] = best + 3 * len(q & bigrams(titles[nid]))
    order = sorted(titles, key=lambda n: (-scores[n], n))
    if focus in titles:
        order.remove(focus)
        order.insert(0, focus)
    return order


def select_chunks(question: str, chunks: Sequence[Chunk], budget: int) -> List[Chunk]:
    """글자 수 예산 안에서 질문과 관련된 조각을 고른다. 전부 들어가면 전부. 들어가지 않으면 공고마다 본문 첫 조각은 항상 두고
    나머지는 질문과 겹치는 이정표(2글자 묶음)가 드문 것일수록 큰 점수를 받아 높은 순으로 채운다. 결과는 원래 순서로 돌려준다."""
    if sum(len(c.text) for c in chunks) <= budget:
        return list(chunks)
    q = bigrams(question)
    sets = [bigrams(c.text) for c in chunks]
    df: Dict[str, int] = {}
    for s in sets:
        for b in s:
            df[b] = df.get(b, 0) + 1
    n = len(chunks)
    score = [sum(math.log(1 + n / df[b]) for b in q & s) for s in sets]
    chosen = set()
    used = 0
    seen_notice = set()
    for i, c in enumerate(chunks):                                       # 각 공고의 첫 조각 (제목·개요가 있는 곳)
        if c.notice_id not in seen_notice and c.source == "본문":
            seen_notice.add(c.notice_id)
            if used + len(c.text) <= budget:
                chosen.add(i)
                used += len(c.text)
    for i in sorted(range(n), key=lambda k: (-score[k], k)):
        if i in chosen or score[i] <= 0:
            continue
        if used + len(chunks[i].text) <= budget:
            chosen.add(i)
            used += len(chunks[i].text)
    return [c for i, c in enumerate(chunks) if i in chosen]
