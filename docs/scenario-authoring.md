# Guided scenario authoring

The New Game scenario catalog supports three provider-independent paths: bundled
templates, import/manual editing, and reopening a saved scenario. When a backend
provider is configured, **Create with AI** adds a guided proposal workflow.

Authors supply a premise, genre, tone, duration, difficulty, boundaries, rules
restrictions, and optional party capabilities. Follow-up instructions can refine
the whole scenario or only its brief, opening, world/routes, objectives/endings,
or characters/encounters. Generation is bounded to one to three validation and
repair attempts.

Generation never mutates a scenario. The server persists a job first, runs the
provider outside database transactions, and saves its output as an untrusted
proposal with authoritative validation diagnostics. The author must accept the
proposal into the editor, save a new revision, resolve hard errors, publish it,
create a game, assign legal characters, and explicitly start play. A late result
cannot replace newer browser edits or a newer catalog revision.

The proposal view exposes only the public premise, opening hook, party roles, and
validation summary. Hidden graph details and GM notes require the explicit
authorized author/GM reveal. Player preview endpoints continue to return the
allowlisted `PlayerScenarioExport` payload.

## Recovery and provider states

The browser stores only the current job ID in session storage. Reloading resumes
polling the durable job. Queued or running jobs left by a server restart become an
actionable interrupted state and can be retried. Running work can be cancelled;
late callbacks observe the cancelled version and cannot publish output. Provider
timeouts, unavailable/login failures, invalid output, and exhausted repair budgets
are distinct from scenario validation findings. Saved/template/manual paths never
invoke the provider.

## Opt-in real-provider smoke test

Normal CI uses deterministic provider fakes and needs no model credential. To
exercise a configured Responses or Codex subscription provider locally:

1. Complete the normal provider setup in `README.md` and start Wayfarer.
2. Open **New game**, authenticate, and choose **Create with AI**.
3. Generate a short scenario, reload while the job is running, and confirm the job
   recovers. Review the spoiler-safe proposal, then enable author/GM mode.
4. Accept, save, validate, publish, and create the game. Assign every player slot,
   mark the party ready, and use the explicit **Start game** action.
5. Export the accepted scenario, import it as a new scenario, and launch the
   imported revision. Confirm no new generation job or provider call occurs.

For failure-path smoke coverage, cancel one generation and temporarily stop or
expire the provider login for another. Both drafts must remain editable and the
template/manual paths must continue to work.
