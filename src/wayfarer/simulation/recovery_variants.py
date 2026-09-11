"""Compatibility aliases for the unified medical procedure API."""

from wayfarer.simulation.medical import (
    BeginRecovery as BeginRecoveryVariant,
)
from wayfarer.simulation.medical import (
    CareContext as RecoveryVariantContext,
)
from wayfarer.simulation.medical import (
    FinishRecovery as FinishRecoveryVariant,
)
from wayfarer.simulation.medical import (
    RecoveryResult as RecoveryVariantResult,
)
from wayfarer.simulation.medical import (
    apply_recovery as apply_recovery_variant,
)
from wayfarer.simulation.medical import (
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
