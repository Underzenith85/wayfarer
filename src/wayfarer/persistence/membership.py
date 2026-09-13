"""Which principals a campaign envelope names, for the index both adapters keep.

Persistence may not interpret a campaign, so this is a plain walk over the
envelope and the JSON strings it carries, not a model load. It is deliberately
generous: an extra row in `campaign_principals` costs a filtered read, a missing
one would hide a campaign from the person who owns it.
"""

from __future__ import annotations

import json

from wayfarer.contracts import Campaign

# Keys whose string value is a principal wherever they appear.
PRINCIPAL_KEYS = frozenset({"principal_id", "host_id"})


def _walk(value: object, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRINCIPAL_KEYS and isinstance(item, str) and item:
                found.add(item)
            elif isinstance(item, str) and key.endswith("_json"):
                try:
                    _walk(json.loads(item), found)
                except ValueError:
                    continue
            else:
                _walk(item, found)
    elif isinstance(value, list | tuple):
        for item in value:
            _walk(item, found)


def principals(state: Campaign) -> frozenset[str]:
    """Every principal named in this campaign, its embedded checkpoints included."""
    found: set[str] = set()
    _walk(dict(state), found)
    return frozenset(found)
