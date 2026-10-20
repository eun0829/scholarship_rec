# -*- coding: utf-8 -*-
"""카드 시험용 공고 고르기."""
import json

import run_pick_samples as R
from scholarship.recommend import pick_samples as P


def notices(n_one=5, counts=(2, 3, 5, 9)):
    out = {}
    for i in range(n_one):
        out["EXT01:{}".format(100 + i)] = {"src_id": str(100 + i), "title": "단일 {}".format(i), "elig": {"variants": []}}
    for i, c in enumerate(counts):
        out["EXT02:{}".format(200 + i)] = {"src_id": str(200 + i), "title": "다중 {}".format(c), "elig": {"variants": [{"name": "v{}".format(j)} for j in range(c)]}}
    return out


def test_pick_splits_by_variant_count_and_is_deterministic():
    n = notices()
    ones, many = P.pick(n)
    assert len(ones) == 3 and all(P.variant_count(n[k]) < 2 for k in ones)
    assert len(many) == 3 and all(P.variant_count(n[k]) >= 2 for k in many)
    assert P.pick(n) == (ones, many)
    counts = sorted(P.variant_count(n[k]) for k in many)
    assert counts[0] == 2 and counts[-1] == 9                                     # 적은 것과 많은 것이 함께 들어간다


def test_pick_returns_what_exists_when_short():
    ones, many = P.pick(notices(n_one=1, counts=(2,)))
    assert len(ones) == 1 and len(many) == 1


def test_cli_copies_the_picked_files(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    for k, r in notices().items():
        rec = dict(r, src_cd=k.split(":")[0], src_id=k.split(":")[1])
        (src / "{}.json".format(k.replace(":", "_"))).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    assert R.main(["--dir", str(src), "--out", str(out)]) == 0
    assert len(list(out.glob("*.json"))) == 6
    assert "유형 여러 개" in capsys.readouterr().out
