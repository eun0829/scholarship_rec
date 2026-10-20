#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[RAG] 챗봇 색인 만들기: 마감 안 된 공고를 조각내 OpenAI 로 임베딩하고 Pinecone 에 올린다. 수집(run_ext01 / run_ext02) 뒤에 하루 한 번 돌린다.

  python run_build_rag_index.py --plan                 # 호출 없이 공고·조각 수와 글자 수만 센다 (키 불필요, DB 만 읽음)
  python run_build_rag_index.py                        # 바뀐 공고만 임베딩해 올리고, 마감된 공고는 지운다
  python run_build_rag_index.py --force                # 모든 공고를 다시 임베딩
  python run_build_rag_index.py --include-closed       # 시험용: 마감 지난 공고까지 (운영에서는 쓰지 않는다)
준비(한 번): Pinecone 콘솔에서 색인(차원 RAG_EMBED_DIMS=1024, 지표 cosine)을 만들고 그 주소를 .env 의 PINECONE_HOST 에 적는다.
키는 python set_secret.py OPENAI_API_KEY / PINECONE_API_KEY 로 저장한다 (.env 에 적지 않는다).
"""
import argparse
import logging
import sys
from datetime import date

from scholarship.common.config import Settings, bootstrap
from scholarship.common.secret_store import SecretFileError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="챗봇 색인 만들기")
    ap.add_argument("--plan", action="store_true", help="호출 없이 개수·글자 수만 센다")
    ap.add_argument("--force", action="store_true", help="바뀌지 않은 공고도 다시 임베딩")
    ap.add_argument("--include-closed", action="store_true", help="시험용: 마감 지난 공고 포함 (운영 배치에서는 쓰지 않음)")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 (시험용. 이때는 지난 공고 정리를 하지 않는다)")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--secret-file")
    ap.add_argument("--log-file")
    a = ap.parse_args(argv)
    handlers = [logging.StreamHandler()] + ([logging.FileHandler(a.log_file, encoding="utf-8")] if a.log_file else [])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers, force=True)
    try:
        bootstrap(a.secret_file, a.env)
    except SecretFileError as e:
        print("오류: {}".format(e), file=sys.stderr)
        return 2
    s = Settings.from_env()
    missing = []
    if not s.db_password:
        missing.append("DB_PASSWORD")
    if not a.plan:
        if not s.embedding_endpoint()[0]:
            missing.append("OPENAI_API_KEY (또는 게이트웨이를 쓰면 AI_API_KEY)")
        if not s.embedding_endpoint()[1]:
            print("필요한 설정이 없습니다: 임베딩 주소 (RAG_BASE_URL 또는 AI_BASE_URL)", file=sys.stderr)
            return 2
        if not s.pinecone_api_key:
            missing.append("PINECONE_API_KEY")
        if not s.pinecone_host:
            print("필요한 설정이 없습니다: PINECONE_HOST (.env 에 Pinecone 색인 주소를 적으세요)", file=sys.stderr)
            return 2
    if missing:
        print("필요한 비밀 값이 없습니다: {}\n  -> python set_secret.py <항목>  으로 한 번 저장하세요.".format(", ".join(missing)), file=sys.stderr)
        return 2
    from run_api import build_source_factory
    from scholarship.rag.embed import OpenAIEmbedder
    from scholarship.rag.indexer import build_index
    from scholarship.rag.store import PineconeStore
    src = build_source_factory(s)()
    try:
        if a.plan:
            class _P:
                model, dims = s.rag_embed_model, s.rag_embed_dims
            st = build_index(src, _P(), None, date.today(), include_closed=a.include_closed, limit=a.limit, plan_only=True)
            print("공고 {}건, 문서 {}개, 총 {:,}자 (임베딩 토큰은 대략 {:,}~{:,}개로 추정)".format(st["notices"], st["docs"], st["chars"], int(st["chars"] * 0.5), int(st["chars"] * 1.0)))
            return 0
        ekey, ebase = s.embedding_endpoint()
        print("임베딩: {} / 모델 {} / 차원 {}".format(ebase, s.rag_embed_model, s.rag_embed_dims))
        emb = OpenAIEmbedder(ekey, s.rag_embed_model, s.rag_embed_dims, ebase, timeout=60)
        store = PineconeStore(s.pinecone_api_key, s.pinecone_host, s.pinecone_namespace, timeout=60)
        st = build_index(src, emb, store, date.today(), include_closed=a.include_closed, force=a.force, limit=a.limit)
        print("공고 {notices}건 -> 새로 올림 {indexed}, 그대로 {skipped}, 지운 공고 {removed}, 실패 {failed} (문서 {docs}개, {chars:,}자, 임베딩 토큰 {t:,})".format(t=emb.tokens_used, **st))
        return 1 if st["failed"] and st["failed"] == st["notices"] else 0
    except Exception as e:
        msg = str(e)
        for secret in (s.db_password, s.openai_api_key, s.ai_api_key, s.pinecone_api_key):
            if secret:
                msg = msg.replace(secret, "****")
        logging.getLogger("run_build_rag_index").error("색인 실패: %s", msg)
        return 1
    finally:
        src.close()


if __name__ == "__main__":
    sys.exit(main())
