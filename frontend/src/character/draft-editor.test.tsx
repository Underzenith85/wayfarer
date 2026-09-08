import { useState } from "react";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  CharacterDraftEditor,
  type CharacterPreview,
  type PreviewCharacter,
  type Proposal,
} from "./draft-editor";

const proposal: Proposal = {
  draft: {
    name: "Scout",
    backstory: "A watchful traveler",
    purchases: [{ definition_id: "attribute:st", amount: 10 }],
  },
  custom: [],
};
const result: CharacterPreview = {
  catalog: [
    {
      id: "attribute:st",
      name: "Strength",
      kind: "attribute",
      status: "implemented",
      point_cost: 10,
      skill: null,
      trait: null,
    },
  ],
  spent: 73,
  remaining: 77,
  legal: true,
  diagnostics: [],
  derived: [["secondary:hp", "13"]],
  breakdown: [{ definition_id: "attribute:st", amount: 10, cost: 23 }],
};
afterEach(cleanup);
function Editor({ preview }: { preview: PreviewCharacter }) {
  const [value, setValue] = useState(proposal);
  return (
    <CharacterDraftEditor
      proposal={value}
      onChange={setValue}
      preview={preview}
      templates={[{ title: "Scout", proposal }]}
    />
  );
}
it("shows service totals, per-purchase costs and derived statistics; edits request a new preview", async () => {
  const preview = vi
    .fn<PreviewCharacter>()
    .mockResolvedValueOnce(result)
    .mockResolvedValue({
      ...result,
      spent: 160,
      remaining: -10,
      legal: false,
      derived: [],
      breakdown: [],
      diagnostics: ["Point budget exceeded"],
    });
  render(<Editor preview={preview} />);
  expect(await screen.findByText("73 / 150 points")).toBeVisible();
  expect(screen.getByText("23 pts")).toBeVisible();
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(10);
  expect(
    within(
      screen.getByRole("region", { name: "Derived statistics" }),
    ).getByText("13"),
  ).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Increase Strength" }),
  );
  expect(screen.queryByText("73 / 150 points")).toBeNull();
  expect(await screen.findByText("160 / 150 points")).toBeVisible();
  expect(screen.getByLabelText("Point budget")).toHaveAttribute(
    "data-overspent",
    "true",
  );
  expect(screen.getByText("10 points over budget")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("Point budget exceeded");
  expect(preview.mock.calls[1]![0].draft.purchases[0]!.amount).toBe(11);
});
it("ignores a late preview even when the transport does not honor cancellation", async () => {
  let resolveOld!: (value: CharacterPreview) => void;
  const preview = vi
    .fn<PreviewCharacter>()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValue({ ...result, spent: 80, remaining: 70 });
  render(<Editor preview={preview} />);
  await waitFor(() => expect(preview).toHaveBeenCalledOnce());
  await userEvent.type(
    screen.getByLabelText("Name", { exact: true }),
    " revised",
  );
  expect(await screen.findByText("80 / 150 points")).toBeVisible();
  await act(async () => resolveOld(result));
  expect(screen.queryByText("73 / 150 points")).toBeNull();
  expect(screen.getByText("80 / 150 points")).toBeVisible();
  expect(preview.mock.calls[0]![1].aborted).toBe(true);
});
it("keeps character controls uniquely labelled in a multi-character party and template changes editable", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(result);
  render(
    <>
      <Editor preview={preview} />
      <Editor preview={preview} />
    </>,
  );
  const names = screen.getAllByLabelText("Name", { exact: true });
  expect(names).toHaveLength(2);
  expect(names[0]!.id).not.toBe(names[1]!.id);
  await userEvent.clear(names[0]!);
  await userEvent.type(names[0]!, "Custom hero");
  expect(names[1]).toHaveValue("Scout");
  await userEvent.selectOptions(
    screen.getAllByLabelText("Start from a character template")[0]!,
    "0",
  );
  expect(names[0]).toHaveValue("Scout");
});
it("clears previous profile figures when the preview source changes", async () => {
  const old = vi.fn<PreviewCharacter>().mockResolvedValue(result);
  const next = vi
    .fn<PreviewCharacter>()
    .mockRejectedValue(new Error("Profile unavailable"));
  const { rerender } = render(<Editor preview={old} />);
  expect(await screen.findByText("73 / 150 points")).toBeVisible();
  rerender(<Editor preview={next} />);
  expect(screen.queryByText("73 / 150 points")).toBeNull();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Profile unavailable",
  );
});

/** A catalog with one of everything, including a trait the ruleset lists but
 *  does not implement — the shape the bundled prototype actually returns. */
const mixed: CharacterPreview = {
  ...result,
  catalog: [
    {
      id: "attribute:st",
      name: "ST",
      kind: "attribute",
      status: "implemented",
      point_cost: 10,
      skill: null,
      trait: null,
    },
    {
      id: "skill:stealth",
      name: "Stealth",
      kind: "skill",
      status: "implemented",
      point_cost: null,
      skill: null,
      trait: null,
    },
    {
      id: "trait:keen-senses",
      name: "Keen senses",
      kind: "trait",
      status: "manual-adjudication",
      point_cost: 5,
      skill: null,
      trait: null,
    },
    {
      id: "trait:code-of-honor",
      name: "Code of honor",
      kind: "trait",
      status: "manual-adjudication",
      point_cost: -5,
      skill: null,
      trait: null,
    },
  ],
  derived: [
    ["attribute:st", "14"],
    ["secondary:hp", "13"],
    ["secondary:dodge", "8"],
  ],
};
const section = (name: string) =>
  [...document.querySelectorAll("details")].find((d) =>
    d.querySelector("summary")!.textContent!.startsWith(name),
  )!;
/** Sections a character sheet opens on demand; a closed one shows nothing. */
const opened = async (name: string) => {
  const found = section(name);
  if (!found.open) await userEvent.click(found.querySelector("summary")!);
  return within(found);
};

it("scopes each picker to its own section and never lists what cannot be bought (#267, #272)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(mixed);
  render(<Editor preview={preview} />);
  await screen.findByText("73 / 150 points");
  // The one purchase is an attribute, so its picker offers attributes alone:
  // a skill has a section of its own and is never added from here (#267).
  const picker = within(section("Attributes")).getByRole("combobox");
  expect(
    within(picker)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["ST"]);
  // Nothing is offered as a disabled row suffixed "Unavailable"; the traits the
  // ruleset lists but cannot play are named once, in their section (#272).
  expect(screen.queryByText(/Unavailable/)).toBeNull();
  expect(
    (await opened("Advantages")).getByText(
      /Listed in this game’s ruleset but not playable yet: Keen senses\./,
    ),
  ).toBeVisible();
  expect(
    (await opened("Disadvantages")).getByText(/Code of honor\./),
  ).toBeVisible();
});

it("says plainly when a section cannot be used instead of leaving it empty (#268)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(mixed);
  render(<Editor preview={preview} />);
  await screen.findByText("73 / 150 points");
  // A section with something to add offers the control that adds it.
  expect(
    within(section("Skills")).getByRole("button", { name: "Add skill" }),
  ).toBeEnabled();
  // A section with nothing to add says so, rather than showing a heading, a
  // point subtotal and no content at all.
  for (const [name, text] of [
    ["Advantages", "Advantages are not available in this game yet."],
    ["Disadvantages", "Disadvantages are not available in this game yet."],
    ["Equipment", "Equipment is not available in this game yet."],
  ] as const) {
    const region = await opened(name);
    expect(region.getByText(text)).toBeVisible();
    expect(region.queryByRole("button")).toBeNull();
  }
});

it("names each entry once and keeps removal a quiet control (#270)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(mixed);
  render(<Editor preview={preview} />);
  await screen.findByText("73 / 150 points");
  const row = document.querySelector<HTMLElement>(".purchase-row")!;
  // The chosen option is the only place the entry is named; the stepper and the
  // remove control take their accessible names from it without printing it.
  expect(within(row).getByRole("combobox")).toHaveDisplayValue("ST");
  expect([...row.querySelectorAll("label")].map((l) => l.className)).toEqual([
    "visually-hidden",
    "visually-hidden",
  ]);
  const remove = within(row).getByRole("button", {
    name: "Remove Strength",
  });
  expect(remove).toHaveClass("button-outline");
  expect(remove).toHaveClass("purchase-remove");
  expect(screen.queryByRole("button", { name: /Remove ability/ })).toBeNull();
  await userEvent.click(remove);
  expect(document.querySelector(".purchase-row")).toBeNull();
});

it("shows what the build derives and never repeats the primary attributes (#269)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(mixed);
  render(<Editor preview={preview} />);
  await screen.findByText("73 / 150 points");
  const panel = within(
    screen.getByRole("region", { name: "Derived statistics" }),
  );
  expect(panel.getAllByRole("term").map((t) => t.textContent)).toEqual([
    "Hit points",
    "Dodge",
  ]);
  expect(panel.queryByText("Strength")).toBeNull();
  expect(panel.queryByText("14")).toBeNull();
});

it("states that a ruleset derives nothing rather than echoing the attributes (#269)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue({
    ...mixed,
    derived: [
      ["attribute:st", "14"],
      ["attribute:ht", "10"],
    ],
  });
  render(<Editor preview={preview} />);
  await screen.findByText("73 / 150 points");
  expect(
    within(
      screen.getByRole("region", { name: "Derived statistics" }),
    ).getByText(/derives no secondary characteristics/),
  ).toBeVisible();
});
