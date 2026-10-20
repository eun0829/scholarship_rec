#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""비밀 값(DB 비밀번호, AI 키)을 '처음 한 번' 입력해서 권한이 제한된 파일에 저장한다.
이후 run_ext01.py 는 이 파일을 자동으로 읽는다. 값은 화면/로그에 출력되지 않는다.

  python set_secret.py                      # 전체 항목을 차례로 입력 (Enter = 변경 안 함)
  python set_secret.py DB_PASSWORD          # 한 항목만 입력
  python set_secret.py AI_API_KEY --stdin   # 표준입력에서 한 줄 읽기 (자동화용. 명령줄 인자로 값을 넘기지 말 것)
  python set_secret.py --show               # 저장된 항목과 글자 수만 표시
  python set_secret.py --delete AI_API_KEY  # 항목 삭제
  python set_secret.py --test-db            # 저장된 값으로 DB 접속 테스트 (홈페이지 DB / 저장 DB)
  python set_secret.py --test-ai            # 저장된 키로 AI 를 아주 짧게 1회 호출해서 키/모델명 확인 (토큰 몇 개 소모)
  python set_secret.py --test-dreamspon     # 드림스폰 목록(로그인 불필요) -> 로그인 -> 상세 본문 읽기 확인
  python set_secret.py --credits            # (게이트웨이) 크레딧 잔액 조회
  python set_secret.py --models             # (게이트웨이) 이 키로 쓸 수 있는 LLM 모델 목록

저장 위치: 기본 ~/.scholarship/secrets.env  (--file 또는 환경변수 SCHOLARSHIP_SECRET_FILE 로 변경)
※ 배치를 실행하는 OS 계정으로 입력하세요. (계정이 다르면 파일을 읽지 못합니다)
"""
import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

from scholarship.common import secret_store as ss
from scholarship.common.config import Settings, bootstrap

LABELS = {
    "DB_PASSWORD": "저장 DB 비밀번호",
    "EXT01_DB_PASSWORD": "홈페이지 DB 비밀번호 (홈페이지 DB 가 저장 DB 와 같으면 입력하지 않아도 됨)",
    "AI_API_KEY": "AI API 키",
    "API_KEY": "추천 API 키 (포털이 X-API-Key 헤더로 보내는 값. 길고 무작위인 값을 쓰세요)",
    "DREAMSPON_ID": "드림스폰 로그인 아이디(이메일). 수집 전용 계정 권장",
    "DREAMSPON_PASSWORD": "드림스폰 로그인 비밀번호",
    "OPENAI_API_KEY": "OpenAI API 키 (챗봇 색인용 임베딩)",
    "PINECONE_API_KEY": "Pinecone API 키 (챗봇 색인 저장소)",
}


def prompt_value(key: str, ask: Optional[Callable[[str], str]] = None) -> Optional[str]:
    """입력 화면에 값이 표시되지 않는다. 비밀번호는 두 번 입력받아 오타를 막는다. Enter 만 치면 None(변경 안 함)."""
    ask = ask or getpass.getpass                 # 호출 시점에 결정 (테스트에서 교체 가능)
    for _ in range(3):
        v = ask("{} [{}] (Enter=건너뜀): ".format(key, LABELS[key]))
        if not v:
            return None
        if key.endswith("PASSWORD"):
            if ask("{} 다시 한 번 입력: ".format(key)) != v:
                print("  두 입력이 다릅니다. 다시 입력하세요.")
                continue
        return v
    print("  {}: 3회 실패, 건너뜁니다.".format(key))
    return None


def test_connections(settings: Settings, cfg, connect: Optional[Callable] = None) -> List[str]:
    """저장 DB / 홈페이지 DB 접속을 시험한다. 실패 사유만 출력하고 비밀번호는 출력하지 않는다. 반환: 실패한 대상 목록"""
    if connect is None:
        from scholarship.common import db
        connect = db.connect
    failed = []
    targets = (
        ("저장 DB", (settings.db_user, "DB_USER"), (settings.db_password, "DB_PASSWORD"), (settings.db_dsn, "DB_DSN")),
        ("홈페이지 DB", (cfg.db_user, "EXT01_DB_USER 또는 DB_USER"), (cfg.db_password, "EXT01_DB_PASSWORD 또는 DB_PASSWORD"),
         (cfg.db_dsn, "EXT01_DB_DSN 또는 DB_DSN")),
    )
    for name, (user, ul), (pw, pl), (dsn, dl) in targets:
        empty = [label for val, label in ((user, ul), (pw, pl), (dsn, dl)) if not val]
        if empty:
            print("[건너뜀] {}: 비어 있는 항목 -> {}".format(name, ", ".join(empty)))
            failed.append(name)
            continue
        try:
            conn = connect(user, pw, dsn, settings.oracle_client_lib)
            conn.close()
            print("[성공] {} 접속 ({} @ {})".format(name, user, dsn))
        except Exception as e:
            print("[실패] {}: {}".format(name, str(e).replace(pw, "****")))
            failed.append(name)
    return failed


def show_credits(settings: Settings, fetch: Optional[Callable] = None) -> bool:
    """게이트웨이 크레딧 잔액 표시 (게이트웨이가 아니면 건너뜀). 실패해도 예외를 내지 않는다."""
    from scholarship.common.ai import gateway
    if not gateway.is_gateway(settings.ai_base_url):
        print("[건너뜀] 크레딧 조회는 게이트웨이(AI_BASE_URL)를 쓸 때만 가능합니다.")
        return False
    try:
        data = (fetch or gateway.fetch_credits)(settings.ai_base_url, settings.ai_api_key)
    except Exception as e:
        print("[실패] 크레딧 조회: {}".format(str(e).replace(settings.ai_api_key, "****")))
        return False
    print("[크레딧]")
    for line in gateway.format_credits(data):
        print(line)
    return True


def show_models(settings: Settings, fetch: Optional[Callable] = None, model_type: str = "llm") -> bool:
    from scholarship.common.ai import gateway
    if not gateway.is_gateway(settings.ai_base_url):
        print("[건너뜀] 모델 목록 조회는 게이트웨이(AI_BASE_URL)를 쓸 때만 가능합니다.")
        return False
    try:
        if fetch:
            models = fetch(settings.ai_base_url, settings.ai_api_key)
        else:
            models = gateway.fetch_models(settings.ai_base_url, settings.ai_api_key, "" if model_type == "all" else model_type)
    except Exception as e:
        print("[실패] 모델 목록 조회: {}".format(str(e).replace(settings.ai_api_key, "****")))
        return False
    ids = [str(m.get("id")) for m in models if m.get("id")]
    mark = "  <- 현재 AI_MODEL" 
    print("[모델] 종류={} {}개 (현재 AI_MODEL={})".format(model_type, len(ids), settings.ai_model))
    for i in ids:
        print("  {}{}".format(i, mark if i == settings.ai_model else ""))
    if settings.ai_model not in ids and model_type == "llm":
        print("  ※ 현재 AI_MODEL 이 목록에 없습니다. 위 id 중 하나로 .env 의 AI_MODEL 을 바꾸세요.")
    return True


def test_ai(settings: Settings, client_factory: Optional[Callable] = None) -> bool:
    """AI 키/모델명/호출 주소를 아주 짧은 호출 1회로 확인한다. 성공 여부를 돌려준다. 키는 출력하지 않는다."""
    key = settings.ai_api_key
    if not key:
        print("[건너뜀] AI: 비어 있는 항목 -> AI_API_KEY (python set_secret.py AI_API_KEY)")
        return False
    where = "provider={}, model={}{}".format(settings.ai_provider, settings.ai_model,
                                             ", base_url={}".format(settings.ai_base_url) if settings.ai_base_url else "")
    try:
        if client_factory is None:
            from scholarship.common.ai.client import make_client
            client_factory = lambda st: make_client(st, max_tokens=16, timeout=60)
        resp = client_factory(settings).complete("Reply with one short word.", "ping", [])
        print("[성공] AI 호출 ({}, 응답 모델={}, 토큰 입력 {} / 출력 {})".format(
            where, resp.model or settings.ai_model, resp.input_tokens, resp.output_tokens))
        return True
    except Exception as e:
        print("[실패] AI ({}): {}".format(where, str(e).replace(key, "****")))
        low = str(e).lower()
        if isinstance(e, ValueError) or "ai_base_url" in low or "'/claude'" in low or "/claude" in low and "주소" in low:
            print("  -> .env 의 AI_PROVIDER / AI_BASE_URL 설정을 확인하세요.")
            print("     게이트웨이 + Claude 직접 방식(권장): AI_PROVIDER=anthropic, AI_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway/claude")
            print("     게이트웨이 + OpenAI 호환 방식     : AI_PROVIDER=gateway,   AI_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway")
        elif "http 402" in low or "credit" in low or "balance" in low:
            print("  -> 크레딧이 소진되었거나 부족합니다. (게이트웨이는 API 키를 만든 계정의 크레딧이 차감됩니다) python set_secret.py --credits 로 잔액을 확인하세요.")
        elif "http 401" in low or "authentication" in low or "invalid x-api-key" in low or "unauthorized" in low or "invalid api key" in low:
            print("  -> 키가 올바르지 않거나 해지/비활성화된 키입니다. 호출 방식(직접/게이트웨이)에 맞는 키인지, 복사 중 잘리지 않았는지 확인 후 python set_secret.py AI_API_KEY 로 다시 저장하세요.")
        elif "1010" in low or ("http 403" in low and "model" not in low):
            print("  -> 403: 게이트웨이 앞단(Cloudflare)이 차단했거나 권한이 없습니다. 담당자에게 문의하세요 (비어 있는 User-Agent 는 이 프로그램에서 보내지 않습니다).")
        elif "http 403" in low:
            print("  -> 이 모델이 조직에 활성화되어 있지 않거나 API 로 제공되지 않는 모델입니다. python set_secret.py --models 로 쓸 수 있는 모델을 확인하세요.")
        elif "http 404" in low or "not_found" in low or "not found" in low:
            print("  -> 호출 주소나 모델명을 확인하세요. AI_MODEL={!r}  (python set_secret.py --models 로 목록 확인)".format(settings.ai_model))
        elif "http 413" in low:
            print("  -> 요청 본문이 25MB 를 넘었습니다 (이미지가 큰 경우).")
        elif "http 429" in low:
            print("  -> 호출이 너무 잦습니다 (게이트웨이: 회원당 60초에 120건). 잠시 뒤 다시 시도하세요.")
        elif "요청 실패" in low or "connection" in low or "timed out" in low:
            print("  -> 이 PC/서버에서 호출 주소로 나가는 네트워크(방화벽, 프록시)가 열려 있는지 확인하세요.")
        return False


def test_dreamspon(settings: Settings, adapter_factory: Optional[Callable] = None) -> bool:
    """드림스폰 목록(로그인 불필요) -> 로그인 -> 상세 본문 읽기를 차례로 확인한다. 아이디/비밀번호는 출력하지 않는다."""
    from scholarship.sources.ext02 import DetailError, Ext02Adapter, Ext02Config, LoginError
    uid, pw = settings.dreamspon_id, settings.dreamspon_password
    missing = [k for k, v in (("DREAMSPON_ID", uid), ("DREAMSPON_PASSWORD", pw)) if not v]
    if missing:
        print("[건너뜀] 드림스폰: 비어 있는 항목 -> {}  (python set_secret.py <항목>)".format(", ".join(missing)))
        return False
    mask = lambda t: str(t).replace(pw, "****").replace(uid, "****")
    adapter = (adapter_factory or (lambda st: Ext02Adapter(st, Ext02Config.load())))(settings)
    try:
        rows = adapter.select(adapter.crawl_list())
        print("[성공] 목록 조회 (로그인 불필요): 후보 {}건 (종류 {}, 대상 {})".format(
            len(rows), "/".join(adapter.cfg.include_kinds) or "전체", "/".join(adapter.cfg.include_tags) or "전체"))
        adapter.login()
        print("[성공] 로그인 (계정 {}***)".format(uid[:2]))
        if rows:
            d = adapter.fetch_detail(rows[0].idx)
            print("[성공] 상세 본문 읽기 (idx={}, 고정 항목 {}개, 첨부 {}개)".format(rows[0].idx, len(d.sections), len(d.attachments)))
        else:
            print("[건너뜀] 상세 확인: 후보가 없어 읽어볼 글이 없습니다 (config/ext02.json 의 include_* 조건 확인)")
        return True
    except LoginError as e:
        print("[실패] 로그인: {}".format(mask(e)))
        return False
    except DetailError as e:
        print("[실패] 드림스폰 조회: {}".format(mask(e)))
        return False
    except Exception as e:
        print("[실패] 드림스폰: {}".format(mask(e)))
        return False
    finally:
        adapter.close()


def report_sources(path: Path, env_file: str) -> None:
    env_path = Path(env_file).resolve()
    print(".env      : {} ({})".format(env_path, "찾음" if env_path.is_file() else "없음 - 이름이 .env 인지, 현재 폴더에 있는지 확인"))
    print("비밀 파일 : {} ({})".format(path, "찾음" if path.is_file() else "없음 - python set_secret.py 로 먼저 저장"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="비밀 값 파일 관리")
    ap.add_argument("key", nargs="?", choices=ss.ALLOWED_KEYS, help="입력할 항목 (생략하면 전체)")
    ap.add_argument("--file", help="비밀 파일 경로 (기본: ~/.scholarship/secrets.env)")
    ap.add_argument("--stdin", action="store_true", help="표준입력에서 한 줄을 읽어 저장 (key 필수)")
    ap.add_argument("--show", action="store_true", help="저장된 항목과 글자 수만 표시")
    ap.add_argument("--delete", choices=ss.ALLOWED_KEYS, help="항목 삭제")
    ap.add_argument("--test-db", action="store_true", help="저장된 값으로 DB 접속 테스트")
    ap.add_argument("--test-ai", action="store_true", help="저장된 키로 AI 를 짧게 1회 호출해서 확인 (토큰 몇 개 소모)")
    ap.add_argument("--test-dreamspon", action="store_true", help="드림스폰 목록/로그인/상세 읽기 확인 (계정은 DREAMSPON_ID/PASSWORD)")
    ap.add_argument("--model-type", default="llm", help="--models 에서 볼 모델 종류 (기본 llm, 임베딩은 embedding, 전부는 all)")
    ap.add_argument("--credits", action="store_true", help="(게이트웨이) 크레딧 잔액 조회")
    ap.add_argument("--models", action="store_true", help="(게이트웨이) 이 키로 쓸 수 있는 모델 목록 (기본: LLM)")
    ap.add_argument("--env", default=".env", help=".env 경로 (--test-db / --test-ai 에서 사용)")
    a = ap.parse_args(argv)
    path = Path(a.file) if a.file else ss.default_path()

    try:
        if a.show:
            items = ss.describe(path)
            print("비밀 파일: {} ({})".format(path, "있음" if path.is_file() else "없음"))
            for k in ss.ALLOWED_KEYS:
                n = dict(items).get(k)
                print("  {:<20} {}".format(k, "저장됨 ({}자)".format(n) if n else "-"))
            return 0

        if a.delete:
            print("삭제됨: {}".format(a.delete) if ss.delete(path, a.delete) else "저장된 항목이 아님: {}".format(a.delete))
            return 0

        if a.test_db or a.test_ai or a.test_dreamspon or a.credits or a.models:
            from scholarship.sources.ext01 import Ext01Config
            bootstrap(str(path), a.env)
            st = Settings.from_env()
            report_sources(path, a.env)
            bad = False
            if a.test_db:
                bad = bool(test_connections(st, Ext01Config.from_env(st))) or bad
            if a.test_ai:
                bad = (not test_ai(st)) or bad
            if a.test_dreamspon:
                bad = (not test_dreamspon(st)) or bad
            if a.credits or a.test_ai:
                if not st.ai_api_key:
                    print("[건너뜀] 크레딧 조회: AI_API_KEY 가 없습니다.")
                else:
                    ok_credit = show_credits(st)
                    bad = (a.credits and not ok_credit) or bad       # --test-ai 에서의 크레딧 조회 실패는 참고용
            if a.models:
                bad = (not (st.ai_api_key and show_models(st, model_type=a.model_type))) or bad
            return 1 if bad else 0

        updates = {}
        if a.stdin:
            if not a.key:
                print("--stdin 은 항목 이름(key)이 필요합니다.", file=sys.stderr)
                return 2
            v = sys.stdin.readline().rstrip("\r\n")
            if not v:
                print("입력된 값이 없습니다.", file=sys.stderr)
                return 2
            updates[a.key] = v
        else:
            for k in ([a.key] if a.key else ss.ALLOWED_KEYS):
                v = prompt_value(k)
                if v is not None:
                    updates[k] = v
        if not updates:
            print("변경된 항목이 없습니다.")
            return 0
        ss.write(path, updates)
        print("저장됨: {}".format(", ".join(updates)))
        print("파일: {}".format(path) + ("  (권한 600)" if os.name != "nt" else "  (현재 사용자만 접근하도록 제한 시도)"))
        return 0
    except (ValueError, ss.SecretFileError) as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
