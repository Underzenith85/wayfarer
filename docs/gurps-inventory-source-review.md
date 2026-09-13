# GURPS inventory source review

Reviewer: OpenAI Codex  
Review date: 2026-09-13

## Selected sources

The review used the repository's frozen Basic Set profile and the supplied files:

- *Basic Set: Characters*, Fourth Edition, third printing (February 2008), SHA-256 `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`.
- *Basic Set: Campaigns*, Fourth Edition, fourth printing (April 2008), SHA-256 `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.

Printed page numbers were checked against the page footer, not the PDF page index.

## Mundane trait joins

Every expanded runtime row below was compared with its printed construction and joined to the independently reviewed parent ledger row where one exists. The join is exact-ID based; it does not approve future variants by prefix or page number.

| Runtime family | Reviewed parent | Printed reference |
| --- | --- | --- |
| Low Status | disadvantage Status | B28 |
| Mild, Severe, and Crippling Shyness | disadvantage Shyness | B154 |
| Code of Honor (Soldier) | disadvantage Code of Honor | B127 |
| Appearance levels and options | advantage or disadvantage Appearance, according to sign | B21 |
| Positive and negative Reputation examples | corresponding Reputation parent | B26-B27 |
| Acute Hearing, Taste and Smell, Touch, and Vision | corresponding Acute Sense advantage | B35 |
| Wealth levels | advantage or disadvantage Wealth, according to sign | B25 |
| Sense of Duty scopes | disadvantage Sense of Duty | B153 |
| Trade language modes | direct reviewed construction | B24 |
| Foreign Cultural Familiarity | Cultural Familiarity | B23 |
| Rank and Courtesy Rank variants | corresponding Rank parent | B29-B30 |
| Ally, Contact, Patron, Dependent, and Enemy examples | corresponding relationship parent | B36, B44, B72, B131, B135 |

Zero-point Appearance and Wealth rows were checked as neutral construction states and are bound to the positive parent solely for source accounting; this does not change their point cost or runtime behavior.

## Registered catalog rows

The Characters package definitions for ST, DX, IQ, HT, HP, Will, Per, FP, Basic Speed, and Basic Move were compared at B14-B17 for package versions 0.3.0 and 0.4.0. The two package-only Guns specialties were checked at B198 and against their equipment use at B278. Magery 0 and leveled Magery were checked at B66. These approvals apply only to the exact package versions and definition IDs recorded in the source-audit join table.

The GURPS Lite package remains outside this review because its separate source file was not supplied.

## Vehicle rows

The Wagon and Luxury Car rows were compared against the Campaigns Ground Vehicle Table at B464. Their TL, ST/HP, DR, and cost values agree with the selected printing. Both remain listing-only: source review does not implement vehicle movement or combat.
