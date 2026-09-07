import { expect, it, vi } from "vitest";
import { SetupClient } from "./client";
it("retries the original activation identity after a lost acknowledgement", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValueOnce(new Error("Network failed"))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ id: "c", phase: "active" }), {
        status: 200,
      }),
    );
  const client = new SetupClient("credential");
  const body = {
    id: "activation-id",
    operation: "activate",
    expected_revision: 4,
  };
  await expect(client.write("/c", body)).rejects.toThrow("Network");
  await expect(client.write("/c", { ...body, id: "new-id" })).rejects.toThrow(
    "pending",
  );
  await client.retry();
  expect(fetcher.mock.calls[0]![1]?.body).toBe(fetcher.mock.calls[1]![1]?.body);
  expect(client.hasPending).toBe(false);
});

it("recovers the exact pending payload after refresh and isolates authenticated players", async () => {
  sessionStorage.clear();
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValueOnce(new Error("Lost response"));
  const first = new SetupClient("old-token", "alice");
  const body = { id: "create-id", brief: { premise: "Original" } };
  await expect(first.write("", body)).rejects.toThrow("Lost response");
  body.brief.premise = "Edited after sending";
  expect(new SetupClient("bob-token", "bob").hasPending).toBe(false);
  const refreshed = new SetupClient("rotated-token", "alice");
  expect(refreshed.hasPending).toBe(true);
  fetcher.mockResolvedValueOnce(
    new Response(JSON.stringify({ id: "c", phase: "draft" })),
  );
  await refreshed.retry();
  expect(fetcher.mock.calls[1]![1]?.body).toBe(fetcher.mock.calls[0]![1]?.body);
  expect(new SetupClient("rotated-token", "alice").hasPending).toBe(false);
  expect(sessionStorage.length).toBe(0);
});

it.each([400, 409])(
  "allows correction after a definitive %s without rebasing the stale command",
  async (status) => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "Reload the draft" }), { status }),
      );
    const client = new SetupClient("token");
    await expect(
      client.write("/c", { id: "stale", expected_revision: 1 }),
    ).rejects.toThrow("Reload the draft");
    expect(client.hasPending).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
