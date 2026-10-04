"""B244 five other lawful Body Control purchases, without circular witnesses."""

from collections.abc import Mapping

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.magic.body_control import package
from wayfarer.engine.rules.magic.movement import package as movement_package


def prerequisite_failures(
    definitions: Mapping[str, RuleDefinition], purchases: Mapping[str, int]
) -> tuple[str, ...]:
    """Apply only to exact printed definitions; higher dependent spells cannot count.

    The supported lower graph is topologically ordered. Future unreviewed spell
    definitions cannot become witnesses merely by asserting a college hook.
    """
    canonical = {d.id: d for d in package().definitions}
    target = "spell:paralyze-limb"
    if purchases.get(target, 0) < 1 or definitions.get(target) != canonical[target]:
        return ()

    def purchased(key: str) -> bool:
        return purchases.get(key, 0) >= 1 and definitions.get(key) == canonical[key]

    witnesses: set[str] = set()
    if purchased("spell:itch"):
        witnesses.add("spell:itch")
    if purchased("spell:spasm") and "spell:itch" in witnesses:
        witnesses.add("spell:spasm")
    for key in ("spell:pain", "spell:clumsiness"):
        if purchased(key) and "spell:spasm" in witnesses:
            witnesses.add(key)
    haste = next(d for d in movement_package().definitions if d.id == "spell:haste")
    lawful_haste = purchases.get(haste.id, 0) >= 1 and definitions.get(haste.id) == haste
    if purchased("spell:hinder") and ("spell:clumsiness" in witnesses or lawful_haste):
        witnesses.add("spell:hinder")
    if purchased("spell:rooted-feet") and "spell:hinder" in witnesses:
        witnesses.add("spell:rooted-feet")
    return () if len(witnesses) >= 5 and "spell:pain" in witnesses else (target,)
