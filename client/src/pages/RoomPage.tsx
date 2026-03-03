import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Leaderboard } from "../components/Leaderboard";
import { ListingCarousel } from "../components/ListingCarousel";
import { MiniMap } from "../components/MiniMap";
import { EVENTS } from "../lib/events";
import { emitAck, ensureSocketConnected, socket } from "../lib/socket";
import { clearSession, getSession, saveSession } from "../lib/session";
import type { GameFinishedPayload, RoomStatePayload, RoundResultPayload, SessionData, SocketAckBase } from "../types";

interface ReconnectAck extends SocketAckBase {
  room?: RoomStatePayload;
  session?: SessionData;
}

interface StartGameAck extends SocketAckBase {
  started?: boolean;
}

interface NextRoundAck extends SocketAckBase {
  started?: boolean;
}

interface GuessAck extends SocketAckBase {
  accepted?: boolean;
}

interface HintAck extends SocketAckBase {
  hint?: string;
  value?: string | number;
  currentPenalty?: number;
}

const HINT_LABELS: Record<string, string> = {
  surface: "Surface",
  rooms: "Pièces",
  dpe: "DPE",
};

const PHASE_LABELS: Record<RoomStatePayload["phase"], string> = {
  lobby: "Lobby",
  in_round: "Manche en cours",
  reveal: "Révélation",
  finished: "Terminé",
};

export function RoomPage() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();

  const roomCode = (code ?? "").toUpperCase();

  const [session, setSession] = useState<SessionData | null>(null);
  const [room, setRoom] = useState<RoomStatePayload | null>(null);
  const [roundResult, setRoundResult] = useState<RoundResultPayload | null>(null);
  const [finished, setFinished] = useState<GameFinishedPayload | null>(null);

  const [guessInput, setGuessInput] = useState("");
  const [hintValues, setHintValues] = useState<Record<string, string | number>>({});
  const [hintPenalty, setHintPenalty] = useState<number>(0);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const [showSidePanel, setShowSidePanel] = useState(false);
  const [dismissedRevealRound, setDismissedRevealRound] = useState<number | null>(null);

  useEffect(() => {
    if (!roomCode) {
      navigate("/");
      return;
    }

    let mounted = true;

    const bootstrap = async () => {
      const localSession = getSession(roomCode);
      if (!localSession) {
        setError("Session introuvable. Reviens au lobby pour rejoindre la room.");
        return;
      }

      setSession(localSession);

      try {
        await ensureSocketConnected();
        const ack = await emitAck<ReconnectAck>(EVENTS.PLAYER_RECONNECT, {
          roomCode,
          playerId: localSession.playerId,
        });

        if (!ack.ok || !ack.room || !ack.session) {
          setError(ack.error ?? "Impossible de se reconnecter à la room.");
          return;
        }

        if (!mounted) {
          return;
        }

        saveSession(ack.session);
        setSession(ack.session);
        setRoom(ack.room);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Erreur socket.");
      }
    };

    const onRoomState = (payload: RoomStatePayload) => {
      if (payload.code !== roomCode) {
        return;
      }

      if (payload.phase !== "finished") {
        setFinished(null);
      }

      setRoom(payload);
    };

    const onRoundResult = (payload: RoundResultPayload) => {
      if (payload.roomCode !== roomCode) {
        return;
      }
      setRemainingSeconds(0);
      setRoundResult(payload);
    };

    const onFinished = (payload: GameFinishedPayload) => {
      if (payload.roomCode !== roomCode) {
        return;
      }
      setFinished(payload);
    };

    socket.on(EVENTS.ROOM_STATE, onRoomState);
    socket.on(EVENTS.ROUND_RESULT, onRoundResult);
    socket.on(EVENTS.GAME_FINISHED, onFinished);

    void bootstrap();

    return () => {
      mounted = false;
      socket.off(EVENTS.ROOM_STATE, onRoomState);
      socket.off(EVENTS.ROUND_RESULT, onRoundResult);
      socket.off(EVENTS.GAME_FINISHED, onFinished);
    };
  }, [navigate, roomCode]);

  useEffect(() => {
    setRoundResult(null);
    setGuessInput("");
    setHintValues({});
    setHintPenalty(0);
    setDismissedRevealRound(null);
  }, [room?.currentRound?.roundIndex]);

  useEffect(() => {
    if (!room?.currentRound) {
      setRemainingSeconds(0);
      return;
    }

    const timer = setInterval(() => {
      const ms = Math.max(0, room.currentRound!.endsAtMs - Date.now());
      setRemainingSeconds(Math.ceil(ms / 1000));
    }, 200);

    return () => clearInterval(timer);
  }, [room?.currentRound?.endsAtMs]);

  const me = useMemo(() => {
    if (!room || !session) {
      return null;
    }
    return room.players.find((player) => player.id === session.playerId) ?? null;
  }, [room, session]);

  const myGuessSubmitted =
    Boolean(session && room?.currentRound?.submittedPlayerIds.includes(session.playerId)) ||
    Boolean(room?.currentRound?.myGuessSubmitted);

  const isHost = Boolean(me?.isHost);
  const canStart = Boolean(isHost && room && (room.phase === "lobby" || room.phase === "finished"));
  const canStartNextRound = Boolean(isHost && room?.phase === "reveal" && room?.canHostStartNextRound);
  const currentPhaseLabel = room ? PHASE_LABELS[room.phase] : "...";

  const progressPct =
    room && room.totalRounds > 0 ? Math.min(100, Math.round((room.roundIndex / room.totalRounds) * 100)) : 0;

  const activityLines = useMemo(() => {
    if (!room) {
      return ["Connexion à la room..."];
    }

    const lines: string[] = [];

    if (room.phase === "in_round" && room.currentRound) {
      const submitted = room.currentRound.submittedPlayerIds.length;
      lines.push(`${submitted}/${room.players.length} estimation(s) reçue(s).`);
      lines.push(`Timer serveur: ${remainingSeconds}s restantes.`);

      for (const player of room.players.slice(0, 4)) {
        const submittedGuess = room.currentRound.submittedPlayerIds.includes(player.id);
        if (submittedGuess) {
          lines.push(`${player.nickname} a validé son estimation.`);
          continue;
        }
        lines.push(`${player.nickname} ${player.connected ? "réfléchit..." : "est déconnecté."}`);
      }
    } else if (room.phase === "reveal" && roundResult) {
      lines.push(`Prix réel révélé: ${Math.round(roundResult.truePrice).toLocaleString("fr-FR")} €.`);
      const best = [...roundResult.results].sort((a, b) => b.roundScore - a.roundScore)[0];
      if (best) {
        lines.push(`${best.nickname} prend la manche (+${best.roundScore}).`);
      }
      lines.push("L'hôte peut lancer la manche suivante.");
    } else if (room.phase === "finished") {
      const winner = finished?.leaderboard[0] ?? room.players[0];
      if (winner) {
        lines.push(`Partie terminée, gagnant: ${winner.nickname}.`);
      }
      lines.push("L'hôte peut relancer une nouvelle partie.");
    } else {
      lines.push("Room prête. L'hôte peut démarrer la partie.");
    }

    return lines.slice(0, 7);
  }, [finished?.leaderboard, remainingSeconds, room, roundResult]);

  const myRoundRow = useMemo(() => {
    if (!roundResult || !session) {
      return null;
    }
    return roundResult.results.find((row) => row.playerId === session.playerId) ?? null;
  }, [roundResult, session]);

  const revealModalOpen = Boolean(roundResult && dismissedRevealRound !== roundResult.roundIndex);

  useEffect(() => {
    if (!revealModalOpen) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [revealModalOpen]);

  const onStartGame = async () => {
    setBusy(true);
    setError(null);
    try {
      const ack = await emitAck<StartGameAck>(EVENTS.GAME_START, {});
      if (!ack.ok) {
        setError(ack.error ?? "Impossible de lancer la partie");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur socket");
    } finally {
      setBusy(false);
    }
  };

  const onStartNextRound = async () => {
    setBusy(true);
    setError(null);
    try {
      const ack = await emitAck<NextRoundAck>(EVENTS.ROUND_NEXT, {});
      if (!ack.ok) {
        setError(ack.error ?? "Impossible de lancer la manche suivante");
      } else if (roundResult) {
        setDismissedRevealRound(roundResult.roundIndex);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur socket");
    } finally {
      setBusy(false);
    }
  };

  const onSubmitGuess = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const guess = Number(guessInput);
      const ack = await emitAck<GuessAck>(EVENTS.ROUND_GUESS_SUBMIT, { guess });
      if (!ack.ok) {
        setError(ack.error ?? "Soumission refusée");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur socket");
    } finally {
      setBusy(false);
    }
  };

  const onRequestHint = async (hint: "surface" | "rooms" | "dpe") => {
    setBusy(true);
    setError(null);

    try {
      const ack = await emitAck<HintAck>(EVENTS.ROUND_HINT_REQUEST, { hint });
      if (!ack.ok) {
        setError(ack.error ?? "Indice indisponible");
        return;
      }

      if (ack.hint && ack.value !== undefined) {
        setHintValues((prev) => ({ ...prev, [ack.hint!]: ack.value! }));
      }
      setHintPenalty((prev) => ack.currentPenalty ?? prev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur socket");
    } finally {
      setBusy(false);
    }
  };

  const onLeaveRoom = () => {
    if (roomCode) {
      clearSession(roomCode);
    }
    navigate("/");
  };

  const onDismissReveal = () => {
    if (!roundResult) {
      return;
    }
    setDismissedRevealRound(roundResult.roundIndex);
  };

  if (!roomCode) {
    return null;
  }

  return (
    <main className={`page room-page ${room?.phase === "in_round" && room?.currentRound ? "with-sticky-guess" : ""}`}>
      <header className="panel room-topbar">
        <div className="room-title-wrap">
          <h2>Lobby #{roomCode}</h2>
          <div className="phase-row">
            <span className={`phase-pill phase-${room?.phase ?? "lobby"}`}>{currentPhaseLabel}</span>
            <span className="small muted">
              Manche {room?.roundIndex ?? 0}/{room?.totalRounds ?? "?"}
            </span>
          </div>
          <div className="progress-track" aria-hidden="true">
            <div className="progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
        </div>

        <div className="topbar-actions">
          {canStart && (
            <button type="button" onClick={onStartGame} disabled={busy}>
              {room?.phase === "finished" ? "Relancer la partie" : "Lancer la partie"}
            </button>
          )}
          {canStartNextRound && (
            <button type="button" onClick={onStartNextRound} disabled={busy}>
              Manche suivante
            </button>
          )}
          <button type="button" className="ghost" onClick={onLeaveRoom}>
            Quitter
          </button>
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}
      {finished && <div className="banner success">Partie terminée.</div>}
      <button type="button" className="mobile-sidebar-toggle ghost" onClick={() => setShowSidePanel((prev) => !prev)}>
        {showSidePanel ? "Masquer classement" : "Afficher classement & activité"}
      </button>

      <section className="room-layout">
        <aside className={`room-sidebar ${showSidePanel ? "open" : ""}`}>
          <Leaderboard players={room?.players ?? []} />

          <section className="panel activity-panel">
            <div className="panel-head">
              <h3>Live Activity</h3>
              <span className="live-chip">Live</span>
            </div>
            <div className="activity-feed">
              {activityLines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </section>
        </aside>

        <section className="room-main">
          {room?.currentRound ? (
            <article className="panel stage-panel">
              <div className="map-top-row">
                <span className="map-chip">{room.currentRound.listing.city}</span>
                <span className="map-chip">{room.currentRound.listing.country}</span>
                {hintValues.surface !== undefined ? <span className="map-chip"> {hintValues.surface} m²</span> : null}kdfjdlj
                bonjour
              </div>

              <MiniMap lat={room.currentRound.listing.lat} lng={room.currentRound.listing.lng} />

              <div className="stage-info">
                <div>
                  <h3>{room.currentRound.listing.title}</h3>
                  <p className="muted">
                    {room.currentRound.listing.address || `${room.currentRound.listing.city}, ${room.currentRound.listing.country}`}
                  </p>
                </div>
                <div className={`timer-chip ${remainingSeconds <= 5 ? "danger" : ""}`}>⏱ {remainingSeconds}s</div>
              </div>

              <ListingCarousel imageUrls={room.currentRound.listing.imageUrls} title={room.currentRound.listing.title} />

              {room.config.hintsEnabled && (
                <section className="hint-panel">
                  <div className="hint-head">
                    <h4>Indices optionnels</h4>
                    <span>Malus total: {hintPenalty}</span>
                  </div>
                  <div className="hint-actions">
                    {(["surface", "rooms", "dpe"] as const).map((hintKey) => (
                      <button
                        type="button"
                        key={hintKey}
                        className="hint-btn"
                        disabled={!room.currentRound?.listing.availableHints[hintKey] || busy}
                        onClick={() => onRequestHint(hintKey)}
                      >
                        {HINT_LABELS[hintKey]}
                      </button>
                    ))}
                  </div>
                  <div className="hint-values">
                    {Object.keys(hintValues).length === 0 && <p>Aucun indice débloqué.</p>}
                    {Object.entries(hintValues).map(([key, value]) => (
                      <p key={key}>
                        {HINT_LABELS[key]}: <strong>{String(value)}</strong>
                      </p>
                    ))}
                  </div>
                </section>
              )}
            </article>
          ) : (
            <div className="panel waiting-panel">En attente de la prochaine manche...</div>
          )}

          {roundResult && (
            <section className="panel results-panel">
              <div className="panel-head">
                <h3>Résultats manche {roundResult.roundIndex}</h3>
                <span className="small muted">
                  Prix réel: {Math.round(roundResult.truePrice).toLocaleString("fr-FR")} €
                </span>
              </div>
              <p className="small muted">
                {roundResult.listing.title} · {roundResult.listing.city}, {roundResult.listing.country}
              </p>
              {roundResult.listing.sourceUrl && (
                <p className="small">
                  <a href={roundResult.listing.sourceUrl} target="_blank" rel="noreferrer">
                    Voir l'annonce source
                  </a>
                </p>
              )}

              <table className="leaderboard-table">
                <thead>
                  <tr>
                    <th>Joueur</th>
                    <th>Estimation</th>
                    <th>Erreur</th>
                    <th>Score manche</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {roundResult.results.map((row) => (
                    <tr key={row.playerId}>
                      <td>{row.nickname}</td>
                      <td>{row.guess ? `${Math.round(row.guess).toLocaleString("fr-FR")} €` : "-"}</td>
                      <td>{row.errorPct !== null ? `${(row.errorPct * 100).toFixed(1)}%` : "-"}</td>
                      <td>{row.roundScore}</td>
                      <td>{row.totalScore}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </section>
      </section>

      {room?.phase === "in_round" && room.currentRound && (
        <form onSubmit={onSubmitGuess} className="sticky-guess-wrap">
          <div className="sticky-guess-bar">
            <div className="sticky-guess-top">
              <span className="sticky-guess-title">Estime le loyer mensuel</span>
              <span className={`sticky-timer-chip ${remainingSeconds <= 5 ? "danger" : ""}`}>⏱ {remainingSeconds}s</span>
            </div>
            <div className="sticky-guess-row">
              <div className="sticky-input-wrap">
                <span>€</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={guessInput}
                  onChange={(event) => setGuessInput(event.target.value)}
                  disabled={myGuessSubmitted || remainingSeconds <= 0}
                  required
                  placeholder="1850"
                />
              </div>
              <button type="submit" disabled={myGuessSubmitted || remainingSeconds <= 0 || busy}>
                {myGuessSubmitted ? "Déjà envoyé" : "Valider"}
              </button>
            </div>
          </div>
        </form>
      )}

      {roundResult && revealModalOpen && (
        <div className="reveal-overlay" onClick={onDismissReveal}>
          <section className="panel reveal-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className="reveal-head">
              <h3>Prix du logement</h3>
              <button type="button" className="ghost" onClick={onDismissReveal}>
                Fermer
              </button>
            </div>
            <p className="reveal-price">{Math.round(roundResult.truePrice).toLocaleString("fr-FR")} €</p>
            <p className="small muted">
              {roundResult.listing.title} · {roundResult.listing.city}, {roundResult.listing.country}
            </p>

            {myRoundRow && (
              <div className="reveal-stats">
                <div>
                  <span>Ton estimation</span>
                  <strong>{myRoundRow.guess !== null ? `${Math.round(myRoundRow.guess).toLocaleString("fr-FR")} €` : "-"}</strong>
                </div>
                <div>
                  <span>Erreur</span>
                  <strong>{myRoundRow.errorPct !== null ? `${(myRoundRow.errorPct * 100).toFixed(1)}%` : "-"}</strong>
                </div>
                <div>
                  <span>Score manche</span>
                  <strong>+{myRoundRow.roundScore}</strong>
                </div>
              </div>
            )}

            <div className="reveal-actions">
              {canStartNextRound ? (
                <button type="button" onClick={onStartNextRound} disabled={busy}>
                  Manche suivante
                </button>
              ) : (
                <span className="small muted">En attente de l'hôte pour la suite.</span>
              )}
              <button type="button" className="ghost" onClick={onDismissReveal}>
                Voir le détail
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
