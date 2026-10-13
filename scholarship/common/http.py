# -*- coding: utf-8 -*-
"""[COMMON] 다운로드 공통 유틸 (세션, 재시도, 크기 제한, 형식 검증)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; TUKScholarshipBot/1.0; internal)"

# 확장자별 파일 시작 바이트 (오류 페이지(HTML)를 파일로 저장하는 사고 방지)
MAGIC = {
    "hwp": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    "pdf": [b"%PDF"],
    "hwpx": [b"PK"], "docx": [b"PK"], "xlsx": [b"PK"], "zip": [b"PK"],
    "jpg": [b"\xff\xd8"], "jpeg": [b"\xff\xd8"],
    "png": [b"\x89PNG"], "gif": [b"GIF8"], "webp": [b"RIFF"], "bmp": [b"BM"],
}


class DownloadError(Exception):
    pass


class HtmlResponseError(DownloadError):
    """파일 대신 HTML 이 왔다 (로그인 만료/오류 페이지일 가능성)."""


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def sniff_ok(ext: str, head: bytes) -> bool:
    sigs = MAGIC.get((ext or "").lower())
    if not sigs:
        return True                     # 모르는 확장자는 검사하지 않음
    return any(head.startswith(s) for s in sigs)


def download_file(session: requests.Session, url: str, dest: Path, max_bytes: int,
                  timeout: int = 30, retries: int = 3, ext: str = "", method: str = "GET", data=None, headers=None) -> int:
    """url -> dest. 성공 시 저장 바이트 수. 실패 시 DownloadError (HTML 응답이면 HtmlResponseError).
    method="POST" + data 로 폼 전송 방식의 다운로드(EXT02)도 처리한다."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for attempt in range(1, retries + 1):
        try:
            with session.request(method, url, data=data, headers=headers, stream=True, timeout=timeout) as r:
                if r.status_code != 200:
                    raise DownloadError("HTTP {}".format(r.status_code))
                ctype = (r.headers.get("Content-Type") or "").lower()
                if ctype.startswith("text/html"):
                    raise HtmlResponseError("HTML 응답 (로그인/오류 페이지 가능성)")
                clen = r.headers.get("Content-Length")
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    raise DownloadError("파일 크기 초과 ({} bytes)".format(clen))
                size, head = 0, b""
                with open(tmp, "wb") as fp:
                    for chunk in r.iter_content(65536):
                        if not chunk:
                            continue
                        if not head:
                            head = chunk[:16]
                        size += len(chunk)
                        if size > max_bytes:
                            raise DownloadError("파일 크기 초과 (>{} bytes)".format(max_bytes))
                        fp.write(chunk)
            if size == 0:
                raise DownloadError("빈 파일")
            if not sniff_ok(ext, head):
                raise DownloadError("파일 형식 불일치 (.{} 가 아님)".format(ext))
            tmp.replace(dest)
            return size
        except DownloadError as e:
            last = e
            if isinstance(e, HtmlResponseError) or "HTTP 4" in str(e) or "형식" in str(e) or "크기" in str(e):
                break                   # 재시도해도 소용없는 오류
        except requests.RequestException as e:
            last = DownloadError("요청 실패: {}".format(e))
        time.sleep(2.0 * attempt)
    if tmp.exists():
        tmp.unlink()
    raise last if last else DownloadError("알 수 없는 오류")
