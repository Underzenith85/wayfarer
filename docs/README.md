# Wayfarer documentation

This index separates current reference material from historical delivery records.
Start with the engine and architecture guides when changing behavior; use the
conformance and certification pages when making claims about GURPS coverage.

## Start here

- [Engine guide](engine.md) — package boundaries, execution flow, and extension points.
- [Architecture reference and decision record](architecture.md) — authority, persistence,
  compatibility, and accepted architectural decisions.
- [API v1 runtime](api-v1-runtime.md) — the frozen player-facing transport contract.
- [Scenario authoring](scenario-authoring.md) and [scenario documents](scenario-documents.md)
  — guided authoring and the portable schema.
- [Operations](operations.md), [testing](testing.md), and [quality](quality.md) — local and CI
  workflows.
- [Release gates](release-gates.md) — engine and product evidence required for release.

## Engine, runtime, and campaign state

- [Persistence](persistence.md), [migration and backups](migration.md), and
  [orchestrator jobs](orchestrator-jobs.md)
- [Rules catalog](rules-catalog.md) and [rules profiles](rules-profiles.md)
- [Scenario catalog](scenario-catalog.md), [encounter scenes](encounter-scenes.md),
  [mixed activity](mixed-activity.md), and [battlefield templates](battlefield-templates.md)
- [Campaign administration](gurps-campaign-administration.md),
  [character creation](gurps-character-creation.md),
  [character development](gurps-character-development.md), and
  [templates](gurps-templates.md)

## Play, combat, and the player interface

- [Basic combat contract](gurps-basic-combat.md), [mapless combat](mapless-combat.md),
  [combat timing](combat-timing.md), [maneuvers](gurps-maneuvers.md), and
  [withdrawal](combat-withdrawal.md)
- [Tactical geometry](tactical-geometry.md), [tactical play](tactical-play.md),
  [tactical combat status](gurps-tactical-combat.md), and
  [reinforcements and escalation](combat-reinforcements.md)
- [Melee](gurps-melee.md), [ranged combat](gurps-ranged.md),
  [projectile readiness](gurps-projectile-readiness.md),
  [unarmed combat](gurps-unarmed.md), [hit locations](gurps-hit-locations.md),
  [injury state](gurps-injury-state.md), and [object integration](gurps-object-integration.md)
- [UI onboarding](ui-onboarding.md), [availability states](ui-availability.md),
  [voice controls](ui-voice.md), and [PWA/accessibility](pwa-accessibility.md)

## GURPS conformance and source evidence

The capability registry and executable reports are authoritative. Topic pages
describe bounded implementations; a topic page is not a whole-profile certificate.

- [Conformance baseline](gurps-conformance.md), [Basic Set certification](gurps-basic-set-certification.md),
  [source audit](gurps-source-audit.md), [selected-source review](gurps-basic-set-source-review.md),
  [inventory source review](gurps-inventory-source-review.md), and
  [equipment source review](gurps-equipment-source-review.md)
- [Abilities](gurps-abilities.md), [mundane skills](gurps-mundane-skills.md),
  [cinematic skills](gurps-cinematic-skills.md), and [mundane traits](gurps-mundane-traits.md)
- [Attack and defense traits](gurps-attack-defense-traits.md),
  [physiology traits](gurps-physiology-traits.md), [sensory traits](gurps-sensory-traits.md),
  [mental and spirit traits](gurps-mental-spirit-traits.md),
  [movement and form traits](gurps-movement-forms.md), and
  [world-travel traits](gurps-world-travel-traits.md)
- [Equipment audit](gurps-equipment-audit.md), [firearms](gurps-firearms.md),
  [exotic malfunctions](gurps-exotic-malfunctions.md), [thrown recovery](gurps-thrown-recovery.md),
  [vehicles](gurps-vehicles.md), and [artifacts](gurps-artifacts.md)
- [Recovery](gurps-recovery.md), [advanced treatment](gurps-advanced-treatment.md),
  [disease and aging](gurps-disease-aging.md), [hazards](gurps-hazards.md),
  [survival](gurps-survival.md), [toxins](gurps-toxins.md),
  [creatures](gurps-creatures.md), and [transformations](gurps-transformations.md)
- [Economics](gurps-economics.md), [law](gurps-law.md),
  [electronics](gurps-electronics.md), [inventions](gurps-inventions.md),
  [gadgeteering](gurps-gadgeteering.md), [propaganda and media](gurps-propaganda-media.md),
  [social runtime](gurps-social-runtime.md), [social activity bindings](gurps-social-activity-bindings.md),
  and [social material outcomes](gurps-social-material-outcomes.md)
- [World and technology context](gurps-world-context.md),
  [Infinite Worlds boundary](gurps-infinite-worlds-boundary.md), and
  [optional rules](gurps-optional-rules.md)

## Magic and supernatural rules

- [Executable spellcasting](gurps-spell-execution.md), [enchanting](gurps-enchanting.md),
  [magic-craft skills](gurps-magic-craft-skills.md), [mana/divine traits](gurps-mana-divine-traits.md),
  [psi powers](gurps-psi-powers.md), and [supernatural catalog](gurps-supernatural-catalog.md)
- Spell families: [air](gurps-spell-air.md), [body control](gurps-spell-body-control.md),
  [communication and empathy](gurps-spell-communication-empathy.md), [earth](gurps-spell-earth.md),
  [enchantment](gurps-spell-enchantment.md), [fire](gurps-spell-fire.md),
  [healing](gurps-spell-healing.md), [knowledge](gurps-spell-knowledge.md),
  [mind control](gurps-spell-mind-control.md), [movement](gurps-spell-movement.md),
  [necromantic](gurps-spell-necromantic.md), and [water](gurps-spell-water.md)

## Design system

Read the [Field Journal principles](design-system/00-principles.md) before visual
work, then use the focused references for [color](design-system/01-color.md),
[typography](design-system/02-typography.md), [space and layout](design-system/03-space-layout.md),
[surfaces and ornament](design-system/04-surfaces-ornament.md),
[components](design-system/05-components.md), [screens](design-system/06-screens.md),
[multiplayer states](design-system/07-multiplayer-states.md),
[accessibility](design-system/08-accessibility.md), [migration](design-system/09-migration-map.md),
and the [implementation plan](design-system/10-implementation-plan.md).

## Historical delivery and verification records

These pages explain what a delivery wave or verification PR established at that
time. Their issue-status language is historical; follow the current guides and
GitHub issue tracker for present behavior and ownership.

- Delivery waves: [6](wave-6.md), [7](wave-7.md), [8](wave-8.md), [9](wave-9.md),
  [10](wave-10.md), [11](wave-11.md), [12](wave-12.md), [13](wave-13.md), and
  [14](wave-14.md)
- Verification records: [voice/text parity](issue-24-verification.md) and
  [two-player/full-campaign integration](issue-59-verification.md)
- Machine-readable product prerequisite ledger: [`product-release.json`](product-release.json)
