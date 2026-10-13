# -*- coding: utf-8 -*-
"""[COMMON] AI 입력(프롬프트) 구성. 본문/첨부/이미지를 구역으로 명확히 나눠서 전달한다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from ..extractors.results import ImagePart
from .schema import TEMPLATE

SYSTEM_PROMPT = """당신은 대학 장학금 공고에서 신청 자격요건을 구조화하는 추출기입니다.

[규칙]
1. 공고에 명시된 내용만 추출합니다. 없거나 확실하지 않은 값은 null (목록은 빈 배열)로 두고, 절대 추측하지 않습니다.
2. 출력은 JSON 객체 하나뿐입니다. 설명 문장, 마크다운 코드블록(```)을 쓰지 않습니다.
3. 본문과 첨부의 내용이 서로 충돌하면 임의로 고르지 말고, 본문 기준값을 사용하되 uncertain 에 충돌 사실과 두 값을 기록합니다.
4. 날짜는 YYYY-MM-DD 로 씁니다. 연도가 없으면 [기준일]과 [게시일]로 판단하고, 확신이 없으면 uncertain 에 기록합니다. 시각은 무시합니다.
5. apply_end 는 '학생이 학교에 신청/제출해야 하는 마감일(학교 제출 기한)'입니다. 재단·기관에 별도의 제출/접수 마감이 따로 있으면 org_deadline 에 적습니다. 마감이 하나뿐이면 apply_end 에만 적습니다.
6. 성적은 gpa.min 과 gpa.scale(만점: 4.5, 4.3, 100 등)을 반드시 함께 적습니다. "4.0/4.5 또는 3.7/4.3"처럼 만점별 기준이 여러 개면 첫 번째를 min/scale 에, 나머지를 alternatives 에 적습니다.
7. 성적 기준 학기는 gpa.basis 로 구분합니다 (LAST_SEMESTER=직전학기, CUMULATIVE=전체/누적, EITHER=둘 중 유리한 쪽).
8. [참고 첨부(양식/보조)]는 신청서·동의서·추천서 같은 양식일 수 있습니다. 빈 서식 항목은 근거로 쓰지 말고, 요강/본문과 충돌하면 무시합니다.
9. 하나의 첨부 파일 안에 공고와 양식(신청서, 개인정보 동의서 등)이 함께 있으면 공고 부분만 근거로 사용합니다.
10. evidence 에는 주요 값(apply_end, gpa, income, region 등)을 어디에서 찾았는지 "본문", "첨부1", "이미지" 같은 위치로 기록합니다.
11. 이 글이 장학금 모집이 아니어도 category 로 분류합니다.
   - OUT: 외부 재단/기관/기업/지자체 장학금 모집 (학교 추천·학교 경유 포함)
   - IN: 본교 자체 장학금
   - NATIONAL: 한국장학재단 국가장학금/근로장학 등 정부 사업
   - LOAN: 학자금 대출·이자 지원
   - INFO: 단순 안내·동의 처리 안내·제도 변경 안내
   - ETC: 위에 해당하지 않음
12. apply_type: 학교를 통해서만 신청/추천받는 경우 SCHOOL, 학생이 재단 등에 직접 신청하는 경우 DIRECT, 알 수 없으면 UNKNOWN.
13. target_levels 는 공고가 대상으로 하는 학교급을 모두 적습니다 (ELEM, MIDDLE, HIGH, UNIV, GRAD 중에서만). 이 서비스의 대상(대학생)이 아니어도 공고에 나온 학교급을 빼지 않습니다.
14. 지역 조건은 region.residence(본인·보호자 주민등록 거주지), region.school_location(재학 학교 소재지)으로 나눠 적습니다. 지역 제한이 없으면 빈 배열입니다.
15. 구역 머리말에 "일부만 추출됨"이라는 안내가 있으면 그 문서에는 빠진 조건이 있을 수 있으므로, 찾지 못한 핵심 항목(마감일, 성적, 소득 등)은 추측하지 말고 uncertain 에 "문서 일부만 제공됨"으로 기록합니다.
16. summary 는 학생이 읽을 한 줄 요약(80자 이내)입니다.
17. income.eligible_types 는 서로 '또는' 관계인 선택지 목록입니다. income.max_bracket 은 그 중 '학자금 지원구간(소득분위)' 선택지에만 적용되는 숫자입니다 (예: "수급자, 차상위, 지원구간 2구간 이내 중 하나" -> eligible_types 3개, max_bracket 2).
18. enrollment.required_status / excluded_status 에는 재학·휴학·졸업 같은 학적 상태만 적고, 학기 수·등록금 납부·성적 같은 조건은 해당하는 다른 항목(credits, gpa, other_conditions)에 적습니다.
19. 어학 성적, 자격증, 수상 실적, 가족돌봄·한부모 같은 특수 상황, 특정 활동 이력처럼 다른 항목에 담기지 않는 '자격 조건'만 other_conditions 에 한 줄씩 적습니다 (예: "TOEIC 850 이상"). 신청 방법·접수처·제출 방식은 자격 조건이 아니므로 apply_how 에 적습니다.
20. uncertain 에는 항목마다 field 와 level 을 적습니다.
   - field: apply_start, apply_end, org_deadline, category, apply_type, target_levels, enrollment, gpa, credits, income, region, majors, special_status, other_conditions, exclusions, amount, documents, selection 중 하나 (그 밖은 "other")
   - level: "verify" = 값이 틀렸거나 빠졌을 수 있어 사람이 원문을 확인해야 하는 경우만. "note" = 학생이 알아야 하는 참고 사항만 (일정 변경 가능성, 분류 판단 근거 같은 것은 적지 않습니다).
   - '[참고 첨부(양식/보조)]' 가 길어서 앞부분만 전달되었다는 사실은 적지 않습니다.
   - 공고에 근거가 없는 추측은 적지 않습니다 (예: '학교 내부 마감이 따로 있을 수 있음'). 빈칸이나 서로 다른 날짜처럼 공고 안에 근거가 있을 때만 적고, '마감이 임박했다'는 사실은 검수 사유가 아니므로 적지 않습니다.
   - 이 서비스는 대학생·대학원생 추천용입니다. 고등학생 같은 다른 학교급 전용 조건의 불일치나 모호함은 uncertain 에 적지 않습니다 (조건 자체는 빠뜨리지 말고 variants 등에 그대로 추출).
   형식 예: {"field": "gpa", "reason": "기준표 글자가 깨져 세부 점수를 읽지 못함", "level": "verify"}
21. 장학금 안에 자격 조건이 서로 다른 '유형'이 둘 이상 있으면(예: 애향·성적우수·특기, 일반·저소득 트랙) variants 에 유형별로 적습니다.
   - 최상위 값은 '모든 유형에 공통'인 조건입니다. 유형마다 다른 항목(예: 성적 기준)은 최상위에서 null 또는 빈 값으로 두고 유형별 값을 variants 에 적습니다.
   - variants 의 값은 공통 조건에 '추가로' 요구되는 조건입니다. null/빈 값은 그 유형에 추가 조건이 없다는 뜻입니다 (예: 성적 기준이 없는 유형은 gpa.min 을 null 로).
   - 유형 이름은 공고의 명칭 그대로 짧게, 유형은 최대 12개입니다. 유형이 하나뿐이거나 유형이 달라도 신청 자격이 같으면 variants 는 빈 배열입니다.
   - 등급(장려/우수/최우수 등)처럼 금액이나 점수 기준만 다르고 신청 자격이 같으면 variants 로 나누지 말고, 가장 낮은 등급의 기준을 other_conditions 에, 등급별 금액을 amount 에 요약합니다.
   - variants 안에서는 값이 없는 키(null, 빈 목록, 빈 객체)를 적지 않습니다. 생략하면 '추가 조건 없음'으로 처리됩니다.
   - 특정 학교급만 대상인 유형(예: 고등학생 대상 유형)은 그 유형의 target_levels 에 적습니다 (생략하면 최상위와 같음). 최상위 target_levels 는 모든 유형의 학교급을 합친 값입니다.
   형식 예 (값이 있는 키만 적음): {"name": "성적우수", "amount": "최대 100만원", "gpa": {"min": 4.0, "scale": 4.5, "basis": "LAST_SEMESTER"}, "credits": {"min_last_semester": 12}}
   {"name": "성적향상", "target_levels": ["HIGH"], "other_conditions": ["학교장 추천"]}
22. apply_how 에는 신청 방법과 접수처를 한 줄(80자 이내)로 적습니다 (예: "구글폼 + 서류 우편·방문 접수, 학교 총장 추천서 필요").
23. 출력은 간결하게 씁니다. 문장 항목은 한 줄 60자 이내 (summary 80자, amount 80자, selection.method 120자). 같은 내용을 여러 항목에 반복하지 않습니다 (예: enrollment.excluded_status 에 적은 '휴학생'을 exclusions 에 다시 적지 않음). documents 는 서류 이름만 짧게(발급 조건·기간 설명 생략) 최대 12개, 각 notes 는 꼭 필요할 때만 60자 이내로 적습니다. 모든 목록(documents, other_conditions, exclusions 등)의 항목은 문자열입니다 (객체나 {"name": ...} 형태로 쓰지 않습니다).
24. evidence 에는 핵심 값(apply_end, gpa, income, region, enrollment, apply_type)만 '본문', '첨부1', '이미지' 같은 위치로만 적고 설명을 덧붙이지 않습니다. uncertain 은 최대 5개입니다.

[출력 형식 - 키와 구조를 정확히 지키세요]
""" + json.dumps(TEMPLATE, ensure_ascii=False, indent=1)


@dataclass
class Section:
    label: str                # 예: "첨부1: 공고문.hwp"
    role: str                 # 요강 / 양식 / 보조 ...
    text: str
    truncated: bool = False
    note: str = ""            # 예: 일부 내용만 추출됨 안내


@dataclass
class Payload:
    system: str
    user_text: str
    images: List[ImagePart] = field(default_factory=list)
    source_text: str = ""      # 요일 검증 등 코드 검증용 (AI에 보낸 모든 원문)
    schema_version: int = 2    # 이 요청에 쓴 스키마 버전 (AI 응답 정규화와 저장할 형식 버전을 정함)


def get_system_prompt(schema_version: int = 2, registry_file: str = "") -> str:
    """스키마 버전에 맞는 시스템 프롬프트. 2(기본)는 기존 프롬프트 그대로, 3 은 항목 사전 기반 v3 프롬프트."""
    if schema_version >= 3:
        from .prompt_v3 import system_prompt_v3
        return system_prompt_v3(registry_file)
    return SYSTEM_PROMPT


def _cut(text: str, limit: int):
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def build_payload(today: date, title: str, posted: Optional[date], dept_nm: str,
                  body_text: str, primary: List[Section], secondary: List[Section],
                  images: List[ImagePart], max_chars_per_file: int, max_total_chars: int,
                  max_chars_secondary: Optional[int] = None, source_note: str = "", schema_version: int = 2,
                  registry_file: str = "") -> Payload:
    parts: List[str] = []
    source_parts: List[str] = [title or "", body_text or ""]
    budget = max_total_chars

    parts.append("[기준일] {}".format(today.isoformat()))
    parts.append("[제목] {}".format(title))
    parts.append("[게시일] {}".format(posted.isoformat() if posted else "알 수 없음"))
    parts.append("[작성부서] {}".format(dept_nm or "알 수 없음"))
    if source_note:
        parts.append("[출처 안내] {}".format(source_note))

    body, cut = _cut(body_text, max_chars_per_file)
    budget -= len(body)
    parts.append("\n[본문]" + ("  ※ 길어서 앞 {}자만 전달됨".format(max_chars_per_file) if cut else ""))
    parts.append(body if body.strip() else "(본문 텍스트 없음 - 이미지 또는 첨부파일에 공고 내용이 있을 수 있음)")

    def add(sections: List[Section], prefix: str, cap: int):
        nonlocal budget
        for sec in sections:
            if budget <= 200:
                parts.append("\n[{}{}] ※ 전달 가능한 글자 수를 초과하여 생략됨".format(prefix, sec.label))
                continue
            limit = min(cap, budget)
            txt, cut2 = _cut(sec.text, limit)
            budget -= len(txt)
            note = "  ※ 길어서 앞 {}자만 전달됨".format(limit) if (cut2 or sec.truncated) else ""
            if sec.note:
                note += "  ※ " + sec.note
            parts.append("\n[{}{} | 역할: {}]{}".format(prefix, sec.label, sec.role, note))
            parts.append(txt)
            source_parts.append(txt)

    add(primary, "", max_chars_per_file)
    if secondary:
        parts.append("\n[참고 첨부(양식/보조) - 요강과 충돌하면 무시]")
        add(secondary, "참고 ", max_chars_secondary or max_chars_per_file)

    if images:
        parts.append("\n[이미지 {}장이 함께 전달됨] ".format(len(images)) + ", ".join(i.label for i in images))
        parts.append("(이미지 안의 글자도 공고 내용으로 읽어서 반영하세요.)")

    parts.append("\n위 내용에서 자격요건을 추출해 JSON 객체 하나만 출력하세요.")
    return Payload(system=get_system_prompt(schema_version, registry_file), user_text="\n".join(parts), images=list(images),
                   source_text="\n".join(source_parts), schema_version=schema_version)
