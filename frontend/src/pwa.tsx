import { useEffect, useState } from "react";

export function PwaStatus() {
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    if (
      !import.meta.env.PROD ||
      import.meta.env.VITE_PLAY_FIXTURES === "true" ||
      !("serviceWorker" in navigator)
    )
      return;
    let active = true;
    let registration: ServiceWorkerRegistration | undefined;
    const check = () => {
      if (active)
        setWaiting(
          !!registration?.waiting && !!navigator.serviceWorker.controller,
        );
    };
    const update = () => {
      void registration?.update().catch(() => {});
    };
    void navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .then((value) => {
        registration = value;
        check();
        value.addEventListener("updatefound", () =>
          value.installing?.addEventListener("statechange", check),
        );
      })
      .catch(() => {
        /* The application remains usable when installation is unavailable. */
      });
    window.addEventListener("online", update);
    return () => {
      active = false;
      window.removeEventListener("online", update);
    };
  }, []);
  return waiting ? (
    <div className="offline-banner" role="status">
      An update is ready. Finish pending actions, then close all Wayfarer tabs
      and reopen. Saved drafts are retained.
    </div>
  ) : null;
}
