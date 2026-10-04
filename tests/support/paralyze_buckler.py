"""Genuine Staff/learned Paralyze producer with original-genesis hand-held buckler."""

from pathlib import Path

from support.paralyze_limb import cast, revision
from support.wither_limb import fixture as staff_fixture
from wayfarer.contracts import Campaign
from wayfarer.orchestration.play import PlayService

__all__ = ["cast", "fixture", "revision"]


async def fixture(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    return await staff_fixture(path, backend, equipment_target="buckler")
