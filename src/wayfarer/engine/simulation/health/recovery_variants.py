"""Compatibility aliases for the unified medical procedure API."""

from wayfarer.engine.simulation.health.medical import (
    BeginRecovery as BeginRecoveryVariant,
)
from wayfarer.engine.simulation.health.medical import (
    CareContext as RecoveryVariantContext,
)
from wayfarer.engine.simulation.health.medical import (
    FinishRecovery as FinishRecoveryVariant,
)
from wayfarer.engine.simulation.health.medical import (
    RecoveryResult as RecoveryVariantResult,
)
from wayfarer.engine.simulation.health.medical import (
    apply_recovery as apply_recovery_variant,
)
from wayfarer.engine.simulation.health.medical import (
    surgery_equipment_modifier as surgery_equipment_modifier,
)

__all__ = [
    "BeginRecoveryVariant",
    "FinishRecoveryVariant",
    "RecoveryVariantContext",
    "RecoveryVariantResult",
    "apply_recovery_variant",
    "surgery_equipment_modifier",
]
