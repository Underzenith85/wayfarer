"""B244–245 each named purchase rejects its own missing prerequisite."""

import pytest
from test_body_control_learning import LOWER, compile_spells


def test_rooted_requires_purchased_hinder_with_genuine_learning_route() -> None:
    valid = compile_spells(("haste", "hinder", "rooted-feet"), magery=1)
    assert valid.legal and valid.build is not None, valid.diagnostics
    values = {v.target: v.value for v in valid.build.sheet.values}
    # IQ10 + Magery1 and one point in IQ/Hard gives effective skill9.
    assert values["spell:rooted-feet"] == 9
    invalid = compile_spells(("haste", "rooted-feet"), magery=1)
    assert not invalid.legal and invalid.build is None
    assert any(
        d.code == "skill.prerequisite"
        and d.message == "Missing trained prerequisite: spell:rooted-feet"
        for d in invalid.diagnostics
    )


@pytest.mark.parametrize("missing", ["paralyze", "magery-two"])
def test_wither_requires_actual_paralyze_and_magery_two(missing: str) -> None:
    route = (*LOWER, "paralyze-limb", "wither-limb")
    valid = compile_spells(route, magery=2)
    assert valid.legal and valid.build is not None, valid.diagnostics
    values = {v.target: v.value for v in valid.build.sheet.values}
    assert values["spell:wither-limb"] == 10
    invalid = compile_spells(
        tuple(s for s in route if s != "paralyze-limb") if missing == "paralyze" else route,
        magery=1 if missing == "magery-two" else 2,
    )
    assert not invalid.legal and invalid.build is None
    assert any(
        d.code == "skill.prerequisite"
        and d.message == "Missing trained prerequisite: spell:wither-limb"
        for d in invalid.diagnostics
    )


def test_deathtouch_requires_actual_wither_purchase() -> None:
    lower = (*LOWER, "paralyze-limb")
    valid = compile_spells((*lower, "wither-limb", "deathtouch"))
    assert valid.legal and valid.build is not None, valid.diagnostics
    assert next(v.value for v in valid.build.sheet.values if v.target == "spell:deathtouch") == 10
    invalid = compile_spells((*lower, "deathtouch"))
    assert not invalid.legal and invalid.build is None
    assert any(
        d.code == "skill.prerequisite"
        and d.message == "Missing trained prerequisite: spell:deathtouch"
        for d in invalid.diagnostics
    )
