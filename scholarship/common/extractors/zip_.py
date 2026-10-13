# -*- coding: utf-8 -*-
"""[COMMON] ZIP 첨부: 내부 파일을 같은 규칙으로 처리 (1단계만, 임시 폴더 사용).

방어: 멤버 수 상한, 개별/누적 크기 상한, 경로 탈출 방지(디스크에 원본 경로로 쓰지 않음),
      한글 파일명 인코딩(cp437 -> cp949) 보정, 중첩 ZIP 무시.
"""
from __future__ import annotations

import logging
import tempfile
import zipfile
from pathlib import Path

from .results import ExtractError, ExtractResult

log = logging.getLogger(__name__)


def _member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not (info.flag_bits & 0x800):                    # UTF-8 플래그 없음 -> 옛 한글 압축 프로그램
        try:
            name = name.encode("cp437").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def extract(path, settings, kw=None, depth=0, label: str = "zip", **_) -> ExtractResult:
    if depth >= 1:
        raise ExtractError("중첩 ZIP은 처리하지 않음")
    try:
        zf = zipfile.ZipFile(str(path))
    except zipfile.BadZipFile:
        raise ExtractError("ZIP 구조 손상")
    try:
        return _extract(zf, settings, kw, depth, label)
    finally:
        zf.close()


def _extract(zf, settings, kw, depth, label) -> ExtractResult:
    from . import extract as dispatch                      # 순환 import 방지
    from .. import file_role
    from ..storage import file_ext

    kw = kw or file_role.load_keywords(None)
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > settings.max_zip_members:
        raise ExtractError("ZIP 내부 파일이 너무 많음 ({}개)".format(len(infos)))

    sections, images, warns = [], [], []
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, info in enumerate(infos):
            name = _member_name(info)
            ext = file_ext(name)
            role, _kw = file_role.classify(name, kw)
            if role == file_role.EXCLUDED:
                warns.append("제외: {}".format(name))
                continue
            if ext == "zip" or not ext:
                continue
            total += info.file_size
            if info.file_size > settings.max_file_bytes or total > settings.max_file_bytes * 2:
                warns.append("크기 초과로 건너뜀: {}".format(name))
                continue
            dest = Path(tmp) / "m{}.{}".format(n, ext)       # 원본 경로를 쓰지 않음
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            try:
                r = dispatch(dest, ext, settings, kw=kw, depth=depth + 1, label="{}/{}".format(label, name))
            except ExtractError as e:
                warns.append("{}: {}".format(name, e))
                continue
            sections.append("[ZIP 내 파일: {} | 분류: {}]\n{}".format(name, role, r.text))
            images.extend(r.images)
            warns.extend(r.warnings)
    if not sections and not images:
        raise ExtractError("ZIP 안에서 처리 가능한 파일이 없음" + (" ({})".format("; ".join(warns[:3])) if warns else ""))
    return ExtractResult(text="\n\n".join(sections), method="VISION" if images else "TEXT",
                         images=images, warnings=warns)


# ------------------------------------------------------------------ 내부 파일을 각각 분류해서 처리 (EXT02 등)
from dataclasses import dataclass as _dataclass


@_dataclass
class MemberOutcome:
    name: str                      # ZIP 안의 파일명 (한글 보정됨)
    ext: str
    role: str                      # GUIDE / FORM / AUX / EXCLUDED / UNKNOWN
    status: str                    # EXTRACTED / FAILED / SKIPPED
    result: "ExtractResult" = None
    err: str = ""
    size: int = 0


def extract_members(path, settings, kw=None, parent_role: str = "UNKNOWN", label: str = "zip") -> list:
    """ZIP 내부 파일을 파일별로 분류(요강/양식/참고/제외)하고 각각 텍스트를 추출한다.
    - 이름만으로 분류가 안 되는 파일(UNKNOWN)은 ZIP 이름이 양식/보조(예: '...(지원서).zip')면 그 분류를 따른다
    - 파일 하나의 실패가 다른 파일 처리에 영향을 주지 않는다 (암호 걸린 파일, 변환 실패 등은 그 파일만 FAILED)
    - 중첩 ZIP, 지원하지 않는 형식, 제외 대상 파일명은 SKIPPED"""
    from . import SUPPORTED_EXT, UNSUPPORTED_HINT, extract as dispatch
    from .. import file_role
    from ..storage import file_ext

    kw = kw or file_role.load_keywords(None)
    try:
        zf = zipfile.ZipFile(str(path))
    except zipfile.BadZipFile:
        raise ExtractError("ZIP 구조 손상")
    out = []
    try:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > settings.max_zip_members:
            raise ExtractError("ZIP 내부 파일이 너무 많음 ({}개)".format(len(infos)))
        total = 0
        with tempfile.TemporaryDirectory() as tmp:
            for n, info in enumerate(infos):
                name = _member_name(info)
                ext = file_ext(name)
                role, hit = file_role.classify(name, kw)
                if role == file_role.UNKNOWN and parent_role in (file_role.FORM, file_role.AUX):
                    role = parent_role
                m = MemberOutcome(name=name, ext=ext, role=role, status="SKIPPED", size=info.file_size)
                out.append(m)
                if role == file_role.EXCLUDED:
                    m.err = "제외 대상 파일명 ('{}')".format(hit)
                elif ext == "zip":
                    m.err = "중첩 ZIP 은 처리하지 않음"
                elif ext not in SUPPORTED_EXT and ext != "txt":
                    m.err = UNSUPPORTED_HINT.get(ext) or "지원하지 않는 형식: .{}".format(ext or "?")
                elif info.flag_bits & 0x1:
                    m.status, m.err = "FAILED", "암호가 걸린 파일"
                elif info.file_size > settings.max_file_bytes or total + info.file_size > settings.max_file_bytes * 2:
                    m.err = "크기 초과로 건너뜀"
                else:
                    total += info.file_size
                    dest = Path(tmp) / "m{}.{}".format(n, ext)
                    try:
                        with zf.open(info) as src, open(dest, "wb") as fp:
                            fp.write(src.read())
                        m.result = dispatch(dest, ext, settings, kw=kw, depth=1, label="{}/{}".format(label, name))
                        m.status = "EXTRACTED"
                        m.err = "; ".join(m.result.warnings)
                    except ExtractError as e:
                        m.status, m.err = "FAILED", str(e)
                    except Exception as e:                      # 압축 해제 오류(RuntimeError 등) 포함
                        m.status, m.err = "FAILED", "처리 중 오류: {}".format(e)
    finally:
        zf.close()
    return out
