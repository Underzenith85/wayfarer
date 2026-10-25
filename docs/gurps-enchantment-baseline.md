# Supported Enchant, Staff and Power baseline

The written criteria of [#785](https://github.com/Underzenith85/wayfarer/issues/785) require valid and invalid construction, actual source-derived consequences and stateful authority, visibility, retry and replay proof for these three spells. They permit unsupported variants to remain visible and unverified. The following merged evidence establishes the supported baseline without certifying every enchantment, spell carrier or source section. The supported-baseline issue is closed after whole-criteria review and exact-head main CI; remaining variants retain explicit implementation owners below.

| Named spell | Construction and actual consequence evidence |
| --- | --- |
| Enchant | `test_spell_construction.py` checks IQ/Very Hard, Magery 2 and ten distinct other colleges. `test_enchanting_source.py` checks exact recipe contracts and invalid prerequisites. `test_enchanting_energy_persistence.py`, `test_enchanting_nighttime.py` and `test_enchanting_settlement.py` exercise approved-build project work, source energy/deadlines, current custody/approval, interruption, success and failure. These are actual persisted commands in addition to source reducer tests. |
| Staff | `test_enchanting_source.py` validates purchased Enchant, IQ/Hard and the 30-energy permanent Staff contract. Actual manufacture is covered by `test_enchanting_energy_persistence.py`. `test_staff_casting.py` and `test_staff_casting_integration.py` prove personal roll-range/contact consequences, current sight/custody/mana, loss, ending and seeded replay. The older casting fixture supplies its own binding; these independent producer and consumer tests do not claim every same-campaign pairing. |
| Power | `test_enchanting_source.py` checks purchased Enchant/Recover Energy and exact level energy. `test_power_project_haste.py` observes actual installation and failure. `test_haste_manufacture_power_composition.py` starts from a mundane item, manufactures Haste, adds Power 1/2 and observes actual FP, Move/Dodge, upkeep, off/on and source-distinct failure with original-genesis seeded replay on both stores. |

Public implementation anchors are [construction #877](https://github.com/Underzenith85/wayfarer/pull/877), [Enchant/Staff/Power #939](https://github.com/Underzenith85/wayfarer/pull/939), [Staff Area targeting #948](https://github.com/Underzenith85/wayfarer/pull/948), [persisted energy/proximity #949](https://github.com/Underzenith85/wayfarer/pull/949) and [two actual producers #999](https://github.com/Underzenith85/wayfarer/pull/999). Source-derived expected state, authority, stale revision, exact retry/restart and deterministic replay are checked by the corresponding host suites. No success string or catalog flag substitutes for a resulting-state oracle.

The earlier critical enhancement visibility defect is repaired by [inventory privacy #1002](https://github.com/Underzenith85/wayfarer/pull/1002) and [receipt privacy #1003](https://github.com/Underzenith85/wayfarer/pull/1003). `test_critical_item_power_privacy.py` and `test_item_receipt_privacy.py` preserve canonical rating, costs and checks while hiding undiscovered bonus Power from players. [Analyze Magic #1004](https://github.com/Underzenith85/wayfarer/pull/1004) adds bounded observer-specific discovery; true and false reports share their audience shape. These repairs supersede earlier documentation that described the active leakage as merely unverified.

The separate manufactured Staff → held Deathtouch → actual attack/defense → HP consumer is described in [Deathtouch casting](gurps-deathtouch-staff.md) and [Staff contact](gurps-staff-melee-contact.md). Its scripted arithmetic cases, original-genesis seeded cases and canonical checkpoint diagnostics provide distinct evidence. It strengthens the bounded Staff consumer without establishing the other named Body Control effects; the previously merged baseline anchors above remain independently inspectable.

The selected-source [Staff forms consumer](gurps-staff-forms.md) additionally
manufactures baton and short-staff carriers and delivers their ordinary physical
blow plus the held spell through Shortsword and Smallsword. Fresh contacts bind
the selected melee mode to the trusted B240 construction; historical contacts
retain their captured behavior. The [heavy barehanded Parry consumer](gurps-staff-heavy-parry.md) now
settles automatic failure, injury, one-yard knockback and the DX fall check in
its supported geometry. Wand contact without a physical blow remains separate.

Timed [physical activities during enchanting rest](gurps-enchanting-physical.md)
now join the existing noncombat approach consumer for their documented bounded
routes. They capture trusted geometry and admit the entire source-derived
interval before effects; grouped travel and other physical routes remain open.

## Remaining ownership

[#779](https://github.com/Underzenith85/wayfarer/issues/779) owns Deathtouch, Paralyze Limb, Rooted Feet and Wither Limb. A Staff-delivered Deathtouch subset does not complete the other three effects. [#817](https://github.com/Underzenith85/wayfarer/issues/817) owns broader mana eligibility/cost context; [#791](https://github.com/Underzenith85/wayfarer/issues/791) owns the Knowledge spell family and its unsupported discovery carriers.

The current checkout repairs [#1010](https://github.com/Underzenith85/wayfarer/issues/1010): item Power above 100 now survives manufacture, charged spell use and persistent replay. The high-Power tests exercise both enchanting methods, extra-energy and critical bonuses, and ordinary item casting costs. This bounded repair makes no whole-source certification or issue-closure claim.

The remaining named enchantment scope has separate bounded owners: [#1011](https://github.com/Underzenith85/wayfarer/issues/1011) outward zero-cost and missile consumers; [#1012](https://github.com/Underzenith85/wayfarer/issues/1012) repeated-Power composition; [#1013](https://github.com/Underzenith85/wayfarer/issues/1013) grouped project clocks, active-work casting and remaining timed nighttime activities (the [ordinary noncombat consumer](gurps-enchanting-noncombat.md) and [Lift/Jump consumers](gurps-enchanting-physical.md) are now implemented); [#1014](https://github.com/Underzenith85/wayfarer/issues/1014) enchantment-specific failure continuations; [#1015](https://github.com/Underzenith85/wayfarer/issues/1015) Staff forms/contact, beyond the bounded manufactured short forms and heavy barehand-Parry consumer; and [#1016](https://github.com/Underzenith85/wayfarer/issues/1016) contextual barehand critical continuation. These remaining variants are unimplemented and unverified. Broader magical sight remains explicitly reserved under [#742](https://github.com/Underzenith85/wayfarer/issues/742#issuecomment-5977700119) pending a selected source/producer owner; Symptoms and the named Knowledge consumers do not own it by inference. New Staff Luck variants need a bounded [#854](https://github.com/Underzenith85/wayfarer/issues/854) child coordinated with open [#867](https://github.com/Underzenith85/wayfarer/issues/867), rather than silently reopening completed #869/#871 consumers.

The supported #785 baseline was closed at main `e4f1b51736f59f0e45df8c595ce1b342455f1097` after [whole-criteria reconciliation](https://github.com/Underzenith85/wayfarer/issues/785#issuecomment-5977703488) and [ordinary main CI 37184170580](https://github.com/Underzenith85/wayfarer/actions/runs/37184170580): all eight jobs and 114 steps, seven verified official artifacts, 24,206 test outcomes and 86.65% combined branch coverage. This closure supplies no acceptance credit to the bounded followups.

Source-section implementation remains partial. The broader enchantment inventory, public schemas and global Basic Set certification are unchanged.
