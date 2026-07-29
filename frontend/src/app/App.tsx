import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";

const ChatbotPage = lazy(() =>
  import("@/features/chatbot/ChatbotPage").then((module) => ({
    default: module.ChatbotPage,
  })),
);
const DiagnosticPage = lazy(() =>
  import("@/features/diagnostics/DiagnosticPage").then((module) => ({
    default: module.DiagnosticPage,
  })),
);
const LibraryPage = lazy(() =>
  import("@/features/library/LibraryPage").then((module) => ({
    default: module.LibraryPage,
  })),
);
const PilotFeedbackPage = lazy(() =>
  import("@/features/pilot-feedback/PilotFeedbackPage").then((module) => ({
    default: module.PilotFeedbackPage,
  })),
);
const SettingsPage = lazy(() =>
  import("@/features/settings/SettingsPage").then((module) => ({
    default: module.SettingsPage,
  })),
);
const SynthesisPage = lazy(() =>
  import("@/features/synthesis/SynthesisPage").then((module) => ({
    default: module.SynthesisPage,
  })),
);

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route element={<LazyPage page={<ChatbotPage />} />} index />
        <Route element={<LazyPage page={<DiagnosticPage />} />} path="diagnostic" />
        <Route element={<LegacyCorpusRedirect />} path="corpus" />
        <Route element={<LazyPage page={<LibraryPage />} />} path="bibliotheque" />
        <Route element={<LazyPage page={<PilotFeedbackPage />} />} path="retours-pilote" />
        <Route element={<LazyPage page={<SynthesisPage />} />} path="syntheses" />
        <Route element={<LazyPage page={<SettingsPage />} />} path="parametres" />
        <Route element={<Navigate replace to="/" />} path="*" />
      </Route>
    </Routes>
  );
}

function LazyPage({ page }: { page: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div
          aria-live="polite"
          className="mx-auto max-w-7xl px-4 py-16 text-sm text-slate-500 sm:px-6"
          role="status"
        >
          Chargement de la page…
        </div>
      }
    >
      {page}
    </Suspense>
  );
}

function LegacyCorpusRedirect() {
  const location = useLocation();
  const search = new URLSearchParams(location.search);
  search.set("section", "pdf");
  return <Navigate replace to={`/bibliotheque?${search.toString()}`} />;
}
