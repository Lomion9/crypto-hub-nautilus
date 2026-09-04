import Setup from "./Setup";

type Props = {
  initialToken?: string;
  initialChatId?: string;
  onSaved: () => void;
};

export default function Settings({ initialToken = "", initialChatId = "", onSaved }: Props) {
  return (
    <div className="px-6 py-8">
      <h1 className="mb-1 text-lg font-semibold text-zinc-100">Ayarlar</h1>
      <p className="mb-6 text-sm text-zinc-500">
        Telegram bilgileri settings tablosuna yazılır. Bot ortam değişkeni yoksa buradaki
        değerleri kullanır.
      </p>
      <Setup
        embedded
        initialToken={initialToken}
        initialChatId={initialChatId}
        onSaved={onSaved}
      />
    </div>
  );
}
