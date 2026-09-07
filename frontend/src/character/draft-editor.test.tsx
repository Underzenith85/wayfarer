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
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
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
