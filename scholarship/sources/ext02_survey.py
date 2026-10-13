# -*- coding: utf-8 -*-
"""[EXT02] 학생 정보 조건 조사 (1회성 분석용).

목적: '외부 장학금을 판단하려면 학생 정보로 무엇을 받아야 하는가'를 실제 모집공고에서 도출한다.
  - 최근 공고 N건(마감 포함)의 상세 페이지를 읽는다. 목록의 해시태그로 결론을 내지 않고 상세 본문(고정 7개 항목)을 본다.
  - DB 저장 없음, AI 호출 없음 (크레딧 0). 기존 어댑터(로그인, 목록, 상세 해석)를 그대로 쓴다.
  - 결과 파일: 구조화(jsonl), 사람이 읽는 전체 본문(txt), 코드가 센 1차 통계(summary), (선택) 공고문 첨부 텍스트
  - 통계의 '개념 키워드'는 1차 훑어보기용이다. 최종 결론은 본문을 직접 읽고 정리한다.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..common.text_clean import clean_html
from . import ext02_parse as P

log = logging.getLogger(__name__)

# (키, 이 개념이 있으면 필요해지는 학생 정보, 정규식). 지원대상·신청자격·장학혜택·접수방법 문구에 적용한다.
CONCEPTS: List[Tuple[str, str, str]] = [
    ("grade", "학년", r"[1-6]\s*학년|저학년|고학년"),
    ("newcomer", "신입생·편입생 구분", r"신입생|편입생|편입|신입학"),
    ("graduating", "졸업(예정)·졸업학기 여부", r"졸업\s*(예정|학기|유예|연기|생)"),
    ("leave", "휴학·복학 여부", r"휴학|복학"),
    ("enroll", "재학·등록 상태", r"재학|재적|등록\s*(완료|생|한)|학적"),
    ("last_term", "직전학기 성적·학점", r"직전\s*학기|전\s*학기|이전\s*학기|지난\s*학기"),
    ("gpa", "평점·성적", r"평점|학점\s*평균|성적|석차|상위\s*\d+\s*%|\d\.\d+\s*/\s*\d\.\d|백분율|GPA"),
    ("credits", "이수·취득 학점", r"\d+\s*학점\s*(이상|이수|취득)|취득\s*학점|이수\s*학점"),
    ("income", "소득구간·분위", r"소득\s*(분위|구간)|\d+\s*(분위|구간)\s*이하|학자금\s*지원\s*구간|소득\s*(기준|인정액)|월\s*소득"),
    ("basic", "기초생활수급·차상위", r"기초\s*(생활)?\s*수급|차상위|수급자|기초생활"),
    ("single_parent", "한부모·조손·보호종료·시설 보호", r"한부모|조손|소년소녀|위탁|보호\s*종료|자립\s*준비|시설\s*(보호|퇴소)|아동복지"),
    ("family", "다자녀·가족 구성·가족 재학", r"다자녀|\d\s*자녀|형제|가족\s*(동시|중)|세대"),
    ("disability", "장애 여부·등급", r"장애"),
    ("veteran", "국가유공자·보훈 대상", r"국가\s*유공|보훈|독립\s*유공|참전"),
    ("multicultural", "다문화·북한이탈·외국인·재외동포", r"다문화|북한\s*이탈|새터민|외국인|유학생|재외\s*동포|귀화|이주"),
    ("nationality", "국적(내국인 여부)", r"국적|내국인|대한민국\s*국민|외국\s*국적"),
    ("gender", "성별", r"여학생|남학생|여성|남성|성별|여자|남자"),
    ("age", "나이·생년", r"만\s*\d+\s*세|\d+\s*세\s*(이하|이상|미만)|생년|나이|연령"),
    ("military", "병역(군필 여부)", r"군\s*필|병역|전역|군\s*복무"),
    ("residence", "거주지(주민등록)·출신·연고", r"거주|주민\s*등록|전입|주소지|출생|출신|연고"),
    ("residence_period", "거주 기간", r"거주.{0,12}\d+\s*(년|개월)|\d+\s*(년|개월)\s*이상.{0,6}거주|계속\s*거주|거주\s*기간"),
    ("school_location", "학교 소재지·재학 대학 지정", r"소재|소재지|소재\s*대학|지역\s*대학|지정\s*(대학|학교)"),
    ("high_school", "출신 고교·졸업 학교", r"출신\s*(고|학교)|고등학교\s*(졸업|출신)|졸업한\s*자"),
    ("major", "학과·전공·계열", r"전공|학과|계열|이공|공학|인문|예체능|의학|간호|사범|소프트웨어|인공지능|반도체"),
    ("language", "어학 성적·능력", r"TOEIC|토익|TOEFL|토플|OPIc|오픽|TEPS|텝스|JLPT|HSK|TOPIK|어학|영어\s*능력|독일어|일본어|중국어"),
    ("license", "자격증·면허", r"자격증|기사|산업기사|면허"),
    ("activity", "활동·수상·봉사 실적", r"봉사|동아리|대회|수상|공모전|리더십|활동\s*(실적|경력)|인턴|연구\s*실적|논문|특허"),
    ("recommend", "추천(학교장·교수·기관)", r"추천(서|인|\s*받은|\s*자)|학교장\s*추천|지도\s*교수|총장\s*추천|교수\s*추천|학과장"),
    ("designated", "재단·기관이 지정한 학교(학교 경유)", r"지정\s*(대학|학교)|협약|추천\s*의뢰|학교\s*경유|대학\s*경유"),
    ("duplicate", "다른 장학금 수혜(중복수혜) 여부", r"중복\s*(수혜|지원|수령)|타\s*(기관|장학)|다른\s*장학|등록금\s*(범위|초과)"),
    ("prior", "이전 수혜·재신청 이력", r"이전\s*(수혜|선발)|기\s*수혜|재\s*신청|계속\s*지원|이미\s*(선발|수혜)|수혜\s*경험|전년도\s*(수혜|장학생)"),
    ("parent_job", "부모·본인의 직업·소속(선원, 농어업, 임직원 등)", r"(선원|어업|농업|어민|농민|소방|경찰|군인|교직원|근로자|직원|임직원|조합원|회원).{0,8}(자녀|가족|본인|배우자)"),
    ("grad", "대학원생 여부", r"대학원|석사|박사"),
    ("entrance", "입학년도·입학전형·수능", r"입학\s*(년도|연도|전형)|수능"),
    ("religion", "종교", r"종교|교인|목회|신학"),
    ("health", "질병·건강 상태", r"질병|환자|암\b|난치|희귀"),
    ("asset", "재산·자산", r"재산|자산|주택|금융"),
]
_COMPILED = [(k, label, re.compile(rx, re.I)) for k, label, rx in CONCEPTS]
SIDO_NAMES = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
              "충청북도", "충청남도", "전라북도", "전라남도", "경상북도", "경상남도"]


# ================================================================== 선별
def is_population(row: P.ListRow, kinds: Sequence[str], audiences: Sequence[str]) -> bool:
    """조사 대상: 종류와 대상이 맞는 공고 (마감·모집완료 포함). 비워 두면 해당 조건을 보지 않는다."""
    if kinds and row.kind not in kinds:
        return False
    if audiences and not any(a in row.audiences for a in audiences):
        return False
    return True


def select_recent(rows: Iterable[P.ListRow], n: int, kinds: Sequence[str], audiences: Sequence[str]) -> List[P.ListRow]:
    """idx 가 큰(최근 등록) 순으로 n건."""
    pop = [r for r in rows if is_population(r, kinds, audiences)]
    return sorted(pop, key=lambda r: -int(r.idx))[:n]


def collect_candidates(adapter, ordby_list: Sequence[int], n: int, kinds: Sequence[str], audiences: Sequence[str],
                       max_pages: int) -> Tuple[List[P.ListRow], List[str]]:
    """목록을 정렬 방식(ordby)별로 훑어 후보를 모은다. 대상이 n건 이상 모이면 멈춘다. 로그인 불필요."""
    merged: Dict[str, P.ListRow] = {}
    notes: List[str] = []
    adapter.cfg.max_pages = max_pages
    for ob in ordby_list:
        adapter.cfg.ordby = [ob]
        rows = adapter.crawl_list()
        idxs = [int(r.idx) for r in rows]
        monotonic = all(a >= b for a, b in zip(idxs, idxs[1:]))
        pop = [r for r in rows if is_population(r, kinds, audiences)]
        notes.append("ordby={} -> 목록 {}건 (조사 대상 {}건, 최근순 정렬 {})".format(ob, len(rows), len(pop), "맞음" if monotonic else "아님"))
        for r in rows:
            merged.setdefault(r.idx, r)
        if sum(1 for r in merged.values() if is_population(r, kinds, audiences)) >= n:
            break
    return list(merged.values()), notes


def format_selection(selected: Sequence[P.ListRow]) -> str:
    states = Counter(r.state or "-" for r in selected)
    lines = ["=== 조사 대상 {}건 (최근 등록순) ===".format(len(selected)),
             "상태: " + ", ".join("{} {}".format(k, v) for k, v in states.most_common())]
    if selected:
        lines.append("idx 범위: {} ~ {}".format(selected[-1].idx, selected[0].idx))
    lines.append("")
    for i, r in enumerate(selected, 1):
        lines.append("  {:>3}. idx {} | {} | {} | {}".format(i, r.idx, r.state or "-", r.org[:14], r.title[:44]))
    return "\n".join(lines)


# ================================================================== 한 건 -> 기록
def _scope_text(d: P.Detail) -> str:
    """학생 정보 개념을 찾을 문구: 지원대상, 신청자격, 장학혜택(제외·중복 조건), 접수방법, 모르는 항목"""
    parts: List[str] = []
    for key in ("target", "eligibility", "benefit", "method"):
        parts.extend(d.sections.get(key, []))
    for lines in d.extra_sections.values():
        parts.extend(lines)
    return "\n".join(parts)


def concept_hits(text: str) -> Dict[str, str]:
    """개념 -> 처음 걸린 부분 문구(앞뒤 문맥 포함)"""
    out: Dict[str, str] = {}
    flat = re.sub(r"\s+", " ", text or "")
    for key, _label, rx in _COMPILED:
        m = rx.search(flat)
        if m:
            s, e = max(0, m.start() - 18), min(len(flat), m.end() + 26)
            out[key] = flat[s:e].strip()
    return out


def build_record(d: P.Detail, row: P.ListRow, today: date) -> Dict[str, Any]:
    fields, _hints = P.compose(d, row, today)
    body, _ = clean_html(P.build_body_html(d))
    return {
        "idx": row.idx, "title": d.title or row.title, "org": d.org_name or row.org, "state": row.state, "dday": row.dday,
        "list_tags": row.tags,                                      # 참고용 (결론 근거로 쓰지 않음)
        "period": fields["period"], "headcount": fields.get("headcount"), "benefit": fields.get("benefit"),
        "method": fields.get("method"), "target": fields.get("target"), "eligibility": fields.get("eligibility"),
        "contact": fields.get("contact"), "extra_sections": fields.get("extra_sections"),
        "original_url": d.original_url, "attachments": [a.name for a in d.attachments],
        "concepts": concept_hits(_scope_text(d)), "text": body,
    }


def render_text(records: Sequence[Dict[str, Any]]) -> str:
    out: List[str] = []
    for i, r in enumerate(records, 1):
        out.append("### [{}] idx={} | {} | {} | {}".format(i, r["idx"], r["state"] or "-", r["title"], r["org"]))
        out.append("신청기간 원문: {} | 목록 태그(참고): {} | 첨부: {}".format(
            (r["period"] or {}).get("raw") or "-", " ".join("#" + t for t in r["list_tags"]) or "-", ", ".join(r["attachments"]) or "없음"))
        out.append(r["text"])
        out.append("")
    return "\n".join(out)


# ================================================================== 1차 통계
def summarize(records: Sequence[Dict[str, Any]], failures: Sequence[str] = ()) -> str:
    n = len(records)
    L: List[str] = []
    pct = lambda c: "{} ({:.0f}%)".format(c, 100.0 * c / n) if n else str(c)
    L.append("=== 드림스폰 최근 공고 {}건 1차 통계 (코드가 센 값, 최종 결론은 본문을 직접 읽고 정리) ===".format(n))
    if failures:
        L.append("상세를 읽지 못한 공고 {}건: {}".format(len(failures), ", ".join(failures)))
    idxs = [int(r["idx"]) for r in records]
    if idxs:
        L.append("idx 범위 {} ~ {}".format(min(idxs), max(idxs)))
    L.append("상태: " + ", ".join("{} {}".format(k or "-", v) for k, v in Counter(r["state"] for r in records).most_common()))

    L += ["", "[1] 지원대상 학교급(상세 '지원대상' 항목 기준)"]
    lv = Counter(",".join((r["target"] or {}).get("levels") or ["(없음)"]) for r in records)
    L += ["  {}: {}".format(k, pct(v)) for k, v in lv.most_common()]

    L += ["", "[2] 접수방법(접수방법 문구로 본 신청 방식)"]
    at = Counter(((r["method"] or {}).get("apply_type_hint")) or "(키워드 미해당)" for r in records)
    L += ["  {}: {}".format(k, pct(v)) for k, v in at.most_common()]
    L += ["  접수방법 첫 줄 상위: " + " / ".join("{}({})".format(k, v) for k, v in Counter((r["method"] or {}).get("headline") or "-" for r in records).most_common(8))]

    L += ["", "[3] 신청기간 해석"]
    L += ["  {}: {}".format(k, pct(v)) for k, v in Counter((r["period"] or {}).get("kind") for r in records).most_common()]

    L += ["", "[4] 신청자격 이름표 (상세 신청자격 항목의 [이름표], 공고 수)"]
    tags = Counter()
    for r in records:
        for k, v in ((r["eligibility"] or {}).items()):
            if k != "lead":
                tags[(v or {}).get("tag") or k] += 1
    L += ["  [{}]: {}".format(k, pct(v)) for k, v in tags.most_common()]
    no_elig = sum(1 for r in records if not (r["eligibility"] or {}))
    L.append("  신청자격 항목이 비어 있는 공고: {}".format(pct(no_elig)))

    L += ["", "[5] 학생 정보 개념별 언급 공고 수 (키워드 1차 집계. 같은 단어가 조건이 아닌 설명에 나올 수도 있음)"]
    cnt: Counter = Counter()
    ex: Dict[str, List[str]] = {}
    for r in records:
        for k, snip in r["concepts"].items():
            cnt[k] += 1
            ex.setdefault(k, [])
            if len(ex[k]) < 3:
                ex[k].append("[{}] …{}…".format(r["idx"], snip))
    labels = {k: lab for k, lab, _ in CONCEPTS}
    for k, c in cnt.most_common():
        L.append("  - {} ({}): {}".format(labels[k], k, pct(c)))
        L += ["        " + e for e in ex[k]]
    L += ["  (한 번도 안 나온 개념: {})".format(", ".join(labels[k] for k, _, _ in CONCEPTS if k not in cnt) or "없음")]

    L += ["", "[6] 지역 조건 단서 (신청자격·지원대상 문구)"]
    sido = Counter()
    for r in records:
        t = "\n".join(l for v in ((r["eligibility"] or {}).values()) if isinstance(v, dict) for l in (v.get("lines") or []) + [v.get("label") or ""])
        for name in SIDO_NAMES:
            if name in t:
                sido[name] += 1
    L += ["  시·도 이름이 신청자격에 나온 공고: " + (", ".join("{} {}".format(k, v) for k, v in sido.most_common()) or "없음")]
    L += ["  '거주' 표현 공고 {}, '소재' 표현 공고 {}, '출신/출생' 표현 공고 {}".format(
        pct(sum("거주" in (r["concepts"].get("residence") or "") for r in records)),
        pct(sum("school_location" in r["concepts"] for r in records)),
        pct(sum(bool(re.search(r"출신|출생", (r["concepts"].get("residence") or ""))) for r in records)))]

    L += ["", "[7] 첨부"]
    with_att = [r for r in records if r["attachments"]]
    guide_like = [r for r in with_att if any(re.search(r"공고|요강|안내|모집|선발", a) for a in r["attachments"])]
    L.append("  첨부가 있는 공고 {} | 이름에 공고·요강·안내·모집·선발이 들어간 첨부가 있는 공고 {}".format(pct(len(with_att)), pct(len(guide_like))))
    return "\n".join(L) + "\n"


# ================================================================== 파일 입출력
def load_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_record(path: Path, rec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_survey(adapter, selected: Sequence[P.ListRow], out_dir: Path, today: date, resume: bool = False,
               guides=None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """selected 의 상세를 읽어 out_dir/ext02_survey_notices.jsonl 에 한 건씩 저장한다 (중단돼도 이어받을 수 있음).
    guides: (adapter, detail, idx) -> 공고문 텍스트 목록을 돌려주는 함수 (선택). 로그인 실패는 그대로 올린다."""
    from .ext02 import DetailError
    jl = out_dir / "ext02_survey_notices.jsonl"
    done = {r["idx"]: r for r in load_records(jl)} if resume else {}
    if not resume and jl.exists():
        jl.unlink()
    records: List[Dict[str, Any]] = []
    failures: List[str] = []
    for i, row in enumerate(selected, 1):
        if row.idx in done:
            records.append(done[row.idx])
            continue
        try:
            d = adapter.fetch_detail(row.idx)
        except DetailError as e:
            log.warning("[%d/%d] idx %s 상세를 읽지 못함 - 건너뜀: %s", i, len(selected), row.idx, e)
            failures.append(row.idx)
            continue
        rec = build_record(d, row, today)
        if guides is not None:
            rec["guide_texts"] = guides(adapter, d, row.idx)
        append_record(jl, rec)
        records.append(rec)
        log.info("[%d/%d] idx %s | %s | 신청자격 %d항목 | 첨부 %d", i, len(selected), row.idx, rec["title"][:24],
                 len([k for k in (rec["eligibility"] or {}) if k != "lead"]), len(rec["attachments"]))
    return records, failures


def write_outputs(out_dir: Path, records: Sequence[Dict[str, Any]], failures: Sequence[str]) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"text": out_dir / "ext02_survey_notices.txt", "summary": out_dir / "ext02_survey_summary.txt"}
    paths["text"].write_text(render_text(records), encoding="utf-8")
    paths["summary"].write_text(summarize(records, failures), encoding="utf-8")
    guides = [r for r in records if r.get("guide_texts")]
    if guides:
        paths["guides"] = out_dir / "ext02_survey_guides.txt"
        blocks = []
        for r in guides:
            blocks.append("### idx={} | {}".format(r["idx"], r["title"]))
            for g in r["guide_texts"]:
                blocks.append("--- 첨부: {} ({}, {}자{})".format(g["name"], g["role"], g["chars"], ", 앞부분만" if g.get("cut") else ""))
                blocks.append(g["text"])
            blocks.append("")
        paths["guides"].write_text("\n".join(blocks), encoding="utf-8")
    return paths


# ================================================================== (선택) 공고문 첨부 텍스트
def make_guide_collector(settings, out_dir: Path, max_chars: int = 6000, max_files: int = 4):
    """공고문(요강)·미분류 첨부만 받아서 텍스트를 뽑는다. 양식·제외 파일은 건너뜀. 실패해도 조사는 계속한다."""
    from ..common import file_role
    from ..common.extractors import SUPPORTED_EXT, extract as dispatch, zip_
    from ..common.models import SourceAttachment
    from ..common.storage import file_ext, safe_token
    kw = file_role.load_keywords(None)

    def collect(adapter, d: P.Detail, idx: str) -> List[Dict[str, Any]]:
        got: List[Dict[str, Any]] = []
        for n, a in enumerate(d.attachments[:6], 1):
            ext = file_ext(a.name)
            role, _ = file_role.classify(a.name, kw)
            if role in (file_role.EXCLUDED,) or (role in file_role.SECONDARY_ROLES and ext != "zip"):
                continue
            att = SourceAttachment(file_seq=str(n), sort_no=n, org_file_nm=a.name, extra={"file1": a.filepath, "idx": idx})
            dest = out_dir / "files" / safe_token(idx) / "{}.{}".format(n, ext or "bin")
            try:
                if not (dest.exists() and dest.stat().st_size > 0):
                    adapter.download_attachment(att, dest, settings.max_file_bytes)
                items: List[Tuple[str, str, str]] = []                      # (이름, 역할, 텍스트)
                if ext == "zip":
                    for m in zip_.extract_members(dest, settings, kw=kw, parent_role=role, label=a.name):
                        if m.status == "EXTRACTED" and m.role in file_role.PRIMARY_ROLES and m.result is not None and m.result.text:
                            items.append(("{}/{}".format(a.name, m.name), m.role, m.result.text))
                elif ext in SUPPORTED_EXT or ext == "txt":
                    res = dispatch(dest, ext, settings, kw=kw, depth=0, label=a.name)
                    if res.text:
                        items.append((a.name, role, res.text))
                for name, r, text in items:
                    if len(got) >= max_files:
                        break
                    got.append({"name": name, "role": r, "chars": len(text), "cut": len(text) > max_chars, "text": text[:max_chars]})
            except Exception as e:                                          # 한 파일의 실패가 조사 전체를 멈추지 않게
                log.warning("[idx %s] 첨부 처리 실패 (%s): %s", idx, a.name, str(e)[:120])
        return got

    return collect
