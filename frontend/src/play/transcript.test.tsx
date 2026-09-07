import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Transcript } from "./workspace";
vi.mock("./use-play", () => ({ usePlay: () => ({ state: {}, store: {} }) }));
it("bounds a 10,000-entry history to 50 mounted rows and exposes older entries", async () => {
  const user = userEvent.setup();
  render(
    <Transcript
      entries={Array.from({ length: 10000 }, (_, i) => ({
        id: String(i),
        channel: "action",
        text: `Message ${i}`,
        action: null,
        narration: null,
      }))}
    />,
  );
  expect(screen.getAllByRole("listitem")).toHaveLength(50);
  expect(screen.getByText("Message 9999")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Older entries" }));
  expect(screen.getAllByRole("listitem")).toHaveLength(50);
  expect(screen.getByText("Message 9949")).toBeVisible();
  expect(screen.queryByText("Message 9999")).not.toBeInTheDocument();
});
