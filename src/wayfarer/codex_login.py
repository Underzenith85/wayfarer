"""Explicit operator login into the dedicated Codex-managed Wayfarer profile."""

import argparse
import asyncio

from wayfarer.config import Settings
from wayfarer.orchestration.codex import CodexSettings, SDKBackend


async def login(*, device: bool) -> None:
    settings = Settings()
    backend = SDKBackend(CodexSettings(home=settings.codex_home))
    try:
        async with asyncio.timeout(300):
            if device:
                handle = await backend.client.login_chatgpt_device_code()
                print(f"Open {handle.verification_url} and enter {handle.user_code}", flush=True)
                completed = await handle.wait()
            else:
                browser = await backend.client.login_chatgpt()
                print(f"Open {browser.auth_url}", flush=True)
                completed = await browser.wait()
            if not completed.success:
                raise RuntimeError("Codex login did not complete")
            print("Codex login completed. Wayfarer can now use this subscription profile.")
    except Exception:
        raise SystemExit(
            "Codex login failed or timed out. Retry login; no game state was changed."
        ) from None
    finally:
        await backend.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign in to Wayfarer's dedicated Codex profile")
    parser.add_argument("--device-auth", action="store_true")
    args = parser.parse_args()
    asyncio.run(login(device=args.device_auth))
