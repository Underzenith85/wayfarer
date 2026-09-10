# Repository guidance

## Design system — Field Journal

The design-system documentation lives in `docs/design-system/`; its tokens live in
`frontend/src/styles/tokens.css`. Wayfarer should read as a field journal kept at
the table, not as a dashboard. Read `docs/design-system/00-principles.md` before
changing visual code.

- Keep all colour literals in `tokens.css`.
- Gradients may only draw the paper ground, margin rule, narration ruling, or deckle.
- Use `var(--radius)`; reserve `var(--radius-pill)` for lamp glow.
- Serif is the world, sans is the machine, mono is for comparable numbers, and
  handwriting is only for what a person wrote.
- Use the existing `.eyebrow`, `.label`, and `.tag` label roles.
- Never communicate state by colour alone.
- Do not add toasts, badge counts, red dots, overlapping avatars, emoji, shimmer,
  glossy button highlights, or another elevation.
- Interactive targets are 44px normally and never below 36px in dense panels.
- Another player's state must not steal focus, move the page, or interrupt reading.
- Run `pnpm lint:design` and `pnpm test` from `frontend/` after visual changes.
