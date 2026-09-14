# Creature construction, training, and combat (#521, #522, #687)

The engine implements a bounded animal and monster catalog from the selected
Campaigns fourth printing, B455-460. It does not claim a complete bestiary.
The catalog currently contains a house cat, large guard dog, timber wolf,
cavalry horse, draft horse, basilisk and gryphon.

The seven selected creature rows and three swarm examples are source-reviewed
and implemented. Their exact traits, skill levels, natural attacks, special
effects, and the bee swarm's hive-distance rule are pinned independently in
`tests/fixtures/gurps/residual-creatures.json`.

## Construction boundary

`CreatureCatalog` owns trusted species defaults. Primary attributes and
secondary characteristics become ordinary `CharacterDraft` purchases and pass
through the existing `CharacterCompiler`; the resulting build revision, point
total, package pin and per-purchase cost are retained as provenance. Size,
weight, occupied hexes, DR, movement modes, abbreviated traits, skills and
natural-attack descriptions are creature facts because the source's encounter
stat blocks do not give complete point-built characters for them.

`CreatureVariation` can replace the declared individual statistics, skills and
personality traits. The template digest remains stable, while the individual
digest and build revision include the variation. A species whose mentality is
not listed as interchangeable rejects a domestic/wild switch. No variation can
add a narrated action or arbitrary rule field.

Natural attacks retain their compiled damage basis, damage type and close-combat
reach. The B460 damage adapter reads ST, Brawling and anatomy traits from that
same creature, uses the profile damage table, and sends the resulting wound
through the canonical DR, Injury Tolerance and injury reducer.

The basilisk's death gaze is an approved Toxic Attack construction. Its
Malediction 1 and Vision-Based selections pass through the canonical ability
modifier registry, while its Psychokinesis membership passes through the psi
power registry. Resolution requires Concentrate, authoritative mutual vision
contact and target Will, applies the per-yard penalty in the shared resisted
supernatural check, ignores DR, and sends successful 3d toxic damage through
the common injury reducer. The receipt retains the projected effect and all
recorded dice. An unavailable or drifting effect, modifier, or power adapter
fails before resolution.

The gryphon's winged Flight is checked against the canonical movement-form
binding and approved Air Move, and its sharp beak is checked against the
natural-weapon binding. Its claw and large-piercing beak attacks then use the
same natural-damage path as other creatures.

## Persistent training

Creature instances live beside other persistent resources and reference a
world actor. Owner and handler are separate relationships. General training
uses the finite B459 IQ-by-target-level table; a separately taught command uses
the B458 IQ-specific duration. Impossible table cells reject. Training records
the Animal Handling specialty, task competence, handler, the wild-animal
handling penalty when applicable, and a deadline on the shared campaign clock.

Completing general training materializes each species command available at
that level as a `LearnedCommand`. `DirectCreature` authorizes only one of those
records and only for the current handler, so narration cannot grant an
untrained action. All relationship, training and direction transitions use the
resource revision, receipts and events; retries are idempotent and conflicting
payloads fail.

War-mount training requires completed level-three training and one campaign
year. Extra post-basic war-training years and their combat modifiers are
explicitly outside this slice.

## Mounted movement

`mount_transport` projects ordinary and enhanced ground movement plus body
footprint from the creature into the existing `Transport` adapter. Transport
activation checks those values against the persistent creature when one is
present. Riding, draft and war-training capabilities stay on the creature, so
changing owner or handler does not erase or duplicate them. Mounted combat
continues to use the #396/#397 transport and injury paths. A creature attacking
while ridden must name its active ground-mount transport; separated, fallen,
lost and crashed states reject instead of manufacturing a second mounted state.

## Combat behavior and swarms

Each species declares a finite maneuver set, attack motivations and preferred
natural attacks. `propose_creature_actions` intersects those facts with learned
orders, war-mount training, manipulator anatomy and persistent injury condition.
It therefore cannot offer human-only Aim, Feint, Ready or Block to an ordinary
animal; an incapacitated or stunned creature can only Do Nothing. These are
legal proposals, while the authoritative command still performs CAS and rule
validation.

The three finite B461 examples (bats, bees and rats) compile to typed swarm
specifications. A swarm persists its axial area and occupant facts, automatic
attack cadence, dispersal HP, protection timing, and explicit immune or
vulnerable countermeasures. It attacks only the occupants recorded inside its
area. Target harm and diffuse swarm harm both use the common injury reducer;
shared hex distance validates area connectivity. A bee swarm additionally
persists its hive origin and disengages once its pursued occupant reaches 50
yards, clearing occupants without changing dispersal HP and recording the exact
command that ended pursuit. Dispersal likewise clears occupants and records its
command. Receipts and embedded outcomes make retries and JSON replay exact-once.

Unsupported adjacent behavior remains explicit: arbitrary narrated motives,
unlisted countermeasures, disconnected swarm areas, and unregistered special
monster bindings are rejected. The engine does not infer tactics or new
bestiary entries from descriptive prose.
