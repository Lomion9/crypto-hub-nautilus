import { useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import Layout from "./components/Layout";
import { fetchSettings, type Settings as SettingsData } from "./api";
import Charts from "./pages/Charts";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Liquidations from "./pages/Liquidations";
import Settings from "./pages/Settings";
import Setup from "./pages/Setup";

export default function App() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState("");
  const navigate = useNavigate();

  async function load() {
    try {
      const data = await fetchSettings();
      setSettings(data);
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Yükleme hatası");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-red-400">
        {loadError}
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-zinc-500">
        Yükleniyor...
      </div>
    );
  }

  const setup = (
    <Setup
      initialToken={settings.telegram_bot_token}
      initialChatId={settings.telegram_chat_id}
      onSaved={async () => {
        await load();
        navigate("/");
      }}
    />
  );

  function guarded(page: ReactNode) {
    if (!settings!.configured) return setup;
    return <Layout>{page}</Layout>;
  }

  return (
    <Routes>
      <Route path="/setup" element={setup} />
      <Route path="/" element={guarded(<Dashboard />)} />
      <Route path="/charts" element={guarded(<Charts />)} />
      <Route path="/liquidations" element={guarded(<Liquidations />)} />
      <Route path="/history" element={guarded(<History />)} />
      <Route
        path="/settings"
        element={guarded(
          <Settings
            initialToken={settings.telegram_bot_token}
            initialChatId={settings.telegram_chat_id}
            onSaved={async () => {
              await load();
            }}
          />,
        )}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
