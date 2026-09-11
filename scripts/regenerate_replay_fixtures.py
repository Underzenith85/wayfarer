"""Regenerate replay goldens after an intentional ENGINE_VERSION change."""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

# Fixture factories are development tooling, never imported by the installed engine.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from scripts.replay_fixtures import (
    CASES,
    FIXTURES,
    ReplayFixture,
    capture,
    engine_for,
    regenerate,
    verify_fixture,
)


async def run(*, initialize: bool, check: bool) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name in CASES:
        path = FIXTURES / f"{name}.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            creating = initialize and not path.exists()
            fixture = (
                await capture(name, root / "capture")
                if creating
                else ReplayFixture.model_validate_json(path.read_text())
            )
            engine = await engine_for(name, root / "engine")
            if not check and not creating:
                fixture = await regenerate(fixture, engine, root / "regenerate")
            await verify_fixture(fixture, engine, root / "verify")
            if not check:
                path.write_text(fixture.model_dump_json(indent=2) + "\n")
            print(f"{name}: {len(fixture.commands)} commands folded and re-executed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true", help="create missing fixtures only")
    parser.add_argument("--check", action="store_true", help="verify without changing fixtures")
    args = parser.parse_args()
    asyncio.run(run(initialize=args.initialize, check=args.check))


if __name__ == "__main__":
    main()
