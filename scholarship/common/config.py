# -*- coding: utf-8 -*-
"""[COMMON] 공통 설정. 환경변수(.env 포함)에서 읽는다. 비밀번호/키는 코드에 넣지 않는다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]          # scholarship_app/
CONFIG_DIR = PROJECT_ROOT / "config"


def load_dotenv(path: str = ".env") -> None:
    """간단한 .env 로더 (python-dotenv 불필요). 이미 있는 환경변수는 덮어쓰지 않는다."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        # 값 뒤의 '  # 주석' 제거 (따옴표로 감싼 값은 그대로)
        val = val.strip()
        if val[:1] in ('"', "'"):
            val = val.strip("\"'")
        elif val.startswith("#"):
            val = ""                                   # 값이 비어 있고 주석만 있는 경우
        else:
            val = val.split("  #")[0].split("\t#")[0].strip()
        os.environ.setdefault(key.strip(), val)


def bootstrap(secret_file: Optional[str] = None, env_file: str = ".env") -> list:
    """환경 준비. 우선순위: 실제 환경변수 > 비밀 파일(set_secret.py) > .env
    (비밀 파일을 먼저 올리면 .env 의 setdefault 가 그 값을 덮어쓰지 않는다.)"""
    from . import secret_store
    loaded = secret_store.load_into_environ(secret_file)
    load_dotenv(env_file)
    return loaded


RECOMMENDABLE_CATEGORIES = ("OUT", "IN", "NATIONAL")      # 추천할 수 있는 공고 구분 (교외 / 교내 / 국가). INFO·LOAN·ETC 는 장학금 안내가 아니라서 선택할 수 없다


def parse_categories(text: str, default: Tuple[str, ...] = ("OUT",)) -> Tuple[str, ...]:
    """'OUT,NATIONAL' -> ('OUT', 'NATIONAL'). 비어 있으면 기본값. 허용 목록에 없는 값이면 ValueError (조용히 무시하면 공고가 사라진 이유를 찾기 어렵다)."""
    items = [x.strip().upper() for x in (text or "").split(",") if x.strip()]
    if not items:
        return tuple(default)
    bad = [x for x in items if x not in RECOMMENDABLE_CATEGORIES]
    if bad:
        raise ValueError("RECOMMEND_CATEGORIES 에 쓸 수 없는 값: {} (가능: {})".format(", ".join(bad), ", ".join(RECOMMENDABLE_CATEGORIES)))
    return tuple(dict.fromkeys(items))


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off", "n")


def env_float(name: str, default: Optional[float]) -> Optional[float]:
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else default
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    try:
        return int(v) if v else default
    except ValueError:
        return default


@dataclass
class Settings:
    # --- 저장 대상 DB ---
    db_user: str = ""
    db_password: str = field(default="", repr=False)      # repr 에서 숨김 (로그/예외에 노출 방지)
    db_dsn: str = ""
    oracle_client_lib: Optional[str] = None

    # --- NAS ---
    nas_root: Path = Path("/info/ai_bundle/scholarship")

    # --- AI ---
    ai_provider: str = "anthropic"
    ai_api_key: str = field(default="", repr=False)
    dreamspon_id: str = field(default="", repr=False)          # 외부02 로그인 계정 (비밀 파일에서 읽음)
    dreamspon_password: str = field(default="", repr=False)
    ext02_attach_to_ai: str = "guide_only"                     # 외부02 첨부를 AI 에 보내는 범위: guide_only / guide_and_form / none
    ai_base_url: str = ""                # OpenAI 호환 게이트웨이 주소 (AI_PROVIDER=gateway/openai 일 때)
    ai_model: str = "claude-sonnet-5-5"
    ai_timeout: int = 300                # 긴 응답(16000토큰)은 생성에 약 2분이 걸림
    ai_max_tokens: int = 16000           # AI 응답(JSON) 최대 토큰. 유형이 많은 공고는 8000 을 넘길 수 있음 (AI_MAX_TOKENS). 쓴 만큼만 과금됨
    ai_max_tokens_limit: int = 24000     # 응답이 잘렸을 때 자동 재시도에서 올릴 수 있는 상한
    ai_anthropic_beta: str = ""          # 필요할 때만 anthropic-beta 헤더 값 (예: prompt-caching-2024-07-31)
    ai_vision: bool = True               # 이미지를 AI 에 보낼지 (게이트웨이가 이미지를 지원하지 않으면 AI_VISION=false)
    ai_schema_version: int = 2           # 공고 구조화(AI) 형식 버전. 2=기존, 3=항목 사전 기반 확장 조건 (AI_SCHEMA_VERSION). 3 은 시험 추출로 확인한 뒤에 켭니다
    item_registry_file: str = ""         # 항목 사전 파일 (ITEM_REGISTRY_FILE). 비우면 config/item_registry.json

    # --- 파일/텍스트 한도 ---
    max_file_bytes: int = 50 * 1024 * 1024
    max_hwp_bytes: int = 20 * 1024 * 1024
    max_zip_members: int = 30
    max_chars_per_file: int = 20000      # 요강/미분류 문서 1개당 AI에 보내는 최대 글자 수
    max_chars_secondary: int = 4000      # 양식/보조 문서 1개당 최대 글자 수 (참고용이라 앞부분만)
    max_total_chars: int = 60000         # 공고 1건당 AI에 보내는 최대 글자 수
    body_short_len: int = 200            # 본문이 이보다 짧으면 '첨부/이미지 의존'으로 판단
    min_text_for_no_vision: int = 500    # 본문+요강 텍스트가 이보다 적으면 이미지도 함께 전달
    max_images: int = 6                  # AI에 보내는 이미지 최대 장수
    max_image_bytes_total: int = 15 * 1024 * 1024   # AI 요청에 실을 이미지 원본 합계 (게이트웨이 요청 본문 25MB 제한, base64 로 약 33% 커짐)
    max_pdf_pages_vision: int = 5        # 스캔 PDF를 이미지로 보내는 최대 페이지 수
    pdf_min_chars_per_page: int = 30     # 페이지당 글자 수가 이보다 적으면 스캔형으로 판단

    # --- HTTP ---
    http_timeout: int = 30
    http_retries: int = 3
    request_interval: float = 0.3        # 다운로드 사이 대기(초)

    # --- 이미 저장된 글 처리 ---
    skip_same_title: bool = True         # 제목이 같은 (다른 글번호의) 글은 건너뜀. 활성(USE_YN=Y) 글과 비교
    update_changed: bool = False         # 이미 완료된 글의 내용이 바뀌면 다시 처리 (기본: 건너뜀)

    # --- 추천 단계 ---
    recommend_urgent_days: int = 2                 # 마감까지 이 일수 이하면 '마감 임박' 경고
    recommend_excluded_detail: bool = True         # 미달 항목에도 요약·혜택·조건 재료를 붙인다 (RECOMMEND_EXCLUDED_DETAIL). 응답 크기가 문제면 끈다
    recommend_include_closed: bool = False         # 시험용: 마감이 지난 공고까지 DB 전체로 판정한다 (RECOMMEND_INCLUDE_CLOSED). 운영에서는 켜지 않는다
    recommend_min_days_left: int = 0               # 이보다 적게 남은 공고는 제외 (0 = 오늘 마감까지 포함, 1 = 오늘 마감 제외)
    recommend_include_need_review: bool = True     # 검수 대기(NEED_REVIEW) 공고 포함 (경고 표시)
    recommend_dedupe: bool = True                  # 같은 장학의 원 공고/연장 공고 묶기
    recommend_categories: Tuple[str, ...] = ("OUT",)   # 추천 대상 공고 구분 (RECOMMEND_CATEGORIES=OUT,NATIONAL,IN). 안내(INFO)·대출(LOAN)·기타(ETC)는 항상 제외
    recommend_max_results: int = 20                # 충족/조건부 각각 최대 개수
    default_gpa_scale: Optional[float] = 4.5       # 학생 프로필에 평점 만점이 없을 때 가정 (본교 기준 확인 필요)
    recommend_query_file: Path = CONFIG_DIR / "recommend_query.txt"
    recommend_regular_terms: int = 8               # 정규 학기 수 (v3 판정이 이수학기에서 남은 학기·졸업학기를 계산할 때 사용. 4년제 학사 8)
    recommend_defer_keys: tuple = ()               # 코드가 탈락시키지 않고 확인 필요로 남길 조건 (예: region) - 나중에 AI 프롬프트로 판단
    # 학교 고정 속성: 학생 프로필에 없을 때 채워 넣는다 (불필요한 '확인 필요'를 줄임). 예: GENERAL / 경기도 / 시흥시
    recommend_school_type: str = ""                # GENERAL(일반대학) / COLLEGE(전문대학) / REMOTE / OTHER
    recommend_school_sido: str = ""
    recommend_school_sigungu: str = ""
    recommend_school_programs: tuple = ()          # 본교가 참여하는 사업: NATIONAL_SCHOLARSHIP(국가장학금) / LOCAL_TALENT(지역인재) / HOUSING_STABILITY(주거안정). 비우면 '설정 없음'으로 표시

    # --- 추천 API 서버 (6-B) ---
    api_key: str = field(default="", repr=False)    # 포털이 X-API-Key 로 보내는 키 (python set_secret.py API_KEY)
    api_require_key: bool = True                    # False 는 개발용: 키 없이 호출을 받는다 (운영에서는 켜 둔다)
    api_max_body_bytes: int = 262144                # 요청 본문 최대 크기 (넘으면 413)
    api_cache_seconds: int = 300                    # 공고 목록을 DB 에서 다시 읽는 간격
    api_stale_seconds: int = 21600                  # DB 가 끊겼을 때 이 시간까지는 마지막으로 읽은 목록으로 계속 응답
    feasibility_ai: bool = True                     # 조건부 건의 '일반적 충족 가능성'을 AI 로 보조 판단 (FEASIBILITY_AI). 키가 없거나 실패하면 규칙값만 쓴다
    feasibility_timeout: int = 8                    # AI 보조 판단을 기다리는 최대 초 (넘으면 규칙값으로 응답)
    feasibility_rules_file: str = ""                # 실현 가능성 규칙 파일 (FEASIBILITY_RULES_FILE). 비우면 config/feasibility_rules.json
    recommend_school_majors: tuple = ()             # 학교 주력 계열 (RECOMMEND_SCHOOL_MAJORS, 예: ENGINEERING). 비우면 규칙 파일의 학교_계열
    condition_summary_ai: bool = True               # 조건이 많은 조건부 건을 AI 로 요약 (CONDITION_SUMMARY_AI). 키가 없거나 실패하면 코드 규칙으로 묶어 요약
    condition_summary_timeout: int = 15             # AI 요약을 기다리는 최대 초
    condition_summary_min_rows: int = 4             # 확인할 조건 행이 이 이상일 때만 요약
    condition_summary_max_items: int = 8            # 한 요청에서 요약하는 조건부 건 최대 개수
    feasibility_max_items: int = 20                 # 한 요청에서 AI 에 묻는 조건 문장 최대 개수
    api_enable_demo: bool = False                   # /demo 시험 화면 (키 없이 열리므로 개발용에서만 켠다)

    # --- 공고 질문 챗봇 (POST /v1/chat). 판정은 코드가 하고, 챗봇은 공고 원문을 근거로 설명만 한다. 기본은 꺼짐 ---
    chat_enabled: bool = False                      # CHAT_ENABLED. 켜면 학생 질문마다 AI 를 부른다 (AI_API_KEY 필요)
    chat_model: str = ""                            # CHAT_MODEL. 비우면 AI_MODEL
    chat_max_tokens: int = 3500                     # CHAT_MAX_TOKENS 답변 최대 토큰
    chat_timeout: int = 60                          # CHAT_TIMEOUT AI 응답을 기다리는 최대 초
    chat_max_concurrent: int = 4                    # CHAT_MAX_CONCURRENT 동시에 처리하는 질문 수 (넘으면 429 BUSY)
    chat_context_chars: int = 30000                 # CHAT_CONTEXT_CHARS AI 에 보내는 공고 원문 최대 글자 수
    chat_cache_seconds: int = 3600                  # CHAT_CACHE_SECONDS 같은 질문(첫 질문)의 답을 다시 쓰는 시간

    # --- 챗봇 색인 (RAG). 하루 한 번 run_build_rag_index.py 가 만들고, 챗봇이 질문 때 검색한다. 기본은 꺼짐. 키는 set_secret.py 로 저장 ---
    rag_enabled: bool = False                       # RAG_ENABLED 챗봇이 색인 검색을 쓴다 (실패하면 예전 방식으로 대신)
    rag_embed_model: str = "text-embedding-3-large"  # RAG_EMBED_MODEL
    rag_embed_dims: int = 1024                      # RAG_EMBED_DIMS 임베딩 차원 (Pinecone 색인을 만들 때 쓴 값과 같아야 함)
    rag_top_k: int = 6                              # RAG_TOP_K 질문마다 꺼내는 원문 조각 수
    rag_base_url: str = ""                          # RAG_BASE_URL 임베딩 API 주소. 비우면 OPENAI_API_KEY 가 있으면 OpenAI, 없으면 AI_BASE_URL 의 게이트웨이(.../v1/gateway)
    rag_timeout: int = 15                           # RAG_TIMEOUT 질문 때 임베딩·검색을 기다리는 최대 초 (넘으면 예전 방식)
    openai_api_key: str = ""                        # OPENAI_API_KEY (set_secret.py 로 저장). 없으면 AI_API_KEY 를 게이트웨이 임베딩에 같이 쓴다
    pinecone_api_key: str = ""                      # PINECONE_API_KEY (set_secret.py 로 저장)
    pinecone_host: str = ""                         # PINECONE_HOST 콘솔에서 만든 색인의 주소 (예: xxx.svc.xxx.pinecone.io)
    pinecone_namespace: str = "scholarship"         # PINECONE_NAMESPACE

    keywords_file: Path = CONFIG_DIR / "file_keywords.json"
    major_exclude: bool = True                      # MAJOR_EXCLUDE 농업·예체능 계열 전용 공고는 AI 추출 전에 건너뜀 (false 로 끄기)
    major_exclude_file: Path = CONFIG_DIR / "major_exclude.json"      # MAJOR_EXCLUDE_FILE 제외 계열 키워드

    def embedding_endpoint(self):
        """임베딩 호출에 쓸 (키, 주소). 우선순위: RAG_BASE_URL 이 있으면 그 주소 / OPENAI_API_KEY 가 있으면 OpenAI / AI_BASE_URL 이 있으면 같은 게이트웨이(.../v1/gateway)와 AI_API_KEY.
        게이트웨이 주소가 '/claude' 로 끝나면(Claude 방식) 그 부분을 떼어 OpenAI 호환 주소로 바꾼다."""
        explicit = (self.rag_base_url or "").rstrip("/")
        if self.openai_api_key:
            return self.openai_api_key, explicit or "https://api.openai.com/v1"
        base = explicit
        if not base and self.ai_base_url:
            base = self.ai_base_url.rstrip("/")
            if base.endswith("/claude"):
                base = base[: -len("/claude")]
        return self.ai_api_key, base

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.db_user = env_str("DB_USER")
        s.db_password = env_str("DB_PASSWORD")
        s.db_dsn = env_str("DB_DSN")
        s.oracle_client_lib = env_str("ORACLE_CLIENT_LIB") or None
        s.nas_root = Path(env_str("SCHOLARSHIP_NAS_ROOT", str(s.nas_root)))
        s.ai_provider = env_str("AI_PROVIDER", s.ai_provider).lower()
        s.ai_api_key = env_str("AI_API_KEY")
        s.dreamspon_id = env_str("DREAMSPON_ID")
        s.dreamspon_password = env_str("DREAMSPON_PASSWORD")
        mode = env_str("EXT02_ATTACH_TO_AI", s.ext02_attach_to_ai).strip().lower()
        s.ext02_attach_to_ai = mode if mode in ("guide_only", "guide_and_form", "none") else "guide_only"
        s.ai_base_url = env_str("AI_BASE_URL")
        s.ai_anthropic_beta = env_str("AI_ANTHROPIC_BETA")
        s.ai_model = env_str("AI_MODEL", s.ai_model)
        s.ai_vision = env_str("AI_VISION", "true").lower() not in ("0", "false", "no", "off", "n")
        s.ai_timeout = env_int("AI_TIMEOUT", s.ai_timeout)
        s.ai_schema_version = env_int("AI_SCHEMA_VERSION", s.ai_schema_version)
        if s.ai_schema_version not in (2, 3):
            raise ValueError("AI_SCHEMA_VERSION 은 2 또는 3 이어야 합니다: {}".format(s.ai_schema_version))
        s.item_registry_file = env_str("ITEM_REGISTRY_FILE", s.item_registry_file)
        s.ai_max_tokens = env_int("AI_MAX_TOKENS", s.ai_max_tokens)
        s.ai_max_tokens_limit = max(env_int("AI_MAX_TOKENS_LIMIT", s.ai_max_tokens_limit), s.ai_max_tokens)
        s.keywords_file = Path(env_str("KEYWORDS_FILE", str(s.keywords_file)))
        s.recommend_urgent_days = env_int("RECOMMEND_URGENT_DAYS", s.recommend_urgent_days)
        s.recommend_excluded_detail = env_bool("RECOMMEND_EXCLUDED_DETAIL", s.recommend_excluded_detail)
        s.recommend_include_closed = env_bool("RECOMMEND_INCLUDE_CLOSED", s.recommend_include_closed)
        s.recommend_min_days_left = env_int("RECOMMEND_MIN_DAYS_LEFT", s.recommend_min_days_left)
        s.recommend_include_need_review = env_bool("RECOMMEND_INCLUDE_NEED_REVIEW", s.recommend_include_need_review)
        s.recommend_dedupe = env_bool("RECOMMEND_DEDUPE", s.recommend_dedupe)
        s.recommend_categories = parse_categories(env_str("RECOMMEND_CATEGORIES"), s.recommend_categories)
        s.recommend_max_results = env_int("RECOMMEND_MAX_RESULTS", s.recommend_max_results)
        s.recommend_regular_terms = env_int("RECOMMEND_REGULAR_TERMS", s.recommend_regular_terms)
        s.default_gpa_scale = env_float("RECOMMEND_DEFAULT_GPA_SCALE", s.default_gpa_scale)
        s.recommend_defer_keys = tuple(k.strip().lower() for k in env_str("RECOMMEND_DEFER_KEYS").split(",") if k.strip())
        s.recommend_school_type = env_str("RECOMMEND_SCHOOL_TYPE")
        s.recommend_school_sido = env_str("RECOMMEND_SCHOOL_SIDO")
        s.recommend_school_sigungu = env_str("RECOMMEND_SCHOOL_SIGUNGU")
        s.recommend_school_programs = tuple(k.strip() for k in env_str("RECOMMEND_SCHOOL_PROGRAMS").split(",") if k.strip())
        s.recommend_query_file = Path(env_str("RECOMMEND_QUERY_FILE") or str(s.recommend_query_file))
        s.api_key = env_str("API_KEY")
        s.api_require_key = env_bool("API_REQUIRE_KEY", s.api_require_key)
        s.api_max_body_bytes = env_int("API_MAX_BODY_BYTES", s.api_max_body_bytes)
        s.api_cache_seconds = env_int("API_CACHE_SECONDS", s.api_cache_seconds)
        s.api_stale_seconds = env_int("API_STALE_SECONDS", s.api_stale_seconds)
        s.api_enable_demo = env_bool("API_ENABLE_DEMO", s.api_enable_demo)
        s.chat_enabled = env_bool("CHAT_ENABLED", s.chat_enabled)
        s.chat_model = env_str("CHAT_MODEL", s.chat_model)
        s.chat_max_tokens = env_int("CHAT_MAX_TOKENS", s.chat_max_tokens)
        s.chat_timeout = env_int("CHAT_TIMEOUT", s.chat_timeout)
        s.chat_max_concurrent = max(1, env_int("CHAT_MAX_CONCURRENT", s.chat_max_concurrent))
        s.chat_context_chars = env_int("CHAT_CONTEXT_CHARS", s.chat_context_chars)
        s.chat_cache_seconds = env_int("CHAT_CACHE_SECONDS", s.chat_cache_seconds)
        s.rag_enabled = env_bool("RAG_ENABLED", s.rag_enabled)
        s.rag_embed_model = env_str("RAG_EMBED_MODEL", s.rag_embed_model)
        s.rag_embed_dims = env_int("RAG_EMBED_DIMS", s.rag_embed_dims)
        s.rag_top_k = max(1, env_int("RAG_TOP_K", s.rag_top_k))
        s.rag_base_url = env_str("RAG_BASE_URL", s.rag_base_url)
        s.rag_timeout = env_int("RAG_TIMEOUT", s.rag_timeout)
        s.openai_api_key = env_str("OPENAI_API_KEY")
        s.pinecone_api_key = env_str("PINECONE_API_KEY")
        s.pinecone_host = env_str("PINECONE_HOST", s.pinecone_host)
        s.pinecone_namespace = env_str("PINECONE_NAMESPACE", s.pinecone_namespace)
        s.feasibility_ai = env_bool("FEASIBILITY_AI", s.feasibility_ai)
        s.feasibility_timeout = env_int("FEASIBILITY_TIMEOUT", s.feasibility_timeout)
        s.condition_summary_ai = env_bool("CONDITION_SUMMARY_AI", s.condition_summary_ai)
        s.condition_summary_timeout = env_int("CONDITION_SUMMARY_TIMEOUT", s.condition_summary_timeout)
        s.condition_summary_min_rows = env_int("CONDITION_SUMMARY_MIN_ROWS", s.condition_summary_min_rows)
        s.condition_summary_max_items = env_int("CONDITION_SUMMARY_MAX_ITEMS", s.condition_summary_max_items)
        s.feasibility_rules_file = env_str("FEASIBILITY_RULES_FILE", s.feasibility_rules_file)
        s.recommend_school_majors = tuple(k.strip().upper() for k in env_str("RECOMMEND_SCHOOL_MAJORS").split(",") if k.strip())
        s.feasibility_max_items = env_int("FEASIBILITY_MAX_ITEMS", s.feasibility_max_items)
        s.skip_same_title = env_bool("SKIP_SAME_TITLE", s.skip_same_title)
        s.major_exclude = env_bool("MAJOR_EXCLUDE", s.major_exclude)
        s.major_exclude_file = Path(env_str("MAJOR_EXCLUDE_FILE", str(s.major_exclude_file)))
        s.update_changed = env_bool("UPDATE_CHANGED", s.update_changed)
        s.max_chars_per_file = env_int("MAX_CHARS_PER_FILE", s.max_chars_per_file)
        s.max_chars_secondary = env_int("MAX_CHARS_SECONDARY", s.max_chars_secondary)
        s.max_total_chars = env_int("MAX_TOTAL_CHARS", s.max_total_chars)
        return s
