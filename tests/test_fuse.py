"""证据红线：P0/P1 必须有参照物证据（PROMPT.md §2/§11）。"""

from app.pipeline.fuse import cap_level_without_anchor, has_anchor_evidence, highest_level


def test_p1_without_anchor_downgrades_to_p2():
    level, downgraded = cap_level_without_anchor("P1", [{"kind": "bbox"}])
    assert (level, downgraded) == ("P2", True)


def test_p0_with_reference_stays_p0():
    level, downgraded = cap_level_without_anchor("P0", [{"kind": "reference"}])
    assert (level, downgraded) == ("P0", False)


def test_clause_and_license_are_anchors():
    assert has_anchor_evidence([{"kind": "clause"}])
    assert has_anchor_evidence([{"kind": "license"}])
    assert not has_anchor_evidence([{"kind": "metadata"}, {"kind": "timestamp"}])


def test_p3_never_downgraded():
    assert cap_level_without_anchor("P3", []) == ("P3", False)


def test_highest_level_order():
    assert highest_level(["P3", "P1", "P2"]) == "P1"
    assert highest_level(["P2", "P0"]) == "P0"
    assert highest_level([]) is None
