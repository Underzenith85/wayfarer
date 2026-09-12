# Projectile readiness (#286)

`RangedMode.readiness` is an opt-in Basic Set protocol. Catalogs that omit it keep
legacy magazine/per-round loading and existing saved reload progress. Existing
loads must be unloaded before switching to the new protocol.

B195 (Characters third printing) supplies the Arrow and Ammo Fast-Draw outcomes.
A trained exact specialty is required: `skill:fast-draw-arrow`, or
`skill:fast-draw-ammo-tlN` matching the firearm TL. Catalog bindings require the
`skill:fast-draw` family and Arrow/Ammo specialty metadata. Arrow saves one Ready;
Ammo savings are explicit pinned weapon facts. The roll, chosen source, and
weapon are recorded in the existing command transaction. Failure drops one
available round; critical failure drops the available source stack. Already
loaded reservations remain untouched. Dropped rounds preserve total inventory
quantity and have the actor's ground position, rather than disappearing.

B382 (Campaigns fourth printing) supplies separate arrow preparation and draw
stages. Loaded bows represent a held draw. Tactical v2 Ready with `let_down_bow`
returns to the draw stage; preparing another arrow is unnecessary. Holding adds
**no additional FP cost**, as approved for this implementation; normal fatigue
and effective-ST checks still apply. This choice is not independently certified.

The explicitly selected B270 crossbow protocol takes 4 Ready maneuvers at or below
the wielder's effective ST, 8 at ST+1/+2, and 20 at ST+3/+4. The last case requires
standing and an accessible, owned, functional instance of the pinned cocking aid.
ST+5 or higher rejects. Each step rechecks fatigue-adjusted ST and aid access;
partial progress survives non-Ready turns and process restarts. B410's separate
cocking-time wording and the selected-printing delta remain source-audit
items; this implementation follows the B270 timing explicitly requested by #286.

`unload_seconds_per_round` is an **authored, uncertified timing**, approved by the
user, not a claimed Basic Set numeric rule. Each completed timer releases one
reservation; a partial timer survives interruptions. Complete unloading removes
the load, permitting a later source switch. Firing available rounds cancels
unfinished unloading/loading work. Neither loading nor unloading changes the
physical total. `fast_draw`, `cocking_aid_id`, and `let_down_bow` are additive
v2 command fields; v1 inputs stay frozen.

`tests/test_projectile_readiness.py` covers Fast-Draw outcomes, conservation,
restart/retry, bow stages and let-down, authored unloading, and crossbow timing.
These fixtures use the supplied third/fourth printings. #191 still owns the frozen
selected-printing audit; #180 owns production equipment validation. The
capability remains partial, and the general Fast-Draw family still includes
weapon-drawing specialties outside this projectile procedure.
