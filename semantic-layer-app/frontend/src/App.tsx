import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { cx } from "./components/ui";
import ChatPage from "./pages/ChatPage";
import OnboardWizard from "./pages/OnboardWizard";
import ProvidersPage from "./pages/ProvidersPage";
import ReviewPage from "./pages/ReviewPage";
import SettingsPage from "./pages/SettingsPage";
import SourceDetailPage from "./pages/SourceDetailPage";
import SourcesPage from "./pages/SourcesPage";
import UsagePage from "./pages/UsagePage";

const NAV = [
  { to: "/chat", label: "Chat", icon: "💬" },
  { to: "/sources", label: "Data sources", icon: "🗂" },
  { to: "/providers", label: "Providers", icon: "🔌" },
  { to: "/usage", label: "Usage", icon: "📈" },
  { to: "/settings", label: "Settings", icon: "⚙️" },
];

function HealthDot() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    const check = () =>
      fetch("/health")
        .then((r) => setOk(r.ok))
        .catch(() => setOk(false));
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
      <span
        className={cx(
          "h-2 w-2 rounded-full",
          ok === null ? "bg-slate-300" : ok ? "bg-emerald-500" : "bg-rose-500",
        )}
      />
      {ok === false ? "Backend unreachable" : "Backend connected"}
    </div>
  );
}

export default function App() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-row items-center gap-1 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900 md:sticky md:top-0 md:h-screen md:w-56 md:flex-col md:items-stretch md:border-b-0 md:border-r md:px-3 md:py-5">
        <div className="mr-3 px-2 md:mb-6 md:mr-0">
          <div className="text-sm font-bold tracking-tight text-indigo-700 dark:text-indigo-300">
            Semantic Layer AI
          </div>
          <div className="hidden text-xs text-slate-500 md:block">Chat with your data, locally</div>
        </div>
        <nav className="flex flex-1 gap-1 overflow-x-auto md:flex-col" aria-label="Main">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm font-medium",
                  isActive
                    ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
                )
              }
            >
              <span aria-hidden>{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="hidden px-2 md:block">
          <HealthDot />
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:sessionId" element={<ChatPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/sources/new" element={<OnboardWizard />} />
          <Route path="/sources/:id/onboard" element={<OnboardWizard />} />
          <Route path="/sources/:id" element={<SourceDetailPage />} />
          <Route path="/review/:versionId" element={<ReviewPage />} />
          <Route path="/providers" element={<ProvidersPage />} />
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </main>
    </div>
  );
}
