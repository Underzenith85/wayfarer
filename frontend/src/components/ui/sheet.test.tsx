import { expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sheet } from "./sheet";
import { Button } from "./button";
const sheet = () => (
  <Sheet title="Session" trigger={<Button>Session</Button>}>
    <Button>Switch campaign</Button>
  </Sheet>
);
it("opens on a single pointer activation", async () => {
  render(sheet());
  await userEvent.click(screen.getByRole("button", { name: "Session" }));
  expect(screen.getByRole("dialog", { name: "Session" })).toBeVisible();
});
it("opens on a single keyboard activation", async () => {
  render(sheet());
  screen.getByRole("button", { name: "Session" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("dialog", { name: "Session" })).toBeVisible();
});
it("survives a trigger that toggles twice for one activation (#205)", async () => {
  render(sheet());
  const trigger = screen.getByRole("button", { name: "Session" });
  // The reported symptom: a trigger left marked expanded with no dialog on
  // screen. One activation is one decision, however many times it arrives.
  act(() => {
    trigger.click();
    trigger.click();
  });
  expect(screen.getByRole("dialog", { name: "Session" })).toBeVisible();
  expect(trigger).toHaveAttribute("aria-expanded", "true");
});
it("turns away a duplicate that arrives a tick after the open (#205)", async () => {
  render(sheet());
  const trigger = screen.getByRole("button", { name: "Session" });
  act(() => trigger.click());
  await act(async () => {
    await Promise.resolve();
    trigger.click();
  });
  expect(screen.getByRole("dialog", { name: "Session" })).toBeVisible();
});
it("closes at once on the close control and on Escape, however soon", async () => {
  render(sheet());
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "Session" });
  // Deliberate dismissals are never held back by the same-activation window.
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: "Close details" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(trigger);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  // The trigger is usable again once the sheet is out of the way.
  await user.click(trigger);
  expect(screen.getByRole("dialog")).toBeVisible();
});
