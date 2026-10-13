# 장학금 추천 API 계약 v1

포털(교내 통합정보시스템)이 228번 서버의 추천 API를 호출하는 방법이다. 기계가 읽는 정의는 `docs/openapi_v1.json`(OpenAPI 3.1)이고, 이 문서는 뜻과 규칙을 설명한다.
응답의 모양은 `docs/result_format_v1.md` 와 `docs/result_v1.schema.json` 이 정한다.

## 역할

| | 하는 일 |
|---|---|
| **포털** | 학생 로그인, **학교 DB에서 학적·성적·소득구간을 읽어 JSON 구성**, 후속 질문 화면, 학생이 직접 답한 값의 **임시 보관**, 민감 정보(소득·신분) 동의 |
| **추천 API(228번 서버)** | 입력 검증, 판정(충족·조건부·미달), 후속 질문 생성, 답을 넣기 전·후의 변화 비교. **학생 정보를 저장하지 않는다.** 학적 DB에는 접근하지 않는다 |

## 호출 흐름

```
1차  POST /v1/recommend  { profile: 학교 DB 값 + 본인 주민등록 }
     ← 판정 + follow_up.questions (학생이 직접 답할 질문)
        (포털이 질문을 보여 주고, 학생이 답한 값을 임시로 보관)
2차  POST /v1/recommend  { profile: 지금까지의 입력, answers: 이번 답, declined: 건너뛴 항목, round: 2 }
     ← 판정 + changes(무엇이 바뀌었나) + follow_up(남은 질문)
3차  같은 방식 (포털이 2차의 answers 를 자기 profile 에 합쳐 두고 보낸다). 질문은 최대 3회, 한 번에 최대 5개
```

학교 DB 값은 보관할 필요 없이 매번 DB에서 읽어 `profile` 에 넣는다. 포털이 보관하는 것은 **학생이 직접 답한 값**, 건너뛴 항목 코드, 회차뿐이다.

## 인증·네트워크·개인정보

- 내부망에서만 호출한다. 포털 서버 IP만 허용하고, 모든 요청에 **`X-API-Key`** 헤더를 담는다 (키는 포털 서버가 보관하고 화면(브라우저)에 내려보내지 않는다).
- **학번·이름·연락처·주민등록번호 등 학생을 식별하는 값을 보내지 않는다.** 이런 키(`name`, `student_id`, `phone`, `email` …)는 받지 않고 응답의 `profile_notes.dropped_sensitive_keys` 로 알린다.
  요청 번호(`request_id`)에도 학번을 쓰지 않는다 (무작위 값 권장).
- **소득구간(`income_bracket`)과 신분(`special_codes` 등)은 민감 정보**다. 학생이 동의한 경우에만 보낸다. 동의하지 않으면 보내지 않고, 질문에서 건너뛰면 `declined` 에 `SPECIAL` 을 담는다.
- 서버는 **입력값을 로그에 남기지 않는다.** 요청 번호, 처리 시간, 건수만 기록한다. 학생 정보는 요청이 끝나면 서버에 남지 않는다.

## 엔드포인트

| 메서드·경로 | 용도 |
|---|---|
| `POST /v1/recommend` | 판정과 후속 질문 (1차와 이어 답하는 호출 모두) |
| `GET /v1/health` | 상태 점검 (공고 목록 갱신 시각 포함) — 6-B 에서 구현 |
| `GET /v1/meta` | API·결과 형식 버전, 질문 한도 — 6-B 에서 구현 |

## 요청 본문

| 필드 | 형식 | 설명 |
|---|---|---|
| `profile` | 객체 (**필수**) | 학생 프로필 (아래 필드 표). 항목은 최대 100개 |
| `request_id` | 문자열 | 호출하는 쪽이 정하는 요청 번호 (영문·숫자·`._-` 1~64자). 응답과 오류에 그대로 돌려준다 |
| `answers` | 객체 | 이번 회차에 학생이 새로 답한 값. `profile` 의 같은 키를 덮어쓴다 (키는 질문의 `answer.keys`) |
| `answers_text` | 문자열 | 답을 `- 라벨: 값` 줄로 보낼 때 (개발·시험용, 5000자 이하) |
| `declined` | 문자열 배열 | 학생이 건너뛴 항목 코드. 다시 묻지 않는다 |
| `round` | 정수 1~10 | 몇 번째 질문 회차인지 (기본 1) |
| `max_questions` | 정수 1~10 | 한 번에 묻는 최대 질문 수 (기본 5) |
| `today` | `YYYY-MM-DD` | 판정 기준일 (시험용. 보내지 않으면 서버의 오늘) |
| `strict` | 불리언 | `true` 면 응답에 `contract_violations`(계약과 다른 입력 목록)를 더한다. 연동 시험용 (아래 엄격 모드) |

알 수 없는 최상위 키는 무시하고 응답의 `profile_warnings` 로 알린다.

## 프로필 필드

**등급**: 필수(첫 호출에 꼭 있어야 정확해지는 값) / 권장 / 선택. **출처**가 `학교 DB` 인 값은 포털이 DB에서 읽어 첫 호출에 보내고, `학생` 인 값은 포털이 처음에는 `본인 주민등록`만 보내고 나머지는 **후속 질문**으로 받는다.
값이 없는 필드는 **보내지 않는다** (`null` 대신 키를 생략). 모르는 값을 추측해서 채우면 안 된다 (없으면 확인 필요로 남고, 후속 질문이나 `data_gap` 으로 알려 준다).

<!-- FIELDS:BEGIN -->
| JSON 키 | 출처 | 등급 | 형식 | 허용 범위·값 | 설명 |
|---|---|---|---|---|---|
| `level` | 학교 DB | 필수 | string | `UNIV` / `GRAD` | 과정. 예: `UNIV` |
| `status` | 학교 DB | 필수 | string | `ENROLLED` / `LEAVE` / `GRADUATED` | 학적 상태. 예: `ENROLLED` |
| `grade` | 학교 DB | 필수 | integer | 1 ~ 6 | 학년. 예: `4` |
| `semesters_completed` | 학교 DB | 필수 | integer | 0 ~ 30 | 등록(이수)한 학기 수. 신입생은 0. 예: `7` |
| `entry_year` | 학교 DB | 필수 | integer | 1990 ~ (올해+1) | 입학년도. 예: `2021` |
| `admission_type` | 학교 DB | 권장 | string | `NEW` / `TRANSFER` | 입학 구분 (신입학 / 편입학). 예: `NEW` |
| `major` | 학교 DB | 필수 | string | 1 ~ 100자 | 학과 이름. 예: `전자공학부` |
| `major_group` | 학교 DB | 권장 | string | `ENGINEERING` / `NATURAL` / `MEDICAL` / `ARTS_PE` / `HUMANITIES` / `SOCIAL` / `EDUCATION` / `AGRICULTURE` | 학과가 속한 계열 (학교 시스템의 분류를 표준 코드로 전달). 예: `ENGINEERING` |
| `nationality` | 학교 DB | 필수 | string | `KR` / `FOREIGN` / `OVERSEAS_KR` | 국적 (내국인 / 외국인 / 재외동포). 다른 값은 받지 않는다. 예: `KR` |
| `gender` | 학교 DB | 선택 | string | `M` / `F` | 성별. 예: `M` |
| `gpa_last` | 학교 DB | 필수 | number | 0 ~ `gpa_scale` | 직전학기 평점. 만점은 gpa_scale 로 함께 보낸다. 예: `3.79` |
| `gpa_scale` | 동반 키 | (동반) | number | 4.0 / 4.3 / 4.5 / 5.0 | 평점 만점. **성적 값(gpa_last, gpa_cumulative)과 함께 보낸다.** 보내지 않으면 4.5로 가정하고 결과의 profile_notes.assumptions 에 알린다. 예: `4.5` |
| `gpa_last_term` | 학교 DB | 필수 | string | `YYYY-1` 또는 `YYYY-2` (연도 1990~2099) | 직전학기 `YYYY-N`. 예: `2026-1` |
| `credits_last` | 학교 DB | 필수 | number | 0 ~ 60 | 직전학기 취득학점. 예: `15` |
| `gpa_cumulative` | 학교 DB | 권장 | number | 0 ~ `gpa_scale` | 누적(전체) 평점. 만점은 gpa_scale 로 함께 보낸다. 예: `3.62` |
| `term_history` | 학교 DB | 권장 | array | 1 ~ 20개. 같은 학기 중복 불가, 순서는 무관 | 학기별 성적, 최근 학기부터: `[{term, gpa, credits}]`. 예: `[{"term": "2026-1", "gpa": 3.79, "credits": 15}, {"term": "2025-2", "gpa": 3.11, "credits": 18}]` |
| `gpa_percent` | 학교 DB | 권장 | number | 0 ~ 100 | 직전학기 100점 환산 점수 (공고가 100점제일 때만 쓰임). 0~100. 예: `88` |
| `gpa_cumulative_percent` | 학교 DB | 권장 | number | 0 ~ 100 | 누적(전체) 100점 환산 점수 (공고의 전체 평점 기준이 100점제일 때만 쓰임). 0~100. 예: `85` |
| `income_bracket` | 학교 DB | 필수 (민감) | integer | 0 ~ 10 | 학자금 지원구간(소득분위) 0~10. **민감: 학생 동의 후에만 보낸다**. 예: `2` |
| `residence_sido` | 학생 | 필수 | string | 17개 시·도 공식 이름 | 본인 주민등록 시·도. 공식 이름: `서울특별시`, `부산광역시`, `대구광역시`, `인천광역시`, `광주광역시`, `대전광역시`, `울산광역시`, `세종특별자치시`, `경기도`, `강원특별자치도`, `충청북도`, `충청남도`, `전북특별자치도`, `전라남도`, `경상북도`, `경상남도`, `제주특별자치도`. 약칭·옛 이름(`경기`, `강원도`)은 공식 이름으로 바꿔 받지만 오타는 거절한다. 예: `경기도` |
| `residence_sigungu` | 학생 | 필수 | string | 1 ~ 40자 | 본인 주민등록 시·군·구 (읍·면·동까지 적어도 된다: `수원시 장안구 연무동`). 예: `수원시` |
| `residence_years` | 학생 | 권장 | number | 0 ~ 100 | 거주 기간(년). 구간으로 받았으면 하한 (`3년 이상` -> 3). 소수 가능(0.5). 예: `3` |
| `residence_years_upper` | 동반 키 | (동반) | number | `residence_years` ~ 100 | 거주 기간 구간의 상한(포함). `1~3년` -> 3. 정확한 값이면 보내지 않는다. 예: `3` |
| `residence_years_open` | 동반 키 | (동반) | boolean | `true` / `false` (JSON 불리언만) | `N년 이상`처럼 상한이 없으면 true. 예: `true` |
| `parent_residence_sido` | 학생 | 권장 | string | 17개 시·도 공식 이름 | 부모 주민등록 시·도 (`residence_sido` 와 같은 공식 이름). 예: `경기도` |
| `parent_residence_sigungu` | 학생 | 권장 | string | 1 ~ 40자 | 부모 주민등록 시·군·구. 예: `수원시` |
| `hs_sido` | 학생 | 권장 | string | 17개 시·도 공식 이름 | 졸업한 고등학교가 있는 시·도 (`residence_sido` 와 같은 공식 이름). 예: `경기도` |
| `hs_sigungu` | 학생 | 권장 | string | 1 ~ 40자 | 졸업한 고등학교가 있는 시·군·구. 예: `안양시` |
| `age` | 학생 | 권장 | integer | 0 ~ 100 | 만 나이. 예: `24` |
| `special_codes` | 학생 | 선택 (민감) | array | 아래 8개 코드, 중복 없이 (없으면 `[]`) | 해당하는 신분 코드 목록. **민감: 학생 동의 후에만 보낸다**. 예: `["DISABLED"]` |
| `special_complete` | 동반 키 | (동반) | boolean | `true` / `false` (JSON 불리언만) | 신분 항목에 모두 답했으면 true (목록에 없는 신분은 해당 없음으로 본다). 일부만 답했으면 false 또는 보내지 않는다. 예: `true` |
| `is_basic_livelihood` | 학생 | 권장 (민감) | boolean | `true` / `false` (JSON 불리언만) | 기초생활수급자 여부 (special_codes 와 같은 뜻의 개별 답. 둘 중 하나만 써도 된다). 예: `false` |
| `is_near_poor` | 학생 | 권장 (민감) | boolean | `true` / `false` (JSON 불리언만) | 차상위계층 여부 (개별 답). 예: `false` |
| `is_single_parent` | 학생 | 선택 (민감) | boolean | `true` / `false` (JSON 불리언만) | 한부모가정 여부 (개별 답). 예: `false` |
| `degree_held` | 학생 | 선택 | boolean | `true` / `false` (JSON 불리언만) | 학위 소지 여부. 예: `false` |
| `national_scholarship_applied` | 학생 | 선택 | boolean | `true` / `false` (JSON 불리언만) | 이번 학기 국가장학금 신청 여부. 예: `true` |
| `language_scores` | 학생 | 선택 | object | 값 0 ~ 1000 (성적이 없으면 `{}`) | 어학 성적 `{시험 이름: 점수 또는 등급 숫자}` (값은 숫자). 독일어 같은 유럽언어기준(CEFR) 등급은 언어 이름을 키로, A1=1 A2=2 B1=3 B2=4 C1=5 C2=6 으로 보낸다. 성적이 없으면 빈 객체 `{}` (보내지 않으면 '답하지 않음'). 예: `{"TOEIC": 780, "HSK": 5, "독일어": 3}` |
<!-- FIELDS:END -->

## 입력 검증

운영에서는 **가능한 한 받아들이되, 틀린 것은 조용히 넘기지 않고 알린다.**

- **요청 구조가 틀리면** (`profile` 이 객체가 아님, `round` 가 범위 밖 등) 판정하지 않고 `400 INVALID_REQUEST` 로 **필드별 사유**를 돌려준다.
- **프로필의 값 하나가 틀리면** 판정을 거절하지 않는다. 그 키만 빼고 나머지로 판정하고, 응답의 `profile_warnings` 에 `profile.age: ...` 처럼 알린다. 포털은 이 경고를 로그로 남겨 입력 오류를 고친다. 각 값은 맞는데 서로 모순이면 `400 INVALID_PROFILE`.
- **시·도는 17개 공식 이름**만 받는다. 약칭·옛 이름(`경기`, `대구시`, `강원도`)은 공식 이름으로 바꿔 받지만, **오타(`서울틐별시`)는 거절하고 알린다** (인식하지 못한 값으로 미달 처리하지 않는다). 시·군·구만 있고 시·도가 없는 입력은 같은 이름의 다른 지역(예: 여러 도시의 `중구`)을 추측하지 않고 `확인 필요`로 둔다.
- **국적은 `KR` / `FOREIGN` / `OVERSEAS_KR`** 로 정리한다 (`내국인`, `한국인`, `Korean`, `외국인`, `재외동포` 등). 알 수 없는 값은 거절하고 알린다.
- **JSON 형식**: 불리언은 `true`/`false`, 신분은 배열, 어학 성적 값은 숫자여야 한다. `"true"`, `1`, `"DISABLED"`(배열 아님), `"780"` 은 받지 않고 알린다.
- **키 철자**: 키의 앞뒤 공백·대소문자·하이픈 차이(`GPA_LAST`, ` grade`)는 무시하고 계약의 키로 읽는다. 그 밖에 알 수 없는 키는 **무시하고 `profile_warnings` 에 알린다** (철자 오류를 알아챌 수 있다).
- **일관성**: `gpa_last` 가 `term_history` 의 해당 학기 평점과 다르면 `profile_warnings` 로 알린다 (판정에서 빼지는 않는다). `term_history` 는 **순서와 무관**하다 (학기로 정렬해 쓴다).
- 성적 값을 보내면서 `gpa_scale` 을 빼면 4.5로 가정하고 `profile_notes.assumptions` 에 알린다.
- 빈 문자열과 공백만 있는 값은 "입력 없음"으로 본다 (키를 생략하는 것이 맞다).

## 엄격 모드 (연동 시험용)

요청에 `"strict": true` 를 담으면 판정은 평소처럼 하고, 응답에 **`contract_violations`**(계약과 한 글자라도 다른 입력의 목록)를 더한다. 운영 입력은 위처럼 관대하게 받아도, **포털의 변환 코드(학교 DB → JSON)는 이 목록이 비어 있도록** 고친다.
검사하는 것: 키 철자·알 수 없는 키·개인정보 키, `null`, 형식(정수에 `"4"`·`4.0`, 불리언에 `"true"`), 값 목록(`level: "대학생"`, `residence_sido: "경기"`, `nationality: "Korean"`), 범위(위 표), 문자열 길이, `term_history` 구조, `gpa_scale` 누락, 거주 기간 상한 모순.
`answers` 도 같은 방식으로 검사한다 (`answers.` 로 시작).

## 오류

```json
{"error": {"code": "INVALID_REQUEST", "message": "요청 형식이 올바르지 않습니다 (1개 항목)",
           "fields": [{"field": "round", "message": "1~10 사이의 정수여야 합니다"}], "request_id": "a1b2c3"}}
```

| 코드 | HTTP | 뜻 | 포털이 할 일 |
|---|---|---|---|
| `INVALID_REQUEST` | 400 | 요청 구조 오류 | `fields` 를 보고 요청을 고친다 (재시도해도 같다) |
| `INVALID_PROFILE` | 400 | 프로필 항목 사이의 모순 | 입력을 고친다 |
| `UNAUTHORIZED` | 401 | API 키 없음·불일치 | 설정 확인 |
| `PAYLOAD_TOO_LARGE` | 413 | 요청이 너무 큼 | 요청 크기를 줄인다 |
| `SERVER_CONFIG` | 500 | 서버 환경설정 오류 | 관리자에게 알린다 (재시도해도 같다) |
| `INTERNAL` | 500 | 서버 내부 오류 | 잠시 후 한 번 재시도, 계속되면 관리자에게 알린다 |
| `UNAVAILABLE` | 503 | 공고 DB를 읽을 수 없음 | 잠시 후 재시도 |

## 연동 시험

`docs/samples/api_samples.json` 에 **학생 30명의 첫 호출 요청(`first_request`)과 기대 결과**(판정 개수, 후속 질문 코드, 대화가 끝났을 때의 개수)가 있다. 포털 쪽 변환 코드(학교 DB → JSON)가 만든 요청을 이 샘플과 같은 입력으로 맞춰 보고,
응답이 기대 결과와 같은지 확인한다. 샘플은 고정한 공고 20건(기준일 2026-10-11, 학교: 시흥)으로 만든 것이라 공고가 바뀌면 판정 개수는 달라진다 (필드 구조와 흐름이 맞는지 보는 용도).

## 버전

API 버전은 `1.x`. 필드를 더하는 변경은 같은 버전에서 하고, 필드를 없애거나 뜻을 바꾸면 `/v2` 를 연다. 포털은 **모르는 응답 필드를 무시**해야 한다.
