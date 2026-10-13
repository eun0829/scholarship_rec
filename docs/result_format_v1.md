# 장학금 추천 결과 JSON 형식 v1

포털(교내 통합정보시스템)이 받는 판정 결과의 형식이다. 기계가 읽는 정의는 `docs/result_v1.schema.json`(JSON Schema draft-07)이고, 이 문서는 뜻을 설명한다.
요청(입력) 형식은 6단계(API 서비스화)의 계약서에서 확정한다. 이 문서는 **응답**과, 응답에 들어 있는 **후속 질문의 답변 형식**을 다룬다.

## 호환 규칙

- `result_version` 은 현재 `1`. **필드를 더하는 변경은 같은 버전**에서 한다. 필드를 없애거나 뜻·형식을 바꾸면 버전을 올린다.
- 포털은 **모르는 필드를 무시**해야 한다 (더해진 필드 때문에 깨지면 안 된다).
- 값이 없는 문자열 필드는 `null` 이다. 배열 필드는 비어 있어도 항상 존재한다.

## 호출 흐름 (서버는 상태를 저장하지 않는다)

```
1차  요청: profile(학교 DB 값 + 학생이 이미 답한 것)              → 응답: 판정 + follow_up(질문)
        학생이 질문에 답함 (포털이 답을 임시로 보관)
2차  요청: profile(지금까지의 입력) + answers(이번 답) + declined + round
                                                             → 응답: 판정 + changes(무엇이 바뀌었나) + follow_up(남은 질문)
```

- **성적 값(`gpa_last`, `gpa_cumulative`)을 보낼 때는 만점 `gpa_scale`(4.5, 4.3 등)을 함께 보낸다.** 보내지 않으면 4.5로 가정하고 `profile_notes.assumptions` 에 알린다 (4.3점 만점 학교 학생이 잘못 판정될 수 있다).
- `answers` 는 프로필 JSON 키로 보낸다 (질문의 `answer.keys` 가 알려 준다). 한 키가 틀려도 나머지 답은 적용되고, 틀린 키는 응답의 `answers.warnings` 에 나온다.
- 학생이 건너뛴 항목은 `declined` 에 항목 코드를 담아 보낸다 (다시 묻지 않는다).
- `round` 는 몇 번째 질문 회차인지(1부터). 질문은 최대 3회, 한 번에 최대 5개까지 한다.
- 3차부터는 포털이 2차의 `answers` 를 자기 `profile` 에 합쳐 두고 같은 방식으로 보낸다.

## 최상위

| 필드 | 형식 | 뜻 |
|---|---|---|
| `result_version` | 정수 | 형식 버전 (현재 1) |
| `today` | 문자열 | 판정 기준일 `YYYY-MM-DD` |
| `profile_notes` | 객체 | 입력 처리 안내: `dropped_sensitive_keys`(받지 않은 개인정보 키), `ignored_keys`(알 수 없는 키), `assumptions`(판정에 쓴 가정) |
| `counts` | 객체 | 후보 수와 판정별 수 (`match`, `conditional`, `no_match` …) |
| `recommended` | 배열 | **충족** 공고 (항목 형식은 아래) |
| `conditional` | 배열 | **조건부** 공고: 확인할 것이 남음 |
| `excluded` | 배열 | **미달** 공고와 사유 (`id`, `src_id`, `title`, `url`, `reasons`) |
| `round` / `answers` / `follow_up` / `changes` / `change_summary` | | 이어 답하는 호출(`recommend_step`)의 응답에만 있음 |
| `request_id` | 문자열 또는 null | 요청에 담아 보낸 요청 번호를 그대로 돌려줌 (API 가 붙임) |
| `profile_warnings` | 문자열 배열 | **받지 못한 입력과 이유**: 값이 틀린 프로필 키(`profile.age: ...`), 알 수 없어 무시한 키, 무시한 요청 항목, 평점 불일치. 나머지 입력으로 판정은 이루어졌다 (API 가 붙임) |
| `contract_violations` | 문자열 배열 | 요청에 `strict: true` 가 있을 때만: 계약과 한 글자라도 다른 입력의 목록 (연동 시험용). 판정에는 영향이 없다 |

## 판정의 뜻

| `verdict` | `verdict_label` | 뜻 |
|---|---|---|
| `MATCH` | 충족 | 구조화된 자격 조건을 모두 충족. (판정에 영향 없는 선행 조건·우대·확인 사항은 별도 필드로 따로 표시) |
| `CONDITIONAL` | 조건부 | 미달은 아니지만 확인할 것이 남음 (`to_confirm`) |
| `NO_MATCH` | 미달 | 확실히 대상이 아님 (`fail_reasons`) |

## 공고 항목 (`recommended`, `conditional` 의 원소)

| 필드 | 형식 | 뜻 |
|---|---|---|
| `id` | 문자열 | 공고 식별자 `출처:글번호` (예: `EXT02:9096`). `resolves`, `changes` 가 이 값을 쓴다 |
| `src_cd`, `src_id`, `title`, `url` | | 출처, 글번호, 제목, 원문 주소 |
| `apply_start`, `apply_end`, `days_left` | | 신청 기간과 남은 일수 |
| `verdict`, `verdict_label` | | 위 표 |
| `best_variant` | 객체 또는 null | 가장 가까운 유형 `{name, verdict, verdict_label}` (충족 유형 우선, 없으면 조건부 유형 중 확인 항목이 가장 적은 것). 유형이 없거나 미달이면 `null`. 선택 필드 |
| `tier`, `tier_label` | 문자열 | 추천 등급. `HIGH`(지원 가능성 높음: 충족 유형이 있고 공통 확인 항목 없음) / `PARTIAL`(일부 확인 필요: 충족 유형은 있으나 공통 확인 항목이 남음) / `CHECK`(확인 필요: 조건부) / `NONE`(해당 없음: 미달). 선택 필드 |
| `to_confirm` | 문자열 배열 | 확인이 필요한 판정 항목 (사람이 읽는 문장) |
| `matched_variants` | 배열 | 해당할 수 있는 유형: `name`, `verdict`, `amount`, `apply_end`(유형별 마감), `expired`(유형 마감이 지났는가), `preferences` |
| `unmatched_variants` | 배열 | 해당하지 않는 유형과 이유 |
| `prerequisites` | 배열 | 선행 조건 (판정에 영향 없음). `type`: `NATIONAL_SCHOLARSHIP_APPLIED`(국가장학금 신청), `BRACKET_CALCULATED`(지원구간 산정), `DEGREE_HELD`(학위 소지), `ADMISSION_OFFER`, `EXCHANGE_SELECTED`, `OTHER`. `status`: `MET` 충족 / `TODO` 신청 필요 / `UNMET` 미충족 / `UNKNOWN` 확인 필요 |
| `preferences` | 배열 | 우대 조건. `applies`: `true` 해당 / `false` 해당 없음 / `null` 모름 |
| `school_programs` | 배열 | 학교 참여 사업. `program`: `NATIONAL_SCHOLARSHIP`(국가장학금), `LOCAL_TALENT`(지역인재), `HOUSING_STABILITY`(주거안정). `school`: `PARTICIPATES` 본교 참여 / `NOT` 미참여 / `UNKNOWN` 설정 없음 |
| `notes` | 배열 | 확인 사항: 중복수혜 제한, 선발 후 의무, 심사 기준, 추천, 절차 |
| `assumptions` | 문자열 배열 | 판정에 쓴 가정 (예: 공고에 평점 만점이 없어 4.5로 가정) |
| `passed`, `fail_reasons` | 문자열 배열 | 충족한 판정 항목 / 미달 사유 |
| `scholarship_name`, `foundation`, `summary`, `amount`, `apply_how`, `org_deadline`, `status` | | 공고의 이름·재단·요약·금액·신청 방법·재단 마감일·처리 상태 (표시용) |
| `schema_version`, `merged_from` | | 공고 구조화 버전(1·2·3), 같은 장학으로 묶은 다른 글 |
| `warnings` | 배열 | 안내 (`code`, `message`): 마감 임박, 추천서 필요, 검수 대기 등 |

## 후속 질문 (`follow_up`)

| 필드 | 뜻 |
|---|---|
| `round`, `max_rounds`, `max_questions` | 지금 회차, 최대 회차(3), 한 번에 묻는 최대 개수(5) |
| `questions` | **지금 학생에게 물을 질문** (아래) |
| `max_rounds`, `max_questions` | 질문 한도 (이미 위 줄에 있음: 지금 회차 `round`) |
| `remaining` | 이번에 못 물은 질문 수 (다음 회차) |
| `done`, `done_reason` | 더 물을 것이 없으면 `done=true`. 이유: `NO_QUESTIONS`(더 물을 것 없음), `ALL_DECLINED`(건너뛴 항목만 남음), `MAX_ROUNDS`(최대 회차) |
| `data_gap` | **학교 DB에서 가져오지 못한 값**. 학생에게 묻지 않는다. 포털이 함께 보내 주면 더 많은 공고가 확정된다 (`field` 가 JSON 키) |
| `document_only` | **입력으로는 풀 수 없는 조건**: 공고문에서 직접 확인해야 하는 문장 조건 (공고별 `items`) |

`questions` 의 원소: `code`(항목 코드), `label`, `type`(입력 형식: `place` 지역, `bucket` 구간 선택, `int` 정수, `list` 목록), `question`(학생에게 보일 문구), `sensitive`(민감 항목은 마지막에 나오며 "답하지 않아도 됩니다"를 표시), `skippable`,
`resolves`(이 질문에 답하면 확정될 수 있는 공고 `id` 목록), `resolves_count`, 그리고 `answer`:

| `answer` 필드 | 뜻 |
|---|---|
| `keys` | 답을 보낼 프로필 JSON 키 (예: `["parent_residence_sido", "parent_residence_sigungu"]`) |
| `format`, `example`, `options`, `range` | 값의 형식, 예시, 선택지(구간·신분 코드), 범위 |
| `text` | 텍스트로 답할 때의 한 줄 예시 (`- 부모 주민등록: 경기도 수원시`) |

질문 순서: 일반 항목이 먼저, 민감 항목(신분)이 마지막. 같은 그룹에서는 **더 많은 공고를 확정시키는 항목** 순이다.

## 변화 (`changes`, `change_summary`)

답을 넣기 전과 비교해 **판정이 바뀐 공고**만 `changes` 에 있다. `from`/`to` 는 판정 코드, `from_label`/`to_label` 은 화면에 보일 문구(충족·조건부·미달), `reason` 은 바뀐 이유다 (충족으로 바뀌면 "남은 확인 항목이 모두 풀렸습니다", 미달로 바뀌면 미달 사유).
`change_summary`: `changed`(바뀐 공고 수), `to_match`/`to_conditional`/`to_no_match`(바뀐 뒤의 판정별 수), `unchanged`(판정이 그대로인 공고 수), `reduced_confirms`(판정은 같지만 확인 필요 항목이 줄어든 공고 수).
정보를 더 줘도 이미 `미달`인 공고가 되살아나는 일은 없다 (시험으로 확인: 학생 30명 × 공고 20건 × 학교 3개 전체).

## 답변 처리 결과 (`answers`)

`applied`(적용된 키), `warnings`(받지 못한 키와 이유), `declined`(건너뛴 항목 코드).

## 예시 (일부만)

```json
{
  "result_version": 1,
  "today": "2026-10-11",
  "counts": {"candidates": 20, "after_filter": 17, "after_dedupe": 17, "match": 0, "conditional": 6, "no_match": 11},
  "conditional": [
    {"id": "EXT02:9163", "src_cd": "EXT02", "src_id": "9163", "title": "인천광역시 중소기업 노동자 자녀 장학금 지원사업",
     "verdict": "CONDITIONAL", "verdict_label": "조건부", "days_left": 20,
     "to_confirm": ["확인 필요: 부 or 모가 중소기업에 근무"],
     "prerequisites": [], "preferences": [], "school_programs": [], "notes": [], "matched_variants": [], "unmatched_variants": [],
     "passed": ["대상 학교급 해당 (UNIV)"], "fail_reasons": [], "warnings": [], "assumptions": [], "schema_version": 3}
  ],
  "round": 2,
  "answers": {"applied": ["age", "hs_sido", "hs_sigungu", "parent_residence_sido", "parent_residence_sigungu"], "warnings": [], "declined": ["SPECIAL"]},
  "changes": [
    {"id": "EXT02:9071", "from": "CONDITIONAL", "to": "NO_MATCH", "from_label": "조건부", "to_label": "미달",
     "reason": "지역 조건 미달 (필요: [부모 주민등록 경기도 3년 이상])"}
  ],
  "change_summary": {"changed": 4, "to_match": 0, "to_conditional": 0, "to_no_match": 4, "unchanged": 16, "reduced_confirms": 1},
  "follow_up": {
    "round": 2, "max_rounds": 3, "max_questions": 5, "done": true, "done_reason": "ALL_DECLINED", "questions": [], "remaining": 0,
    "data_gap": [{"code": "GCP", "label": "누적 백분위", "field": "gpa_cumulative_percent", "resolves_count": 2, "resolves": ["EXT02:9058", "EXT02:9141"]}],
    "document_only": [{"id": "EXT02:9098", "title": "사랑의 날개 장애인 장학 (2026년)", "count": 1, "items": ["확인 필요: 경제적 부담으로 학업에 어려움이 있는 자"]}]
  }
}
```

질문 하나의 예 (1차 응답의 `follow_up.questions[0]`):

```json
{"code": "RESP", "label": "부모 주민등록", "question": "부모님 주민등록상 시·도와 시·군·구는 어디인가요?", "type": "place",
 "sensitive": false, "skippable": true, "resolves": ["EXT02:9071", "EXT02:9096", "EXT02:9181"], "resolves_count": 3,
 "answer": {"keys": ["parent_residence_sido", "parent_residence_sigungu"], "format": "시·도와 시·군·구를 각각",
            "example": {"parent_residence_sido": "경기도", "parent_residence_sigungu": "수원시"}, "text": "- 부모 주민등록: 경기도 수원시"}}
```
