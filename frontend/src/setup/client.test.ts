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
