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
    () =>
      new PlayStore(
        transport,
        () => client.clear(),
        1000,
        (key, view) => {
          client.setQueryData(key, view);
        },
      ),
  );
  useEffect(() => {
    let active = true;
    void store.loadCampaigns().then(() => {
      if (
        active &&
        store.getSnapshot().connection === "online" &&
        store.transport.initialCampaignId
      )
        void store.select(store.transport.initialCampaignId);
    });
    const offline = () => store.disconnect();
    const online = () => void store.reconnect();
    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    if (!navigator.onLine) offline();
    return () => {
      active = false;
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
      store.dispose();
    };
  }, [store]);
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
