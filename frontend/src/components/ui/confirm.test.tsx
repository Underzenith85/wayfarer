import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./confirm";
const dialog = (handlers: { onConfirm: () => void; onCancel: () => void }) => (
  <ConfirmDialog
    open
    title="End “Courier”?"
    description="Ending this campaign cannot be undone."
    confirmLabel="End this campaign"
    cancelLabel="Keep playing"
    {...handlers}
  />
);
it("names what it acts on and states the consequence", () => {
  render(dialog({ onConfirm: vi.fn(), onCancel: vi.fn() }));
  const asked = screen.getByRole("dialog", { name: "End “Courier”?" });
  expect(asked).toHaveTextContent("Ending this campaign cannot be undone.");
});
it("opens with cancelling in hand, and confirms only when asked to", async () => {
  const onConfirm = vi.fn(),
    onCancel = vi.fn();
  render(dialog({ onConfirm, onCancel }));
  const user = userEvent.setup();
  // The safe answer holds the focus the dialog moves inside itself, so Enter
  // or Space on arrival keeps the campaign rather than ending it.
  expect(screen.getByRole("button", { name: "Keep playing" })).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "End this campaign" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});
it("treats Escape and the overlay as cancelling", async () => {
  const onConfirm = vi.fn(),
    onCancel = vi.fn();
  render(dialog({ onConfirm, onCancel }));
  await userEvent.keyboard("{Escape}");
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});
it("holds the confirmation while the request it sent is in flight", () => {
  render(
    <ConfirmDialog
      open
      busy
      title="End “Courier”?"
      description="Ending this campaign cannot be undone."
      confirmLabel="End this campaign"
      onConfirm={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("button", { name: "End this campaign" }),
  ).toBeDisabled();
});
