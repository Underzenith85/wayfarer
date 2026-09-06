import { createContext, useContext, useSyncExternalStore } from "react";
import type { PlayStore } from "./store";
export const Context = createContext<PlayStore | null>(null);
export function usePlay() {
  const store = useContext(Context);
  if (!store) throw new Error("PlayProvider is missing");
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return { store, state };
}
