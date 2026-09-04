import { FormEvent, useState } from "react";
import { saveSettings } from "../api";

type Props = {
  initialToken?: string;
  initialChatId?: string;
  onSaved: () => void;
};

export default function Setup({ initialToken = "", initialChatId = "", onSaved }: Props) {
  const [token, setToken] = useState(initialToken);
  const [chatId, setChatId] = useState(initialChatId);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      await saveSettings({
        telegram_bot_token: token.trim(),
        telegram_chat_id: chatId.trim(),
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kayıt başarısız");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-950/80 p-8 shadow-xl"
      >
        <p className="mb-1 text-xs font-medium tracking-[0.2em] text-zinc-500 uppercase">
          CryptoHub
        </p>
        <h1 className="mb-2 text-xl font-semibold text-zinc-100">Kurulum</h1>
        <p className="mb-6 text-sm text-zinc-400">
          Telegram bildirimleri için bot token ve chat ID girin. Bu bilgiler
          veritabanındaki settings tablosuna kaydedilir.
        </p>

        <label className="mb-1 block text-xs text-zinc-400">Bot Token</label>
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="mb-4 w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
          placeholder="123456:ABC..."
          required
        />

        <label className="mb-1 block text-xs text-zinc-400">Chat ID</label>
        <input
          type="text"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          className="mb-6 w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500"
          placeholder="-100..."
          required
        />

        {error ? <p className="mb-4 text-sm text-red-400">{error}</p> : null}

        <button
          type="submit"
          disabled={saving}
          className="w-full rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-950 disabled:opacity-50"
        >
          {saving ? "Kaydediliyor..." : "Kaydet ve devam et"}
        </button>
      </form>
    </div>
  );
}
