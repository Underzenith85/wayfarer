import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupLobby } from "./lobby";
import { providerBanner } from "../presentation/availability";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("shows a saved conclusion and restores an archive to completed", async () => {
  const lobby = {
    id: "c",
    revision: 8,
    host_id: "alice",
    title: "Courier",
    phase: "archived",
    brief: {
      premise: "Find the courier",
      genre: "Mystery",
      tone: "Tense",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    },
    graph: null,
    party: [{ actor_id: "a", name: "Mira" }],
    seats: [],
    rules: {},
    next_adventure: null,
    adventures: [
      {
        adventure_id: "first",
        title: "Courier",
        outcome: "failure",
        at: 100,
        evidence: [{ id: "find", title: "Find evidence", satisfied: false }],
        discoveries: [],
        casualties: [],
        commitments: [
          { id: "debt", description: "Repay the ferryman", status: "active" },
        ],
        rewards: [],
        advancement: [],
        pools: [{ id: "hp:a", current: 2, maximum: 10 }],
      },
    ],
  };
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const path = input instanceof Request ? input.url : String(input);
      if (path.endsWith("/session"))
        return new Response(
          JSON.stringify({
            principal_id: "alice",
            generation_available: true,
            legacy_available: false,
          }),
        );
      if (path.endsWith("/api/v1/campaigns"))
        return new Response(JSON.stringify({ items: [], next_cursor: null }));
      return new Response(
        JSON.stringify(
          init?.method === "POST"
            ? { ...lobby, phase: "completed", revision: 9 }
            : path.endsWith("/templates") || path.endsWith("/profiles")
              ? []
              : path.endsWith("/c")
                ? lobby
                : [lobby],
        ),
      );
    });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.click(
    await screen.findByRole("button", { name: "Courier · Archived" }),
  );
  expect(
    await screen.findByText("Repay the ferryman · active"),
  ).toBeInTheDocument();
  expect(screen.getByText("Mira · HP 2/10")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Continue to next adventure" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Restore from archive" }),
  );
  expect(
    await screen.findByRole("button", {
      name: "Generate next-adventure preview",
    }),
  ).toBeInTheDocument();
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    operation: "unarchive",
    expected_revision: 8,
  });
});

it("renders seats as structured rows and lifecycle controls as actions", async () => {
  const lobby = {
    id: "c",
    revision: 4,
    host_id: "alice",
    title: "Courier",
    phase: "active",
    brief: {
      premise: "Find the courier",
      genre: "Mystery",
      tone: "Tense",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    },
    graph: null,
    party: [{ actor_id: "a", name: "Mira" }],
    seats: [
      { principal_id: "alice", joined: true, ready: true, actor_ids: ["a"] },
      { principal_id: "bob", joined: false, ready: false, actor_ids: [] },
    ],
    rules: {},
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = input instanceof Request ? input.url : String(input);
    if (path.endsWith("/session"))
      return new Response(
        JSON.stringify({
          principal_id: "alice",
          generation_available: false,
          legacy_available: false,
        }),
      );
    if (path.endsWith("/api/v1/campaigns"))
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    return new Response(
      JSON.stringify(
        path.endsWith("/templates") || path.endsWith("/profiles")
          ? []
          : path.endsWith("/c")
            ? lobby
            : [lobby],
      ),
    );
  });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.click(
    await screen.findByRole("button", { name: "Courier · In play" }),
  );
  const [host, guest] = within(
    await screen.findByRole("list", { name: "Players" }),
  ).getAllByRole("listitem");
  expect(within(host!).getByText("alice")).toBeVisible();
  expect(within(host!).getByText("Mira")).toBeVisible();
  expect(within(host!).getByText("Ready")).toBeVisible();
  expect(within(guest!).getByText("No character assigned")).toBeVisible();
  expect(within(guest!).getByText("Invited")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Pause session" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "End campaign" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "pause" })).toBeNull();
  // Without a provider the setup shell states the condition once, in the same
  // wording play uses, and lists what it costs behind a disclosure.
  expect(screen.getAllByText(providerBanner.summary)).toHaveLength(1);
  expect(screen.getByText(providerBanner.disclosure)).toBeVisible();
  expect(screen.queryByText(/AI generation and free-text actions/)).toBeNull();
  expect(screen.queryByText(/AI creation is unavailable/)).toBeNull();
});

describe("grounded ending journeys", () => {
  for (const outcome of ["success", "partial-success", "failure"] as const) {
    it(`renders a resumable ${outcome} projection without host controls for a player`, async () => {
      const lobby = {
        id: "c",
        revision: 8,
        host_id: "alice",
        title: "Courier",
        phase: "completed",
        brief: {
          premise: "Find the courier",
          genre: "Mystery",
          tone: "Tense",
          duration_minutes: 90,
          difficulty: "standard",
          restrictions: [],
        },
        graph: null,
        party: [
          { actor_id: "a", name: "Mira" },
          { actor_id: "b", name: "Iven" },
        ],
        seats: [
          { principal_id: "bob", joined: true, ready: true, actor_ids: ["b"] },
        ],
        rules: {},
        next_adventure: null,
        adventures: [
          {
            adventure_id: "first",
            title: "Courier",
            outcome,
            at: 100,
            evidence: [
              {
                id: "find",
                title: "Find evidence",
                satisfied: outcome === "success",
              },
            ],
            discoveries: [{ id: "known", predicate: "status", value: "safe" }],
            casualties: outcome === "failure" ? ["b"] : [],
            commitments: [
              {
                id: "debt",
                description: "Repay the ferryman",
                status: "active",
              },
            ],
            rewards:
              outcome === "failure"
                ? []
                : [{ id: "xp", points: 3, item_id: null }],
            advancement: [],
            pools: [{ id: "hp:b", current: 2, maximum: 10 }],
          },
        ],
      };
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = input instanceof Request ? input.url : String(input);
        if (path.endsWith("/session"))
          return new Response(
            JSON.stringify({
              principal_id: "bob",
              generation_available: true,
              legacy_available: false,
            }),
          );
        if (path.endsWith("/api/v1/campaigns"))
          return new Response(JSON.stringify({ items: [], next_cursor: null }));
        return new Response(
          JSON.stringify(
            path.endsWith("/templates") || path.endsWith("/profiles")
              ? []
              : path.endsWith("/c")
                ? lobby
                : [lobby],
          ),
        );
      });
      const user = userEvent.setup();
      render(<SetupLobby onOpen={vi.fn()} />);
      await user.type(screen.getByLabelText("Access token"), "secret");
      await user.click(screen.getByRole("button", { name: "Sign in" }));
      await user.click(
        await screen.findByRole("button", { name: "Courier · Finished" }),
      );
      expect(
        await screen.findByRole("heading", { name: `Courier · ${outcome}` }),
      ).toBeVisible();
      expect(screen.getByText("Iven · HP 2/10")).toBeVisible();
      expect(screen.queryByText("Mira", { exact: false })).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Archive campaign" }),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Continue to next adventure" }),
      ).toBeNull();
    });
  }

  it("previews and continues without browser-authored outcomes or rewards", async () => {
    const brief = {
      premise: "Find the courier",
      genre: "Mystery",
      tone: "Tense",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    };
    const adventure = {
      adventure_id: "first",
      title: "Courier",
      outcome: "success",
      at: 3,
      evidence: [{ id: "find", title: "Find evidence", satisfied: true }],
      discoveries: [],
      casualties: [],
      commitments: [],
      rewards: [{ id: "xp", points: 3, item_id: null }],
      advancement: [],
      pools: [{ id: "hp:a", current: 9, maximum: 10 }],
    };
    const initial = {
      id: "c",
      revision: 8,
      host_id: "alice",
      title: "Courier",
      phase: "completed",
      party: [{ actor_id: "a", name: "Mira" }],
      brief,
      graph: null,
      seats: [],
      rules: {},
      next_adventure: null,
      adventures: [adventure],
    };
    const sequel = {
      id: "sequel",
      title: "The debt",
      brief,
      npc_actor_ids: [],
      actors: [],
      opening_action: "Return to the ferryman",
    };
    const writes: Record<string, unknown>[] = [];
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = input instanceof Request ? input.url : String(input);
        if (path.endsWith("/session"))
          return new Response(
            JSON.stringify({
              principal_id: "alice",
              generation_available: true,
              legacy_available: false,
            }),
          );
        if (path.endsWith("/api/v1/campaigns"))
          return new Response(JSON.stringify({ items: [], next_cursor: null }));
        if (init?.method === "POST") {
          const body = JSON.parse(String(init.body)) as Record<string, unknown>;
          writes.push(body);
          return new Response(
            JSON.stringify(
              body.operation === "preview"
                ? {
                    ...initial,
                    revision: 9,
                    next_adventure: {
                      id: "sequel",
                      title: "The debt",
                      opening_action: "Return to the ferryman",
                    },
                  }
                : { ...initial, revision: 10, phase: "active" },
            ),
          );
        }
        return new Response(
          JSON.stringify(
            path.endsWith("/profiles")
              ? []
              : path.endsWith("/templates")
                ? [sequel]
                : path.endsWith("/c")
                  ? initial
                  : [initial],
          ),
        );
      });
    const user = userEvent.setup();
    render(<SetupLobby onOpen={vi.fn()} />);
    await user.type(screen.getByLabelText("Access token"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(
      await screen.findByRole("button", { name: "Courier · Finished" }),
    );
    await user.selectOptions(
      screen.getByLabelText("Authored next adventure"),
      "sequel",
    );
    await user.click(
      screen.getByRole("button", { name: "Save next-adventure preview" }),
    );
    expect(
      await screen.findByRole("article", { name: "Next adventure preview" }),
    ).toHaveTextContent("The debt");
    await user.click(
      screen.getByRole("button", { name: "Continue to next adventure" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("In play");
    expect(writes.map((value) => value.operation)).toEqual([
      "preview",
      "continue",
    ]);
    for (const body of writes) {
      expect(body).not.toHaveProperty("outcome");
      expect(body).not.toHaveProperty("rewards");
    }
    expect(fetcher).toHaveBeenCalled();
  });
});

it("creates a game with an exact rules profile and disables unsupported ones", async () => {
  const brief = {
    premise: "Carry the warning",
    genre: "Fantasy",
    tone: "Adventurous",
    duration_minutes: 30,
    difficulty: "gentle",
    restrictions: [],
  };
  const template = {
    id: "beacon-1",
    title: "The Last Beacon",
    brief,
    npc_actor_ids: [],
    actors: [],
  };
  const profiles = [
    {
      id: "profile:wayfarer-lite",
      version: 1,
      title: "Wayfarer prototype rules",
      edition: "wayfarer-lite",
      supported: true,
      conformance_profile_id: null,
      unverified_capabilities: [],
    },
    {
      id: "profile:gurps-lite-4e-2004",
      version: 1,
      title: "GURPS Lite, Fourth Edition (2004)",
      edition: "gurps-4e-2004",
      supported: false,
      conformance_profile_id: "gurps-lite-4e-2004",
      unverified_capabilities: ["gurps.check.success", "gurps.check.margin"],
    },
  ];
  const created = {
    id: "c",
    revision: 0,
    host_id: "alice",
    title: "The Last Beacon",
    phase: "draft",
    brief,
    graph: null,
    seats: [
      { principal_id: "alice", joined: true, ready: false, actor_ids: [] },
    ],
    rules: { edition: "wayfarer-lite" },
    rules_profile: {
      id: "profile:wayfarer-lite",
      version: 1,
      title: "Wayfarer prototype rules",
      supported: true,
    },
    next_adventure: null,
    adventures: [],
  };
  const writes: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = input instanceof Request ? input.url : String(input);
    if (path.endsWith("/session"))
      return new Response(
        JSON.stringify({
          principal_id: "alice",
          generation_available: false,
          legacy_available: false,
        }),
      );
    if (path.endsWith("/api/v1/campaigns"))
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    if (init?.method === "POST") {
      writes.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(created), { status: 201 });
    }
    return new Response(
      JSON.stringify(
        path.endsWith("/profiles")
          ? profiles
          : path.endsWith("/templates")
            ? [template]
            : [],
      ),
    );
  });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.click(await screen.findByRole("button", { name: "Adventure" }));
  await user.click(screen.getByRole("button", { name: "Next: Rules" }));
  const select = await screen.findByLabelText("Rules profile");
  const unsupported = screen.getByRole("option", {
    name: /GURPS Lite, Fourth Edition \(2004\) \(v1\) · Not yet supported/,
  });
  expect(unsupported).toBeDisabled();
  // The capability gap belongs to maintainers, not to a player choosing rules.
  expect(screen.queryByText(/unverified capabilit/i)).not.toBeVisible();
  await user.click(screen.getByText("Technical details"));
  expect(
    screen.getByText(
      "GURPS Lite, Fourth Edition (2004) (v1) unverified capabilities: gurps.check.success, gurps.check.margin",
    ),
  ).toBeVisible();
  await user.selectOptions(select, "profile:wayfarer-lite@1");
  await user.click(screen.getByRole("button", { name: "Back" }));
  await user.selectOptions(
    screen.getByLabelText("Adventure and starting party"),
    "beacon-1",
  );
  // The draft is created from the review step, never from a collecting one.
  expect(
    screen.queryByRole("button", { name: "Create game draft" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Next: Rules" }));
  await user.click(screen.getByRole("button", { name: "Next: Ready" }));
  expect(
    await screen.findByRole("form", { name: "Review and create" }),
  ).toHaveTextContent("Wayfarer prototype rules");
  await user.click(screen.getByRole("button", { name: "Create game draft" }));
  // Creating a draft replaces the step view; the pin is on the rules step.
  await user.click(await screen.findByRole("button", { name: "Rules" }));
  expect(
    await screen.findByText("Campaign rules: Wayfarer prototype rules (v1)"),
  ).toBeInTheDocument();
  expect(writes[0]).toMatchObject({
    rules_profile: { id: "profile:wayfarer-lite", version: 1 },
  });
});

it("keeps an authored concept unless its adventure brief is explicitly chosen", async () => {
  const adventureBrief = {
    premise: "Carry the harbor warning to the beacon before the storm arrives.",
    genre: "Fantasy",
    tone: "Adventurous",
    duration_minutes: 30,
    difficulty: "gentle",
    restrictions: [],
  };
  const template = {
    id: "beacon-2",
    title: "The Last Beacon (two players)",
    brief: adventureBrief,
    npc_actor_ids: [],
    actors: [],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = input instanceof Request ? input.url : String(input);
    if (path.endsWith("/session"))
      return new Response(
        JSON.stringify({
          principal_id: "alice",
          generation_available: false,
          legacy_available: false,
        }),
      );
    if (path.endsWith("/api/v1/campaigns"))
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    return new Response(
      JSON.stringify(path.endsWith("/templates") ? [template] : []),
    );
  });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));

  const premise =
    "A storm-battered harbor town whose lighthouse has gone dark.";
  await user.type(screen.getByLabelText("Premise"), premise);
  await user.clear(screen.getByLabelText("Duration (minutes)"));
  await user.type(screen.getByLabelText("Duration (minutes)"), "90");
  await user.selectOptions(screen.getByLabelText("Difficulty"), "standard");
  await user.click(screen.getByRole("button", { name: "Next: Adventure" }));
  await user.selectOptions(
    screen.getByLabelText("Adventure and starting party"),
    "beacon-2",
  );

  expect(screen.getByRole("status")).toHaveTextContent(
    "Your Concept answers were kept",
  );
  await user.click(screen.getByRole("button", { name: "Concept" }));
  expect(screen.getByLabelText("Premise")).toHaveValue(premise);
  expect(screen.getByLabelText("Duration (minutes)")).toHaveValue(90);
  expect(screen.getByLabelText("Difficulty")).toHaveValue("standard");

  await user.click(screen.getByRole("button", { name: "Adventure" }));
  await user.click(
    screen.getByRole("button", { name: "Use adventure concept" }),
  );
  await user.click(screen.getByRole("button", { name: "Concept" }));
  expect(screen.getByLabelText("Premise")).toHaveValue(adventureBrief.premise);
  expect(screen.getByLabelText("Duration (minutes)")).toHaveValue(30);
  expect(screen.getByLabelText("Difficulty")).toHaveValue("gentle");

  await user.click(
    screen.getByRole("button", { name: "Restore previous concept" }),
  );
  expect(screen.getByLabelText("Premise")).toHaveValue(premise);
  expect(screen.getByLabelText("Duration (minutes)")).toHaveValue(90);
  expect(screen.getByLabelText("Difficulty")).toHaveValue("standard");
});

it("walks the setup steps and creates the draft only from the review step", async () => {
  const created = {
    id: "c",
    revision: 0,
    host_id: "alice",
    title: "Carry the warning",
    phase: "draft",
    brief: {
      premise: "Carry the warning",
      genre: "Fantasy",
      tone: "Adventurous",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    },
    graph: null,
    party: [],
    seats: [
      { principal_id: "alice", joined: true, ready: false, actor_ids: [] },
    ],
    rules: {},
    next_adventure: null,
    adventures: [],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = input instanceof Request ? input.url : String(input);
    if (path.endsWith("/session"))
      return new Response(
        JSON.stringify({
          principal_id: "alice",
          generation_available: false,
          legacy_available: false,
        }),
      );
    if (path.endsWith("/api/v1/campaigns"))
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    if (init?.method === "POST")
      return new Response(JSON.stringify(created), { status: 201 });
    return new Response(JSON.stringify([]));
  });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  const chip = async (name: string) =>
    within(
      await screen.findByRole("navigation", { name: "Setup steps" }),
    ).getByRole("button", { name });
  // The first step offers a way forward, not the action that finishes setup.
  expect(
    screen.queryByRole("button", { name: "Create game draft" }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  expect(await chip("Party")).toBeDisabled();
  expect(await chip("Ready")).toBeDisabled();
  await user.type(screen.getByLabelText("Premise"), "Carry the warning");
  // A validated concept unlocks review; the party still waits for the draft.
  expect(await chip("Ready")).toBeEnabled();
  expect(await chip("Party")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Next: Adventure" }));
  expect(await chip("Adventure")).toHaveAttribute("aria-current", "step");
  await user.click(screen.getByRole("button", { name: "Next: Rules" }));
  // The party is skipped forward over, because it cannot be opened yet.
  await user.click(screen.getByRole("button", { name: "Next: Ready" }));
  expect(
    screen.getByRole("form", { name: "Review and create" }),
  ).toHaveTextContent("Carry the warning");
  expect(screen.queryByRole("button", { name: /^Next/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Back" }));
  expect(await chip("Rules")).toHaveAttribute("aria-current", "step");
  await user.click(await chip("Ready"));
  await user.click(screen.getByRole("button", { name: "Create game draft" }));
  // The created draft opens its party, and every step is reachable from there.
  expect(await chip("Party")).toHaveAttribute("aria-current", "step");
  expect(await chip("Ready")).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Create game draft" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Next: Ready" }),
  ).toBeInTheDocument();
});

/** The signed-in lobby: what the page is for, before what keeps it tidy (#204). */
async function signedIn(lobbies: object[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = input instanceof Request ? input.url : String(input);
    if (path.endsWith("/session"))
      return new Response(
        JSON.stringify({
          principal_id: "alice",
          generation_available: true,
          legacy_available: false,
        }),
      );
    if (path.endsWith("/api/v1/campaigns"))
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    return new Response(
      JSON.stringify(
        path.endsWith("/templates") || path.endsWith("/profiles")
          ? []
          : lobbies,
      ),
    );
  });
  const user = userEvent.setup();
  render(<SetupLobby onOpen={vi.fn()} />);
  await user.type(screen.getByLabelText("Access token"), "secret");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("heading", {
    name: "Your saved games and unfinished drafts",
  });
  return user;
}
it("leads with the games list and keeps upkeep out of primary position (#204)", async () => {
  const draft = {
    id: "c",
    revision: 1,
    host_id: "alice",
    title: "Courier",
    phase: "draft",
    brief: {
      premise: "Find the courier",
      genre: "Mystery",
      tone: "Tense",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    },
    graph: null,
    party: [],
    seats: [],
    rules: {},
  };
  await signedIn([draft]);
  const panel = screen.getByRole("region", { name: "New game and lobby" });
  const heading = screen.getByRole("heading", {
    name: "Your saved games and unfinished drafts",
  });
  const saved = await screen.findByRole("button", {
    name: "Courier · Draft",
  });
  // The list is the first content, and every control comes after it.
  const order = [...panel.querySelectorAll("h3, button")];
  expect(order[0]).toBe(heading);
  expect(order[1]).toBe(saved);
  // Upkeep reads as upkeep: user wording, and never a primary action.
  for (const name of ["Refresh this list", "Sign out"]) {
    const control = screen.getByRole("button", { name });
    expect(control).toHaveClass("button-outline");
    expect(control.compareDocumentPosition(saved)).toBe(
      Node.DOCUMENT_POSITION_PRECEDING,
    );
  }
  expect(screen.queryByRole("button", { name: /reconcile/i })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Sign out of setup" }),
  ).toBeNull();
});
it("names every step at every width and numbers them for narrow rows (#206)", async () => {
  await signedIn();
  const steps = within(
    screen.getByRole("navigation", { name: "Setup steps" }),
  ).getAllByRole("button");
  expect(steps.map((s) => s.textContent)).toEqual([
    "1Concept",
    "2Adventure",
    "3Rules",
    "4Party",
    "5Ready",
  ]);
  // The number is decoration; the step name stays the accessible label.
  expect(steps[0]).toHaveAccessibleName("Concept");
  expect(steps[0]).toHaveAttribute("aria-current", "step");
  expect(screen.getByText("Step 1 of 5: Concept")).toBeVisible();
});

describe("campaign lifecycle safety (#163)", () => {
  const active = {
    id: "c",
    revision: 4,
    host_id: "alice",
    title: "Courier",
    phase: "active",
    brief: {
      premise: "Find the courier",
      genre: "Mystery",
      tone: "Tense",
      duration_minutes: 90,
      difficulty: "standard",
      restrictions: [],
    },
    graph: null,
    party: [{ actor_id: "a", name: "Mira" }],
    seats: [
      { principal_id: "alice", joined: true, ready: true, actor_ids: ["a"] },
    ],
    rules: {},
  };
  /** Serves the host's own active campaign and echoes each lifecycle write. */
  const serve = () => {
    let current: Record<string, unknown> = active;
    return vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = input instanceof Request ? input.url : String(input);
        if (path.endsWith("/session"))
          return new Response(
            JSON.stringify({
              principal_id: "alice",
              generation_available: false,
              legacy_available: false,
            }),
          );
        if (path.endsWith("/api/v1/campaigns"))
          return new Response(JSON.stringify({ items: [], next_cursor: null }));
        if (init?.method === "POST") {
          const { operation } = JSON.parse(String(init.body)) as {
            operation: string;
          };
          const phase = {
            pause: "paused",
            resume: "active",
            complete: "completed",
          }[operation];
          current = {
            ...current,
            revision: (current.revision as number) + 1,
            ...(phase ? { phase } : {}),
          };
          return new Response(JSON.stringify(current));
        }
        return new Response(
          JSON.stringify(
            path.endsWith("/templates") || path.endsWith("/profiles")
              ? []
              : path.endsWith("/c")
                ? current
                : [current],
          ),
        );
      });
  };
  const openPanel = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.type(screen.getByLabelText("Access token"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(
      await screen.findByRole("button", { name: "Courier · In play" }),
    );
  };
  const writes = (fetcher: ReturnType<typeof serve>) =>
    fetcher.mock.calls
      .filter(([, init]) => init?.method === "POST")
      .map(
        ([, init]) => JSON.parse(String(init?.body)) as { operation?: string },
      )
      .filter((body) => !!body.operation);

  it("ends a campaign only after a confirmation that names it", async () => {
    const fetcher = serve();
    const user = userEvent.setup();
    render(<SetupLobby onOpen={vi.fn()} />);
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "End campaign" }));
    // The press opens the question; nothing has been sent to the service yet.
    expect(writes(fetcher)).toEqual([]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("End “Courier”?")).toBeVisible();
    expect(
      within(dialog).getByText(/cannot be returned to play/),
    ).toBeVisible();
    await user.click(
      within(dialog).getByRole("button", { name: "Keep playing" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(writes(fetcher)).toEqual([]);
    expect(
      screen.getByRole("button", { name: "Open playing scene" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "End campaign" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "End this campaign",
      }),
    );
    expect(writes(fetcher)).toMatchObject([
      { operation: "complete", expected_revision: 4 },
    ]);
    expect(
      await screen.findByRole("button", { name: "Archive campaign" }),
    ).toBeInTheDocument();
  });

  it("pauses in one press and offers an undo that stays in setup", async () => {
    const fetcher = serve();
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<SetupLobby onOpen={onOpen} />);
    await openPanel(user);
    // Reaching an active campaign from the list enters play, so the undo is
    // measured against the entries made before it, not against none at all.
    await user.click(screen.getByRole("button", { name: "Pause session" }));
    const entered = onOpen.mock.calls.length;
    expect(screen.queryByRole("dialog")).toBeNull();
    const undone = await screen.findByText(/Paused “Courier”/);
    expect(undone).toBeVisible();
    expect(writes(fetcher)).toMatchObject([{ operation: "pause" }]);
    await user.click(screen.getByRole("button", { name: "Undo pause" }));
    expect(writes(fetcher)).toMatchObject([
      { operation: "pause", expected_revision: 4 },
      { operation: "resume", expected_revision: 5 },
    ]);
    // Undo restores the campaign where it was; it does not enter play, and the
    // notice it belongs to is gone with the pause it undid.
    expect(onOpen).toHaveBeenCalledTimes(entered);
    expect(
      await screen.findByRole("button", { name: "Pause session" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Paused “Courier”/)).toBeNull();
  });

  it("separates lifecycle controls from the navigation beside them", async () => {
    serve();
    const user = userEvent.setup();
    render(<SetupLobby onOpen={vi.fn()} />);
    await openPanel(user);
    const lifecycle = await screen.findByRole("region", {
      name: "Campaign lifecycle",
    });
    expect(
      within(lifecycle).getByRole("button", { name: "End campaign" }),
    ).toHaveClass("button-danger");
    expect(
      within(lifecycle).getByRole("button", { name: "Pause session" }),
    ).toBeInTheDocument();
    expect(
      within(lifecycle).queryByRole("button", { name: "Open playing scene" }),
    ).toBeNull();
    expect(
      within(lifecycle).queryByRole("button", { name: "Create another game" }),
    ).toBeNull();
  });
});
