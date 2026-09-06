import { useEffect, useState } from "react";
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
import { unconfiguredAdapter, type CampaignAdapter } from "./api/adapter";
import { CampaignState } from "./components/campaign-state";
import { Button } from "./components/ui/button";
import { Sheet } from "./components/ui/sheet";
const destinations = [
  { path: "/", name: "Play", icon: Compass },
  { path: "/character", name: "Character", icon: UserRound },
  { path: "/inventory", name: "Inventory", icon: Backpack },
  { path: "/journal", name: "Journal", icon: BookOpen },
  { path: "/campaign", name: "Campaign", icon: Flag },
] as const;
function ContextDetails() {
  return (
    <div className="context-details">
      <h3>Character</h3>
      <p>No character selected.</p>
      <h3>Inventory</h3>
      <p>Equipment will appear with your character.</p>
    </div>
  );
}
function Shell() {
  const pathname = useLocation({ select: (location) => location.pathname });
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
    document.getElementById("page-title")?.focus();
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
            <p>No active campaign</p>
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
              <ContextDetails />
            </Sheet>
          </div>
          <Outlet />
          <footer className="scene-footer">
            The engine keeps the facts. The story brings them to life.
          </footer>
        </main>
        <aside className="character-panel" aria-label="At a glance">
          <span className="eyebrow">At a glance</span>
          <ContextDetails />
        </aside>
      </div>
    </>
  );
}
function makeRouter(adapter: CampaignAdapter = unconfiguredAdapter) {
  const root = createRootRoute({
    component: Shell,
    notFoundComponent: () => (
      <p>This page doesn’t exist. Choose a destination from the navigation.</p>
    ),
  });
  const play = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: () => (
      <section className="scene-card" aria-label="Current scene">
        <CampaignState adapter={adapter} />
      </section>
    ),
  });
  const routes = destinations.slice(1).map(({ path, name }) =>
    createRoute({
      getParentRoute: () => root,
      path,
      component: () => (
        <section className="scene-card">
          <span className="eyebrow">{name}</span>
          <h2>
            {name === "Campaign"
              ? "No campaign selected"
              : `Your ${name.toLowerCase()}`}
          </h2>
          <p>
            {name === "Character"
              ? "Your character’s status and abilities will appear here."
              : name === "Inventory"
                ? "Your character’s equipment and carried items will appear here."
                : name === "Journal"
                  ? "Your discoveries and session notes will appear here."
                  : "Campaign information will appear once a campaign is connected."}
          </p>
        </section>
      ),
    }),
  );
  return createRouter({ routeTree: root.addChildren([play, ...routes]) });
}
export function App({
  adapter = unconfiguredAdapter,
}: {
  adapter?: CampaignAdapter;
}) {
  const [client] = useState(() => new QueryClient());
  const [router] = useState(() => makeRouter(adapter));
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
