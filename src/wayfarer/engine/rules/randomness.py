"""Versioned deterministic random source; entropy is supplied by the caller."""

import hashlib

from wayfarer.errors import ValidationError

RNG_ALGORITHM = "sha256-counter-v1"


class SeededRandom:
    """SHA-256 counter stream with rejection sampling, independent of Python's PRNG."""

    def __init__(self, seed: str) -> None:
        try:
            self._key = bytes.fromhex(seed)
        except ValueError as exc:
            raise ValidationError("Invalid command seed") from exc
        if len(self._key) != 32 or len(seed) != 64:
            raise ValidationError("Command seed must contain 256 bits")
        self._counter = 0

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        if exclusive_upper_bound < 1:
            raise ValidationError("Random bound must be positive")
        width = max(1, (exclusive_upper_bound.bit_length() + 7) // 8)
        ceiling = 1 << (8 * width)
        limit = ceiling - ceiling % exclusive_upper_bound
        while True:
            data = b""
            while len(data) < width:
                data += hashlib.sha256(
                    b"wayfarer:sha256-counter-v1:" + self._key + self._counter.to_bytes(16, "big")
                ).digest()
                self._counter += 1
            value = int.from_bytes(data[:width], "big")
            if value < limit:
                return value % exclusive_upper_bound
