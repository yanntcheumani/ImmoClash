from __future__ import annotations

import asyncio
import random
import string
import time
import uuid
from pathlib import Path
from typing import Any

import socketio

from . import events
from .config import SETTINGS
from .db import get_listing_by_id, get_random_listings_from_ids
from .models import HINT_KEYS, PRICE_MODES, Guess, Player, Room, RoomConfig, RoundState
from .rules import compute_round_score, true_price_for_mode
from .scraper import scrape_and_store_live_listings


class RoomManager:
    def __init__(self, db_path: Path, hint_penalty: int, inter_round_delay_seconds: int):
        self.db_path = db_path
        self.hint_penalty = hint_penalty
        self.inter_round_delay_seconds = inter_round_delay_seconds
        self.rooms: dict[str, Room] = {}
        self.sid_index: dict[str, tuple[str, str]] = {}
        # one timer task per room (current round)
        self.room_tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.sio: socketio.AsyncServer | None = None

    def bind_socket_server(self, sio: socketio.AsyncServer) -> None:
        self.sio = sio

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _generate_room_code(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(random.choice(alphabet) for _ in range(6))
            if code not in self.rooms:
                return code

    def _validate_config(self, payload: dict[str, Any]) -> RoomConfig:
        rounds_count = int(payload.get("roundsCount", 5))
        timer_seconds = int(payload.get("timerSeconds", 25))
        price_mode = payload.get("priceMode", "total")
        hints_enabled = bool(payload.get("hintsEnabled", True))
        search_query = str(payload.get("searchQuery", SETTINGS.default_search_query)).strip()

        if rounds_count < 1 or rounds_count > 20:
            raise ValueError("Le nombre de manches doit être entre 1 et 20.")
        if timer_seconds < 10 or timer_seconds > 120:
            raise ValueError("Le timer doit être entre 10s et 120s.")
        if price_mode not in PRICE_MODES:
            raise ValueError("Mode de prix invalide.")
        if len(search_query) < 2 or len(search_query) > 80:
            raise ValueError("La recherche internet doit contenir entre 2 et 80 caractères.")

        return RoomConfig(
            rounds_count=rounds_count,
            timer_seconds=timer_seconds,
            price_mode=price_mode,
            hints_enabled=hints_enabled,
            search_query=search_query,
        )

    def _player_payload(self, player: Player, is_host: bool) -> dict[str, Any]:
        return {
            "id": player.id,
            "nickname": player.nickname,
            "score": player.score,
            "connected": player.connected,
            "isHost": is_host,
        }

    def _sorted_players_payload(self, room: Room) -> list[dict[str, Any]]:
        return [
            self._player_payload(player, player.id == room.host_player_id)
            for player in sorted(room.players.values(), key=lambda p: (-p.score, p.nickname.lower()))
        ]

    def _room_snapshot(self, room: Room, viewer_player_id: str | None = None) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "code": room.code,
            "phase": room.phase,
            "hostPlayerId": room.host_player_id,
            "config": {
                "roundsCount": room.config.rounds_count,
                "timerSeconds": room.config.timer_seconds,
                "priceMode": room.config.price_mode,
                "hintsEnabled": room.config.hints_enabled,
                "searchQuery": room.config.search_query,
            },
            "roundIndex": room.round_index,
            "players": self._sorted_players_payload(room),
            "totalRounds": room.config.rounds_count,
            "canHostStartNextRound": room.phase == "reveal" and room.round_index < len(room.listing_ids),
        }

        if room.current_round:
            current = {
                "roundIndex": room.current_round.index,
                "endsAtMs": room.current_round.ends_at_ms,
                "locked": room.current_round.locked,
                "listing": room.current_round.listing.as_round_payload(),
                "submittedPlayerIds": sorted(room.current_round.guesses.keys()),
                "hintCounts": {pid: len(hints) for pid, hints in room.current_round.hints_used.items()},
            }
            if viewer_player_id:
                current["myGuessSubmitted"] = viewer_player_id in room.current_round.guesses
                current["myHints"] = sorted(room.current_round.hints_used.get(viewer_player_id, set()))
            snapshot["currentRound"] = current

        return snapshot

    def _identity_from_sid(self, sid: str) -> tuple[Room, Player]:
        identity = self.sid_index.get(sid)
        if not identity:
            raise ValueError("Session non reconnue.")
        room_code, player_id = identity
        room = self.rooms.get(room_code)
        if not room:
            raise ValueError("Room introuvable.")
        player = room.players.get(player_id)
        if not player:
            raise ValueError("Joueur introuvable.")
        return room, player

    async def create_room(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        nickname = str(payload.get("nickname", "")).strip()
        if len(nickname) < 2 or len(nickname) > 20:
            raise ValueError("Le pseudo doit contenir entre 2 et 20 caractères.")

        config = self._validate_config(payload.get("config", {}))

        async with self.lock:
            room_code = self._generate_room_code()
            host_id = str(uuid.uuid4())
            host = Player(id=host_id, nickname=nickname, sid=sid)
            room = Room(code=room_code, host_player_id=host_id, config=config, players={host_id: host})
            self.rooms[room_code] = room
            self.sid_index[sid] = (room_code, host_id)
            snapshot = self._room_snapshot(room, viewer_player_id=host_id)

        return {
            "room": snapshot,
            "session": {
                "roomCode": room_code,
                "playerId": host_id,
                "nickname": nickname,
                "isHost": True,
            },
        }

    async def join_room(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        room_code = str(payload.get("roomCode", "")).strip().upper()
        nickname = str(payload.get("nickname", "")).strip()

        if not room_code:
            raise ValueError("Code room manquant.")
        if len(nickname) < 2 or len(nickname) > 20:
            raise ValueError("Le pseudo doit contenir entre 2 et 20 caractères.")

        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                raise ValueError("Room introuvable.")
            if room.phase != "lobby":
                raise ValueError("La partie a déjà commencé.")

            taken = {p.nickname.lower() for p in room.players.values()}
            if nickname.lower() in taken:
                raise ValueError("Pseudo déjà utilisé dans cette room.")

            player_id = str(uuid.uuid4())
            player = Player(id=player_id, nickname=nickname, sid=sid)
            room.players[player_id] = player
            self.sid_index[sid] = (room_code, player_id)
            snapshot = self._room_snapshot(room, viewer_player_id=player_id)

        await self.emit_room_state(room_code)

        return {
            "room": snapshot,
            "session": {
                "roomCode": room_code,
                "playerId": player_id,
                "nickname": nickname,
                "isHost": False,
            },
        }

    async def reconnect_player(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        room_code = str(payload.get("roomCode", "")).strip().upper()
        player_id = str(payload.get("playerId", "")).strip()

        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                raise ValueError("Room introuvable.")
            player = room.players.get(player_id)
            if not player:
                raise ValueError("Joueur introuvable pour cette room.")

            for known_sid, identity in list(self.sid_index.items()):
                if identity == (room_code, player_id):
                    del self.sid_index[known_sid]

            player.sid = sid
            player.connected = True
            self.sid_index[sid] = (room_code, player_id)
            snapshot = self._room_snapshot(room, viewer_player_id=player_id)

        await self.emit_room_state(room_code)

        return {
            "room": snapshot,
            "session": {
                "roomCode": room_code,
                "playerId": player_id,
                "nickname": player.nickname,
                "isHost": player_id == room.host_player_id,
            },
        }

    async def mark_disconnected(self, sid: str) -> None:
        async with self.lock:
            identity = self.sid_index.pop(sid, None)
            if not identity:
                return
            room_code, player_id = identity
            room = self.rooms.get(room_code)
            if not room:
                return
            player = room.players.get(player_id)
            if not player:
                return
            player.connected = False

        await self.emit_room_state(room_code)

    async def start_game(self, sid: str) -> None:
        room_code: str
        rounds_count: int
        timer_seconds: int
        price_mode: str
        search_query: str
        used_listing_ids: set[str]

        async with self.lock:
            room, player = self._identity_from_sid(sid)
            if player.id != room.host_player_id:
                raise ValueError("Seul l'host peut lancer la partie.")
            if room.phase not in {"lobby", "finished"}:
                raise ValueError("La partie est déjà en cours.")
            if len(room.players) < 1:
                raise ValueError("Aucun joueur dans la room.")

            room_code = room.code
            rounds_count = room.config.rounds_count
            timer_seconds = room.config.timer_seconds
            price_mode = room.config.price_mode
            search_query = room.config.search_query
            # Conserve l'historique des annonces déjà vues dans la room,
            # y compris après une relance de partie.
            used_listing_ids = set(room.used_listing_ids)

        try:
            scrape_result = await scrape_and_store_live_listings(
                db_path=self.db_path,
                public_dir=SETTINGS.public_dir,
                search_query=search_query,
                rounds_count=rounds_count,
                price_mode=price_mode,
            )
        except Exception as exc:
            raise ValueError(
                "Echec du scraping live. Verifie la connexion internet ou change la zone de recherche."
            ) from exc

        scraped_ids = scrape_result["listingIds"]
        listings = get_random_listings_from_ids(
            db_path=self.db_path,
            ids=scraped_ids,
            count=rounds_count,
            mode=price_mode,
            exclude_ids=used_listing_ids,
        )
        if len(listings) < rounds_count:
            raise ValueError(
                "Le scraping n'a pas renvoyé assez de logements exploitables. "
                "Les annonces déjà vues sont exclues: essaie une autre ville ou relance plus tard."
            )

        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                raise ValueError("Room introuvable.")
            if room.phase not in {"lobby", "finished"}:
                raise ValueError("La partie n'est plus relançable.")

            if room.phase == "finished":
                for existing_player in room.players.values():
                    existing_player.score = 0

            room.listing_ids = [listing.id for listing in listings]
            room.phase = "in_round"
            room.round_index = 0
            room.current_round = None

            previous = self.room_tasks.get(room.code)
            if previous and not previous.done():
                previous.cancel()

        await self.emit_to_room(
            room_code,
            events.GAME_STARTED,
            {
                "code": room_code,
                "totalRounds": rounds_count,
                "timerSeconds": timer_seconds,
                "scrape": {
                    "source": scrape_result.get("source"),
                    "query": scrape_result.get("query"),
                    "fetchedCount": len(scraped_ids),
                },
            },
        )
        await self.emit_room_state(room_code)
        await self._start_next_round(room_code)

    async def start_next_round(self, sid: str) -> dict[str, Any]:
        async with self.lock:
            room, player = self._identity_from_sid(sid)
            if player.id != room.host_player_id:
                raise ValueError("Seul l'host peut lancer la manche suivante.")
            if room.phase == "lobby":
                raise ValueError("La partie n'a pas commencé.")
            if room.phase == "finished":
                raise ValueError("La partie est déjà terminée.")
            if room.phase != "reveal":
                raise ValueError("La manche actuelle n'est pas encore terminée.")

            if room.round_index >= len(room.listing_ids):
                raise ValueError("Il n'y a plus de manche à lancer.")
            room_code = room.code

        await self._start_next_round(room_code)
        return {"started": True}

    async def _start_next_round(self, room_code: str) -> None:
        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                raise ValueError("Room introuvable.")
            if room.round_index >= len(room.listing_ids):
                raise ValueError("Aucune manche restante.")

            room.round_index += 1
            listing_id = room.listing_ids[room.round_index - 1]
            listing = get_listing_by_id(self.db_path, listing_id)
            if not listing:
                raise ValueError(f"Listing manquant en DB: {listing_id}")

            room.used_listing_ids.add(listing_id)
            room.phase = "in_round"
            room.current_round = RoundState(
                index=room.round_index,
                listing=listing,
                ends_at_ms=self._now_ms() + room.config.timer_seconds * 1000,
            )

            previous = self.room_tasks.get(room.code)
            if previous and not previous.done():
                previous.cancel()
            self.room_tasks[room.code] = asyncio.create_task(
                self._round_timer_task(room.code, room.current_round.index)
            )

            payload = {
                "roomCode": room.code,
                "roundIndex": room.current_round.index,
                "totalRounds": room.config.rounds_count,
                "endsAtMs": room.current_round.ends_at_ms,
                "listing": listing.as_round_payload(),
                "hintsEnabled": room.config.hints_enabled,
                "priceMode": room.config.price_mode,
            }

        await self.emit_to_room(room_code, events.ROUND_STARTED, payload)
        await self.emit_room_state(room_code)

    async def _round_timer_task(self, room_code: str, round_index: int) -> None:
        try:
            while True:
                async with self.lock:
                    room = self.rooms.get(room_code)
                    if not room or not room.current_round:
                        return
                    if room.current_round.index != round_index:
                        return
                    remaining_ms = room.current_round.ends_at_ms - self._now_ms()
                if remaining_ms <= 0:
                    break
                await asyncio.sleep(min(remaining_ms / 1000, 0.5))

            await self.finalize_round(room_code, expected_round_index=round_index)
        except asyncio.CancelledError:
            return

    async def _finish_game(self, room_code: str) -> None:
        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                return
            room.phase = "finished"
            room.current_round = None
            leaderboard = self._sorted_players_payload(room)

            task = self.room_tasks.pop(room_code, None)
            if task and not task.done():
                task.cancel()

        await self.emit_to_room(
            room_code,
            events.GAME_FINISHED,
            {"roomCode": room_code, "leaderboard": leaderboard},
        )
        await self.emit_room_state(room_code)

    async def finalize_round(self, room_code: str, expected_round_index: int | None = None) -> None:
        finish_after_reveal = False

        async with self.lock:
            room = self.rooms.get(room_code)
            if not room or not room.current_round:
                return

            round_state = room.current_round
            if expected_round_index is not None and round_state.index != expected_round_index:
                return
            if round_state.locked:
                return

            round_state.locked = True
            room.phase = "reveal"

            true_price = true_price_for_mode(round_state.listing, room.config.price_mode)
            round_results = []

            for player in room.players.values():
                guess_obj = round_state.guesses.get(player.id)
                hints_used = sorted(round_state.hints_used.get(player.id, set()))
                hint_penalty = len(hints_used) * self.hint_penalty

                if guess_obj:
                    scoring = compute_round_score(guess_obj.value, true_price, hint_penalty)
                    round_score = int(scoring["finalScore"])
                    base_score = int(scoring["baseScore"])
                    error_pct = float(scoring["errorPct"])
                    player.score += round_score
                    guess_value: float | None = guess_obj.value
                else:
                    base_score = 0
                    round_score = 0
                    error_pct = None
                    guess_value = None

                round_results.append(
                    {
                        "playerId": player.id,
                        "nickname": player.nickname,
                        "guess": guess_value,
                        "errorPct": error_pct,
                        "baseScore": base_score,
                        "hintPenalty": hint_penalty,
                        "roundScore": round_score,
                        "totalScore": player.score,
                        "hintsUsed": hints_used,
                    }
                )

            round_payload = {
                "roomCode": room.code,
                "roundIndex": round_state.index,
                "truePrice": true_price,
                "priceMode": room.config.price_mode,
                "listing": {
                    "listingId": round_state.listing.id,
                    "title": round_state.listing.title,
                    "city": round_state.listing.city,
                    "country": round_state.listing.country,
                    "sourceUrl": round_state.listing.source_url,
                },
                "results": sorted(round_results, key=lambda r: (-r["roundScore"], r["nickname"].lower())),
                "leaderboard": self._sorted_players_payload(room),
            }

            if room.round_index >= len(room.listing_ids):
                finish_after_reveal = True

        await self.emit_to_room(room_code, events.ROUND_RESULT, round_payload)

        if finish_after_reveal:
            await self._finish_game(room_code)
        else:
            await self.emit_room_state(room_code)

    async def submit_guess(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        guess_raw = payload.get("guess")
        try:
            guess_value = float(guess_raw)
        except (TypeError, ValueError):
            raise ValueError("Prix invalide.")

        if guess_value <= 0 or guess_value > 100_000_000:
            raise ValueError("Le prix doit être positif et réaliste.")

        should_finalize_now = False
        round_index_for_finalize: int | None = None

        async with self.lock:
            room, player = self._identity_from_sid(sid)
            if room.phase not in {"in_round", "reveal"} or not room.current_round:
                raise ValueError("Aucune manche active.")
            if room.current_round.locked or self._now_ms() >= room.current_round.ends_at_ms:
                raise ValueError("Le timer est terminé, réponse verrouillée.")
            if player.id in room.current_round.guesses:
                raise ValueError("Une seule soumission est autorisée.")

            room.current_round.guesses[player.id] = Guess(value=guess_value)
            total_players = len(room.players)
            guessed_count = len(room.current_round.guesses)
            room_code = room.code

            # Si tous les joueurs ont répondu, on termine immédiatement la manche
            # et on force le timer à 0 côté serveur.
            if guessed_count >= total_players and room.phase == "in_round":
                room.current_round.ends_at_ms = self._now_ms()
                should_finalize_now = True
                round_index_for_finalize = room.current_round.index

                timer_task = self.room_tasks.pop(room_code, None)
                if timer_task and not timer_task.done():
                    timer_task.cancel()

        if should_finalize_now and round_index_for_finalize is not None:
            await self.finalize_round(room_code, expected_round_index=round_index_for_finalize)
        else:
            await self.emit_room_state(room_code)

        return {
            "accepted": True,
            "guess": guess_value,
            "guessedCount": guessed_count,
            "playersCount": total_players,
        }

    async def request_hint(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        hint_key = str(payload.get("hint", "")).strip().lower()
        if hint_key not in HINT_KEYS:
            raise ValueError("Indice invalide.")

        async with self.lock:
            room, player = self._identity_from_sid(sid)
            if not room.config.hints_enabled:
                raise ValueError("Le mode indice est désactivé dans cette room.")
            if room.phase not in {"in_round", "reveal"} or not room.current_round:
                raise ValueError("Aucune manche active.")
            if room.current_round.locked or self._now_ms() >= room.current_round.ends_at_ms:
                raise ValueError("Le timer est terminé, impossible de prendre un indice.")

            hint_value = room.current_round.listing.hint_value(hint_key)
            if hint_value in (None, ""):
                raise ValueError("Indice indisponible pour ce logement.")

            used = room.current_round.hints_used.setdefault(player.id, set())
            already_unlocked = hint_key in used
            if not already_unlocked:
                used.add(hint_key)
            penalty = len(used) * self.hint_penalty

        return {
            "hint": hint_key,
            "value": hint_value,
            "alreadyUnlocked": already_unlocked,
            "currentPenalty": penalty,
        }

    async def emit_room_state(self, room_code: str) -> None:
        async with self.lock:
            room = self.rooms.get(room_code)
            if not room:
                return
            payload = self._room_snapshot(room)
        await self.emit_to_room(room_code, events.ROOM_STATE, payload)

    async def emit_to_room(self, room_code: str, event: str, payload: dict[str, Any]) -> None:
        if self.sio is None:
            return
        await self.sio.emit(event, payload, room=room_code)
