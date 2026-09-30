# Basic Set optional-rule profile (#493)

The exact prerelease Basic Set profile records a decision for every named
optional-rule section in the selected Characters third printing and Campaigns
fourth printing. An absent decision never enables behavior. The current profile
disables all eleven stable rule identifiers:

| Source | Stable identifier | Profile decision |
| --- | --- | --- |
| B111 | `gurps.optional.limited-enhancements` | disabled |
| B175 | `gurps.optional.wildcard-skills` | disabled |
| B269 | `gurps.optional.modifying-dice-adds` | disabled |
| B279 | `gurps.optional.malfunction` | disabled |
| B294 | `gurps.optional.maintaining-skills` | disabled |
| B347 | `gurps.optional.influencing-success-rolls` | disabled |
| B352 | `gurps.optional.detailed-jumping` | disabled |
| B395 | `gurps.optional.posture-in-armor` | disabled |
| B420 | `gurps.optional.injury.bleeding` | disabled |
| B420 | `gurps.optional.injury.accumulated-wounds` | disabled |
| B420 | `gurps.optional.injury.last-wounds` | disabled |

The three B420 choices remain independent; there is no umbrella injury switch.
Definitions also carry an execution-availability flag. Profile lookup rejects
unknown or duplicate identifiers, the latest Basic Set profile rejects missing
decisions, and runtime access rejects disabled or unavailable rules. This keeps
existing baseline behavior unchanged and prevents catalog presence or a default
value from activating a rule.

The decision set is part of the profile digest and public profile view. A change
therefore requires selection of a different immutable profile and follows the
existing host-authorized migration, receipt, and replay path. Existing profile
versions retain their historic digest and unspecified legacy state. Version 9
adds the explicit decisions; version 10 retains them while adding the separate
Infinite Worlds exclusion. Neither changes package pins or the prerelease engine
version.


## Selected-source reconciliation (#744)

The selected generic profile remains `profile:gurps-basic-set-4e-2004@11`, with
Characters third printing (February 2008) and Campaigns fourth printing (April
2008). Package pins, immutable profile selections, and prerelease engine version
are unchanged. The eleven named switches above join nine source section rows;
the three B420 switches share the injury section without becoming one switch.

Campaigns B417 explicitly makes cinematic combat optional. Both its general
section and its independent Dual-Weapon Attacks box are now `optional-disabled`
and `profile-excluded`, with no armor-divisor capability link. Existing custom
profiles can still select the implemented `gurps.techniques.dual-weapon-attack`
rule; the selected generic profile does not select it. The other B417 variants
remain unavailable. No optional implementation is enabled by a ledger review.

The optional B357 combat uses of extra effort remain disabled in the scope
manifest. This does not exclude the required physical extra-effort procedure
that starts at B356; the source section records this distinction explicitly.

Standard Basic Set magic and psi are required, including their construction and
applicable procedures. B242 alternative-system discussion and B257 Other Powers
are construction/reference guidance, not permission to discard B234-B257.
The audit issues retain ownership of missing or unverified effects.

B523-B546 Infinite Worlds has separate classifications for setting prose,
reusable mechanics, material needing a separate profile, and other reviewed
exclusions. Excluding the setting does not silently certify its parachronic
travel procedures; those mechanics remain explicitly excluded from this generic
profile and require a separate reviewed selection.

The frozen source row identities and counts are unchanged. Reclassifying the two
B417 rows corrects their required/disabled conflict; the membership fingerprint
is updated for those reviewed disposition changes only.
