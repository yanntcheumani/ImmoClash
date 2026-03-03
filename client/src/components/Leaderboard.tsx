import type { PlayerState } from "../types";

interface Props {
  players: PlayerState[];
}

function initials(value: string): string {
  const chunks = value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((chunk) => chunk[0]?.toUpperCase() ?? "");
  return chunks.join("") || "?";
}

export function Leaderboard({ players }: Props) {
  return (
    <section className="panel leaderboard-panel">
      <div className="panel-head leaderboard-head">
        <h3>Lobby Players</h3>
        <span className="live-chip">Live</span>
      </div>

      <div className="player-list">
        {players.map((player, index) => (
          <article className="player-row" key={player.id}>
            <div className="player-main">
              <div className="avatar">{initials(player.nickname)}</div>
              <div>
                <div className="player-name">
                  {player.nickname}
                  {player.isHost ? <span className="host-tag">Host</span> : null}
                </div>
                <div className="player-status">{player.connected ? "Connected" : "Disconnected"}</div>
              </div>
            </div>
            <div className="player-score-wrap">
              <span className="rank-chip">#{index + 1}</span>
              <strong className="player-score">{player.score}</strong>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
