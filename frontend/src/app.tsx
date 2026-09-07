import { CharacterPage, InventoryPage } from "./character/pages";
import { useEffect, useRef, useState } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  RouterProvider,
  useLocation,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Compass,
  UserRound,
  Backpack,
  BookOpen,
  Flag,
  SunMoon,
  PanelRight,
} from "lucide-react";
import { disconnectedTransport, type PlayTransport } from "./play/transport";
import { PlayProvider } from "./play/context";
import { usePlay } from "./play/use-play";
import {
  CampaignHome,
  CharacterSummary,
  Journal,
  PlayWorkspace,
} from "./play/workspace";
import { Button } from "./components/ui/button";
import { Sheet } from "./components/ui/sheet";
const destinations = [
  { path: "/", name: "Play", icon: Compass },
  { path: "/character", name: "Character", icon: UserRound },
  { path: "/inventory", name: "Inventory", icon: Backpack },
  { path: "/journal", name: "Journal", icon: BookOpen },
  { path: "/campaign", name: "Campaign", icon: Flag },
] as const;
function Shell() {
  const { state } = usePlay();
  const pathname = useLocation({ select: (location) => location.pathname });
  const previousPathname = useRef(pathname);
  const [dark, setDark] = useState(() => {
    try {
      const value = localStorage.getItem("wayfarer-theme");
      return value
        ? value === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch {
      return true;
    }
  });
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try {
      localStorage.setItem("wayfarer-theme", dark ? "dark" : "light");
    } catch {
      /* Storage can be disabled. */
    }
  }, [dark]);
  useEffect(() => {
    document.title = `Wayfarer — ${destinations.find((item) => item.path === pathname)?.name ?? "Page not found"}`;
    // Preserve the initial tab order; announce only client-side navigation.
    if (previousPathname.current !== pathname) {
      document.getElementById("page-title")?.focus();
      previousPathname.current = pathname;
    }
  }, [pathname]);
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/" className="brand">
          <Compass aria-hidden="true" />
          WAYFARER
        </Link>
        <span className="session-label">Campaign companion</span>
        <Button
          variant="outline"
          aria-label="Toggle dark theme"
          aria-pressed={dark}
          onClick={() => setDark(!dark)}
        >
          <SunMoon size={20} />
        </Button>
      </header>
      {!online && (
        <div role="status" className="offline-banner">
          You’re offline. Reconnect to load campaign updates.
        </div>
      )}
      <div className="workspace">
        <aside className="navigation-panel">
          <p className="eyebrow desktop-only">Your table</p>
          <nav aria-label="Main navigation">
            {destinations.map(({ path, name, icon: Icon }) => (
              <Link
                key={path}
                to={path}
                activeOptions={{ exact: true }}
                activeProps={{ className: "active", "aria-current": "page" }}
              >
                <Icon size={21} aria-hidden="true" />
                <span>{name}</span>
              </Link>
            ))}
          </nav>
          <div className="table-note desktop-only">
            <span className="eyebrow">Campaign</span>
            <p>{state.snapshot?.campaign.name ?? "No active campaign"}</p>
          </div>
        </aside>
        <main id="main" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <span className="eyebrow">At the table</span>
              <h1 id="page-title" tabIndex={-1}>
                {destinations.find((item) => item.path === pathname)?.name ??
                  "Page not found"}
              </h1>
            </div>
            <Sheet
              title="At a glance"
              trigger={
                <Button variant="outline">
                  <PanelRight size={18} aria-hidden="true" />
                  <span>Details</span>
                </Button>
              }
            >
              <CharacterSummary />
            </Sheet>
          </div>
          <Outlet />
          <footer className="scene-footer">
            The engine keeps the facts. The story brings them to life.
          </footer>
        </main>
        <aside className="character-panel" aria-label="At a glance">
          <span className="eyebrow">At a glance</span>
          <CharacterSummary />
        </aside>
      </div>
    </>
  );
}
function makeRouter() {
  const root = createRootRoute({
    component: Shell,
    notFoundComponent: () => (
      <p>This page doesn’t exist. Choose a destination from the navigation.</p>
    ),
  });
  const play = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: PlayWorkspace,
  });
  const routes = destinations.slice(1).map(({ path }) =>
    createRoute({
      getParentRoute: () => root,
      path,
      component:
        path === "/campaign"
          ? CampaignHome
          : path === "/journal"
            ? Journal
            : path === "/character"
              ? CharacterPage
              : InventoryPage,
    }),
  );
  return createRouter({ routeTree: root.addChildren([play, ...routes]) });
}
export function App({
  transport = disconnectedTransport,
}: {
  transport?: PlayTransport;
}) {
  const [client] = useState(() => new QueryClient());
  const [router] = useState(() => makeRouter());
  return (
    <QueryClientProvider client={client}>
      <PlayProvider key={transport.principalId} transport={transport}>
        <RouterProvider router={router} />
      </PlayProvider>
    </QueryClientProvider>
  );
}
