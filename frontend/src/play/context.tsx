import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { PlayStore } from "./store";
import type { PlayTransport } from "./transport";
import { Context } from "./use-play";
export function PlayProvider({
  transport,
  children,
}: {
  transport: PlayTransport;
  children: ReactNode;
}) {
  const client = useQueryClient();
  const [store] = useState(
    () => new PlayStore(transport, () => client.clear()),
  );
  useEffect(() => {
    void store.loadCampaigns();
    return () => store.dispose();
  }, [store]);
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
