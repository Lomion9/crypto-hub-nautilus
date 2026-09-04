import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import Layout from "./components/Layout";
import { fetchSettings, type Settings } from "./api";
import Charts from "./pages/Charts";
import Dashboard from "./pages/Dashboard";
import Setup from "./pages/Setup";

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
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

  return (
    <Routes>
      <Route path="/setup" element={setup} />
      <Route
        path="/"
        element={
          settings.configured ? (
            <Layout>
              <Dashboard />
            </Layout>
          ) : (
            setup
          )
        }
      />
      <Route
        path="/charts"
        element={
          settings.configured ? (
            <Layout>
              <Charts />
            </Layout>
          ) : (
            setup
          )
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}