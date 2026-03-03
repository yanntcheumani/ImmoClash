import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EVENTS } from "../lib/events";
import { apiUrl } from "../lib/runtime";
import { emitAck, ensureSocketConnected } from "../lib/socket";
import { saveSession } from "../lib/session";
import type { PriceMode, SessionData, SocketAckBase } from "../types";

interface RoomMutationAck extends SocketAckBase {
  room?: unknown;
  session?: SessionData;
}

export function LobbyPage() {
  const navigate = useNavigate();

  const [createNickname, setCreateNickname] = useState("");
  const [joinNickname, setJoinNickname] = useState("");
  const [roomCode, setRoomCode] = useState("");

  const [roundsCount, setRoundsCount] = useState(5);
  const [timerSeconds, setTimerSeconds] = useState(30);
  const [priceMode, setPriceMode] = useState<PriceMode>("rent");
  const [hintsEnabled, setHintsEnabled] = useState(true);
  const [searchQuery, setSearchQuery] = useState("Paris, France");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listingsCount, setListingsCount] = useState<number | null>(null);

  useEffect(() => {
    const loadCount = async () => {
      try {
        const response = await fetch(apiUrl("/api/admin/listings-count"));
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as { count: number };
        setListingsCount(payload.count);
      } catch {
        setListingsCount(null);
      }
    };

    void loadCount();
  }, []);

  const onCreateRoom = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      await ensureSocketConnected();
      const ack = await emitAck<RoomMutationAck>(EVENTS.ROOM_CREATE, {
        nickname: createNickname,
        config: {
          roundsCount,
          timerSeconds,
          priceMode,
          hintsEnabled,
          searchQuery,
        },
      });

      if (!ack.ok || !ack.session) {
        setError(ack.error ?? "Impossible de créer la room");
        return;
      }

      saveSession(ack.session);
      navigate(`/room/${ack.session.roomCode}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur réseau/socket");
    } finally {
      setBusy(false);
    }
  };

  const onJoinRoom = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      await ensureSocketConnected();
      const ack = await emitAck<RoomMutationAck>(EVENTS.ROOM_JOIN, {
        roomCode,
        nickname: joinNickname,
      });

      if (!ack.ok || !ack.session) {
        setError(ack.error ?? "Impossible de rejoindre la room");
        return;
      }

      saveSession(ack.session);
      navigate(`/room/${ack.session.roomCode}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur réseau/socket");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page lobby-page">
      <section className="panel panel-hero lobby-hero">
        <div className="hero-kicker">Mode Party Game</div>
        <h1>Devine le loyer mensuel</h1>
        <p>
          Crée une room, scrape des annonces en direct pour ta zone, puis compare les estimations en temps réel avec tes
          amis.
        </p>

        <div className="hero-stats">
          <div className="stat-pill">
            <span className="stat-label">Dataset cache</span>
            <strong>{listingsCount ?? "?"} annonces</strong>
          </div>
          <div className="stat-pill">
            <span className="stat-label">Source</span>
            <strong>Locations web uniquement</strong>
          </div>
          <div className="stat-pill">
            <span className="stat-label">Formats</span>
            <strong>Loyer ou €/m²</strong>
          </div>
        </div>
      </section>

      {error && <div className="banner error">{error}</div>}

      <section className="lobby-grid">
        <form className="panel form-panel" onSubmit={onCreateRoom}>
          <header className="panel-head">
            <h2>Créer une room</h2>
            <p>Tu définis les règles puis tu lances la partie quand tout le monde est prêt.</p>
          </header>

          <div className="field-grid">
            <label>
              Pseudo host
              <input
                value={createNickname}
                onChange={(e) => setCreateNickname(e.target.value)}
                required
                minLength={2}
                maxLength={20}
                placeholder="Ex: Sam"
              />
            </label>

            <label>
              Zone de scraping
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                required
                minLength={2}
                maxLength={80}
                placeholder="Ex: Paris, France"
              />
            </label>

            <label>
              Nombre de manches
              <input
                type="number"
                value={roundsCount}
                min={1}
                max={20}
                onChange={(e) => setRoundsCount(Number(e.target.value))}
              />
            </label>

            <label>
              Timer par manche (s)
              <input
                type="number"
                value={timerSeconds}
                min={10}
                max={120}
                onChange={(e) => setTimerSeconds(Number(e.target.value))}
              />
            </label>

            <label>
              Mode d'estimation
              <select value={priceMode} onChange={(e) => setPriceMode(e.target.value as PriceMode)}>
                <option value="rent">Loyer mensuel</option>
                <option value="sqm">€/m²</option>
              </select>
            </label>

            <label className="check-row">
              <input type="checkbox" checked={hintsEnabled} onChange={(e) => setHintsEnabled(e.target.checked)} />
              <span>Indices activés (surface/pièces/DPE) avec malus score</span>
            </label>
          </div>

          <button type="submit" disabled={busy}>
            {busy ? "Création..." : "Créer la room"}
          </button>
        </form>

        <div className="lobby-side-stack">
          <form className="panel form-panel" onSubmit={onJoinRoom}>
            <header className="panel-head">
              <h2>Rejoindre une room</h2>
              <p>Entre un code room et joue en multijoueur ou en solo.</p>
            </header>

            <div className="field-grid compact">
              <label>
                Code room
                <input
                  value={roomCode}
                  onChange={(e) => setRoomCode(e.target.value.toUpperCase())}
                  required
                  minLength={4}
                  maxLength={10}
                  placeholder="AB12CD"
                />
              </label>

              <label>
                Pseudo joueur
                <input
                  value={joinNickname}
                  onChange={(e) => setJoinNickname(e.target.value)}
                  required
                  minLength={2}
                  maxLength={20}
                  placeholder="Ex: Alex"
                />
              </label>
            </div>

            <button type="submit" disabled={busy}>
              {busy ? "Connexion..." : "Rejoindre"}
            </button>
          </form>

          <section className="panel tips-panel" aria-label="Astuces de jeu">
            <h3>Règles rapides</h3>
            <ul>
              <li>Un seul envoi de prix par manche.</li>
              <li>Le serveur gère le timer et verrouille les réponses.</li>
              <li>Les prix réels sont révélés uniquement en fin de manche.</li>
              <li>L'hôte peut relancer une partie terminée.</li>
            </ul>
          </section>
        </div>
      </section>
    </main>
  );
}
