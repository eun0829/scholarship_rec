# -*- coding: utf-8 -*-
"""[COMMON] HWP(5.0) -> 텍스트. pyhwp 의 XML 출력(hwp5proc xml)에서 표/글상자를 복원한다.

검증된 사항 (샘플 4건): 표 안 자격요건/신청기간/서류 추출, XML 전체 글자 대비 96~99%.
알려진 한계: 병합 셀 값은 첫 행에만 나옴 / 하이퍼링크 텍스트 소실 / 그림(이미지) 속 글자 미추출

변환이 실패하면
  1) FileHeader 플래그(암호/배포용/DRM)와 pyhwp 오류 메시지로 원인을 알려준다
  2) 미리보기 텍스트(PrvText, 앞부분 약 1,000자)가 있으면 그것만이라도 돌려준다 (partial=True -> 확인필요 처리)
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
from typing import List

from lxml import etree

from .results import ExtractError, ExtractResult


def _para(p) -> str:
    out: List[str] = []
    for ls in p.findall("LineSeg"):
        for ch in ls:
            if ch.tag == "Text" and ch.text:
                out.append(ch.text)
            elif ch.tag == "TableControl":
                out.append("\n" + _table(ch) + "\n")
            elif ch.tag not in ("ControlChar", "LineSeg"):
                for tb in ch.iter("TextboxParagraphList"):        # 글상자/도형 안 문단
                    parts = [_para(q).strip() for q in tb.findall("Paragraph")]
                    parts = [x for x in parts if x]
                    if parts:
                        out.append("\n[글상자] " + " / ".join(parts) + "\n")
    return "".join(out)


def _table(tc) -> str:
    rows = []
    body = tc.find("TableBody")
    if body is None:
        return "[표]\n[/표]"
    for tr in body.findall("TableRow"):              # 직속 행만 (중첩 표 중복 방지)
        cells = []
        for td in tr.findall("TableCell"):
            parts = [_para(p).strip() for p in td.findall("Paragraph")]
            txt = " / ".join(x for x in parts if x)
            txt = re.sub(r"[ \t]+", " ", txt).strip()
            if txt:
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    return "[표]\n" + "\n".join(rows) + "\n[/표]"


def xml_to_text(xml_bytes: bytes) -> str:
    root = etree.fromstring(xml_bytes)
    lines = []
    for cs in root.iter("ColumnSet"):
        for p in cs.findall("Paragraph"):
            t = _para(p)
            if t.strip():
                lines.append(t.rstrip())
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cmd(path: str) -> List[str]:
    exe = shutil.which("hwp5proc")
    if exe:
        return [exe, "xml", path]
    return [sys.executable, "-c",
            "import sys; from hwp5.hwp5proc import main; sys.exit(main())", "xml", path]


# ---------------------------------------------------------------- 진단
FLAG_NAMES = {1: "암호 설정 문서", 2: "배포용 문서", 4: "DRM 보안 문서"}      # 비트 번호 -> 설명 (HWP 5.0 파일 형식의 FileHeader 속성)
_NOISE = ("UnderlineStyle", "defined name/values")                          # pyhwp 가 대량으로 찍는 무해한 경고


def flag_reasons(flags: int) -> List[str]:
    return [name for bit, name in FLAG_NAMES.items() if flags & (1 << bit)]


def read_header(path):
    """(버전 문자열, 플래그 정수) 또는 None (OLE 구조가 아님)"""
    try:
        import olefile
        ole = olefile.OleFileIO(str(path))
        try:
            hdr = ole.openstream("FileHeader").read()
        finally:
            ole.close()
        if not hdr.startswith(b"HWP Document File") or len(hdr) < 40:
            return None
        ver = struct.unpack("<I", hdr[32:36])[0]
        flags = struct.unpack("<I", hdr[36:40])[0]
        return "{}.{}.{}.{}".format(ver >> 24 & 255, ver >> 16 & 255, ver >> 8 & 255, ver & 255), flags
    except Exception:
        return None


def preview_text(path) -> str:
    """HWP 안에 들어 있는 미리보기 텍스트(PrvText, UTF-16LE, 앞부분 약 1,000자). 없으면 빈 문자열."""
    try:
        import olefile
        ole = olefile.OleFileIO(str(path))
        try:
            if not ole.exists("PrvText"):
                return ""
            raw = ole.openstream("PrvText").read()
        finally:
            ole.close()
        return raw.decode("utf-16-le", errors="ignore").replace("\r\n", "\n").replace("\x00", "").strip()
    except Exception:
        return ""


def stderr_tail(err: bytes, n: int = 3) -> str:
    lines = [l.strip() for l in err.decode("utf-8", errors="replace").splitlines()
             if l.strip() and not any(x in l for x in _NOISE)]
    return " / ".join(l[:160] for l in lines[-n:])


def describe_failure(path, err: bytes = b"") -> str:
    parts = []
    hdr = read_header(path)
    if hdr is None:
        parts.append("HWP 5.0 형식이 아님 (구버전 HWP 3.0 이거나 다른 형식을 .hwp 로 저장한 파일)")
    else:
        ver, flags = hdr
        why = flag_reasons(flags)
        parts.append("문서 버전 {}{}".format(ver, (", " + ", ".join(why)) if why else ""))
    tail = stderr_tail(err)
    if tail:
        parts.append("pyhwp: " + tail)
    return "HWP 변환 실패 ({})".format("; ".join(parts))


def check_environment():
    """pyhwp 를 불러올 수 있는지 점검한다. (정상 여부, 안내 메시지).
    오래된 방식(setup.py install)으로 설치되면 의존 패키지(six 등)가 빠질 수 있다."""
    try:
        import hwp5.hwp5proc  # noqa: F401
    except ModuleNotFoundError as e:
        name = getattr(e, "name", None) or str(e)
        return False, "pyhwp 를 불러올 수 없습니다 (No module named {!r}) -> pip install {}  (또는 pip install -r requirements.txt)".format(name, name)
    except Exception as e:
        return False, "pyhwp 초기화 오류: {}".format(e)
    return True, ""


def extract(path, settings=None, **_) -> ExtractResult:
    try:
        proc = subprocess.run(_cmd(str(path)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    except subprocess.TimeoutExpired:
        raise ExtractError("HWP 변환 시간 초과")
    except FileNotFoundError:
        raise ExtractError("pyhwp(hwp5proc) 미설치")

    reason, text = "", ""
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            text = xml_to_text(proc.stdout)
        except etree.XMLSyntaxError as e:
            reason = "HWP XML 해석 실패: {}".format(e)
        if text:
            return ExtractResult(text=text, method="TEXT")
        reason = reason or "HWP 변환은 되었으나 추출된 텍스트가 없음 (이미지 위주 문서일 수 있음)"
    else:
        reason = describe_failure(path, proc.stderr)

    prv = preview_text(path)                                  # 폴백: 미리보기 텍스트
    if len(prv) >= 50:
        return ExtractResult(text=prv, method="TEXT", partial=True,
                             warnings=["{} -> 미리보기 텍스트(앞부분 약 {}자)만 추출됨, 일부 내용만 있음".format(reason, len(prv))])
    raise ExtractError(reason)
