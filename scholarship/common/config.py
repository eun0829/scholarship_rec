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

    keywords_file: Path = CONFIG_DIR / "file_keywords.json"

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
        s.skip_same_title = env_bool("SKIP_SAME_TITLE", s.skip_same_title)
        s.update_changed = env_bool("UPDATE_CHANGED", s.update_changed)
        s.max_chars_per_file = env_int("MAX_CHARS_PER_FILE", s.max_chars_per_file)
        s.max_chars_secondary = env_int("MAX_CHARS_SECONDARY", s.max_chars_secondary)
        s.max_total_chars = env_int("MAX_TOTAL_CHARS", s.max_total_chars)
        return s
