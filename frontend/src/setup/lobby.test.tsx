import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupLobby } from "./lobby";

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
            : path.endsWith("/templates")
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
  await user.click(
    screen.getByRole("button", { name: "Load games and invitations" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Courier · archived" }),
  );
  expect(
    await screen.findByText("Repay the ferryman · active"),
  ).toBeInTheDocument();
  expect(screen.getByText("hp:a: 2/10")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "continue" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "unarchive" }));
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
            path.endsWith("/templates")
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
      await user.click(
        screen.getByRole("button", { name: "Load games and invitations" }),
      );
      await user.click(
        await screen.findByRole("button", { name: "Courier · completed" }),
      );
      expect(
        await screen.findByRole("heading", { name: `Courier · ${outcome}` }),
      ).toBeVisible();
      expect(screen.getByText("hp:b: 2/10")).toBeVisible();
      expect(screen.queryByText("hp:a:", { exact: false })).toBeNull();
      expect(screen.queryByRole("button", { name: "archive" })).toBeNull();
      expect(screen.queryByRole("button", { name: "continue" })).toBeNull();
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
            path.endsWith("/templates")
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
    await user.click(
      screen.getByRole("button", { name: "Load games and invitations" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Courier · completed" }),
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
    await user.click(screen.getByRole("button", { name: "continue" }));
    expect(await screen.findByRole("status")).toHaveTextContent("active");
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
