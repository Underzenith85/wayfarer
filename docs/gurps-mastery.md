# Combat mastery

The selected Characters third printing B93/B99 and Campaigns fourth printing
B370/B376 were inspected against the supplied PDFs for this implementation.
Canonical purchases are `trait:advantage:trained-by-a-master` and
`trait:advantage:weapon-master`; cinematic prerequisites use these same IDs.

Weapon Master purchases select `point-cost` plus `weapon-scope`. Each supported
muscle-powered catalog weapon is a 20-point scope. Printed paired examples cost
25, fencing/knightly classes 30, swords 35, bladed/one-handed classes 40, and all
muscle-powered weapons 45. A 45-point purchase may omit the scope because its
source meaning is unambiguous. Prices cannot substitute for an authored narrower
scope. Main-Gauche use of the large knife belongs to the fencing class; ordinary
Knife use does not. Guns, energy and powered weapons are outside these classes.
Arbitrary thematic classes still require a separately bounded GM-authoring model.

For a learned skill in the purchased scope, Weapon Master adds +1 per die to
basic thrust/swing damage at DX+1, or +2 per die at DX+2 or higher. The melee and
ST-based ranged damage reducers consume the bonus. Default skill use receives no
bonus. Weapon Master halves repeated weapon-parry penalties within scope;
Trained By A Master halves repeated melee and unarmed parry penalties. Fencing
halves the ordinary penalty before mastery halves it again.

The additive tactical v2 and direct engine commands accept
`attack_option: "rapid-strike"` on an ordinary Attack. The existing durable
second-attack machinery resolves two melee attacks at -6 each, or -3 with the
applicable mastery benefit, without forfeiting ordinary active defenses.
Unarmed punch/kick sequences use the same retained penalty. Frozen tactical v1
remains unchanged. All-Out/Extra Attack combinations and arbitrary custom weapon
classes are still unsupported; these limitations prevent closing the whole
#767 compliance scope or promoting certification rows from these tests alone.

`tests/test_mastery_combat.py` checks actual injury, scope exclusion, price
validation, ordinary and master repeated-parry scores, armed/unarmed two-strike
outcomes, retained defenses and durable replay. Cinematic compiler tests purchase
real canonical trait definitions, rather than synthetic family tokens.
