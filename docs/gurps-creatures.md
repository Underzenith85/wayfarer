# Creature construction and training (#521)

The engine implements a bounded animal and monster catalog from the selected
Campaigns fourth printing, B455-460. It does not claim a complete bestiary.
The catalog currently contains a house cat, large guard dog, timber wolf,
cavalry horse, draft horse, basilisk and gryphon.

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

Natural attacks in this catalog describe anatomy and the source damage basis;
they do not resolve attacks. #522 owns legal animal actions, damage, injury and
swarm behavior through the shared combat reducers.

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
continues to use the #396/#397 transport and injury paths.
