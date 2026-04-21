"""Shared guide metadata and file-system access for the guide corpus."""

from __future__ import annotations

from pathlib import Path

GUIDES_DIR = Path(__file__).resolve().parent / "guides"

GUIDE_TOPICS: tuple[str, ...] = (
    "fantasy",
    "player_stats",
    "play_by_play",
    "drives",
    "postseason",
    "player_profile",
    "games",
)

GUIDE_INDEX_ROWS: tuple[tuple[str, str], ...] = (
    ("Fantasy scoring, fantasy leaderboards, kicker scoring", 'get_guide({"topic": "fantasy"})'),
    ("Weekly / season stats, snap counts, NGS, PFR advanced, QBR", 'get_guide({"topic": "player_stats"})'),
    ("Any `play_by_play` query (EPA, WPA, sacks, INTs, red zone, etc.)", 'get_guide({"topic": "play_by_play"})'),
    ("Drive-level analytics (longest drives, three-and-outs, scoring drives, TOP)", 'get_guide({"topic": "drives"})'),
    ("Playoffs, Super Bowls, Wild Card / Divisional / Conference games", 'get_guide({"topic": "postseason"})'),
    ("Player bio, IDs, draft, combine, depth chart", 'get_guide({"topic": "player_profile"})'),
    ("Schedules, game results, weather, betting lines", 'get_guide({"topic": "games"})'),
)
