"""
Módulo para la extracción de datos de la Liga Profesional de Fútbol Argentina (LPF).

Soporta dos proveedores:
1. `365scores` (POR DEFECTO - API pública en vivo sin bloqueos ni API Key):
   Consulta `https://webws.365scores.com/web` para obtener los 30 equipos de la
   Liga Profesional Argentina (`competitionId=72`), sus últimos 20 partidos
   finalizados de LPF, todos los jugadores convocados (hayan jugado o no) y las
   métricas completas por partido.
2. `sofascore`:
   Cliente para el esquema de `https://www.sofascore.com/api/v1` (para uso con
   transporte personalizado o datos locales).

Genera dos archivos CSV:
1. jugadores_registrados_lpf.csv: Todos los jugadores registrados en las planillas
   de cada equipo durante sus últimos 20 partidos de Liga Profesional (hayan
   jugado o hayan permanecido en el banco de suplentes sin ingresar).
2. metricas_partidos_lpf.csv: Métricas detalladas por partido y equipo para los
   últimos 20 partidos de Liga Profesional, incluyendo un identificador de debug.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# IDs oficiales de Liga Profesional de Fútbol Argentina (por defecto)
LPF_365SCORES_COMPETITION_ID = 72
SCORES365_API_BASE = "https://webws.365scores.com/web"

LPF_UNIQUE_TOURNAMENT_ID = 155
SOFASCORE_API_BASE = "https://www.sofascore.com/api/v1"


@dataclass(frozen=True)
class CompetitionMetadata:
    """Metadatos de una Liga o Competición en el sistema Multi-Liga."""

    competition_id: int          # ID canónico (coincide con 365Scores competitionId)
    code: str                    # Código corto (ej. LPF, EPL, LALIGA, UCL)
    name: str                    # Nombre completo
    short_name: str              # Nombre abreviado
    country: str                 # País o región
    confederation: str           # CONMEBOL, UEFA, CONCACAF, FIFA
    sofascore_id: int            # ID de uniqueTournament en SofaScore
    strength_factor: float       # Coeficiente de fuerza de liga (LPF = 1.00 base)


COMPETITIONS_CATALOG: Dict[int, CompetitionMetadata] = {
    72: CompetitionMetadata(
        competition_id=72,
        code="LPF",
        name="Liga Profesional Argentina",
        short_name="LPF Argentina",
        country="Argentina",
        confederation="CONMEBOL",
        sofascore_id=155,
        strength_factor=1.00,
    ),
    7: CompetitionMetadata(
        competition_id=7,
        code="EPL",
        name="Premier League",
        short_name="Premier League",
        country="Inglaterra",
        confederation="UEFA",
        sofascore_id=17,
        strength_factor=1.32,
    ),
    11: CompetitionMetadata(
        competition_id=11,
        code="LALIGA",
        name="LaLiga EA Sports",
        short_name="LaLiga",
        country="España",
        confederation="UEFA",
        sofascore_id=8,
        strength_factor=1.26,
    ),
    17: CompetitionMetadata(
        competition_id=17,
        code="SERIEA",
        name="Serie A",
        short_name="Serie A",
        country="Italia",
        confederation="UEFA",
        sofascore_id=23,
        strength_factor=1.24,
    ),
    25: CompetitionMetadata(
        competition_id=25,
        code="BUNDESLIGA",
        name="Bundesliga",
        short_name="Bundesliga",
        country="Alemania",
        confederation="UEFA",
        sofascore_id=35,
        strength_factor=1.22,
    ),
    35: CompetitionMetadata(
        competition_id=35,
        code="LIGUE1",
        name="Ligue 1",
        short_name="Ligue 1",
        country="Francia",
        confederation="UEFA",
        sofascore_id=34,
        strength_factor=1.16,
    ),
    113: CompetitionMetadata(
        competition_id=113,
        code="BRASILEIRAO",
        name="Brasileirão Série A",
        short_name="Brasileirão",
        country="Brasil",
        confederation="CONMEBOL",
        sofascore_id=325,
        strength_factor=1.12,
    ),
    572: CompetitionMetadata(
        competition_id=572,
        code="UCL",
        name="UEFA Champions League",
        short_name="Champions League",
        country="Europa",
        confederation="UEFA",
        sofascore_id=7,
        strength_factor=1.34,
    ),
    102: CompetitionMetadata(
        competition_id=102,
        code="LIBERTADORES",
        name="CONMEBOL Libertadores",
        short_name="Libertadores",
        country="Sudamérica",
        confederation="CONMEBOL",
        sofascore_id=384,
        strength_factor=1.08,
    ),
    63: CompetitionMetadata(
        competition_id=63,
        code="PRIMEIRA",
        name="Primeira Liga",
        short_name="Primeira Liga",
        country="Portugal",
        confederation="UEFA",
        sofascore_id=238,
        strength_factor=1.10,
    ),
    141: CompetitionMetadata(
        competition_id=141,
        code="LIGAMX",
        name="Liga MX",
        short_name="Liga MX",
        country="México",
        confederation="CONCACAF",
        sofascore_id=352,
        strength_factor=0.96,
    ),
    104: CompetitionMetadata(
        competition_id=104,
        code="MLS",
        name="Major League Soccer",
        short_name="MLS",
        country="EE.UU.",
        confederation="CONCACAF",
        sofascore_id=242,
        strength_factor=0.92,
    ),
}


def resolve_competition(query: str | int) -> CompetitionMetadata:
    """Resuelve una competición por ID numérico, código corto (ej. EPL, LPF) o nombre."""
    q_str = str(query).strip()
    if q_str.isdigit():
        cid = int(q_str)
        if cid in COMPETITIONS_CATALOG:
            return COMPETITIONS_CATALOG[cid]
        for meta in COMPETITIONS_CATALOG.values():
            if meta.sofascore_id == cid:
                return meta
        return CompetitionMetadata(
            competition_id=cid,
            code=f"COMP_{cid}",
            name=f"Competición {cid}",
            short_name=f"Comp {cid}",
            country="Internacional",
            confederation="FIFA",
            sofascore_id=cid,
            strength_factor=1.00,
        )

    q_up = q_str.upper()
    for meta in COMPETITIONS_CATALOG.values():
        if meta.code.upper() == q_up or q_up in meta.name.upper() or q_up in meta.short_name.upper():
            return meta

    raise ValueError(
        f"Competición '{query}' no reconocida. Códigos disponibles: "
        + ", ".join(f"{m.code} ({m.competition_id})" for m in COMPETITIONS_CATALOG.values())
    )


class SofaScoreAccessError(RuntimeError):
    """Error lanzado cuando SofaScore bloquea la petición (ej. 403 WAF / challenge)."""


@dataclass
class RegisteredPlayerRecord:
    """Representa un jugador registrado en las convocatorias o plantel actual de un equipo."""

    team_id: int
    team_name: str
    player_id: int
    player_name: str
    short_name: str
    position: str
    jersey_number: str
    convocations_count: int = 0
    matches_played_count: int = 0
    matches_unused_sub_count: int = 0
    total_minutes_played: int = 0
    average_rating: Optional[float] = None
    currently_in_squad: bool = True
    competition_id: int = LPF_365SCORES_COMPETITION_ID
    competition_name: str = "Liga Profesional Argentina"
    debug_match_ids: List[str] = field(default_factory=list)
    event_ids: List[int] = field(default_factory=list)
    _ratings_accum: List[float] = field(default_factory=list, repr=False)

    def register_appearance(
        self,
        debug_match_id: str,
        event_id: int,
        minutes_played: int,
        rating: Optional[float],
        position: Optional[str] = None,
        jersey_number: Optional[str] = None,
    ) -> None:
        self.convocations_count += 1
        if minutes_played > 0:
            self.matches_played_count += 1
            self.total_minutes_played += minutes_played
        else:
            self.matches_unused_sub_count += 1

        if rating is not None and rating > 0:
            self._ratings_accum.append(float(rating))
            self.average_rating = round(
                sum(self._ratings_accum) / len(self._ratings_accum), 2
            )

        if position and not self.position:
            self.position = position
        if jersey_number and not self.jersey_number:
            self.jersey_number = str(jersey_number)

        if debug_match_id not in self.debug_match_ids:
            self.debug_match_ids.append(debug_match_id)
        if event_id not in self.event_ids:
            self.event_ids.append(event_id)

    def to_csv_row(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "equipo": self.team_name,
            "player_id": self.player_id,
            "jugador": self.player_name,
            "nombre_corto": self.short_name,
            "posicion": self.position,
            "dorsal": self.jersey_number,
            "convocatorias_ultimos_20": self.convocations_count,
            "partidos_jugados": self.matches_played_count,
            "partidos_suplente_sin_ingresar": self.matches_unused_sub_count,
            "minutos_totales": self.total_minutes_played,
            "promedio_valoracion": (
                self.average_rating if self.average_rating is not None else ""
            ),
            "pertenece_actualmente": 1 if self.currently_in_squad else 0,
            "competition_id": self.competition_id,
            "competition_name": self.competition_name,
            "debug_match_ids": ";".join(self.debug_match_ids),
            "event_ids": ";".join(str(eid) for eid in self.event_ids),
        }


@dataclass
class MatchMetricsRecord:
    """Representa las métricas solicitadas de un partido desde la perspectiva de un equipo."""

    debug_id: int
    debug_match_id: str
    event_id: int
    fecha_utc: str
    team_id: int
    equipo: str
    opponent_id: int
    rival: str
    condicion: str  # "Local" | "Visitante"
    alineacion_nombres: str  # Todos los convocados (titulares y suplentes)
    goles_a_favor: Optional[int]
    goles_en_contra: Optional[int]
    tiros_al_arco: Optional[int]
    posesion: Optional[float]
    salvadas_arquero: Optional[int]
    corners: Optional[int]
    faltas: Optional[int]
    pases: Optional[int]
    tiros_libres: Optional[int]
    amarillas: Optional[int]
    rojas: Optional[int]
    promedio_valoracion: Optional[float]
    tiros: Optional[int]
    toques_area_rival: Optional[int]
    offsides: Optional[int]
    tacles: Optional[int]
    intercepciones: Optional[int]
    recuperaciones: Optional[int]
    despejes: Optional[int]
    tiros_rival_al_arco: Optional[int]
    competition_id: int = LPF_365SCORES_COMPETITION_ID
    competition_name: str = "Liga Profesional Argentina"

    def to_csv_row(self) -> Dict[str, Any]:
        data = asdict(self)
        return {k: ("" if v is None else v) for k, v in data.items()}


def _parse_numeric_stat(val: Any) -> Optional[float | int]:
    """Convierte un valor de estadística ('55%', '3/4 (75%)', '12') a int o float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        cleaned = val.strip().rstrip("%").strip()
        if "/" in cleaned:
            # Ej: "3/4 (75%)" o "345/410 (84%)" -> primer número
            cleaned = cleaned.split("/", 1)[0].strip()
        try:
            if "." in cleaned:
                return float(cleaned)
            return int(cleaned)
        except ValueError:
            return None
    return None


def _parse_minutes(val: Any) -> int:
    """Convierte cadenas de minutos como \"90'\" o \"90+4'\" a entero."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace("'", "").strip()
    if not s:
        return 0
    if "+" in s:
        parts = [p.strip() for p in s.split("+") if p.strip().isdigit()]
        return sum(int(p) for p in parts)
    try:
        return int(float(s))
    except ValueError:
        return 0


def _normalize_text(text: str) -> str:
    """Normaliza un texto a minúsculas sin tildes para búsquedas de equipos."""
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def filter_teams_by_query(
    teams: Sequence[Dict[str, Any]],
    team_query: str | int,
) -> List[Dict[str, Any]]:
    """
    Filtra la lista de equipos por ID numérico exacto o por coincidencia parcial
    del nombre (ignorando mayúsculas y tildes, ej. 'river', 'velez', '868').
    """
    q_str = str(team_query).strip()
    if q_str.isdigit():
        target_id = int(q_str)
        return [t for t in teams if int(t.get("id", -1)) == target_id]

    q_norm = _normalize_text(q_str)
    exact = [
        t
        for t in teams
        if _normalize_text(t.get("name", "")) == q_norm
        or _normalize_text(t.get("shortName", "")) == q_norm
    ]
    if exact:
        return exact

    return [
        t
        for t in teams
        if q_norm in _normalize_text(t.get("name", ""))
        or q_norm in _normalize_text(t.get("shortName", ""))
    ]


# ==============================================================================
# PROVEEDOR 1 (POR DEFECTO): API PÚBLICA DE 365SCORES (FUNCIONA EN VIVO)
# ==============================================================================


class Public365ScoresClient:
    """
    Cliente HTTP para la API pública de 365Scores (`https://webws.365scores.com/web`).
    No requiere API Key ni tiene bloqueos anti-bot para consultas desde Python.
    Incluye caché en memoria de partidos para evitar descargar dos veces el mismo
    encuentro cuando se procesan ambos equipos de la Liga Profesional.
    """

    DEFAULT_PARAMS = {
        "appTypeId": 5,
        "langId": 14,
        "timezoneName": "America/Buenos_Aires",
        "userCountryId": 30,
    }

    def __init__(
        self,
        base_url: str = SCORES365_API_BASE,
        delay_seconds: float = 0.15,
        transport: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = delay_seconds
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        self._transport = transport
        self._game_cache: Dict[int, Dict[str, Any]] = {}
        self._stats_cache: Dict[int, Dict[str, Any]] = {}

    def get_json(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if path.startswith("http"):
            url = path
        else:
            rel = path.lstrip("/")
            if "?" in rel:
                url = f"{self.base_url}/{rel}"
            else:
                merged = dict(self.DEFAULT_PARAMS)
                if params:
                    merged.update(params)
                url = f"{self.base_url}/{rel}?{urlencode(merged)}"

        if self._transport is not None:
            return self._transport(url, self.headers)

        req = Request(url, headers=self.headers, method="GET")
        with urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8")
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)
            return json.loads(raw)

    def get_lpf_teams(
        self, competition_id: int = LPF_365SCORES_COMPETITION_ID
    ) -> List[Dict[str, Any]]:
        """Obtiene los 30 equipos de la Liga Profesional Argentina desde la tabla oficial."""
        data = self.get_json("standings/", {"competitions": competition_id})
        teams_by_id: Dict[int, Dict[str, Any]] = {}
        for standing in data.get("standings", []):
            for row in standing.get("rows", []):
                comp = row.get("competitor", {})
                cid = comp.get("id")
                if cid:
                    teams_by_id[int(cid)] = {
                        "id": int(cid),
                        "name": str(comp.get("name", f"Team {cid}")),
                        "shortName": str(comp.get("shortName", comp.get("name", ""))),
                    }
        return list(teams_by_id.values())

    def get_team_last_lpf_games(
        self,
        team_id: int,
        competition_id: int = LPF_365SCORES_COMPETITION_ID,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Obtiene los últimos `limit` partidos finalizados de la Liga Profesional para `team_id`.
        """
        collected: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        data = self.get_json(
            "games/results/",
            {"competitors": team_id, "competitions": competition_id},
        )
        while True:
            games = data.get("games", [])
            if not games:
                break

            # Ordenar de más reciente a más antiguo por startTime
            games_sorted = sorted(
                games,
                key=lambda g: str(g.get("startTime", "")),
                reverse=True,
            )
            for g in games_sorted:
                gid = int(g.get("id", 0))
                if not gid or gid in seen_ids:
                    continue
                # statusGroup == 4 indica partido Finalizado
                if int(g.get("statusGroup", 0)) != 4:
                    continue
                if int(g.get("competitionId", competition_id)) != competition_id:
                    continue

                seen_ids.add(gid)
                collected.append(g)
                if len(collected) >= limit:
                    return collected

            prev_page = data.get("paging", {}).get("previousPage")
            if not prev_page or len(collected) >= limit:
                break
            # Si hiciera falta paginar hacia atrás:
            if prev_page.startswith("/web/"):
                prev_page = prev_page[len("/web/") :]
            data = self.get_json(prev_page)

        return collected

    def get_game_details(self, game_id: int) -> Dict[str, Any]:
        if game_id not in self._game_cache:
            payload = self.get_json("game/", {"gameId": game_id})
            self._game_cache[game_id] = payload.get("game", {})
        return self._game_cache[game_id]

    def get_game_stats(self, game_id: int) -> Dict[str, Any]:
        if game_id not in self._stats_cache:
            self._stats_cache[game_id] = self.get_json(
                "game/stats/", {"games": game_id}
            )
        return self._stats_cache[game_id]

    def get_team_current_squad(self, team_id: int) -> Tuple[set[int], set[str]]:
        """
        Obtiene los IDs y nombres normalizados de los jugadores que pertenecen
        actualmente al plantel oficial del equipo en 365Scores.
        """
        athletes = self.get_team_current_squad_athletes(team_id)
        pids = {int(a["id"]) for a in athletes if "id" in a}
        names = {_normalize_text(a.get("name", "")) for a in athletes if a.get("name")}
        return pids, names

    def get_team_current_squad_athletes(self, team_id: int) -> List[Dict[str, Any]]:
        """
        Obtiene la lista completa de atletas del plantel oficial actual en 365Scores,
        permitiendo detectar también fichajes nuevos (0 convocatorias previas).
        """
        try:
            payload = self.get_json("squads/", {"competitors": team_id})
            squads = payload.get("squads", [])
            if not squads:
                return []
            return list(squads[0].get("athletes", []))
        except Exception:
            return []


# Mapeo de IDs de estadísticas de equipo en 365Scores
SCORES365_STAT_ID_MAP: Dict[str, Tuple[int, ...]] = {
    "amarillas": (1,),  # Tarjetas Amarillas
    "rojas": (2,),  # Tarjetas Rojas
    "tiros": (3,),  # Total Remates
    "tiros_al_arco": (4,),  # Remates a Puerta
    "corners": (8,),  # Saques de Esquina
    "offsides": (9,),  # Fueras de Juego
    "posesion": (10,),  # Posesión
    "faltas": (12,),  # Faltas
    "tiros_libres": (13,),  # Saques de falta (Tiros libres)
    "pases": (21, 19),  # 21 = Total de pases, fallback 19 = Pases completados
    "salvadas_arquero": (23,),  # Salvadas de Portero
    "despejes": (40,),  # Despejes
    "intercepciones": (41,),  # Intercepciones
    "tacles": (51,),  # Barridas ganadas
    "toques_area_rival": (146,),  # Acciones / remates dentro del área rival
}


class LigaProfesional365Extractor:
    """
    Extractor principal en vivo utilizando la API pública de 365Scores para obtener
    los últimos 20 partidos de todos los equipos de la Liga Profesional Argentina.
    """

    def __init__(
        self,
        client: Optional[Public365ScoresClient] = None,
        competition_id: int = LPF_365SCORES_COMPETITION_ID,
        matches_per_team: int = 20,
    ) -> None:
        self.client = client or Public365ScoresClient()
        self.competition_id = competition_id
        self.matches_per_team = matches_per_team
        self._debug_counter = 0

    def process_team(
        self, team: Dict[str, Any]
    ) -> Tuple[List[RegisteredPlayerRecord], List[MatchMetricsRecord]]:
        team_id = int(team["id"])
        team_name = str(team.get("name", f"Team {team_id}"))

        games = self.client.get_team_last_lpf_games(
            team_id=team_id,
            competition_id=self.competition_id,
            limit=self.matches_per_team,
        )

        players_registry: Dict[int, RegisteredPlayerRecord] = {}
        fallback_key_to_pid: Dict[Tuple[str, str], int] = {}
        match_records: List[MatchMetricsRecord] = []

        for g_summary in games:
            self._debug_counter += 1
            game_id = int(g_summary["id"])
            debug_match_id = f"DBG-{self._debug_counter:04d}-EV{game_id}-T{team_id}"

            game = self.client.get_game_details(game_id) or g_summary
            stats_payload = self.client.get_game_stats(game_id)

            home_comp = game.get("homeCompetitor", g_summary.get("homeCompetitor", {}))
            away_comp = game.get("awayCompetitor", g_summary.get("awayCompetitor", {}))
            is_home = int(home_comp.get("id", -1)) == team_id

            team_comp = home_comp if is_home else away_comp
            opp_comp = away_comp if is_home else home_comp
            opp_id = int(opp_comp.get("id", 0))
            opp_name = str(opp_comp.get("name", "Desconocido"))
            condicion = "Local" if is_home else "Visitante"

            goles_favor = (
                int(float(team_comp["score"]))
                if team_comp.get("score") is not None and team_comp.get("score") >= 0
                else None
            )
            goles_contra = (
                int(float(opp_comp["score"]))
                if opp_comp.get("score") is not None and opp_comp.get("score") >= 0
                else None
            )

            fecha_str = str(game.get("startTime", g_summary.get("startTime", "")))

            # 1. Procesar plantel convocado (titulares status=1 y suplentes status=2, hayan jugado o no)
            members_catalog: Dict[int, Dict[str, Any]] = {
                int(m["id"]): m for m in game.get("members", []) if "id" in m
            }
            lineup_members = team_comp.get("lineups", {}).get("members", [])

            convocated_names: List[str] = []
            match_ratings: List[float] = []
            team_recoveries_sum = 0
            has_player_recoveries = False

            for m_entry in lineup_members:
                status_code = int(m_entry.get("status", 0))
                # status 1 = Starting (Titular), status 2 = Substitute (Suplente en banco)
                # Excluimos status 3 (Missing/Lesionado) y status 4 (DT/Entrenador)
                if status_code not in (1, 2):
                    continue

                member_id = int(m_entry.get("id", 0))
                if not member_id:
                    continue

                catalog_info = members_catalog.get(member_id, {})
                athlete_id = catalog_info.get("athleteId")
                player_name = str(
                    catalog_info.get("name", f"Jugador {member_id}")
                ).strip()
                short_name = str(catalog_info.get("shortName", player_name)).strip()
                jersey = str(catalog_info.get("jerseyNumber", "") or "").strip()
                fb_key = (short_name.lower(), jersey)

                if athlete_id:
                    player_id = int(athlete_id)
                elif fb_key in fallback_key_to_pid:
                    player_id = fallback_key_to_pid[fb_key]
                else:
                    player_id = member_id

                if fb_key[0] and fb_key not in fallback_key_to_pid:
                    fallback_key_to_pid[fb_key] = player_id

                pos_name = str(
                    m_entry.get("position", {}).get("name")
                    or m_entry.get("formation", {}).get("name")
                    or ""
                ).strip()

                rating_raw = m_entry.get("ranking")
                rating = (
                    float(rating_raw)
                    if rating_raw is not None and float(rating_raw) > 0
                    else None
                )
                if rating is not None:
                    match_ratings.append(rating)

                minutes_played = 0
                for p_stat in m_entry.get("stats", []):
                    st_type = int(p_stat.get("type", 0))
                    if st_type == 30:  # Minutes
                        minutes_played = _parse_minutes(p_stat.get("value"))
                    elif st_type == 86:  # Recuperación de la posesión
                        rec_val = _parse_numeric_stat(p_stat.get("value"))
                        if rec_val is not None:
                            team_recoveries_sum += int(rec_val)
                            has_player_recoveries = True

                if player_id not in players_registry:
                    comp_meta = resolve_competition(self.competition_id)
                    players_registry[player_id] = RegisteredPlayerRecord(
                        team_id=team_id,
                        team_name=team_name,
                        player_id=player_id,
                        player_name=player_name,
                        short_name=short_name,
                        position=pos_name,
                        jersey_number=jersey,
                        competition_id=comp_meta.competition_id,
                        competition_name=comp_meta.name,
                    )

                canonical_name = players_registry[player_id].player_name or player_name
                if canonical_name:
                    convocated_names.append(canonical_name)

                players_registry[player_id].register_appearance(
                    debug_match_id=debug_match_id,
                    event_id=game_id,
                    minutes_played=minutes_played,
                    rating=rating,
                    position=pos_name,
                    jersey_number=jersey,
                )

            promedio_valoracion = (
                round(sum(match_ratings) / len(match_ratings), 2)
                if match_ratings
                else None
            )

            # 2. Procesar estadísticas del partido por equipo y rival
            raw_stats = stats_payload.get("statistics", []) or game.get(
                "statistics", []
            )
            stats_by_comp_and_id: Dict[Tuple[int, int], float | int] = {}
            for s_item in raw_stats:
                c_id = int(s_item.get("competitorId", 0))
                s_id = int(s_item.get("id", 0))
                parsed_val = _parse_numeric_stat(s_item.get("value"))
                if c_id and s_id and parsed_val is not None:
                    stats_by_comp_and_id[(c_id, s_id)] = parsed_val

            def get_team_stat(stat_ids: Tuple[int, ...], target_comp_id: int) -> Optional[float | int]:
                for sid in stat_ids:
                    if (target_comp_id, sid) in stats_by_comp_and_id:
                        return stats_by_comp_and_id[(target_comp_id, sid)]
                return None

            extracted: Dict[str, Optional[float | int]] = {}
            for metric_key, s_ids in SCORES365_STAT_ID_MAP.items():
                val = get_team_stat(s_ids, team_id)
                if metric_key in ("amarillas", "rojas") and val is None and raw_stats:
                    val = 0
                extracted[metric_key] = val

            tiros_rival_arco_val = get_team_stat(
                SCORES365_STAT_ID_MAP["tiros_al_arco"], opp_id
            )

            match_record = MatchMetricsRecord(
                debug_id=self._debug_counter,
                debug_match_id=debug_match_id,
                event_id=game_id,
                fecha_utc=fecha_str,
                team_id=team_id,
                equipo=team_name,
                opponent_id=opp_id,
                rival=opp_name,
                condicion=condicion,
                alineacion_nombres="; ".join(convocated_names),
                goles_a_favor=goles_favor,
                goles_en_contra=goles_contra,
                tiros_al_arco=(
                    int(extracted["tiros_al_arco"])
                    if extracted["tiros_al_arco"] is not None
                    else None
                ),
                posesion=(
                    float(extracted["posesion"])
                    if extracted["posesion"] is not None
                    else None
                ),
                salvadas_arquero=(
                    int(extracted["salvadas_arquero"])
                    if extracted["salvadas_arquero"] is not None
                    else None
                ),
                corners=(
                    int(extracted["corners"])
                    if extracted["corners"] is not None
                    else None
                ),
                faltas=(
                    int(extracted["faltas"])
                    if extracted["faltas"] is not None
                    else None
                ),
                pases=(
                    int(extracted["pases"])
                    if extracted["pases"] is not None
                    else None
                ),
                tiros_libres=(
                    int(extracted["tiros_libres"])
                    if extracted["tiros_libres"] is not None
                    else None
                ),
                amarillas=(
                    int(extracted["amarillas"])
                    if extracted["amarillas"] is not None
                    else None
                ),
                rojas=(
                    int(extracted["rojas"])
                    if extracted["rojas"] is not None
                    else None
                ),
                promedio_valoracion=promedio_valoracion,
                tiros=(
                    int(extracted["tiros"])
                    if extracted["tiros"] is not None
                    else None
                ),
                toques_area_rival=(
                    int(extracted["toques_area_rival"])
                    if extracted["toques_area_rival"] is not None
                    else None
                ),
                offsides=(
                    int(extracted["offsides"])
                    if extracted["offsides"] is not None
                    else None
                ),
                tacles=(
                    int(extracted["tacles"])
                    if extracted["tacles"] is not None
                    else None
                ),
                intercepciones=(
                    int(extracted["intercepciones"])
                    if extracted["intercepciones"] is not None
                    else None
                ),
                recuperaciones=(
                    team_recoveries_sum if has_player_recoveries else None
                ),
                despejes=(
                    int(extracted["despejes"])
                    if extracted["despejes"] is not None
                    else None
                ),
                tiros_rival_al_arco=(
                    int(tiros_rival_arco_val)
                    if tiros_rival_arco_val is not None
                    else None
                ),
                competition_id=resolve_competition(self.competition_id).competition_id,
                competition_name=resolve_competition(self.competition_id).name,
            )
            match_records.append(match_record)

        squad_athletes = self.client.get_team_current_squad_athletes(team_id)
        current_pids = {int(a["id"]) for a in squad_athletes if "id" in a}
        current_names = {
            _normalize_text(a.get("name", ""))
            for a in squad_athletes
            if a.get("name")
        }
        if current_pids or current_names:
            for p_rec in players_registry.values():
                p_rec.currently_in_squad = (
                    p_rec.player_id in current_pids
                    or _normalize_text(p_rec.player_name) in current_names
                )
            # Registrar también jugadores nuevos en el club (en el plantel actual pero con 0 convocatorias previas)
            existing_norm_names = {
                _normalize_text(p_rec.player_name)
                for p_rec in players_registry.values()
            }
            comp_meta = resolve_competition(self.competition_id)
            for ath in squad_athletes:
                aid = int(ath.get("id", 0))
                aname = str(ath.get("name", "")).strip()
                if not aid or not aname:
                    continue
                if aid in players_registry or _normalize_text(aname) in existing_norm_names:
                    continue
                ashort = str(ath.get("shortName", aname)).strip()
                ajersey = str(ath.get("jerseyNumber", "") or "").strip()
                apos = str(
                    ath.get("position", {}).get("name")
                    if isinstance(ath.get("position"), dict)
                    else (ath.get("positionName") or ath.get("position") or "")
                ).strip()
                players_registry[aid] = RegisteredPlayerRecord(
                    team_id=team_id,
                    team_name=team_name,
                    player_id=aid,
                    player_name=aname,
                    short_name=ashort,
                    position=apos,
                    jersey_number=ajersey,
                    convocations_count=0,
                    matches_played_count=0,
                    matches_unused_sub_count=0,
                    total_minutes_played=0,
                    average_rating=None,
                    currently_in_squad=True,
                    competition_id=comp_meta.competition_id,
                    competition_name=comp_meta.name,
                )

        return list(players_registry.values()), match_records

    def run_all_teams(
        self,
        players_csv_path: str | Path = "jugadores_registrados_lpf.csv",
        metrics_csv_path: str | Path = "metricas_partidos_lpf.csv",
        max_teams: Optional[int] = None,
        team_filter: Optional[str | int] = None,
    ) -> Tuple[Path, Path]:
        teams = self.client.get_lpf_teams(self.competition_id)
        if team_filter is not None:
            matched = filter_teams_by_query(teams, team_filter)
            if not matched:
                available = ", ".join(
                    f"{t['name']} (ID: {t['id']})" for t in teams
                )
                raise ValueError(
                    f"No se encontró ningún equipo que coincida con '{team_filter}'. "
                    f"Equipos disponibles: {available}"
                )
            teams = matched

        if max_teams is not None:
            teams = teams[:max_teams]

        all_players: List[RegisteredPlayerRecord] = []
        all_matches: List[MatchMetricsRecord] = []

        total = len(teams)
        for idx, team in enumerate(teams, start=1):
            logger.info(
                "[%d/%d] Procesando equipo: %s (ID: %s)",
                idx,
                total,
                team.get("name"),
                team.get("id"),
            )
            team_players, team_matches = self.process_team(team)
            all_players.extend(team_players)
            all_matches.extend(team_matches)

        players_out = export_players_csv(all_players, players_csv_path)
        metrics_out = export_metrics_csv(all_matches, metrics_csv_path)
        return players_out, metrics_out

    def run_for_team(
        self,
        team_query: str | int,
        players_csv_path: str | Path = "jugadores_registrados_lpf.csv",
        metrics_csv_path: str | Path = "metricas_partidos_lpf.csv",
    ) -> Tuple[Path, Path]:
        """Extrae y exporta los últimos N partidos únicamente para el equipo indicado (por nombre o ID)."""
        return self.run_all_teams(
            players_csv_path=players_csv_path,
            metrics_csv_path=metrics_csv_path,
            team_filter=team_query,
        )


# ==============================================================================
# PROVEEDOR 2: ESQUEMA NATIVO DE SOFASCORE
# ==============================================================================

STAT_KEYS_MAP: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "tiros_al_arco": {
        "keys": ("shotsOnGoal", "shotsOnTarget"),
        "names": ("Shots on target", "Tiros a puerta", "Remates al arco"),
    },
    "posesion": {
        "keys": ("ballPossession",),
        "names": ("Ball possession", "Posesión de balón", "Posesión"),
    },
    "salvadas_arquero": {
        "keys": ("goalkeeperSaves", "saves"),
        "names": ("Goalkeeper saves", "Paradas del portero", "Atajadas"),
    },
    "corners": {
        "keys": ("cornerKicks",),
        "names": ("Corner kicks", "Saques de esquina", "Córners"),
    },
    "faltas": {
        "keys": ("fouls",),
        "names": ("Fouls", "Faltas"),
    },
    "pases": {
        "keys": ("passes", "totalPasses"),
        "names": ("Passes", "Pases", "Pases totales"),
    },
    "tiros_libres": {
        "keys": ("freeKicks",),
        "names": ("Free kicks", "Tiros libres"),
    },
    "amarillas": {
        "keys": ("yellowCards",),
        "names": ("Yellow cards", "Tarjetas amarillas"),
    },
    "rojas": {
        "keys": ("redCards",),
        "names": ("Red cards", "Tarjetas rojas"),
    },
    "tiros": {
        "keys": ("totalShotsOnGoal", "totalShots"),
        "names": ("Total shots", "Tiros totales", "Remates totales"),
    },
    "toques_area_rival": {
        "keys": ("touchesInOppBox",),
        "names": (
            "Touches in penalty area",
            "Toques en el área rival",
            "Touches in opponent box",
        ),
    },
    "offsides": {
        "keys": ("offsides",),
        "names": ("Offsides", "Fueras de juego"),
    },
    "tacles": {
        "keys": ("totalTackle", "tackles"),
        "names": ("Tackles", "Entradas", "Tacles"),
    },
    "intercepciones": {
        "keys": ("interceptionWon", "interceptions"),
        "names": ("Interceptions", "Intercepciones"),
    },
    "recuperaciones": {
        "keys": ("ballRecovery", "recoveries"),
        "names": ("Recoveries", "Recuperaciones", "Ball recoveries"),
    },
    "despejes": {
        "keys": ("totalClearance", "clearances"),
        "names": ("Clearances", "Despejes"),
    },
}


class SofaScoreClient:
    """Cliente HTTP para el esquema de endpoints JSON de SofaScore."""

    def __init__(
        self,
        base_url: str = SOFASCORE_API_BASE,
        delay_seconds: float = 0.5,
        headers: Optional[Dict[str, str]] = None,
        transport: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = delay_seconds
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Referer": "https://www.sofascore.com/",
        }
        if headers:
            self.headers.update(headers)
        self._transport = transport

    def get_json(self, endpoint: str) -> Dict[str, Any]:
        url = (
            endpoint
            if endpoint.startswith("http")
            else f"{self.base_url}/{endpoint.lstrip('/')}"
        )
        if self._transport is not None:
            return self._transport(url, self.headers)

        req = Request(url, headers=self.headers, method="GET")
        try:
            with urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                if self.delay_seconds > 0:
                    time.sleep(self.delay_seconds)
                return json.loads(raw)
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            if exc.code == 403:
                raise SofaScoreAccessError(
                    f"SofaScore devolvió HTTP 403 en {url} (respuesta: {body[:200]})."
                ) from exc
            if exc.code == 404:
                return {}
            raise RuntimeError(
                f"Error HTTP {exc.code} al consultar {url}: {body[:200]}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Error de red al consultar {url}: {exc}") from exc

    def get_seasons(
        self, unique_tournament_id: int = LPF_UNIQUE_TOURNAMENT_ID
    ) -> List[Dict[str, Any]]:
        data = self.get_json(f"/unique-tournament/{unique_tournament_id}/seasons")
        return data.get("seasons", [])

    def get_tournament_teams(
        self,
        unique_tournament_id: int,
        season_id: int,
    ) -> List[Dict[str, Any]]:
        data = self.get_json(
            f"/unique-tournament/{unique_tournament_id}/season/{season_id}/standings/total"
        )
        teams_by_id: Dict[int, Dict[str, Any]] = {}
        for standing in data.get("standings", []):
            for row in standing.get("rows", []):
                team = row.get("team")
                if team and "id" in team:
                    teams_by_id[int(team["id"])] = team
        return list(teams_by_id.values())

    def get_team_last_events_page(
        self, team_id: int, page: int = 0
    ) -> Tuple[List[Dict[str, Any]], bool]:
        data = self.get_json(f"/team/{team_id}/events/last/{page}")
        events = data.get("events", [])
        has_next_page = bool(data.get("hasNextPage", False))
        return events, has_next_page

    def get_event_statistics(self, event_id: int) -> Dict[str, Any]:
        return self.get_json(f"/event/{event_id}/statistics")

    def get_event_lineups(self, event_id: int) -> Dict[str, Any]:
        return self.get_json(f"/event/{event_id}/lineups")

    def get_team_current_squad(self, team_id: int) -> Tuple[set[int], set[str]]:
        athletes = self.get_team_current_squad_athletes(team_id)
        pids = {int(a["id"]) for a in athletes if "id" in a}
        names = {_normalize_text(a.get("name", "")) for a in athletes if a.get("name")}
        return pids, names

    def get_team_current_squad_athletes(self, team_id: int) -> List[Dict[str, Any]]:
        try:
            data = self.get_json(f"/team/{team_id}/players")
            players = data.get("players", [])
            out: List[Dict[str, Any]] = []
            for p_entry in players:
                p_info = p_entry.get("player", {})
                if "id" in p_info:
                    out.append(
                        {
                            "id": int(p_info["id"]),
                            "name": str(p_info.get("name", "")).strip(),
                            "shortName": str(p_info.get("shortName", p_info.get("name", ""))).strip(),
                            "position": str(p_info.get("position", "")).strip(),
                            "jerseyNumber": str(p_info.get("jerseyNumber", p_info.get("shirtNumber", "")) or "").strip(),
                        }
                    )
            return out
        except Exception:
            return []


def extract_all_period_stats(
    statistics_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    periods = statistics_payload.get("statistics", [])
    if not periods:
        return []

    target_period = None
    for period_obj in periods:
        if str(period_obj.get("period", "")).upper() == "ALL":
            target_period = period_obj
            break
    if target_period is None:
        target_period = periods[0]

    items: List[Dict[str, Any]] = []
    for group in target_period.get("groups", []):
        items.extend(group.get("statisticsItems", []))
    return items


def find_stat_values(
    stat_items: Iterable[Dict[str, Any]],
    keys: Tuple[str, ...],
    names: Tuple[str, ...],
) -> Tuple[Optional[float | int], Optional[float | int]]:
    keys_lower = {k.lower() for k in keys}
    names_lower = {n.lower() for n in names}

    for item in stat_items:
        item_key = str(item.get("key", "")).lower()
        item_name = str(item.get("name", "")).lower()
        if item_key in keys_lower or item_name in names_lower:
            home_raw = item.get("homeValue", item.get("home"))
            away_raw = item.get("awayValue", item.get("away"))
            return _parse_numeric_stat(home_raw), _parse_numeric_stat(away_raw)

    return None, None


class LigaProfesionalExtractor:
    """Extractor basado en el esquema de SofaScore."""

    def __init__(
        self,
        client: SofaScoreClient,
        unique_tournament_id: int = LPF_UNIQUE_TOURNAMENT_ID,
        matches_per_team: int = 20,
    ) -> None:
        self.client = client
        self.unique_tournament_id = unique_tournament_id
        self.matches_per_team = matches_per_team
        self._debug_counter = 0

    def get_current_season_id(self) -> int:
        seasons = self.client.get_seasons(self.unique_tournament_id)
        if not seasons:
            raise RuntimeError(
                f"No se encontraron temporadas para el torneo {self.unique_tournament_id}."
            )
        return int(seasons[0]["id"])

    def collect_team_last_lpf_matches(
        self,
        team_id: int,
        max_pages: int = 4,
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for page in range(max_pages):
            events, has_next = self.client.get_team_last_events_page(team_id, page=page)
            if not events:
                break

            events_desc = sorted(
                events,
                key=lambda e: int(e.get("startTimestamp", 0)),
                reverse=True,
            )
            for ev in events_desc:
                ev_id = int(ev.get("id", 0))
                if not ev_id or ev_id in seen_ids:
                    continue

                status_type = str(ev.get("status", {}).get("type", "")).lower()
                if status_type != "finished":
                    continue

                ut_id = (
                    ev.get("tournament", {})
                    .get("uniqueTournament", {})
                    .get("id")
                )
                if ut_id is not None and int(ut_id) != self.unique_tournament_id:
                    continue

                seen_ids.add(ev_id)
                collected.append(ev)
                if len(collected) >= self.matches_per_team:
                    return collected

            if not has_next:
                break

        return collected

    def process_team(
        self,
        team: Dict[str, Any],
    ) -> Tuple[List[RegisteredPlayerRecord], List[MatchMetricsRecord]]:
        team_id = int(team["id"])
        team_name = str(team.get("name", f"Team {team_id}"))

        events = self.collect_team_last_lpf_matches(team_id)
        players_registry: Dict[int, RegisteredPlayerRecord] = {}
        fallback_key_to_pid: Dict[Tuple[str, str], int] = {}
        match_records: List[MatchMetricsRecord] = []

        for event in events:
            self._debug_counter += 1
            event_id = int(event["id"])
            debug_match_id = f"DBG-{self._debug_counter:04d}-EV{event_id}-T{team_id}"

            home_team = event.get("homeTeam", {})
            away_team = event.get("awayTeam", {})
            is_home = int(home_team.get("id", -1)) == team_id

            opponent = away_team if is_home else home_team
            opponent_id = int(opponent.get("id", 0))
            opponent_name = str(opponent.get("name", "Desconocido"))
            condicion = "Local" if is_home else "Visitante"

            home_score = event.get("homeScore", {}).get(
                "current", event.get("homeScore", {}).get("display")
            )
            away_score = event.get("awayScore", {}).get(
                "current", event.get("awayScore", {}).get("display")
            )
            goles_favor = home_score if is_home else away_score
            goles_contra = away_score if is_home else home_score

            start_ts = event.get("startTimestamp")
            fecha_utc = (
                datetime.fromtimestamp(int(start_ts), tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if start_ts
                else ""
            )

            lineups_payload = self.client.get_event_lineups(event_id)
            side_key = "home" if is_home else "away"
            side_lineup = lineups_payload.get(side_key, {})
            raw_players = side_lineup.get("players", [])

            convocated_names: List[str] = []
            match_ratings: List[float] = []

            for p_entry in raw_players:
                p_info = p_entry.get("player", {})
                p_id = int(p_info.get("id", 0))
                if not p_id:
                    continue

                p_name = str(p_info.get("name", "")).strip()
                p_short = str(p_info.get("shortName", p_name)).strip()
                p_pos = str(
                    p_entry.get("position") or p_info.get("position") or ""
                ).strip()
                p_shirt = str(
                    p_entry.get("shirtNumber")
                    or p_entry.get("jerseyNumber")
                    or p_info.get("jerseyNumber")
                    or ""
                ).strip()

                p_stats = p_entry.get("statistics") or {}
                minutes = int(p_stats.get("minutesPlayed", 0) or 0)
                rating_raw = p_stats.get("rating", p_entry.get("avgRating"))
                rating = float(rating_raw) if rating_raw is not None else None

                if rating is not None:
                    match_ratings.append(rating)

                if p_name:
                    convocated_names.append(p_name)

                if p_id not in players_registry:
                    players_registry[p_id] = RegisteredPlayerRecord(
                        team_id=team_id,
                        team_name=team_name,
                        player_id=p_id,
                        player_name=p_name,
                        short_name=p_short,
                        position=p_pos,
                        jersey_number=p_shirt,
                    )

                players_registry[p_id].register_appearance(
                    debug_match_id=debug_match_id,
                    event_id=event_id,
                    minutes_played=minutes,
                    rating=rating,
                    position=p_pos,
                    jersey_number=p_shirt,
                )

            promedio_valoracion = (
                round(sum(match_ratings) / len(match_ratings), 2)
                if match_ratings
                else None
            )

            stats_payload = self.client.get_event_statistics(event_id)
            stat_items = extract_all_period_stats(stats_payload)

            extracted_team_stats: Dict[str, Optional[float | int]] = {}
            tiros_rival_al_arco: Optional[int] = None

            for metric_name, matcher in STAT_KEYS_MAP.items():
                h_val, a_val = find_stat_values(
                    stat_items, matcher["keys"], matcher["names"]
                )
                team_val = h_val if is_home else a_val
                opp_val = a_val if is_home else h_val

                if metric_name in ("amarillas", "rojas") and team_val is None and stat_items:
                    team_val = 0

                extracted_team_stats[metric_name] = team_val

                if metric_name == "tiros_al_arco":
                    tiros_rival_al_arco = (
                        int(opp_val) if opp_val is not None else None
                    )

            match_record = MatchMetricsRecord(
                debug_id=self._debug_counter,
                debug_match_id=debug_match_id,
                event_id=event_id,
                fecha_utc=fecha_utc,
                team_id=team_id,
                equipo=team_name,
                opponent_id=opponent_id,
                rival=opponent_name,
                condicion=condicion,
                alineacion_nombres="; ".join(convocated_names),
                goles_a_favor=int(goles_favor) if goles_favor is not None else None,
                goles_en_contra=int(goles_contra) if goles_contra is not None else None,
                tiros_al_arco=(
                    int(extracted_team_stats["tiros_al_arco"])
                    if extracted_team_stats["tiros_al_arco"] is not None
                    else None
                ),
                posesion=(
                    float(extracted_team_stats["posesion"])
                    if extracted_team_stats["posesion"] is not None
                    else None
                ),
                salvadas_arquero=(
                    int(extracted_team_stats["salvadas_arquero"])
                    if extracted_team_stats["salvadas_arquero"] is not None
                    else None
                ),
                corners=(
                    int(extracted_team_stats["corners"])
                    if extracted_team_stats["corners"] is not None
                    else None
                ),
                faltas=(
                    int(extracted_team_stats["faltas"])
                    if extracted_team_stats["faltas"] is not None
                    else None
                ),
                pases=(
                    int(extracted_team_stats["pases"])
                    if extracted_team_stats["pases"] is not None
                    else None
                ),
                tiros_libres=(
                    int(extracted_team_stats["tiros_libres"])
                    if extracted_team_stats["tiros_libres"] is not None
                    else None
                ),
                amarillas=(
                    int(extracted_team_stats["amarillas"])
                    if extracted_team_stats["amarillas"] is not None
                    else None
                ),
                rojas=(
                    int(extracted_team_stats["rojas"])
                    if extracted_team_stats["rojas"] is not None
                    else None
                ),
                promedio_valoracion=promedio_valoracion,
                tiros=(
                    int(extracted_team_stats["tiros"])
                    if extracted_team_stats["tiros"] is not None
                    else None
                ),
                toques_area_rival=(
                    int(extracted_team_stats["toques_area_rival"])
                    if extracted_team_stats["toques_area_rival"] is not None
                    else None
                ),
                offsides=(
                    int(extracted_team_stats["offsides"])
                    if extracted_team_stats["offsides"] is not None
                    else None
                ),
                tacles=(
                    int(extracted_team_stats["tacles"])
                    if extracted_team_stats["tacles"] is not None
                    else None
                ),
                intercepciones=(
                    int(extracted_team_stats["intercepciones"])
                    if extracted_team_stats["intercepciones"] is not None
                    else None
                ),
                recuperaciones=(
                    int(extracted_team_stats["recuperaciones"])
                    if extracted_team_stats["recuperaciones"] is not None
                    else None
                ),
                despejes=(
                    int(extracted_team_stats["despejes"])
                    if extracted_team_stats["despejes"] is not None
                    else None
                ),
                tiros_rival_al_arco=tiros_rival_al_arco,
                competition_id=resolve_competition(self.unique_tournament_id).competition_id,
                competition_name=resolve_competition(self.unique_tournament_id).name,
            )
            match_records.append(match_record)

        squad_athletes = self.client.get_team_current_squad_athletes(team_id)
        current_pids = {int(a["id"]) for a in squad_athletes if "id" in a}
        current_names = {
            _normalize_text(a.get("name", ""))
            for a in squad_athletes
            if a.get("name")
        }
        if current_pids or current_names:
            for p_rec in players_registry.values():
                p_rec.currently_in_squad = (
                    p_rec.player_id in current_pids
                    or _normalize_text(p_rec.player_name) in current_names
                )
            existing_norm_names = {
                _normalize_text(p_rec.player_name)
                for p_rec in players_registry.values()
            }
            comp_meta = resolve_competition(self.unique_tournament_id)
            for ath in squad_athletes:
                aid = int(ath.get("id", 0))
                aname = str(ath.get("name", "")).strip()
                if not aid or not aname:
                    continue
                if aid in players_registry or _normalize_text(aname) in existing_norm_names:
                    continue
                players_registry[aid] = RegisteredPlayerRecord(
                    team_id=team_id,
                    team_name=team_name,
                    player_id=aid,
                    player_name=aname,
                    short_name=str(ath.get("shortName", aname)).strip(),
                    position=str(ath.get("position", "")).strip(),
                    jersey_number=str(ath.get("jerseyNumber", "")).strip(),
                    convocations_count=0,
                    matches_played_count=0,
                    matches_unused_sub_count=0,
                    total_minutes_played=0,
                    average_rating=None,
                    currently_in_squad=True,
                    competition_id=comp_meta.competition_id,
                    competition_name=comp_meta.name,
                )

        return list(players_registry.values()), match_records

    def run_all_teams(
        self,
        players_csv_path: str | Path = "jugadores_registrados_lpf.csv",
        metrics_csv_path: str | Path = "metricas_partidos_lpf.csv",
        season_id: Optional[int] = None,
    ) -> Tuple[Path, Path]:
        if season_id is None:
            season_id = self.get_current_season_id()

        teams = self.client.get_tournament_teams(self.unique_tournament_id, season_id)
        all_players: List[RegisteredPlayerRecord] = []
        all_matches: List[MatchMetricsRecord] = []

        for team in teams:
            logger.info("Procesando equipo: %s (ID: %s)", team.get("name"), team.get("id"))
            team_players, team_matches = self.process_team(team)
            all_players.extend(team_players)
            all_matches.extend(team_matches)

        players_out = export_players_csv(all_players, players_csv_path)
        metrics_out = export_metrics_csv(all_matches, metrics_csv_path)
        return players_out, metrics_out


def export_players_csv(
    players: Sequence[RegisteredPlayerRecord],
    output_path: str | Path = "jugadores_registrados_lpf.csv",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "team_id",
        "equipo",
        "player_id",
        "jugador",
        "nombre_corto",
        "posicion",
        "dorsal",
        "convocatorias_ultimos_20",
        "partidos_jugados",
        "partidos_suplente_sin_ingresar",
        "minutos_totales",
        "promedio_valoracion",
        "pertenece_actualmente",
        "competition_id",
        "competition_name",
        "debug_match_ids",
        "event_ids",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in players:
            writer.writerow(p.to_csv_row())
    return path


def export_metrics_csv(
    matches: Sequence[MatchMetricsRecord],
    output_path: str | Path = "metricas_partidos_lpf.csv",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "debug_id",
        "debug_match_id",
        "event_id",
        "fecha_utc",
        "team_id",
        "equipo",
        "opponent_id",
        "rival",
        "condicion",
        "alineacion_nombres",
        "goles_a_favor",
        "goles_en_contra",
        "tiros_al_arco",
        "posesion",
        "salvadas_arquero",
        "corners",
        "faltas",
        "pases",
        "tiros_libres",
        "amarillas",
        "rojas",
        "promedio_valoracion",
        "tiros",
        "toques_area_rival",
        "offsides",
        "tacles",
        "intercepciones",
        "recuperaciones",
        "despejes",
        "tiros_rival_al_arco",
        "competition_id",
        "competition_name",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m.to_csv_row())
    return path


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Extrae los últimos 20 partidos por equipo de cualquier Liga o Competición "
            "(Multi-Liga: LPF, EPL, LaLiga, Serie A, Bundesliga, Ligue 1, Brasileirão, Libertadores, UCL) "
            "y genera dos archivos CSV (jugadores registrados y métricas por partido)."
        )
    )
    parser.add_argument(
        "--provider",
        choices=("365scores", "sofascore"),
        default="365scores",
        help="Proveedor de API pública a utilizar (default: 365scores, funciona en vivo sin llaves ni bloqueos)",
    )
    parser.add_argument(
        "--competition",
        default="LPF",
        help="Código o ID de la liga/competición (ej: LPF, EPL, LALIGA, SERIEA, BUNDESLIGA, BRASILEIRAO, LIBERTADORES, UCL, 72)",
    )
    parser.add_argument(
        "--list-competitions",
        action="store_true",
        help="Listar todas las ligas y competiciones preconfiguradas con sus IDs y coeficientes de fuerza",
    )
    parser.add_argument(
        "--players-out",
        default="jugadores_registrados_lpf.csv",
        help="Ruta del CSV de jugadores registrados (default: jugadores_registrados_lpf.csv)",
    )
    parser.add_argument(
        "--metrics-out",
        default="metricas_partidos_lpf.csv",
        help="Ruta del CSV de métricas por partido (default: metricas_partidos_lpf.csv)",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=20,
        help="Cantidad de últimos partidos por equipo (default: 20)",
    )
    parser.add_argument(
        "--team",
        default=None,
        help=(
            "Filtrar por un único equipo usando su nombre o ID "
            "(ej: --team \"River Plate\", --team \"boca\", --team 868)"
        ),
    )
    parser.add_argument(
        "--list-teams",
        action="store_true",
        help="Listar todos los equipos disponibles de la liga seleccionada con sus IDs y salir",
    )
    parser.add_argument(
        "--max-teams",
        type=int,
        default=None,
        help="Limitar la cantidad de equipos a procesar (opcional, útil para pruebas rápidas)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Pausa en segundos entre peticiones HTTP (default: 0.15)",
    )
    args = parser.parse_args()

    if args.list_competitions:
        print("=== CATÁLOGO MULTI-LIGA Y COMPETICIONES ===")
        for meta in COMPETITIONS_CATALOG.values():
            print(
                f"[{meta.code:<12}] ID 365: {meta.competition_id:<4} | SofaScore: {meta.sofascore_id:<4} | "
                f"Fuerza: {meta.strength_factor:.2f} | {meta.name} ({meta.country} - {meta.confederation})"
            )
        return

    comp_meta = resolve_competition(args.competition)

    if args.provider == "365scores":
        client_365 = Public365ScoresClient(delay_seconds=args.delay)
        if args.list_teams:
            for t in client_365.get_lpf_teams(comp_meta.competition_id):
                print(f"ID: {t['id']:<6} | Nombre: {t['name']} ({comp_meta.name})")
            return

        extractor_365 = LigaProfesional365Extractor(
            client=client_365,
            competition_id=comp_meta.competition_id,
            matches_per_team=args.matches,
        )
        p_path, m_path = extractor_365.run_all_teams(
            players_csv_path=args.players_out,
            metrics_csv_path=args.metrics_out,
            max_teams=args.max_teams,
            team_filter=args.team,
        )
    else:
        client_sf = SofaScoreClient(delay_seconds=args.delay)
        extractor_sf = LigaProfesionalExtractor(
            client=client_sf,
            unique_tournament_id=comp_meta.sofascore_id,
            matches_per_team=args.matches,
        )
        p_path, m_path = extractor_sf.run_all_teams(
            players_csv_path=args.players_out,
            metrics_csv_path=args.metrics_out,
        )

    logger.info("Archivo de jugadores guardado en: %s", p_path.resolve())
    logger.info("Archivo de métricas guardado en: %s", m_path.resolve())


if __name__ == "__main__":
    main()
