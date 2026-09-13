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
adds the explicit decisions without changing package pins or the prerelease
engine version.
