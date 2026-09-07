import { afterEach, expect, it, vi } from "vitest";
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
      const path = String(input);
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
  await user.type(screen.getByLabelText("Player ID"), "alice");
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
