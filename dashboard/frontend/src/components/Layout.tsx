import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/charts", label: "Grafik" },
  { to: "/liquidations", label: "Likidasyon" },
  { to: "/history", label: "Geçmiş" },
  { to: "/settings", label: "Ayarlar" },
];

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0b0b0d] text-zinc-200">
      <header className="flex items-center justify-between border-b border-zinc-800/80 px-6 py-3">
        <div className="flex items-center gap-8">
          <span className="text-[11px] font-semibold tracking-[0.28em] text-zinc-500 uppercase">
            CryptoHub
          </span>
          <nav className="flex gap-5 text-sm">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) =>
                  isActive ? "text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}