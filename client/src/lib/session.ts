import type { SessionData } from "../types";

const keyForRoom = (roomCode: string): string => `immo.session.${roomCode.toUpperCase()}`;

export function saveSession(session: SessionData): void {
  localStorage.setItem(keyForRoom(session.roomCode), JSON.stringify(session));
}

export function getSession(roomCode: string): SessionData | null {
  const raw = localStorage.getItem(keyForRoom(roomCode));
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as SessionData;
  } catch {
    return null;
  }
}

export function clearSession(roomCode: string): void {
  localStorage.removeItem(keyForRoom(roomCode));
}
