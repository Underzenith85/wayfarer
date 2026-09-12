"""Compatibility aliases for the unified medical procedure API."""

from wayfarer.engine.simulation.health.medical.commands import BeginRecovery as BeginRecoveryVariant
from wayfarer.engine.simulation.health.medical.commands import CareContext as RecoveryVariantContext
from wayfarer.engine.simulation.health.medical.commands import (
    FinishRecovery as FinishRecoveryVariant,
)
from wayfarer.engine.simulation.health.medical.commands import (
    RecoveryResult as RecoveryVariantResult,
)
from wayfarer.engine.simulation.health.medical.markers import (
    surgery_equipment_modifier as surgery_equipment_modifier,
)
from wayfarer.engine.simulation.health.medical.recovery import (
    apply_recovery as apply_recovery_variant,
)

__all__ = [
    "BeginRecoveryVariant",
    "FinishRecoveryVariant",
    "RecoveryVariantContext",
    "RecoveryVariantResult",
    "apply_recovery_variant",
    "surgery_equipment_modifier",
]
