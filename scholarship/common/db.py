# -*- coding: utf-8 -*-
"""[COMMON] Oracle 접속 (11g -> thick 모드). 프로세스당 init 1회."""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)
_initialized = False


def ensure_thick(lib_dir: Optional[str] = None) -> None:
    global _initialized
    if _initialized:
        return
    import oracledb
    if lib_dir:
        oracledb.init_oracle_client(lib_dir=lib_dir)
    else:
        oracledb.init_oracle_client()
    oracledb.defaults.fetch_lobs = False      # CLOB 을 str 로 바로 받기
    _initialized = True
    log.info("Oracle thick 모드 초기화 완료")


def connect(user: str, password: str, dsn: str, lib_dir: Optional[str] = None):
    import oracledb
    ensure_thick(lib_dir)
    return oracledb.connect(user=user, password=password, dsn=dsn)
