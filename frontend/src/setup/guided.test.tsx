import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

/** One job document, shaped as the service returns it. */
const job = (over: object = {}) => ({
  id: "00000000-0000-4000-8000-000000000001",
  version: 1,
  status: "queued",
  request: { source_digest: null },
  proposal_json: null,
  report: null,
  error_code: null,
  error_message: null,
  ...over,
});

it("opens on the concept already captured and follows it until edited (#262, #264)", async () => {
  const user = userEvent.setup();
  const concept = {
    premise: "A storm-battered harbor town whose lighthouse has gone dark.",
    genre: "Mystery",
    tone: "Bleak",
    duration_minutes: 90,
    difficulty: "hard" as const,
    restrictions: [],
  };
  const { rerender } = render(
    <GuidedScenarioAuthoring
      token="secret"
      principal="alice"
      source=""
      concept={concept}
      onAccept={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  // The five questions the concept step already asked are answered here.
  expect(screen.getByLabelText("Premise")).toHaveValue(concept.premise);
  expect(screen.getByLabelText("Genre")).toHaveValue("Mystery");
  expect(screen.getByLabelText("Tone")).toHaveValue("Bleak");
  expect(screen.getByLabelText("Duration (minutes)")).toHaveValue(90);
  // Difficulty reads the same here as it does in the concept step (#264).
  expect(screen.getByLabelText("Difficulty")).toHaveValue("hard");
  expect(
    within(screen.getByLabelText("Difficulty"))
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Gentle", "Standard", "Hard"]);
  // A later concept edit is carried across, because nothing here has been typed.
  rerender(
    <GuidedScenarioAuthoring
      token="secret"
      principal="alice"
      source=""
      concept={{ ...concept, tone: "Hopeful" }}
      onAccept={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Tone")).toHaveValue("Hopeful");
  // Once the author writes here, this copy is theirs and stops following.
  await user.clear(screen.getByLabelText("Tone"));
  await user.type(screen.getByLabelText("Tone"), "Wry");
  rerender(
    <GuidedScenarioAuthoring
      token="secret"
      principal="alice"
      source=""
      concept={{ ...concept, tone: "Grim" }}
      onAccept={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Tone")).toHaveValue("Wry");
});

it("announces a failed generation and offers one control to run it (#263)", async () => {
  vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
    "00000000-0000-4000-8000-000000000001",
  );
  const failed = job({
    version: 2,
    status: "failed",
    error_code: "provider_unavailable",
    error_message: "The provider is unavailable or limited.",
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_input, init) =>
      new Response(JSON.stringify(init?.method === "POST" ? job() : failed), {
        status: init?.method === "POST" ? 202 : 200,
      }),
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
  // The failure is announced, looks like a failure, and says what to do next.
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveClass("generation-failure");
  expect(alert).toHaveTextContent("Generation failed");
  expect(alert).toHaveTextContent(/Check its login on the server/);
  // Retry replaces Generate rather than standing beside it.
  expect(
    screen.getByRole("button", { name: "Retry generation" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Generate scenario proposal" }),
  ).toBeNull();
  // The failure is not also restated as plain status text beneath the button.
  expect(screen.queryByText(/^Generation: failed/)).toBeNull();
});
