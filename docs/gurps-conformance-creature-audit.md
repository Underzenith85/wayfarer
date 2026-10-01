# Template and creature conformance boundary (#839)

Source: Characters third printing B258–263; Campaigns fourth printing B445–461. This freezes the assigned row IDs below, without changing section status or release certification. Templates are trusted authored constructions; arbitrary published templates and the complete bestiary are not enabled by these tests.

Independent `test_conformance_creatures.py` exercises actual TemplateCatalog compilation: racial ST +2 costs 20, inherited Fit costs 5, resulting ST is 12 and mandatory meta-trait provenance survives. No template discount applies (B258/B262/B454). Existing `test_mundane_traits.py` cases cover mandatory components, omissions, taboo traits, choices, cycles and player-created-race permission. Inherited trait effects retain their individual implementation/evidence owners.

Every registered physical attack is independently settled through `apply_creature_combat`: cat bite 1 cutting, dog/wolf bite 2 cutting, cavalry kick 8 crushing, draft kick 10 crushing, gryphon claw 6 cutting and beak 6 large piercing, using recorded d6=4. Cutting/large piercing injury multiplies by 1.5, rounding down. B16 thrust, B460 Brawling +1/die and Horizontal/Hooves cancellation supply the values. Every case checks resulting HP, JSON exact retry and stale revision. Basilisk's registered 3d toxic vision Malediction has a distinct resistance consumer, covered by `test_creature_combat.py::test_basilisk_death_gaze_uses_registered_resistance_and_modifier_adapters`; swarm consumers remain in that suite. No missing registered attack was found.

## Frozen rows

For template guidance rows, the supported construction is authored racial/occupational/meta-trait composition through TemplateCatalog.preview, with the independent component-cost case above and named existing template cases. Concept, presentation, player perceptions, customization advice and GM selection guidance are guidance, not executable operations or certification claims. Creature rows bind the seven representative species through CreatureCatalog.compile and the actual attack, mount/training and swarm reducers; the complete animal/monster catalog remains unverified.

| Exact row ID | Source | Consumer / executable binding |
|---|---|---|
| `section:characters:b258:7-templates` | B258 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b258:character-templates` | B258 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b258:how-to-use-character-templates` | B258 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b259:are-character-templates-character-classes` | B259 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b259:sample-character-templates` | B259 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b259:uniqueness` | B259 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b260:racial-templates` | B260 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b261:how-to-use-racial-templates` | B261 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b261:sample-racial-templates` | B261 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b262:omitting-racial-traits` | B262 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:characters:b262:meta-traits` | B262 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b445:15-creating-templates` | B445 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b445:character-templates` | B445 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b445:how-gurps-works-character-templates-aren-t-rules` | B445 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b446:types-of-character-templates` | B446 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b446:concept` | B446 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b446:flexibility` | B446 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b446:character-templates-and-player-perceptions` | B446 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b447:selecting-traits` | B447 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b448:setting-the-price` | B448 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b448:writing-it-up` | B448 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b448:listing-skills` | B448 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b448:discounts` | B448 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b448:adjusting-for-player-experience` | B448 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b449:customization-notes` | B449 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b449:additional-options` | B449 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b450:racial-templates` | B450 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b450:concept` | B450 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b451:selecting-traits` | B451 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b451:pc-races-vs-npc-races` | B451 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b451:player-created-races` | B451 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b454:setting-the-price` | B454 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b454:sub-races` | B454 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b454:filling-in-the-blanks` | B454 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b454:character-costs-vs-racial-strength` | B454 | TemplateCatalog.preview; independent meta/racial expansion; existing test_mundane_traits template cases (guidance where applicable) |
| `section:campaigns:b455:16-animals-and-monsters` | B455 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b455:common-animals` | B455 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b456:animal-and-monster-statistics` | B456 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b457:individualizing-animals` | B457 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b458:pets-and-trained-animals` | B458 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b459:riding-and-draft-animals` | B459 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b459:war-trained-mounts` | B459 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b460:fantasy-monsters` | B460 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b460:damage-for-animals` | B460 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b461:animals-in-combat` | B461 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
| `section:campaigns:b461:swarm-attack-examples` | B461 | CreatureCatalog.compile; test_conformance_creatures physical matrix; test_creatures training/mount and test_creature_combat gaze/swarm cases |
