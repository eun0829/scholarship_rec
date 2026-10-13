# -*- coding: utf-8 -*-
"""[EXT02] 드림스폰 화면 해석 (순수 함수 - 네트워크 없음. 저장해 둔 HTML 로 시험할 수 있다).

  parse_list(html)      목록 표 -> ListRow (idx, 장학명, 기관, 태그, D-day, 상태)
  parse_detail(html)    상세 -> Detail (정보표, 고정 7개 항목, 첨부 목록, 원문공고 URL, 기관정보)
  parse_period(text)    신청기간 문구 -> 시작/종료일 (해석 불가면 kind 로 알림)
  compose(...)          Detail -> (source_fields, hints)
                          source_fields : DB(SRC_FIELDS_JSON)에 저장할 출처 구조화 항목 (AI 해석과 분리)
                          hints         : 코드가 확정한 값 (마감일, 인원, 학교급 ...) - 파이프라인이 AI 결과와 교차 검증

상세 본문(#t_content)은 '• 신청기간 / 선발인원 / 장학혜택 / 접수방법 / 지원대상 / 신청자격 / 장학금 문의' 고정 형식이다.
형식이 다른 글도 깨지지 않도록 없는 항목은 비워 두고, 모르는 제목은 extra 로 보존한다.
로그인 계정의 이메일이 들어 있는 문의/오류 제보 양식 등은 읽지 않는다 (필요한 영역만 읽음).
"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

WEEKDAYS = "월화수목금토일"
SECTION_KEYS = {"신청기간": "period", "선발인원": "headcount", "장학혜택": "benefit", "접수방법": "method",
                "지원대상": "target", "신청자격": "eligibility", "장학금문의": "contact"}
SECTION_TITLES = {"period": "신청기간", "headcount": "선발인원", "benefit": "장학혜택", "method": "접수방법",
                  "target": "지원대상", "eligibility": "신청자격", "contact": "장학금 문의"}
ELIG_TAGS = {"지역기준": "region", "성적기준": "gpa", "추천사항": "recommend", "소득기준": "income", "기타조건": "etc"}
INFO_LABELS_FOR_BODY = ("장학종류", "선발인원", "장학혜택", "선발대상", "신청기간")

DEFAULT_APPLY_KW = {"SCHOOL": ["학교경유", "학교추천", "학교장추천", "대학경유", "대학추천", "소속학교추천", "학과추천"],
                    "DIRECT": ["온라인", "직접접수", "직접신청", "이메일", "홈페이지", "우편접수", "방문접수"]}
DEFAULT_TARGET_KW = {"GRAD": ["대학원"], "UNIV": ["대학생", "전문대", "대학교", "대학"], "HIGH": ["고등학생", "고교", "고등학교"],
                     "MIDDLE": ["중학생", "중학교", "중등"], "ELEM": ["초등학생", "초등학교", "초등"]}

OPEN_WORDS = re.compile(r"상시|수시|연중|예산\s*소진|선착순|채용\s*시|미정|추후|별도\s*공지|공고\s*참조")
_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*[.일]?\s*(?:\(\s*([월화수목금토일])\s*\))?")


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _text(el) -> str:
    return _ws(el.get_text("")) if el is not None else ""


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*[-·•*]\s*", "", line).strip()


# ================================================================== 목록
@dataclass
class ListRow:
    idx: str
    title: str
    org: str = ""
    tags: List[str] = field(default_factory=list)      # '#' 없이. 첫 태그는 종류(장학금/기타지원/대출지원/연수지원 ...)
    dday: Optional[int] = None                         # D-0 -> 0, D-12 -> 12, 지난 경우 음수
    state: str = ""                                    # 모집중 / 마감임박 / 모집예정 / 모집완료 ...
    views: Optional[int] = None

    @property
    def kind(self) -> str:
        return self.tags[0] if self.tags else ""

    @property
    def audiences(self) -> List[str]:
        return self.tags[1:]


def parse_dday(text: str) -> Optional[int]:
    m = re.search(r"D\s*([-+])\s*(\d+)", text or "", re.I)
    if not m:
        return None
    n = int(m.group(2))
    return n if m.group(1) == "-" else -n


def parse_list(html: str) -> List[ListRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[ListRow] = []
    for tr in soup.select("table tbody tr"):
        a = tr.select_one("p.title a[href]") or tr.select_one("td.td_subject a[href]")
        if a is None:
            continue
        m = re.search(r"idx=(\d+)", a["href"])
        if not m:
            continue
        tds = tr.find_all("td")
        count, st, hit = tr.select_one(".count"), tr.select_one(".state"), tr.select_one(".hit")
        views = int(re.sub(r"\D", "", hit.get_text())) if hit is not None and re.search(r"\d", hit.get_text()) else None
        rows.append(ListRow(idx=m.group(1), title=_text(a), org=_text(tds[1]) if len(tds) > 1 else "",
                            tags=[_text(t).lstrip("#") for t in tr.select(".hashtag span") if _text(t)],
                            dday=parse_dday(count.get_text()) if count is not None else None,
                            state=_text(st), views=views))
    return rows


def parse_max_page(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pages = [int(m.group(1)) for a in soup.find_all("a", href=True) for m in [re.search(r"[?&]page=(\d+)", a["href"])] if m]
    return max(pages) if pages else 1


# ================================================================== 신청기간
@dataclass
class Period:
    raw: str
    start: Optional[date] = None
    end: Optional[date] = None
    kind: str = "UNPARSED"          # RANGE(시작~종료) / END_ONLY(종료일만) / MULTI(여러 기간) / OPEN(상시 등) / UNPARSED
    warnings: List[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.kind in ("RANGE", "END_ONLY", "MULTI") and self.end is not None

    def to_dict(self, source: str = "") -> Dict[str, Any]:
        return {"raw": self.raw, "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None, "kind": self.kind, "source": source,
                "warnings": list(self.warnings)}


def parse_period(text: str) -> Period:
    raw = _ws(text)
    p = Period(raw=raw)
    found: List[Tuple[date, Optional[str]]] = []
    for m in _DATE.finditer(raw):
        try:
            found.append((date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4)))
        except ValueError:
            p.warnings.append("날짜가 올바르지 않음: {}".format(m.group(0).strip()))
    for d, wd in found:                                       # 요일이 적혀 있으면 날짜와 맞는지 확인
        if wd and WEEKDAYS[d.weekday()] != wd:
            p.warnings.append("요일 불일치: {} 은 {}요일인데 ({}) 로 적혀 있음".format(d.isoformat(), WEEKDAYS[d.weekday()], wd))
    dates = [d for d, _ in found]
    if not dates:
        p.kind = "OPEN" if OPEN_WORDS.search(raw) else "UNPARSED"
        return p
    if len(dates) == 1:
        p.kind, p.end = "END_ONLY", dates[0]
        if not re.search(r"~|까지|부터|이내|마감", raw):
            p.warnings.append("날짜가 하나뿐이라 종료일로 가정함")
        return p
    if len(dates) == 2:
        p.kind, p.start, p.end = "RANGE", dates[0], dates[1]
        if p.start > p.end:
            p.warnings.append("시작일이 종료일보다 늦음")
            p.kind, p.start, p.end = "UNPARSED", None, None
        return p
    p.kind, p.start, p.end = "MULTI", min(dates), max(dates)
    p.warnings.append("신청기간이 여러 개({}개 날짜)라 가장 이른 날짜~가장 늦은 날짜로 처리함".format(len(dates)))
    return p


# ================================================================== 상세
@dataclass
class DetailAttachment:
    name: str
    filepath: str            # 서버가 다운로드 요청에 요구하는 값 (그대로 되돌려 보내기만 한다. 저장/노출하지 않음)


@dataclass
class Detail:
    idx: str = ""
    title: str = ""
    org_name: str = ""
    dday: Optional[int] = None
    has_content: bool = False                                   # 로그인하지 않으면 본문이 없다
    info: Dict[str, str] = field(default_factory=dict)         # 정보표 (장학종류/선발인원/장학혜택/선발대상/신청기간)
    attachments: List[DetailAttachment] = field(default_factory=list)
    attach_summary: str = ""
    original_url: Optional[str] = None
    sections: Dict[str, List[str]] = field(default_factory=dict)       # 고정 7개 (canonical key -> 줄)
    extra_sections: Dict[str, List[str]] = field(default_factory=dict)  # 모르는 제목
    org: Dict[str, str] = field(default_factory=dict)          # 기관정보
    images: List[str] = field(default_factory=list)            # 요약 이미지 주소


def _parse_sections(dd) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    known: Dict[str, List[str]] = {}
    extra: Dict[str, List[str]] = {}
    cur: Optional[List[str]] = None
    for p in dd.find_all("p"):
        strong = p.find("strong")
        txt = _text(p)
        if strong is not None and _text(strong).startswith("•"):
            name = _text(strong).lstrip("•").strip()
            key = SECTION_KEYS.get(re.sub(r"\s+", "", name))
            if key and key not in known:
                known[key] = cur = []
            elif key:
                cur = []                                   # 같은 제목이 또 나오면 첫 항목을 유지
            else:
                extra[name] = cur = []
            continue
        if cur is not None and txt:
            cur.append(txt)
    return known, extra


def _find_url(href: str) -> Optional[str]:
    m = re.search(r"goScholarship\(\s*'[^']*'\s*,\s*'([^']*)'", href or "")
    url = htmllib.unescape(m.group(1)).strip() if m else ""
    return url if re.match(r"https?://", url) else None


def parse_detail(html: str) -> Detail:
    soup = BeautifulSoup(html, "html.parser")
    d = Detail()
    inp = soup.select_one("form#fForm input[name=idx]")
    d.idx = inp.get("value", "") if inp is not None else ""
    subject = soup.select_one("div.v_hd .subject") or soup.select_one("div.subject")
    d.title = _text(subject) or _ws((soup.title.get_text() if soup.title else "").rsplit(",", 1)[0])
    d.org_name = _text(soup.select_one("div.v_hd .name"))
    d.dday = parse_dday(_text(soup.select_one(".opt-bx .state")))

    basic = soup.select_one("div.basic-info")
    if basic is not None:
        for ul in basic.find_all("ul"):
            lis = ul.find_all("li", recursive=False)
            if len(lis) >= 2 and "col" in (lis[0].get("class") or []):
                label = _text(lis[0])
                if label in ("첨부파일", "원문공고"):
                    continue
                d.info[label] = _text(lis[1])
        for a in basic.select("a.btn_file_down"):
            fp = a.get("data-filepath", "")
            if fp and _text(a):
                d.attachments.append(DetailAttachment(name=_text(a), filepath=fp))
        t1 = basic.select_one(".pop-file-bx .t1")
        if t1 is not None:
            d.attach_summary = _ws(re.sub(r"모두\s*저장", "", t1.get_text(" ")))
        for a in basic.find_all("a", href=True):
            url = _find_url(a["href"])
            if url:
                d.original_url = url
                break

    tab1 = soup.select_one("#t_content #tab1s dl dd") or soup.select_one("#t_content dl dd")
    if tab1 is not None:
        d.sections, d.extra_sections = _parse_sections(tab1)
    d.has_content = bool(d.sections or d.extra_sections or d.info)

    content = soup.select_one("#t_content")
    if content is not None:                                    # 기관정보 (첫 번째 dl 은 장학정보)
        for dl in content.find_all("dl")[1:]:
            for dt in dl.find_all("dt"):
                label = _ws(dt.get_text("")).lstrip("·•").strip()
                dd = dt.find_next_sibling("dd")
                if not label or dd is None:
                    continue
                link = dd.find("a", href=True)
                val = _text(dd)
                if label == "홈페이지" and link is not None and re.match(r"https?://", link["href"]):
                    val = link["href"]
                d.org[label] = val
    d.images = [a["href"] for a in soup.select("a[data-size]") if a.get("href", "").startswith("/data/scholarship/")]
    return d


# ================================================================== 항목별 구조화
def levels_from_text(text: str, kw: Optional[Dict[str, List[str]]] = None) -> List[str]:
    kw = kw or DEFAULT_TARGET_KW
    t = re.sub(r"\s+", "", text or "")
    out: List[str] = []
    if any(w in t for w in kw.get("GRAD", [])):
        out.append("GRAD")
    rest = t.replace("대학원", "")                              # '대학원생' 이 UNIV('대학')로 잡히지 않게
    for code in ("UNIV", "HIGH", "MIDDLE", "ELEM"):
        if any(w in rest for w in kw.get(code, [])) and code not in out:
            out.append(code)
    order = ["ELEM", "MIDDLE", "HIGH", "UNIV", "GRAD"]
    return sorted(out, key=order.index)


def classify_apply_type(text: str, kw: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    kw = kw or DEFAULT_APPLY_KW
    t = re.sub(r"\s+", "", text or "")
    for code in ("SCHOOL", "DIRECT"):                          # SCHOOL 먼저 (학교 경유 접수에도 '우편' 이 나올 수 있음)
        if any(w in t for w in kw.get(code, [])):
            return code
    return None


def parse_headcount(text: str) -> Optional[int]:
    """'총 30명' -> 30. 드림스폰이 빈칸으로 두는 '총 00명', '인원 미정', 숫자 없음은 None (0명 선발은 의미가 없으므로 알 수 없음으로 본다)."""
    m = re.search(r"(\d[\d,]*)\s*명", text or "")
    n = int(m.group(1).replace(",", "")) if m else 0
    return n if n > 0 else None


def _tagged_blocks(lines: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    cur: Optional[Dict[str, Any]] = None
    lead: List[str] = []
    for line in lines:
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if m:
            tag = _ws(m.group(1))
            key = ELIG_TAGS.get(re.sub(r"\s+", "", tag), tag)
            cur = out.setdefault(key, {"tag": tag, "label": _ws(m.group(2)), "lines": []})
            if cur["label"] != _ws(m.group(2)) and m.group(2):
                cur["lines"].append(_strip_bullet(m.group(2)))
            continue
        (cur["lines"] if cur is not None else lead).append(_strip_bullet(line))
    if lead:
        out["lead"] = lead
    return out


def structure_sections(known: Dict[str, List[str]], target_kw=None, apply_kw=None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "headcount" in known:
        raw = " ".join(known["headcount"])
        out["headcount"] = {"raw": raw, "count": parse_headcount(raw)}
    if "benefit" in known:
        lines = [_strip_bullet(x) for x in known["benefit"]]
        out["benefit"] = {"headline": lines[0] if lines else "", "lines": lines[1:],
                          "notes": [x for x in lines if x.startswith("※")]}
    if "method" in known:
        lines = [_strip_bullet(x) for x in known["method"]]
        joined = " ".join(lines)
        pick = lambda pat: next((_ws(m.group(1)) for x in lines for m in [re.search(pat, x)] if m), None)
        addr, docs = pick(r"우편\s*접수\s*주소\s*:\s*(.+)"), pick(r"제출\s*서류\s*:\s*(.+)")
        out["method"] = {"headline": lines[0] if lines else "", "lines": lines[1:], "apply_type_hint": classify_apply_type(joined, apply_kw),
                         "mail_address": addr, "submit_docs": docs}
    if "target" in known:
        lines = [_strip_bullet(x) for x in known["target"]]
        first = lines[0] if lines else ""
        levels = levels_from_text(first, target_kw) or levels_from_text(" ".join(lines), target_kw)
        out["target"] = {"raw": first, "levels": levels, "notes": lines[1:]}
    if "eligibility" in known:
        out["eligibility"] = _tagged_blocks(known["eligibility"])
    if "contact" in known:
        lines = [_strip_bullet(x) for x in known["contact"]]
        joined = "\n".join(lines)
        g = lambda pat: (lambda m: _ws(m.group(1)) if m else None)(re.search(pat, joined))
        out["contact"] = {"org": g(r"문의처\s*:\s*(.+)"), "tel": g(r"전화\s*:\s*(.+)"), "email": g(r"(?:이메일|메일|E-?mail)\s*:\s*(.+)"),
                          "lines": lines}
    return out


def build_body_html(d: Detail, include_images: bool = False) -> str:
    """AI/본문 정리용 HTML. 광고, 문의/오류 제보 양식(로그인 계정 이메일 포함), 면책 문구는 넣지 않는다."""
    e = htmllib.escape
    parts = ["<h3>드림스폰 장학 정보 (드림스폰이 공고문을 요약한 내용)</h3>", "<table>"]
    for label in INFO_LABELS_FOR_BODY:
        if d.info.get(label):
            parts.append("<tr><th>{}</th><td>{}</td></tr>".format(e(label), e(d.info[label])))
    parts.append("</table>")
    for key in ("period", "headcount", "benefit", "method", "target", "eligibility", "contact"):
        if key in d.sections:
            parts.append("<h3>• {}</h3>".format(SECTION_TITLES[key]))
            parts.extend("<p>{}</p>".format(e(line)) for line in d.sections[key])
    for name, lines in d.extra_sections.items():
        parts.append("<h3>• {}</h3>".format(e(name)))
        parts.extend("<p>{}</p>".format(e(line)) for line in lines)
    if d.org:
        parts.append("<h3>기관 정보</h3><table>")
        for k, v in d.org.items():
            if v:
                parts.append("<tr><th>{}</th><td>{}</td></tr>".format(e(k), e(v)))
        parts.append("</table>")
    if include_images:
        parts.extend('<img src="{}">'.format(e(src)) for src in d.images)
    return "\n".join(parts)


# ================================================================== 종합: source_fields + hints
def compose(d: Detail, row: Optional[ListRow], today: date, target_kw=None, apply_kw=None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    structured = structure_sections(d.sections, target_kw, apply_kw)

    sec_period = parse_period(" ".join(d.sections["period"])) if "period" in d.sections else None
    info_period = parse_period(d.info["신청기간"]) if d.info.get("신청기간") else None
    main, source, warnings = None, "NONE", []
    if sec_period is not None and sec_period.usable:
        main, source = sec_period, "DETAIL"
    elif info_period is not None and info_period.usable:
        main, source = info_period, "INFO_TABLE"
    else:
        main = sec_period or info_period
        source = "DETAIL" if sec_period is not None else ("INFO_TABLE" if info_period is not None else "NONE")
    if main is not None:
        warnings += main.warnings
    if sec_period is not None and info_period is not None and sec_period.usable and info_period.usable:
        if (sec_period.start, sec_period.end) != (info_period.start, info_period.end):
            warnings.append("신청기간이 본문({} ~ {})과 정보표({} ~ {})에서 다름 - 본문 값을 사용함".format(
                sec_period.start, sec_period.end, info_period.start, info_period.end))
    period_dict = main.to_dict(source) if main is not None else {"raw": "", "start": None, "end": None, "kind": "UNPARSED",
                                                                  "source": "NONE", "warnings": []}
    period_dict["warnings"] = warnings

    dday_end = None
    if row is not None and row.dday is not None and row.dday >= 0:
        dday_end = (today + timedelta(days=row.dday)).isoformat()

    fields: Dict[str, Any] = {"schema": 1, "period": period_dict, **structured,
                              "info_table": dict(d.info), "org": dict(d.org), "original_url": d.original_url,
                              "attachments": [{"name": a.name} for a in d.attachments],
                              "extra_sections": {k: v for k, v in d.extra_sections.items()}}
    if row is not None:
        fields["list"] = {"tags": row.tags, "dday": row.dday, "state": row.state, "seen_at": today.isoformat()}

    usable = main is not None and main.usable
    benefit = structured.get("benefit") or {}
    method = structured.get("method") or {}
    count = (structured.get("headcount") or {}).get("count")
    hints: Dict[str, Any] = {
        "apply_start": main.start.isoformat() if usable and main.start else None,
        "apply_end": main.end.isoformat() if usable else None,
        "period_kind": period_dict["kind"], "period_source": source, "period_warnings": warnings,
        "dday_end": dday_end,
        "recruit_count": "{}명".format(count) if count else None,          # 빈칸/미정 표기는 AI 결과를 덮어쓰지 않도록 None
        "amount": _amount_hint(benefit),
        "apply_how": _apply_how(method),
        "apply_type": method.get("apply_type_hint"),
        "target_levels": (structured.get("target") or {}).get("levels") or [],
    }
    return fields, hints


def _amount_hint(benefit: Dict[str, Any]) -> Optional[str]:
    """혜택 첫 줄이 '[장학금지급] 최대 200 만원' 처럼 내용과 숫자가 있을 때만 금액 힌트로 쓴다 ('[기타혜택]' 같은 이름표만 있는 줄은 제외)."""
    head = (benefit or {}).get("headline") or ""
    body = re.sub(r"^\[[^\]]+\]\s*", "", head)
    return head if body and re.search(r"\d", body) else None


def _apply_how(method: Dict[str, Any]) -> Optional[str]:
    if not method or not method.get("headline"):
        return None
    extra = [x for x in method.get("lines", []) if not x.startswith("*") and "주소" not in x][:2]
    return _ws(" / ".join([method["headline"]] + extra))[:160]
