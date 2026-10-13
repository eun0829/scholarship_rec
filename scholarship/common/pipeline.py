# -*- coding: utf-8 -*-
"""[COMMON] 공고 처리 파이프라인 (소스와 무관).

  어댑터가 준 SourceNotice
    1) 본문 정리 -> 내용 해시 -> 변경 없으면 건너뜀
    2) 공고 기본정보 저장 (status=COLLECTED)
    3) 첨부: 파일명 분류 -> 다운로드(NAS) -> 형식별 텍스트 추출 -> SCHOLARSHIP_NOTICE_FILE 저장
    4) AI 입력 구성(본문/요강 첨부/참고 첨부/필요 시 이미지) -> 자격요건 JSON 추출 -> 코드 검증
    5) 공고 저장 (status=EXTRACTED / NEED_REVIEW / FAILED)
    6) 원본에서 사라진 글은 USE_YN='N'
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import file_role
from .ai.prompt import Section, build_payload
from .ai.runner import run_extraction
from .ai.schema import SCHEMA_VERSION, SCHEMA_VERSION_V3
from .config import Settings
from .extractors import IMAGE_EXT, SUPPORTED_EXT, UNSUPPORTED_HINT, ExtractError, ExtractResult, extract
from .extractors.results import ImagePart
from .models import SourceAttachment, SourceNotice
from .repository import Repository
from .storage import attachment_path, body_image_path, file_ext
from .text_clean import clean_html, normalize_title

log = logging.getLogger(__name__)

ROLE_LABEL = {file_role.GUIDE: "요강", file_role.UNKNOWN: "미분류(요강일 수 있음)",
              file_role.FORM: "양식(낮은 우선순위)", file_role.AUX: "보조(낮은 우선순위)"}


@dataclass
class AttOutcome:
    index: int                      # 1부터 (AI evidence 의 '첨부N')
    att: SourceAttachment
    role: str
    status: str                     # EXTRACTED / FAILED / SKIPPED
    result: Optional[ExtractResult] = None
    err: str = ""


@dataclass
class Summary:
    total: int = 0
    unchanged: int = 0
    skipped_changed: int = 0          # 이미 완료된 글 (내용이 바뀌었지만 기본 설정에 따라 건너뜀)
    skipped_title: int = 0            # 제목이 같은 다른 글이 이미 있어 건너뜀
    extracted: int = 0
    need_review: int = 0
    collected_only: int = 0
    failed: int = 0
    deactivated: int = 0
    period_updated: int = 0           # 신청기간만 바뀐 글 (AI 호출 없이 날짜만 갱신)
    skipped_stub: int = 0             # 상세를 조회하지 못해 건너뜀 (저장된 적 없는 글)
    input_tokens: int = 0
    output_tokens: int = 0
    att_ok: int = 0
    att_failed: int = 0
    att_skipped: int = 0

    def line(self) -> str:
        return ("대상 {total}건 | 변경없음 {unchanged} | 건너뜀(내용변경) {skipped_changed} | 건너뜀(제목중복) {skipped_title} | "
                "추출완료 {extracted} | 확인필요 {need_review} | "
                "수집만 {collected_only} | 실패 {failed} | 비활성화 {deactivated} | 기간갱신 {period_updated} | 상세미조회 {skipped_stub} | "
                "첨부 성공 {att_ok} / 실패 {att_failed} / 건너뜀 {att_skipped} | 토큰 입력 {input_tokens:,} / 출력 {output_tokens:,}"
                ).format(**self.__dict__)


def _to_date(s: Optional[str]) -> Optional[date]:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


class Pipeline:
    def __init__(self, settings: Settings, adapter, repo: Repository, ai_client=None,
                 force: bool = False, dump_dir: Optional[Path] = None, today: Optional[date] = None):
        self.s, self.adapter, self.repo, self.ai = settings, adapter, repo, ai_client
        self.force = force
        self.dump_dir = Path(dump_dir) if dump_dir else None
        self.today = today or date.today()
        self.kw = file_role.load_keywords(settings.keywords_file)
        self.src_cd = adapter.src_cd
        self._registry = None

    def registry(self):
        """항목 사전 (스키마 v3 에서만 읽음). 한 번만 읽고, 잘못된 사전이면 RegistryError 로 시작 전에 알린다."""
        if self._registry is None:
            from .registry import load_registry
            self._registry = load_registry(self.s.item_registry_file or None)
        return self._registry

    # ================================================================== 실행
    def run(self, days: int = 30, limit: Optional[int] = None, only: Optional[List[str]] = None) -> Summary:
        if self.ai is not None and self.s.ai_schema_version >= SCHEMA_VERSION_V3:
            self.registry()                       # 잘못된 항목 사전이면 AI 를 한 번도 부르기 전에 멈춘다 (공고마다 크레딧이 낭비되는 것을 막음)
        notices = self.adapter.fetch_notices(days)
        if getattr(self.adapter, "full_scope", False):
            log.info("[%s] 공고 %d건 조회", self.src_cd, len(notices))
        else:
            log.info("[%s] 최근 %d일 공고 %d건 조회", self.src_cd, days, len(notices))
        all_ids = {n.src_id for n in notices}
        if only:
            notices = [n for n in notices if n.src_id in set(only)]
        if limit:
            notices = notices[:limit]
        summ = Summary(total=len(notices))

        for n in notices:
            try:
                label = self.process_notice(n, summ)
            except Exception as e:                       # 한 건의 실패가 전체를 멈추지 않게
                log.exception("[%s/%s] 처리 중 예외", self.src_cd, n.src_id)
                summ.failed += 1
                try:
                    self.repo.upsert_notice(self.src_cd, n.src_id, {
                        "status_cd": "FAILED",
                        "uncertain_json": json.dumps({"error": "처리 중 예외: {}".format(e)}, ensure_ascii=False)})
                except Exception:
                    log.exception("실패 상태 저장도 실패")
                label = "FAILED"
            log.info("[%s/%s] %s | %s", self.src_cd, n.src_id, label, n.title[:50])

        if not only and not limit:                       # 전체를 본 경우에만 사라진 글 정리
            if not getattr(self.adapter, "deactivation_safe", True):
                log.warning("[%s] 이번 수집이 비정상이라 사라진 글 비활성화를 건너뜁니다", self.src_cd)
            else:
                # full_scope 소스(EXT02): 게시일이 없거나 불확실하므로 기간 제한 없이 활성 글 전체를 기준으로 한다
                since = None if getattr(self.adapter, "full_scope", False) else self.today - timedelta(days=days)
                existing = set(self.repo.list_active_ids_since(self.src_cd, since))
                missing = sorted(existing - all_ids)
                if missing:
                    self.repo.set_use_yn(self.src_cd, missing, "N")
                    summ.deactivated = len(missing)
                    log.info("원본에서 사라진 글 %d건 비활성화: %s", len(missing), missing[:10])
        log.info("[%s] %s", self.src_cd, summ.line())
        return summ

    # ================================================================== 공고 1건
    def process_notice(self, n: SourceNotice, summ: Summary) -> str:
        if n.stub:                                          # 이미 저장된 글이라 상세를 조회하지 않은 항목 (목록에서 확인만)
            state = self.repo.get_notice_state(self.src_cd, n.src_id)
            if state is None:
                summ.skipped_stub += 1
                return "SKIPPED (상세 미조회 - 저장된 적 없는 글)"
            if state.get("use_yn") == "N" and state["status_cd"] in ("EXTRACTED", "NEED_REVIEW"):
                self.repo.set_use_yn(self.src_cd, [n.src_id], "Y")       # 다시 나타난 글 복구
            summ.unchanged += 1
            return "UNCHANGED (상세 미조회)"

        title = normalize_title(n.title)
        body_text, body_imgs = clean_html(n.body_html)
        sig = json.dumps(sorted((a.file_seq, a.org_file_nm) for a in n.attachments), ensure_ascii=False)
        basis = n.hash_basis if n.hash_basis is not None else body_text      # 소스가 조회수/D-day 같은 변동 값을 뺀 기준을 줄 수 있다
        content_hash = hashlib.sha256("|".join([title, basis, sig]).encode("utf-8")).hexdigest()

        state = self.repo.get_notice_state(self.src_cd, n.src_id)
        completed = bool(state) and state["status_cd"] in ("EXTRACTED", "NEED_REVIEW")
        if state and not self.force:
            if state.get("use_yn") == "N" and completed:
                self.repo.set_use_yn(self.src_cd, [n.src_id], "Y")       # 다시 나타난 글 복구
            if completed:
                if state["content_hash"] == content_hash:
                    if self.update_period_only(n, state):                  # 신청기간만 바뀐 경우: AI 없이 날짜만 갱신
                        summ.period_updated += 1
                        return "PERIOD_UPDATED"
                    summ.unchanged += 1
                    return "UNCHANGED"
                if not self.s.update_changed:                              # 글번호가 이미 있으면 내용이 바뀌어도 건너뜀
                    summ.skipped_changed += 1
                    log.info("[%s/%s]   이미 저장된 글번호라 건너뜀 (내용이 바뀌었음 - 재처리하려면 --update-changed 또는 --force)",
                             self.src_cd, n.src_id)
                    return "SKIPPED (기존 글번호, 내용 변경 무시)"
            # COLLECTED / FAILED(미완료)는 이어서 처리한다
        elif not state and not self.force and self.s.skip_same_title:
            dup = self.repo.find_ids_by_title(self.src_cd, title, n.src_id)
            if dup:
                summ.skipped_title += 1
                return "SKIPPED (같은 제목의 기존 글번호 {})".format(", ".join(dup[:3]))

        base = {"title": title, "dept_nm": n.dept_nm, "posted_date": n.posted_date, "origin_url": n.origin_url,
                "body_text": body_text, "content_hash": content_hash, "status_cd": "COLLECTED", "use_yn": "Y"}
        if n.source_fields is not None:
            base["src_fields_json"] = json.dumps(n.source_fields, ensure_ascii=False)
        h = n.hints or {}
        if h.get("apply_end"):                          # 코드가 신청기간을 확정한 경우: AI 단계를 기다리지 않고 날짜 컬럼에 바로 반영
            base["apply_end"] = _to_date(h["apply_end"])
            if h.get("apply_start"):
                base["apply_start"] = _to_date(h["apply_start"])
        self.repo.upsert_notice(self.src_cd, n.src_id, base)

        mode = self.s.ext02_attach_to_ai if self.src_cd == "EXT02" else "guide_and_form"     # 외부01 은 기존 동작 유지

        outcomes = self.collect_outcomes(n)
        for o in outcomes:
            self.log_attachment(n, o, summ)
        if n.source_fields is not None:                 # 공고문(요강) 첨부 여부는 AI 가 아니라 코드가 기록한다 (검수 소음 방지, 추천 단계의 안내 근거)
            roles: Dict[str, int] = {}
            for o in outcomes:
                if o.status == "EXTRACTED":
                    roles[o.role] = roles.get(o.role, 0) + 1
            n.source_fields["attachment_check"] = {"guide_attached": any(r in file_role.PRIMARY_ROLES for r in roles),
                                                   "extracted_by_role": roles, "total": len(outcomes)}
            self.repo.upsert_notice(self.src_cd, n.src_id, {"src_fields_json": json.dumps(n.source_fields, ensure_ascii=False)})
        self.dump_texts(n, body_text, outcomes)

        def relevant(o: AttOutcome) -> bool:             # 외부02 guide_only: 양식/보조 첨부의 실패는 검수 사유로 삼지 않는다
            if mode == "guide_and_form":
                return True
            return mode == "guide_only" and o.role in file_role.PRIMARY_ROLES

        problems = ["첨부{} 처리 실패 ({}): {}".format(o.index, o.att.org_file_nm, o.err)
                    for o in outcomes if o.status == "FAILED" and relevant(o)]
        problems += ["첨부{} 일부 내용만 추출됨 ({})".format(o.index, o.att.org_file_nm)
                     for o in outcomes if o.result is not None and o.result.partial and relevant(o)]

        if self.ai is None:
            summ.collected_only += 1
            return "COLLECTED (AI 미실행)"

        # ---------- 요강/참고 구분 + 안전망
        ok = [o for o in outcomes if o.status == "EXTRACTED"]
        primary = [o for o in ok if o.role in file_role.PRIMARY_ROLES]
        secondary = [o for o in ok if o.role in file_role.SECONDARY_ROLES]
        if mode == "none":
            primary, secondary = [], []                   # 첨부는 저장만 하고 AI 에는 보내지 않음
        elif mode == "guide_only":
            secondary = []                                # 요강(공고문)만 AI 에 전달. 양식/보조는 저장만
        elif len(body_text) < self.s.body_short_len and not primary:
            primary, secondary = secondary, []            # 본문이 짧고 요강 첨부가 없으면 양식/보조도 근거로 승격

        # ---------- 이미지: 첨부 이미지는 항상, 본문 이미지는 텍스트가 부족할 때만
        images: List[ImagePart] = []
        for o in primary:
            images.extend(o.result.images)
        text_len = len(body_text) + sum(len(o.result.text) for o in primary)
        if body_imgs and text_len < self.s.min_text_for_no_vision:
            images.extend(self.load_body_images(n, body_imgs))
        if images and not self.s.ai_vision:
            problems.append("이미지 {}장은 AI 이미지 분석이 꺼져 있어(AI_VISION=false) 전달하지 않음".format(len(images)))
            images = []
        if len(images) > self.s.max_images:
            problems.append("이미지 {}장 중 앞 {}장만 전달".format(len(images), self.s.max_images))
            images = images[:self.s.max_images]
        kept, used = [], 0                                           # 요청 본문 크기 제한(게이트웨이 25MB) 대비
        for im in images:
            if used + len(im.data) > self.s.max_image_bytes_total:
                break
            kept.append(im)
            used += len(im.data)
        if len(kept) < len(images):
            problems.append("이미지 용량 한도({}MB)로 {}장 중 {}장만 전달".format(
                self.s.max_image_bytes_total // (1024 * 1024), len(images), len(kept)))
            images = kept

        def sec(o: AttOutcome) -> Section:
            return Section("첨부{}: {}".format(o.index, o.att.org_file_nm), ROLE_LABEL.get(o.role, o.role), o.result.text,
                           note="일부만 추출됨: 원본 변환 실패로 앞부분 미리보기 텍스트만 있음" if o.result.partial else "")

        payload = build_payload(self.today, n.title, n.posted_date, n.dept_nm, body_text,
                                [sec(o) for o in primary], [sec(o) for o in secondary], images,
                                self.s.max_chars_per_file, self.s.max_total_chars, self.s.max_chars_secondary,
                                source_note=n.source_note, schema_version=self.s.ai_schema_version,
                                registry_file=self.s.item_registry_file)
        self.dump(n, payload)

        # 전달할 근거가 전혀 없으면 AI 호출 의미 없음
        if not body_text.strip() and not primary and not images:
            self.repo.upsert_notice(self.src_cd, n.src_id, {
                "status_cd": "FAILED",
                "uncertain_json": json.dumps({"error": "분석할 본문/첨부/이미지가 없음", "attachment_problems": problems},
                                             ensure_ascii=False)})
            summ.failed += 1
            return "FAILED (근거 없음)"

        res = run_extraction(self.ai, payload, n.posted_date, tokens_limit=self.s.ai_max_tokens_limit)
        summ.input_tokens += res.input_tokens
        summ.output_tokens += res.output_tokens
        if not res.ok:
            raw = res.raw_text or ""
            info = {"error": res.error, "stop_reason": res.stop_reason, "attempts": res.attempts,
                    "output_tokens": res.output_tokens, "raw_length": len(raw),
                    "raw_head": raw[:600], "raw_tail": raw[-400:] if len(raw) > 600 else "",     # 원인 분석용 (앞/뒤 일부)
                    "attachment_problems": problems}
            self.repo.upsert_notice(self.src_cd, n.src_id, {
                "status_cd": "FAILED", "extract_model": res.model,
                "uncertain_json": json.dumps(info, ensure_ascii=False)})
            if self.dump_dir and raw:
                self.dump_dir.mkdir(parents=True, exist_ok=True)
                (self.dump_dir / "{}_{}_ai_raw.txt".format(self.src_cd.lower(), n.src_id)).write_text(
                    "# 실패 사유: {}\n# 중단 사유: {} | 출력 토큰 {} | 길이 {}자\n\n{}".format(
                        res.error, res.stop_reason, res.output_tokens, len(raw), raw), encoding="utf-8")
            summ.failed += 1
            return "FAILED ({})".format(res.error)

        d = res.data
        self.apply_hints(n, d, res.issues, first_time=state is None)
        elig = {"schema_version": payload.schema_version or SCHEMA_VERSION}   # 형식 버전 (구버전 결과만 골라 재처리할 때 사용)
        if payload.schema_version >= SCHEMA_VERSION_V3:
            reg = self.registry()                                       # 어떤 항목 사전으로 분석했는지 (사전에 항목이 늘면 새 항목만 증분 보강하기 위함)
            elig["registry_version"] = reg.version
            elig["processed_items"] = reg.codes()
        elig.update({k: v for k, v in d.items() if k not in ("evidence", "uncertain")})
        review = {"uncertain": d["uncertain"], "evidence": d["evidence"], "issues": res.issues,
                  "attachment_problems": problems}
        needs_review = bool(any(u.get("level", "verify") == "verify" for u in d["uncertain"]) or res.issues or problems)
        status = "NEED_REVIEW" if needs_review else "EXTRACTED"
        self.repo.upsert_notice(self.src_cd, n.src_id, {
            "category_cd": d["category"], "apply_type_cd": d["apply_type"],
            "apply_start": _to_date(d["apply_start"]), "apply_end": _to_date(d["apply_end"]),
            "org_deadline": _to_date(d["org_deadline"]),
            "elig_json": json.dumps(elig, ensure_ascii=False),
            "uncertain_json": json.dumps(review, ensure_ascii=False),
            "summary": d["summary"][:1000], "extract_model": res.model, "extracted_at": datetime.now(),
            "status_cd": status})
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / "{}_{}_ai_raw.txt".format(self.src_cd.lower(), n.src_id)).write_text(
                "# 성공 | 중단 사유: {} | 시도 {}회 | 출력 토큰(누적) {} | 길이 {}자\n# 아래는 AI 가 실제로 쓴 원문입니다 (코드가 빈 항목을 채우기 전)\n\n{}".format(
                    res.stop_reason, res.attempts, res.output_tokens, len(res.raw_text or ""), res.raw_text or ""), encoding="utf-8")
            (self.dump_dir / "{}_{}_ai_result.json".format(self.src_cd.lower(), n.src_id)).write_text(
                json.dumps({"elig": elig, "review": review}, ensure_ascii=False, indent=2), encoding="utf-8")
        if needs_review:
            summ.need_review += 1
        else:
            summ.extracted += 1
        return status

    # ================================================================== 소스가 확정해 준 값 (EXT02: 신청기간, 인원, 접수방법 ...)
    def apply_hints(self, n: SourceNotice, d: Dict, issues: List[Dict[str, str]], first_time: bool) -> None:
        """코드가 읽은 값을 AI 결과와 교차 검증하고 반영한다. 마감일은 코드 값이 우선이고, 다르면 검수 사유로 남긴다."""
        h = n.hints or {}
        if not h:
            return
        end, start = h.get("apply_end"), h.get("apply_start")
        if end:
            if d.get("apply_end") and d["apply_end"] != end:
                issues.append({"field": "apply_end", "reason": "AI 가 읽은 마감일({})과 신청기간({})이 달라 신청기간 값을 사용함".format(d["apply_end"], end)})
            d["apply_end"] = end
            if start:
                if d.get("apply_start") and d["apply_start"] != start:
                    issues.append({"field": "apply_start", "reason": "AI 가 읽은 시작일({})과 신청기간({})이 달라 신청기간 값을 사용함".format(d["apply_start"], start)})
                d["apply_start"] = start
        else:
            dd = h.get("dday_end")
            if first_time and dd:                          # 목록 D-day 는 최초 저장 때만 보조로 사용
                if not d.get("apply_end"):
                    d["apply_end"] = dd
                    issues.append({"field": "apply_end", "reason": "상세에서 신청기간을 읽지 못해 목록 D-day 로 마감일을 추정함 ({})".format(dd)})
                elif d["apply_end"] != dd:
                    issues.append({"field": "apply_end", "reason": "AI 마감일({})과 목록 D-day 추정({})이 다름".format(d["apply_end"], dd)})
            if not d.get("apply_end"):
                issues.append({"field": "apply_end", "reason": "마감일을 확인할 수 없음"})
        for w in h.get("period_warnings") or []:
            issues.append({"field": "apply_end", "reason": w})
        if h.get("recruit_count"):
            d["recruit_count"] = h["recruit_count"]
        if h.get("amount") and not d.get("amount"):
            d["amount"] = h["amount"]
        if h.get("apply_how") and not d.get("apply_how"):
            d["apply_how"] = h["apply_how"]
        ht, at = h.get("apply_type"), d.get("apply_type")
        if ht:
            if at in (None, "", "UNKNOWN"):
                d["apply_type"] = ht
            elif at != ht:
                issues.append({"field": "apply_type", "reason": "AI({})와 접수방법 키워드({})가 달라 확인 필요 - AI 값을 유지함".format(at, ht)})
        if h.get("target_levels") and not d.get("target_levels"):
            d["target_levels"] = list(h["target_levels"])

    def update_period_only(self, n: SourceNotice, state: Dict) -> bool:
        """내용(해시)은 같고 신청기간만 바뀐 글: AI 호출 없이 날짜 컬럼, SRC_FIELDS_JSON, ELIG_JSON 의 날짜만 갱신한다."""
        if not (n.source_fields and n.source_fields.get("period") and n.hints and n.hints.get("apply_end")):
            return False
        try:
            old = (json.loads(state.get("src_fields_json") or "{}") or {}).get("period") or {}
        except ValueError:
            return False
        new = n.source_fields["period"]
        if not old or (old.get("start"), old.get("end")) == (new.get("start"), new.get("end")):
            return False
        merged = dict(n.source_fields)
        try:
            prev_check = (json.loads(state.get("src_fields_json") or "{}") or {}).get("attachment_check")
        except ValueError:
            prev_check = None
        if prev_check:
            merged["attachment_check"] = prev_check
        rec: Dict = {"apply_start": _to_date(n.hints.get("apply_start")), "apply_end": _to_date(n.hints["apply_end"]),
                     "src_fields_json": json.dumps(merged, ensure_ascii=False)}
        try:
            elig = json.loads(state.get("elig_json") or "null")
        except ValueError:
            elig = None
        if isinstance(elig, dict):
            elig["apply_start"], elig["apply_end"] = n.hints.get("apply_start"), n.hints["apply_end"]
            rec["elig_json"] = json.dumps(elig, ensure_ascii=False)
        self.repo.upsert_notice(self.src_cd, n.src_id, rec)
        log.info("[%s/%s]   신청기간 변경 반영 (AI 호출 없음): %s ~ %s -> %s ~ %s", self.src_cd, n.src_id,
                 old.get("start"), old.get("end"), new.get("start"), new.get("end"))
        return True

    # ================================================================== 첨부 전체 (ZIP 은 내부 파일별로 펼침)
    def collect_outcomes(self, n: SourceNotice) -> List[AttOutcome]:
        outcomes: List[AttOutcome] = []
        for a in n.attachments:
            for o in self.process_attachment_multi(n, a):
                o.index = len(outcomes) + 1                  # AI evidence 의 '첨부N' 번호 (ZIP 내부 파일도 각각 번호를 가짐)
                outcomes.append(o)
        return outcomes

    def process_attachment_multi(self, n: SourceNotice, att: SourceAttachment) -> List[AttOutcome]:
        role, _hit = file_role.classify(att.org_file_nm, self.kw)
        if file_ext(att.org_file_nm) == "zip" and role != file_role.EXCLUDED:
            return self.process_zip(n, att, role)
        return [self.process_attachment(n, att, 0)]

    def process_zip(self, n: SourceNotice, att: SourceAttachment, role: str) -> List[AttOutcome]:
        """ZIP 을 받아 내부 파일을 각각 분류/추출한다. 내부 파일마다 SCHOLARSHIP_NOTICE_FILE 한 행 (FILE_SEQ = '번호.순번')."""
        from .extractors import zip_
        rec: Dict = {"sort_no": att.sort_no, "org_file_nm": att.org_file_nm, "file_ext": "zip", "role_cd": role}
        path = attachment_path(self.s.nas_root, self.src_cd, n.src_id, att.file_seq, "zip")

        def fail(err: str) -> List[AttOutcome]:
            rec.update(status_cd="FAILED", err_msg=err[:900])
            self.repo.upsert_file(self.src_cd, n.src_id, att.file_seq, rec)
            return [AttOutcome(0, att, role, "FAILED", None, err)]

        try:
            if path.exists() and path.stat().st_size > 0:
                size = path.stat().st_size
            else:
                size = self.adapter.download_attachment(att, path, self.s.max_file_bytes)
        except Exception as e:
            return fail("다운로드 실패: {}".format(e))
        rec.update(file_size=size, nas_path=str(path))
        try:
            members = zip_.extract_members(path, self.s, kw=self.kw, parent_role=role, label=att.org_file_nm)
        except ExtractError as e:
            return fail(str(e))
        except Exception as e:
            log.exception("ZIP 처리 중 예기치 않은 오류: %s", att.org_file_nm)
            return fail("ZIP 처리 중 오류: {}".format(e))

        rec.update(status_cd="DOWNLOADED", extract_method="ZIP", text_len=0,
                   err_msg="내부 파일 {}개를 개별 항목으로 처리 (FILE_SEQ {}.N)".format(len(members), att.file_seq))
        self.repo.upsert_file(self.src_cd, n.src_id, att.file_seq, rec)
        outcomes: List[AttOutcome] = []
        for j, m in enumerate(members, 1):
            m_att = SourceAttachment(file_seq="{}.{}".format(att.file_seq, j), sort_no=att.sort_no,
                                     org_file_nm="{}/{}".format(att.org_file_nm, m.name))
            mrec: Dict = {"sort_no": att.sort_no, "org_file_nm": m_att.org_file_nm[:500], "file_ext": m.ext, "file_size": m.size,
                          "nas_path": "{}!{}".format(path, m.name)[:1000], "role_cd": m.role, "status_cd": m.status,
                          "err_msg": m.err[:900] if m.err else None}
            if m.result is not None:
                mrec.update(extract_method=m.result.method, extract_text=m.result.text, text_len=len(m.result.text))
            self.repo.upsert_file(self.src_cd, n.src_id, m_att.file_seq, mrec)
            outcomes.append(AttOutcome(0, m_att, m.role, m.status, m.result, m.err))
        return outcomes

    # ================================================================== 첨부 1건
    def process_attachment(self, n: SourceNotice, att: SourceAttachment, index: int) -> AttOutcome:
        ext = file_ext(att.org_file_nm)
        role, hit = file_role.classify(att.org_file_nm, self.kw)
        rec: Dict = {"sort_no": att.sort_no, "org_file_nm": att.org_file_nm, "file_ext": ext, "role_cd": role}

        def finish(status: str, err: str = "", result: Optional[ExtractResult] = None) -> AttOutcome:
            rec["status_cd"] = status
            rec["err_msg"] = err[:900] if err else None
            self.repo.upsert_file(self.src_cd, n.src_id, att.file_seq, rec)
            return AttOutcome(index, att, role, status, result, err)

        if role == file_role.EXCLUDED:
            return finish("SKIPPED", "제외 대상 파일명 ('{}')".format(hit))
        if ext not in SUPPORTED_EXT and ext != "txt":
            return finish("SKIPPED", UNSUPPORTED_HINT.get(ext) or "지원하지 않는 형식: .{}".format(ext or "?"))

        path = attachment_path(self.s.nas_root, self.src_cd, n.src_id, att.file_seq, ext)
        limit = self.s.max_hwp_bytes if ext == "hwp" else self.s.max_file_bytes
        try:
            if path.exists() and path.stat().st_size > 0:
                size = path.stat().st_size
            else:
                size = self.adapter.download_attachment(att, path, limit)
        except Exception as e:
            return finish("FAILED", "다운로드 실패: {}".format(e))
        rec.update(file_size=size, nas_path=str(path))

        try:
            result = extract(path, ext, self.s, kw=self.kw, label=att.org_file_nm)
        except ExtractError as e:
            return finish("FAILED", str(e))
        except Exception as e:
            log.exception("첨부 추출 중 예기치 않은 오류: %s", att.org_file_nm)
            return finish("FAILED", "추출 중 오류: {}".format(e))

        rec.update(extract_method=result.method, extract_text=result.text, text_len=len(result.text))
        return finish("EXTRACTED", "; ".join(result.warnings), result)

    # ================================================================== 첨부 결과 로그
    def log_attachment(self, n: SourceNotice, o: AttOutcome, summ: Summary) -> None:
        if o.status == "EXTRACTED":
            summ.att_ok += 1
        elif o.status == "FAILED":
            summ.att_failed += 1
        else:
            summ.att_skipped += 1
        r = o.result
        detail = ""
        if r is not None:
            detail = " | {} {}자{}{}".format(r.method, len(r.text), " 이미지 {}장".format(len(r.images)) if r.images else "",
                                           " (일부만)" if r.partial else "")
        level = logging.WARNING if (o.status == "FAILED" or (r is not None and r.partial)) else logging.INFO
        log.log(level, "[%s/%s]   첨부%d %s | %s | %s%s%s", self.src_cd, n.src_id, o.index, o.att.org_file_nm[:40],
                o.role, o.status, detail, (" | " + o.err) if o.err else "")

    # ================================================================== 점검용 텍스트 덤프 (AI 없이도 추출 품질 확인)
    def dump_texts(self, n: SourceNotice, body_text: str, outcomes: List[AttOutcome]) -> None:
        if not self.dump_dir:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        base = "{}_{}".format(self.src_cd.lower(), n.src_id)
        (self.dump_dir / (base + "_body.txt")).write_text(
            "# {}\n# 본문 정리 결과 ({}자)\n\n{}".format(n.title, len(body_text), body_text), encoding="utf-8")
        for o in outcomes:
            if o.result is None:
                continue
            head = "# 첨부{}: {}\n# 분류: {} | 방식: {} | {}자 | 이미지 {}장\n# 경고: {}\n\n".format(
                o.index, o.att.org_file_nm, o.role, o.result.method, len(o.result.text), len(o.result.images),
                "; ".join(o.result.warnings) or "-")
            (self.dump_dir / "{}_att{}_{}.txt".format(base, o.index, o.role.lower())).write_text(
                head + o.result.text, encoding="utf-8")

    # ================================================================== 본문 이미지
    def load_body_images(self, n: SourceNotice, srcs: List[str]) -> List[ImagePart]:
        parts: List[ImagePart] = []
        for i, src in enumerate(srcs[:self.s.max_images], 1):
            ext = file_ext(src.split("?")[0]) or "jpg"
            if ext not in IMAGE_EXT:
                continue
            path = body_image_path(self.s.nas_root, self.src_cd, n.src_id, i, ext)
            try:
                if not (path.exists() and path.stat().st_size > 0):
                    self.adapter.download_body_image(src, path, self.s.max_file_bytes)
                parts.extend(extract(path, ext, self.s, label="본문 이미지{}".format(i)).images)
            except Exception as e:
                log.warning("[%s/%s] 본문 이미지 처리 실패 (%s): %s", self.src_cd, n.src_id, src[:60], e)
        return parts

    # ================================================================== 점검용 덤프
    def dump(self, n: SourceNotice, payload) -> None:
        if not self.dump_dir:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        head = "# 이미지 {}장: {}\n\n".format(len(payload.images), ", ".join(i.label for i in payload.images))
        (self.dump_dir / "{}_{}_ai_input.txt".format(self.src_cd.lower(), n.src_id)).write_text(head + payload.user_text, encoding="utf-8")
