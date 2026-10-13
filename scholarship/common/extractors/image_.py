# -*- coding: utf-8 -*-
"""[COMMON] 이미지 전처리 (OCR 없음 -> AI 멀티모달 직접 입력용).

- 긴 변이 MAX_LONG_EDGE 를 넘으면 축소
- 세로로 매우 긴 이미지(높이/너비 > TALL_RATIO)는 겹치게 잘라서 여러 장으로 전달
- 5MB 한도를 넘지 않도록 JPEG 재인코딩
"""
from __future__ import annotations

import io
from typing import List

from .results import ExtractError, ExtractResult, ImagePart

MAX_LONG_EDGE = 1568
TALL_RATIO = 2.2
TILE_W = 1100
TILE_H = 1500
TILE_OVERLAP = 80
MAX_BYTES = 4_500_000
MAX_TILES = 6


def _encode(img, prefer_png: bool = False):
    from PIL import Image
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGB"))
        img = bg
    if prefer_png:
        img.save(buf, "PNG", optimize=True)
        if buf.tell() <= MAX_BYTES:
            return "image/png", buf.getvalue()
        buf = io.BytesIO()
    quality = 90
    while True:
        buf.seek(0)
        buf.truncate()
        img.convert("RGB").save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_BYTES or quality <= 60:
            return "image/jpeg", buf.getvalue()
        quality -= 10


def prepare_image_bytes(data: bytes, label: str) -> List[ImagePart]:
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ExtractError("이미지 열기 실패: {}".format(e))
    w, h = img.size
    if w < 20 or h < 20:
        raise ExtractError("이미지가 너무 작음 ({}x{})".format(w, h))

    parts: List[ImagePart] = []
    if h / float(w) > TALL_RATIO:
        if w > TILE_W:
            img = img.resize((TILE_W, int(h * TILE_W / float(w))))
            w, h = img.size
        step = TILE_H - TILE_OVERLAP
        tops = list(range(0, h, step))
        for i, top in enumerate(tops[:MAX_TILES]):
            tile = img.crop((0, top, w, min(h, top + TILE_H)))
            if tile.size[1] < 40:
                continue
            mt, b = _encode(tile, prefer_png=False)
            parts.append(ImagePart(mt, b, "{} (분할 {}/{})".format(label, i + 1, min(len(tops), MAX_TILES))))
    else:
        long_edge = max(w, h)
        if long_edge > MAX_LONG_EDGE:
            r = MAX_LONG_EDGE / float(long_edge)
            img = img.resize((max(1, int(w * r)), max(1, int(h * r))))
        mt, b = _encode(img, prefer_png=(img.format == "PNG" or img.mode in ("P", "RGBA")))
        parts.append(ImagePart(mt, b, label))
    return parts


def extract(path, settings=None, label: str = "image", **_) -> ExtractResult:
    with open(str(path), "rb") as fp:
        data = fp.read()
    parts = prepare_image_bytes(data, label)
    if not parts:
        raise ExtractError("이미지 처리 결과 없음")
    warns = []
    if len(parts) > 1:
        warns.append("세로로 긴 이미지 -> {}장으로 분할".format(len(parts)))
    return ExtractResult(text="", method="VISION", images=parts, warnings=warns)
