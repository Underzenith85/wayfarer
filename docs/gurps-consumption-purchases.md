# Approved consumption purchases

Source: Basic Set Characters, fourth edition, third printing B80 and B139;
Campaigns, fourth edition, fourth printing B426. This change contributes to
issues #902 and #751. Both retain acceptance beyond this bounded consumer.

The complete mundane catalog identities for Reduced Consumption and Increased
Consumption now project into the existing physiology and survival procedures.
Reduced Consumption has the source maximum of four levels. The two lower
levels reduce both food and water; the higher levels use the existing weekly
and monthly survival deadlines. Increased Consumption shortens the meal period
by a factor of two per level without inventing an additional water requirement.
The integer-second engine rejects a level requiring a fractional-second meal
interval. Duplicate purchases through both catalog identities and combinations
of Reduced and Increased Consumption reject pending a reviewed combination rule.

The internal SurvivalService reads the current approved build inside the shared
command transaction. Scenario code supplies the eligible inventory bindings;
players cannot supply food requirements or physiology in a command. Campaign
membership and current control eligibility are checked before initial submission
and under the commit lock. Existing starvation/dehydration reducers own supply
expenditure, canonical FP/HP changes and exact-once receipts. Restarted services
return prior outcomes without rerolling or spending again. Missing or stale
approval rejects without a committed consequence.

Independent tests cover the three-day food/water totals for ordinary actors,
the two lower Reduced Consumption levels and the first three Increased
Consumption levels; fractional water credit survives serialization and preserves
inventory conservation. Other tests check all four reduced meal periods, source
level bounds, actual persisted starvation, stale revisions and approval rejection.

This is not complete physiology or survival certification. Restricted food or
water variants, fuel/endurance handling, altered deprivation recovery, the full
alcohol/addiction/susceptibility scope of #902 and other #751 variants still need
their own source-derived consumer evidence. Existing higher-level water quantity
rounding and calendar interpretation are not certified by the period tests here.
No API/UI or release gate is promoted by these changes.
