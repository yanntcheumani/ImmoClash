export type PriceMode = "total" | "rent" | "sqm";

export interface SessionData {
  roomCode: string;
  playerId: string;
  nickname: string;
  isHost: boolean;
}

export interface SocketAckBase {
  ok: boolean;
  error?: string;
}

export interface RoomConfig {
  roundsCount: number;
  timerSeconds: number;
  priceMode: PriceMode;
  hintsEnabled: boolean;
  searchQuery: string;
}

export interface PlayerState {
  id: string;
  nickname: string;
  score: number;
  connected: boolean;
  isHost: boolean;
}

export interface ListingPayload {
  listingId: string;
  title: string;
  city: string;
  country: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  imageUrls: string[];
  availableHints: {
    surface: boolean;
    rooms: boolean;
    dpe: boolean;
  };
}

export interface CurrentRoundPayload {
  roundIndex: number;
  endsAtMs: number;
  locked: boolean;
  listing: ListingPayload;
  submittedPlayerIds: string[];
  hintCounts: Record<string, number>;
  myGuessSubmitted?: boolean;
  myHints?: string[];
}

export interface RoomStatePayload {
  code: string;
  phase: "lobby" | "in_round" | "reveal" | "finished";
  hostPlayerId: string;
  config: RoomConfig;
  roundIndex: number;
  totalRounds: number;
  canHostStartNextRound?: boolean;
  players: PlayerState[];
  currentRound?: CurrentRoundPayload;
}

export interface RoundResultRow {
  playerId: string;
  nickname: string;
  guess: number | null;
  errorPct: number | null;
  baseScore: number;
  hintPenalty: number;
  roundScore: number;
  totalScore: number;
  hintsUsed: string[];
}

export interface RoundResultPayload {
  roomCode: string;
  roundIndex: number;
  truePrice: number;
  priceMode: PriceMode;
  listing: {
    listingId: string;
    title: string;
    city: string;
    country: string;
    sourceUrl?: string | null;
  };
  results: RoundResultRow[];
  leaderboard: PlayerState[];
}

export interface GameFinishedPayload {
  roomCode: string;
  leaderboard: PlayerState[];
}
