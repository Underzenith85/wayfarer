import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from "@tanstack/react-query";
import { describe, it, expect, vi, afterEach } from "vitest";
import { CampaignState } from "./campaign-state";
import type { CampaignAdapter } from "../api/adapter";
function mount(adapter: CampaignAdapter) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CampaignState adapter={adapter} />
    </QueryClientProvider>,
  );
}
afterEach(() => onlineManager.setOnline(true));
describe("campaign presentation boundary", () => {
  it("shows empty state without inventing a campaign", async () => {
    mount({ getCurrentCampaign: () => Promise.resolve(null) });
    expect(await screen.findByText("No campaign selected")).toBeVisible();
  });
  it("shows loading while the adapter is pending", () => {
    mount({ getCurrentCampaign: () => new Promise(() => {}) });
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });
  it("retries a failed read and displays server narration as text", async () => {
    const getCurrentCampaign = vi
      .fn()
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValueOnce({
        id: "1",
        name: "A campaign",
        scene: "The gate",
        narration: "<script>not executable</script>",
      });
    mount({ getCurrentCampaign });
    expect(await screen.findByRole("alert")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The gate")).toBeVisible();
    expect(screen.getByText("<script>not executable</script>")).toBeVisible();
    expect(getCurrentCampaign).toHaveBeenCalledTimes(2);
    expect(getCurrentCampaign.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
  });
  it("pauses reads offline", async () => {
    onlineManager.setOnline(false);
    const getCurrentCampaign = vi.fn().mockResolvedValue(null);
    mount({ getCurrentCampaign });
    expect(await screen.findByText("Waiting for a connection")).toBeVisible();
    expect(getCurrentCampaign).not.toHaveBeenCalled();
  });
});
