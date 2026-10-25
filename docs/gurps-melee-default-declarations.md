# Versioned melee default declarations

Basic Set B208 gives each Melee Weapon skill a DX default: −4 for Easy,
−5 for Average and −6 for Hard, in addition to its particular skill defaults.
The source-wide melee declarations previously omitted that inherited edge for
all 22 printed Melee Weapon skills. Lance is expressly not a Melee Weapon skill
(B204); its own DX−5/Spear−3 defaults and Riding prerequisite stay unchanged.
The separately
assembled core Broadsword declaration was already correct.

`melee.definitions(default_revision=2)` supplies the inherited edge for the
22 printed Melee Weapon skills, retaining their particular defaults, acquisition
prerequisites, implementation status and hooks. Brawling, Boxing, Judo, Karate
and other unarmed or defense skills do not acquire a weapon-family default.
The no-argument call remains the exact legacy revision1 declaration set so
existing package assemblers and their pins remain reproducible.

For an actual assembled package containing these source-wide declarations,
`melee.revise_package_defaults(package, version="2.0.0")` produces a new immutable
package version. Its new digest must be pinned explicitly in the campaign rules.
Register both versions in the existing `RulesCatalog` to serve old campaigns;
use the existing compiler/reviewer to obtain approvals for the new configuration.
The helper never upgrades a campaign, approval, profile or recorded command.
A package with customized default permissions requires separate source review;
the helper refuses to replace those permissions. Already correct core weapon
edges are retained exactly. Existing availability and acquisition checks,
including paid Unusual Background admission, still apply.

`test_default_roll_staff_host.py` assembles the actual Staff combat fixture
through that production helper and its real package pin. An approved DX10 actor
can use untrained Staff5 in an actual attack, roll five and inflict one point
through the ordinary damage path. The old declaration still refuses that
untrained attack before entropy. Independent golden digests retain the old
package, engine configuration and trained/untrained approvals. Both old trained
Staff9 and new default Staff5 commands fold and reexecute from their original
genesis on SQLite and PostgreSQL, with exact retry preservation.

This revision does not close the whole B344 Default Rolls section. General
Common Knowledge needs an authoritative GM/actor/society eligibility contract;
package activation, availability and Unusual Background admission do not prove
that a weapon is known in the actor's society. Existing specialized society
contracts, such as Armoury training, are separate. B344 Rule of 20, best legal
default, no-default skills and all actual ordinary/staged default consumers
still require their complete source-bound acceptance review. No capability or
source-section status changes follow from this catalog correction alone.
