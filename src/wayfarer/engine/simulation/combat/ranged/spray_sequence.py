"""The canonical next-recipient projection used by live and no-dice B409 paths."""

import hashlib

from wayfarer.engine.simulation.combat.encounter import PendingDefense


def following(pending: PendingDefense) -> PendingDefense | None:
    if not pending.spray_targets:
        return None
    target, *remaining = pending.spray_targets
    return pending.model_copy(
        update={
            "id": "spray:" + hashlib.sha256(pending.id.encode()).hexdigest(),
            "defender_id": target.target_id,
            "shots": target.shots,
            "hit_location": target.hit_location,
            "target_item_id": None,
            "spray_targets": tuple(remaining),
            "spray_recoil_penalty": target.recoil_penalty,
            "traversal_shots": target.traversal_shots,
            "post_attack_destination": None,
            "post_attack_square_facing": None,
            "post_attack_hex_path": (),
            "post_attack_facing": None,
            "post_attack_posture": None,
        }
    )
