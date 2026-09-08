import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { GuidedScenarioAuthoring } from "./guided";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

it("shows safe provider diagnostics and a job identifier after generation fails", async () => {
  const terminal = {
    id: "00000000-0000-4000-8000-000000000002",
    version: 3,
    status: "failed",
    request: { source_digest: null },
    proposal_json: null,
    report: null,
    error_code: "codex_subscription_limit",
    error_message:
      "Codex usage limit reached. Failed during turn execution / response stream.",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_input, init) =>
      new Response(
        JSON.stringify(
          init?.method === "POST"
            ? {
                ...terminal,
                status: "queued",
                error_code: null,
                error_message: null,
              }
            : terminal,
        ),
        { status: init?.method === "POST" ? 202 : 200 },
      ),
  );
  const user = userEvent.setup();
  render(
    <GuidedScenarioAuthoring
      token="secret"
      principal="alice"
      source=""
      onAccept={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  await user.type(screen.getByLabelText("Premise"), "A dockside mystery");
  await user.click(
    screen.getByRole("button", { name: "Generate scenario proposal" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    terminal.error_message,
  );
  await user.click(screen.getByText("Generation error details"));
  expect(screen.getByText(`Code: ${terminal.error_code}`)).toBeVisible();
  expect(screen.getByText(`Job ID: ${terminal.id}`)).toBeVisible();
});

it("keeps generated spoilers hidden until the author accepts the proposal", async () => {
  const proposal = JSON.stringify({
    public: {
      title: "The Glass Harbor",
      summary: "Find the missing navigator.",
      opening_prompt: "The harbor bell rings twice.",
    },
    party: { slots: [{ actor_id: "scout", role: "Investigator" }] },
    gm_notes: "The navigator staged the disappearance.",
  });
  const terminal = {
    id: "00000000-0000-4000-8000-000000000001",
    version: 3,
    status: "succeeded",
    request: { source_digest: null },
    proposal_json: proposal,
    report: {
      status: "playable",
      findings: [
        {
          code: "challenge.estimate",
          severity: "warning",
          reference: "scenario",
          message: "Playtest the deadline.",
        },
      ],
    },
    error_code: null,
    error_message: null,
  };
  vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
    "00000000-0000-4000-8000-000000000001",
  );
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_input, init) =>
      new Response(
        JSON.stringify(
          init?.method === "POST"
            ? {
                ...terminal,
                version: 1,
                status: "queued",
                proposal_json: null,
                report: null,
              }
            : terminal,
        ),
        { status: init?.method === "POST" ? 202 : 200 },
      ),
  );
  const accept = vi.fn();
  const user = userEvent.setup();
  render(
    <GuidedScenarioAuthoring
      token="secret"
      principal="alice"
      source=""
      onAccept={accept}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  await user.type(screen.getByLabelText("Premise"), "A dockside mystery");
  await user.click(
    screen.getByRole("button", { name: "Generate scenario proposal" }),
  );
  expect(
    await screen.findByRole("heading", { name: "The Glass Harbor" }),
  ).toBeVisible();
  expect(
    screen.getByText(/Challenge warning: Playtest the deadline/),
  ).toBeVisible();
  expect(screen.queryByText(/staged the disappearance/)).toBeNull();
  await user.click(screen.getByLabelText(/Authorized author\/GM mode/));
  expect(screen.getByText(/staged the disappearance/)).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "Accept proposal into editor" }),
  );
  expect(accept).toHaveBeenCalledWith(proposal);
  await waitFor(() => expect(sessionStorage.length).toBe(0));
});
