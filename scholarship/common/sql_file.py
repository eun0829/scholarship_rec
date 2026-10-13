# -*- coding: utf-8 -*-
"""[COMMON] 외부 SQL 파일 로더 (코드를 고치지 않고 쿼리를 수정할 수 있게 한다).

  - 파일은 호출할 때마다 다시 읽는다 (캐시 없음)
  - UTF-8 이 아니면 CP949(메모장 ANSI 저장)로 다시 시도한다
  - 끝의 ; 또는 / 와 주석(--, /* */)은 제거한다 (힌트 /*+ */ 는 유지). 문장이 둘 이상이면 오류
  - {TOKEN} 치환: 호출자가 지정한 토큰만 허용, 모르는 토큰은 오류
  - 실제로 쓰인 바인드 변수만 골라서 넘길 수 있도록 used_binds() 제공
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Set


class SqlFileError(Exception):
    pass


def read_text(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise SqlFileError("SQL 파일이 없습니다: {}".format(p))
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SqlFileError("SQL 파일 인코딩을 읽을 수 없습니다 (UTF-8 또는 CP949 로 저장하세요): {}".format(p))


def mask(sql: str) -> str:
    """주석과 문자열 리터럴의 내용을 공백으로 바꾼 같은 길이의 문자열 (분석용: 위치가 그대로 유지됨)."""
    out: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":                                         # 문자열 ('' 는 이스케이프)
            out.append("'")
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("  ")
                        i += 2
                        continue
                    break
                out.append("\n" if sql[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("'")
                i += 1
        elif sql.startswith("--", i):                        # 한 줄 주석
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
        elif sql.startswith("/*", i):                        # 블록 주석
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in sql[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_comments(sql: str) -> str:
    """주석(-- , /* */)을 제거한다. 문자열 리터럴과 Oracle 힌트(/*+ ... */)는 그대로 둔다."""
    out: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            if sql.startswith("/*+", i):
                out.append(sql[i:end])                 # 힌트는 유지
            else:
                out.append(" ")
            i = end
        else:
            out.append(c)
            i += 1
    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)              # 줄 끝 공백 정리
    return re.sub(r"\n\s*\n+", "\n", text).strip()      # 빈 줄 정리


def clean(text: str) -> str:
    """SQL 문장 하나만 남긴다 (끝의 ; / 와 그 뒤의 주석 제거)."""
    sql = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    m = mask(sql)
    semis = [i for i, ch in enumerate(m) if ch == ";"]
    if semis:
        if len(semis) > 1 or m[semis[0] + 1:].replace("/", " ").strip():
            raise SqlFileError("SQL 파일에는 문장이 하나만 있어야 합니다 (; 가 여러 개이거나 ; 뒤에 내용이 있음)")
        sql = sql[:semis[0]].rstrip()
    else:
        sql = re.sub(r"\n\s*/\s*$", "", sql).rstrip()          # SQL*Plus 식 종료 '/' 한 줄
    if not mask(sql).strip():
        raise SqlFileError("SQL 파일에 실행할 문장이 없습니다 (주석만 있음)")
    return strip_comments(sql)


def used_binds(sql: str) -> Set[str]:
    return {b.lower() for b in re.findall(r":([A-Za-z_]\w*)", mask(sql))}


def render(path: Path, tokens: Dict[str, str], allowed_binds: Iterable[str]) -> str:
    """파일을 읽어 정리하고 {TOKEN} 을 치환한다. 허용되지 않은 바인드 변수/토큰은 오류."""
    sql = clean(read_text(path))
    for k, v in tokens.items():
        sql = sql.replace("{" + k + "}", v)
    left = sorted(set(re.findall(r"\{([A-Z][A-Z_]*)\}", mask(sql))))
    if left:
        raise SqlFileError("{}: 알 수 없는 치환 토큰 {} (사용 가능: {})".format(
            Path(path).name, ", ".join("{" + t + "}" for t in left), ", ".join("{" + t + "}" for t in tokens)))
    allowed = {a.lower() for a in allowed_binds}
    bad = sorted(used_binds(sql) - allowed)
    if bad:
        raise SqlFileError("{}: 사용할 수 없는 바인드 변수 {} (사용 가능: {})".format(
            Path(path).name, ", ".join(":" + b for b in bad), ", ".join(":" + a for a in sorted(allowed))))
    return sql
