# -*- coding: utf-8 -*-
"""[RECOMMEND] DB 없이 --dump-dir(out) 의 ext01_글번호_ai_result.json 으로 추천을 시험하기 위한 후보 공급원.

파일에는 게시일/처리 상태가 없으므로 비워 두고(원문 주소는 글번호로 만든다), 검수 대기 여부는 uncertain 의 verify 항목으로 추정한다.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from ..common.ai import schema

def origin_url(src_cd: str, src_id: str) -> str:
    """파일에는 원문 주소가 없으므로 글번호에서 만든다 (run_ext01 이 저장하는 주소와 같은 규칙). 외부02 는 드림스폰 상세 주소다.
    DB 의 원문 주소(공고 안의 외부 링크 등)와 다를 수 있다: 시험용 폴더에서 원문을 열어 보기 위한 기본 주소."""
    if src_cd.upper() == "EXT02":
        return "https://www.dreamspon.com/scholarship/view.html?idx={}".format(src_id)
    base = (os.environ.get("EXT01_DOWN_BASE") or "https://www.tukorea.ac.kr").rstrip("/")
    return "{}/bbs/tukorea/{}/{}/artclView.do".format(base, os.environ.get("EXT01_BBS_SETUP_SEQ") or "374", src_id)


_NAME = re.compile(r"^(ext\d+)_(\d+)_ai_result\.json$", re.IGNORECASE)


class FileSource:
    def __init__(self, results_dir):
        self.dir = Path(results_dir)

    def list_candidates(self, today: date) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for f in sorted(self.dir.glob("*_ai_result.json")):
            m = _NAME.match(f.name)
            if not m:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ver = data["elig"].get("schema_version") or 1
                elig, _ = schema.normalize(data["elig"], None, "", version=3 if ver >= 3 else 2)      # v3 공고는 v3 규칙으로 정리해야 확장 조건이 사라지지 않는다
            except (ValueError, KeyError, OSError):
                continue
            review = data.get("review") or {}
            if (data["elig"].get("schema_version") or 1) >= 2:
                elig["schema_version"] = data["elig"]["schema_version"]
            verify = any(isinstance(u, dict) and u.get("level", "verify") == "verify" for u in review.get("uncertain") or [])
            try:
                end = datetime.strptime(elig["apply_end"], "%Y-%m-%d").date() if elig.get("apply_end") else None
                start = datetime.strptime(elig["apply_start"], "%Y-%m-%d").date() if elig.get("apply_start") else None
            except ValueError:
                end = start = None
            if end is None or end < today:
                continue
            out.append({"src_cd": m.group(1).upper(), "src_id": m.group(2), "title": elig.get("scholarship_name") or f.stem,
                        "origin_url": origin_url(m.group(1), m.group(2)), "posted_date": None, "category": elig.get("category"),
                        "apply_type": elig.get("apply_type"), "apply_start": start, "apply_end": end, "org_deadline": None,
                        "summary": elig.get("summary"), "status": "NEED_REVIEW" if verify else "EXTRACTED",
                        "elig": elig, "uncertain": {"uncertain": review.get("uncertain") or []}})
        return sorted(out, key=lambda c: c["apply_end"])
