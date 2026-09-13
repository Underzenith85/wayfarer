# Basic Set selected-source review (#191)

This record covers the user-supplied *Characters*, Fourth Edition, third
printing (February 2008), and *Campaigns*, Fourth Edition, fourth printing
(April 2008). Their checked SHA-256 identities are recorded in the certification
ledger. The unavailable August 2004 *GURPS Lite* artifact remains outside this
review and continues to block Lite certification.

The review compared the checked-in source ledger with the two supplied files in
three independent passes:

- PDF outline destinations were enumerated for every structural section and
  checked against the printed page footer. This found and corrected the former
  one-page-low offset for all *Campaigns* destinations.
- The 480 named trait rows and seven trait-list rollups were checked against the
  printed B297-B300 lists, preserving repeated names and positive/negative rows
  as distinct source identities.
- The 87 enhancement, limitation, and gadget-limitation rows were checked
  against the printed B300-B301 lists, including same-name entries in different
  modifier classes.

`scripts/import_basic_set_source_ledgers.py` reproduces the 711 section, 487
trait, and 87 modifier identities from those exact files. CI validates source
digests, page bounds, stable denominator identity, parent links, profile-scoped
runtime joins, review evidence, and open ownership for every unresolved
mechanic. The review records source identity and disposition only: it does not
promote absent or partial runtime behavior.

Every selected-source row is therefore reviewed and assigned either a concrete
mechanics owner or a non-mechanics disposition. The named optional rules remain
explicitly unresolved for #493, and the Chapter 20 setting/mechanics boundary
remains explicitly unresolved for #494. Those are review outcomes, not hidden
gaps. Exact behavior and fixture certification remain with their recorded child
owners, while the unavailable Lite printing remains a separate blocker.
