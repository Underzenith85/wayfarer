"""Public spell schemas remain byte-identical to verified main 33271dca."""

import hashlib
import json

from wayfarer.engine.simulation.magic.spells import (
    SpellCommand,
    SpellEffect,
    SpellEvent,
    SpellResult,
    SpellSpec,
    _executable_spec,
)


def test_five_public_spell_schemas_match_verified_main() -> None:
    payload = json.dumps(
        {
            cls.__name__: cls.model_json_schema()
            for cls in (SpellSpec, SpellEffect, SpellEvent, SpellCommand, SpellResult)
        },
        sort_keys=True,
    )
    # Generated independently from git archive 33271dca507a3a46c651fa0d4434a3c70ae4038f.
    assert (
        hashlib.sha256(payload.encode()).hexdigest()
        == "b8fbebac44a413e2c6f67934d3794649334cb85aebf2435a91b4066b493b9c63"
    )


def test_private_source_kinds_do_not_expand_public_spell_spec() -> None:
    assert _executable_spec("seek-water").kind == "information"
    assert _executable_spec("purify-water").kind == "special"
    assert SpellSpec.model_json_schema()["properties"]["kind"]["enum"] == [
        "regular",
        "resisted",
        "missile",
        "area",
    ]
    assert "water_plan" not in SpellEffect.model_json_schema()["properties"]
