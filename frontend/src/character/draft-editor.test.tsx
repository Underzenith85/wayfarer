import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
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
const attribute = (id: string, name: string) => ({
  id,
  name,
  kind: "attribute" as const,
  status: "implemented" as const,
  point_cost: 10,
  skill: null,
  trait: null,
});
const result: CharacterPreview = {
  catalog: [
    attribute("attribute:st", "Strength"),
    attribute("attribute:dx", "Dexterity"),
    attribute("attribute:iq", "Intelligence"),
    attribute("attribute:ht", "Health"),
    {
      id: "skill:stealth",
      name: "Stealth",
      kind: "skill",
      status: "implemented",
      point_cost: null,
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
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
function Editor({
  preview,
  initial = proposal,
}: {
  preview: PreviewCharacter;
  initial?: Proposal;
}) {
  const [value, setValue] = useState(initial);
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
it("keeps the four primary attributes present, fixed and unremovable", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(result);
  render(<Editor preview={preview} />);
  const section = (await screen.findByText(/^Attributes/)).closest("details")!;
  for (const name of ["Strength", "Dexterity", "Intelligence", "Health"])
    expect(within(section).getByLabelText(name, { exact: true })).toHaveValue(
      10,
    );
  expect(within(section).queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  await waitFor(() =>
    expect(
      preview.mock.lastCall![0].draft.purchases.map((p) => p.definition_id),
    ).toEqual(["attribute:st", "attribute:dx", "attribute:iq", "attribute:ht"]),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Increase Dexterity" }),
  );
  await waitFor(() =>
    expect(preview.mock.lastCall![0].draft.purchases).toContainEqual({
      definition_id: "attribute:dx",
      amount: 11,
    }),
  );
  expect(preview.mock.lastCall![0].draft.purchases).toContainEqual({
    definition_id: "attribute:st",
    amount: 10,
  });
});
it("applies every rapid step without remounting the control", () => {
  const preview = vi.fn<PreviewCharacter>(() => new Promise(() => {}));
  render(<Editor preview={preview} />);
  const increase = screen.getByRole("button", { name: "Increase Strength" });
  act(() => {
    for (let click = 0; click < 5; click += 1) fireEvent.click(increase);
  });
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(15);
  expect(screen.getByRole("button", { name: "Increase Strength" })).toBe(
    increase,
  );

  const decrease = screen.getByRole("button", { name: "Decrease Strength" });
  act(() => {
    for (let click = 0; click < 3; click += 1) fireEvent.click(decrease);
  });
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(12);
});
it("repeats while held and stops on release", () => {
  vi.useFakeTimers();
  const preview = vi.fn<PreviewCharacter>(() => new Promise(() => {}));
  render(<Editor preview={preview} />);
  const increase = screen.getByRole("button", { name: "Increase Strength" });

  fireEvent.pointerDown(increase, { button: 0, pointerId: 1 });
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(11);
  act(() => vi.advanceTimersByTime(650));
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(14);

  fireEvent.pointerUp(increase, { button: 0, pointerId: 1 });
  fireEvent.click(increase);
  act(() => vi.advanceTimersByTime(500));
  expect(screen.getByLabelText("Strength", { exact: true })).toHaveValue(14);
});
it("collapses a duplicated attribute and keeps it out of the variable sections", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(result);
  render(
    <Editor
      preview={preview}
      initial={{
        ...proposal,
        draft: {
          ...proposal.draft,
          purchases: [
            { definition_id: "attribute:st", amount: 12 },
            { definition_id: "attribute:st", amount: 9 },
            { definition_id: "skill:stealth", amount: 1 },
          ],
        },
      }}
    />,
  );
  expect(
    await screen.findAllByLabelText("Strength", { exact: true }),
  ).toHaveLength(1);
  await waitFor(() =>
    expect(
      preview.mock.lastCall![0].draft.purchases.filter(
        (p) => p.definition_id === "attribute:st",
      ),
    ).toEqual([{ definition_id: "attribute:st", amount: 12 }]),
  );
  // A skill row stays a list entry, but its selector cannot reach an attribute.
  expect(screen.getByRole("button", { name: "Remove skill 1" })).toBeVisible();
  expect(
    within(screen.getByLabelText("Skill 1", { exact: true }))
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Stealth"]);
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

/** A catalog with one of everything, including traits the ruleset lists but
 *  does not implement — the shape the bundled prototype actually returns. */
const mixed: CharacterPreview = {
  ...result,
  catalog: [
    attribute("attribute:st", "ST"),
    attribute("attribute:dx", "DX"),
    attribute("attribute:iq", "IQ"),
    attribute("attribute:ht", "HT"),
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
/** A build with the fixed attributes and one entry of a variable section. */
const withSkill: Proposal = {
  ...proposal,
  draft: {
    ...proposal.draft,
    purchases: [
      { definition_id: "attribute:st", amount: 10 },
      { definition_id: "skill:stealth", amount: 1 },
    ],
  },
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
  render(<Editor preview={preview} initial={withSkill} />);
  await screen.findByText("73 / 150 points");
  // The skill row's picker offers skills alone: an attribute is a fixed
  // stepper above, and a trait has a section of its own (#267).
  expect(
    within(screen.getByLabelText("Skill 1", { exact: true }))
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Stealth"]);
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

it("keeps removal a quiet control beside the row it belongs to (#270)", async () => {
  const preview = vi.fn<PreviewCharacter>().mockResolvedValue(mixed);
  render(<Editor preview={preview} initial={withSkill} />);
  await screen.findByText("73 / 150 points");
  const row = within(section("Skills")).getByLabelText("Skill 1", {
    exact: true,
  }).parentElement!;
  // Removing one entry is upkeep: a quiet icon control named after its row,
  // never a full-width filled button on a line of its own.
  const remove = within(row).getByRole("button", { name: "Remove skill 1" });
  expect(remove).toHaveClass("button-outline");
  expect(remove).toHaveClass("purchase-remove");
  expect(remove.textContent).toBe("");
  // The trait is named once, by the option the picker shows; the stepper takes
  // its accessible name from that without printing it again.
  expect(
    [...row.querySelectorAll("label")].filter(
      (l) => !l.classList.contains("visually-hidden"),
    ),
  ).toHaveLength(1);
  await userEvent.click(remove);
  expect(
    within(section("Skills")).queryByLabelText("Skill 1", { exact: true }),
  ).toBeNull();
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
