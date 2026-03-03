import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { LobbyPage } from "./pages/LobbyPage";
import { RoomPage } from "./pages/RoomPage";

function Topbar() {
  const location = useLocation();
  const inRoom = location.pathname.startsWith("/room/");

  return (
    <header className="topbar">
      <div className="topbar-brand">
        <span className="brand-mark" aria-hidden="true">
          ▦
        </span>
        <span>ImmoClash Pro</span>
      </div>

      <nav className="topbar-nav" aria-label="Navigation principale">
        <NavLink to="/" end className={({ isActive }) => `topbar-link ${isActive ? "active" : ""}`}>
          Lobby
        </NavLink>
        <span className={`topbar-link ${inRoom ? "active" : ""}`}>Partie</span>
      </nav>

      <div className="topbar-icons" aria-hidden="true">
        <span className="icon-dot">●</span>
        <span className="icon-dot">●</span>
      </div>
    </header>
  );
}

export function App() {
  return (
    <div className="app-shell">
      <Topbar />
      <Routes>
        <Route path="/" element={<LobbyPage />} />
        <Route path="/room/:code" element={<RoomPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
