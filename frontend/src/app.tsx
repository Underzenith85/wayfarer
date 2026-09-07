import { CharacterPage, InventoryPage } from "./character/pages";
import { ConnectionStatus } from "./multiplayer/panel";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  RouterProvider,
  useLocation,
  useNavigate,
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
  Menu,
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
import { destinations, pagePath, parsePath, type Segment } from "./routes";
import { Button } from "./components/ui/button";
import { ProviderBanner } from "./components/availability";
import { Sheet } from "./components/ui/sheet";
/** Setup is a separate shell; the play header is the way back to it. */
const CampaignMenu = createContext<{
  onNewGame?: (() => void) | undefined;
  onSwitchCampaign?: (() => void) | undefined;
}>({});
const icons: Record<Segment, ComponentType<{ size?: number }>> = {
  "": Compass,
  character: UserRound,
  inventory: Backpack,
  journal: BookOpen,
  campaign: Flag,
};
const pages: Record<Segment, () => ReactNode> = {
  "": PlayWorkspace,
  character: CharacterPage,
  inventory: InventoryPage,
  journal: Journal,
  campaign: CampaignHome,
};
function Shell() {
  const { state, store } = usePlay();
  const { onNewGame, onSwitchCampaign } = useContext(CampaignMenu);
  const navigate = useNavigate();
  const pathname = useLocation({ select: (location) => location.pathname });
  const route = parsePath(pathname);
  const scope = route?.campaignId ?? state.selectedId;
  const title = route
    ? (destinations.find((item) => item.segment === route.segment)?.name ??
      "Page not found")
    : "Page not found";
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
  // Stated once for the whole shell; the composer and scene actions repeat only
  // the part of it that blocks each control.
  const providerMissing =
    !!state.snapshot &&
    !state.snapshot.campaign.capabilities.includes("actions.text");
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
  // A reloaded or pasted campaign URL opens that campaign for an authorized
  // session. Only the address bar can name a campaign the store has not opened,
  // so in-app navigation never contends with this.
  useEffect(() => {
    const current = parsePath(pathname);
    const wanted = current?.campaignId;
    if (
      !current ||
      !wanted ||
      state.selectedId ||
      state.expired ||
      state.loading
    )
      return;
    if (state.campaigns.some((campaign) => campaign.id === wanted))
      void store.select(wanted);
    else if (state.campaigns.length)
      void navigate({
        to: pagePath(null, current.segment),
        search: true,
        replace: true,
      });
  }, [
    pathname,
    state.campaigns,
    state.selectedId,
    state.expired,
    state.loading,
    store,
    navigate,
  ]);
  // Keep the address bar on the open campaign so every view stays shareable.
  useEffect(() => {
    const current = parsePath(pathname);
    if (
      !current ||
      !state.selectedId ||
      current.campaignId === state.selectedId
    )
      return;
    void navigate({
      to: pagePath(state.selectedId, current.segment),
      // Rewriting the scope must not drop a view's own query, such as a
      // permanent journal-entry link.
      search: true,
      replace: true,
    });
  }, [pathname, state.selectedId, navigate]);
  useEffect(() => {
    document.title = `Wayfarer — ${title}`;
    // Preserve the initial tab order; announce only client-side navigation.
    if (previousPathname.current !== pathname) {
      document.getElementById("page-title")?.focus();
      previousPathname.current = pathname;
    }
  }, [pathname, title]);
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to={pagePath(scope, "")} className="brand">
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
        {(!state.expired || onSwitchCampaign || onNewGame) && (
          <Sheet
            title="Session"
            description="Leave this table. Switching or starting a game keeps this campaign saved; ending the session clears this tab’s private state."
            trigger={
              <Button variant="outline">
                <Menu size={18} aria-hidden="true" />
                <span>Session</span>
              </Button>
            }
          >
            <div className="context-actions">
              {onSwitchCampaign && (
                <Button onClick={onSwitchCampaign}>Switch campaign</Button>
              )}
              {onNewGame && <Button onClick={onNewGame}>New game</Button>}
              {!state.expired && (
                <Button
                  variant="outline"
                  onClick={() => {
                    store.expire();
                    if (!store.transport.sample) location.assign("/");
                  }}
                >
                  End session
                </Button>
              )}
            </div>
          </Sheet>
        )}
      </header>
      {!online && (
        <div role="status" className="offline-banner">
          You’re offline. Reconnect to load campaign updates.
        </div>
      )}
      {providerMissing && <ProviderBanner />}
      <div className="workspace">
        <aside className="navigation-panel">
          <p className="eyebrow desktop-only">Your table</p>
          <nav aria-label="Main navigation">
            {destinations.map(({ segment, name }) => {
              const Icon = icons[segment];
              return (
                <Link
                  key={segment}
                  to={pagePath(scope, segment)}
                  activeOptions={{ exact: true }}
                  activeProps={{ className: "active", "aria-current": "page" }}
                >
                  <Icon size={21} aria-hidden="true" />
                  <span>{name}</span>
                </Link>
              );
            })}
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
                {title}
              </h1>
            </div>
            {/* The same summary is a rail at wide widths; its drawer trigger
                exists only where the rail is hidden (.compact-only). */}
            <Sheet
              title="At a glance"
              trigger={
                <Button variant="outline" className="compact-only">
                  <PanelRight size={18} aria-hidden="true" />
                  <span>Details</span>
                </Button>
              }
            >
              <CharacterSummary />
            </Sheet>
          </div>
          <ConnectionStatus />
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
  const unscoped = destinations.map(({ segment }) =>
    createRoute({
      getParentRoute: () => root,
      path: segment ? `/${segment}` : "/",
      component: pages[segment],
    }),
  );
  const scoped = createRoute({
    getParentRoute: () => root,
    path: "/c/$campaignId",
  });
  const scopedPages = destinations.map(({ segment }) =>
    createRoute({
      getParentRoute: () => scoped,
      path: segment ? `/${segment}` : "/",
      component: pages[segment],
    }),
  );
  return createRouter({
    routeTree: root.addChildren([...unscoped, scoped.addChildren(scopedPages)]),
  });
}
export function App({
  transport = disconnectedTransport,
  onSessionEnded,
  onNewGame,
  onSwitchCampaign,
}: {
  transport?: PlayTransport;
  onSessionEnded?: (() => void) | undefined;
  onNewGame?: (() => void) | undefined;
  onSwitchCampaign?: (() => void) | undefined;
}) {
  const [client] = useState(() => new QueryClient());
  const [router] = useState(() => makeRouter());
  // Held stable so route components never re-render on a new callback identity.
  const [menu] = useState(() => ({ onNewGame, onSwitchCampaign }));
  return (
    <QueryClientProvider client={client}>
      <PlayProvider
        key={transport.principalId}
        transport={transport}
        onSessionEnded={onSessionEnded}
      >
        <CampaignMenu.Provider value={menu}>
          <RouterProvider router={router} />
        </CampaignMenu.Provider>
      </PlayProvider>
    </QueryClientProvider>
  );
}
