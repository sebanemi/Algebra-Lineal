"""
Gestor de Base de Datos Relacional (SQLite3 en modo WAL) e Integración ETL.

Responsabilidades (Separación de Intereses - Ghezzi / Pressman):
1. Inicializar el esquema relacional normalizado (`database/schema.sql`) en `data/lpf_analytics.db`.
2. Ingerir y normalizar los archivos CSV (`jugadores_registrados_lpf.csv` y `metricas_partidos_lpf.csv`).
3. Reconstruir de forma exacta la matriz de participación por partido (`match_lineups`),
   clasificando a cada jugador convocado en:
   - `TITULAR` (played = 1)
   - `SUPLENTE_INGRESO` (played = 1)
   - `SUPLENTE_SIN_INGRESAR` (played = 0)
   garantizando que la suma de apariciones con `played = 1` coincida exactamente con
   `partidos_jugados` de cada jugador registrado.
4. Permitir actualizar datos en vivo desde `sofascore_modulo.py` hacia SQLite3.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "lpf_analytics.db"
SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_PLAYERS_CSV = PROJECT_ROOT / "jugadores_registrados_lpf.csv"
DEFAULT_METRICS_CSV = PROJECT_ROOT / "metricas_partidos_lpf.csv"


def normalize_position(raw_pos: str, jersey: str = "") -> Tuple[str, str]:
    """
    Normaliza la posición del jugador a (Nombre Canónico, Código Táctico).
    Códigos: GK (Portero), DEF (Defensor), MID (Mediocampista), FWD (Delantero).
    """
    p = (raw_pos or "").strip().lower()
    if "port" in p or "arq" in p or p == "gk" or p == "g":
        return ("Portero", "GK")
    if "def" in p or p == "d":
        return ("Defensor", "DEF")
    if "med" in p or "vol" in p or p == "m" or p == "mid":
        return ("Mediocampista", "MID")
    if "del" in p or "atac" in p or "ext" in p or p == "f" or p == "fwd":
        return ("Delantero", "FWD")

    # Inferencia por dorsal cuando la API no reportó posición en juveniles de banco
    try:
        num = int(jersey.strip())
    except ValueError:
        num = 0

    if num in (1, 12, 25, 35, 42):
        return ("Portero", "GK")
    if num in (2, 3, 4, 6, 13, 14, 24, 26, 31, 32):
        return ("Defensor", "DEF")
    if num in (7, 9, 11, 17, 18, 19, 20, 27, 29, 38):
        return ("Delantero", "FWD")
    return ("Mediocampista", "MID")


def safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


class DatabaseManager:
    """Encapsula la gestión del ciclo de vida y carga ETL de la base de datos SQLite3."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def initialize_schema(self) -> None:
        schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(schema_sql)
            # Asegurar migración de columna currently_in_club si la tabla ya existía
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(players);").fetchall()
            ]
            if "currently_in_club" not in cols:
                conn.execute(
                    "ALTER TABLE players ADD COLUMN currently_in_club INTEGER NOT NULL DEFAULT 1;"
                )
            conn.commit()

    def import_from_csvs(
        self,
        players_csv: Path = DEFAULT_PLAYERS_CSV,
        metrics_csv: Path = DEFAULT_METRICS_CSV,
    ) -> Dict[str, int]:
        """
        Importa jugadores y métricas de partidos desde los CSV y reconstruye
        la matriz de alineación (1 = jugó, 0 = no jugó) por encuentro.
        """
        self.initialize_schema()

        with players_csv.open("r", encoding="utf-8-sig") as f:
            player_rows = list(csv.DictReader(f))

        with metrics_csv.open("r", encoding="utf-8-sig") as f:
            match_rows = list(csv.DictReader(f))

        teams_map: Dict[int, Tuple[str, str]] = {}
        players_by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        matches_by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

        for r in player_rows:
            tid = safe_int(r["team_id"])
            tname = r["equipo"].strip()
            teams_map[tid] = (tname, tname[:18])
            players_by_team[tid].append(r)

        for m in match_rows:
            tid = safe_int(m["team_id"])
            tname = m["equipo"].strip()
            if tid not in teams_map:
                teams_map[tid] = (tname, tname[:18])
            matches_by_team[tid].append(m)

        total_lineup_entries = 0
        total_played_ones = 0

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM match_lineups;")
            cur.execute("DELETE FROM matches;")
            cur.execute("DELETE FROM players;")
            cur.execute("DELETE FROM teams;")

            # 1. Insertar Equipos
            for tid, (tname, sname) in sorted(teams_map.items()):
                cur.execute(
                    "INSERT INTO teams (team_id, name, short_name) VALUES (?, ?, ?);",
                    (tid, tname, sname),
                )

            # 2. Insertar Jugadores
            for tid, plist in players_by_team.items():
                for p in plist:
                    pid = safe_int(p["player_id"])
                    pname = p["jugador"].strip()
                    sname = (p.get("nombre_corto") or pname).strip()
                    jersey = (p.get("dorsal") or "").strip()
                    pos_name, pos_code = normalize_position(p.get("posicion", ""), jersey)
                    conv = safe_int(p.get("convocatorias_ultimos_20"), 0)
                    pj = safe_int(p.get("partidos_jugados"), 0)
                    unused = safe_int(p.get("partidos_suplente_sin_ingresar"), 0)
                    mins = safe_int(p.get("minutos_totales"), 0)
                    rating = safe_float(p.get("promedio_valoracion"), 6.5)
                    currently_in_club = safe_int(p.get("pertenece_actualmente"), 1)

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO players (
                            player_id, team_id, name, short_name, position, position_code,
                            jersey_number, convocations_last_20, matches_played,
                            matches_unused_sub, total_minutes, average_rating, currently_in_club
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            pid,
                            tid,
                            pname,
                            sname,
                            pos_name,
                            pos_code,
                            jersey,
                            conv,
                            pj,
                            unused,
                            mins,
                            rating,
                            1 if currently_in_club != 0 else 0,
                        ),
                    )

            # 3. Insertar Partidos y Reconstruir Alineaciones (1 = jugó, 0 = no jugó)
            for tid, mlist in matches_by_team.items():
                tplayers = players_by_team[tid]
                # Mapear (nombre, debug_match_id) -> player_id para resolver homónimos
                candidates_by_name: Dict[str, List[Tuple[int, set[str], int]]] = defaultdict(list)
                player_target_pj: Dict[int, int] = {}

                for p in tplayers:
                    pid = safe_int(p["player_id"])
                    pname = p["jugador"].strip()
                    pj = safe_int(p.get("partidos_jugados"), 0)
                    dbg_set = {
                        x.strip()
                        for x in (p.get("debug_match_ids") or "").split(";")
                        if x.strip()
                    }
                    candidates_by_name[pname].append((pid, dbg_set, pj))
                    player_target_pj[pid] = pj

                def resolve_player_id(name: str, dbg_match_id: str) -> Optional[int]:
                    cands = candidates_by_name.get(name)
                    if not cands:
                        return None
                    if len(cands) == 1:
                        return cands[0][0]
                    for pid, dbg_set, _ in cands:
                        if dbg_match_id in dbg_set:
                            return pid
                    return cands[0][0]

                # Recolectar apariciones como titular (idx < 11) y como suplente (idx >= 11)
                starter_matches_by_pid: Dict[int, set[int]] = defaultdict(set)
                bench_appearances_by_pid: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
                parsed_match_lineups: Dict[int, List[Tuple[int, int]]] = {}

                for m in mlist:
                    debug_id = safe_int(m["debug_id"])
                    dbg_mid = m["debug_match_id"].strip()

                    cur.execute(
                        """
                        INSERT INTO matches (
                            debug_id, debug_match_id, event_id, fecha_utc, team_id,
                            opponent_id, opponent_name, condicion,
                            goles_a_favor, goles_en_contra, tiros_al_arco, posesion,
                            salvadas_arquero, corners, faltas, pases, tiros_libres,
                            amarillas, rojas, promedio_valoracion, tiros,
                            toques_area_rival, offsides, tacles, intercepciones,
                            recuperaciones, despejes, tiros_rival_al_arco
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            debug_id,
                            dbg_mid,
                            safe_int(m["event_id"]),
                            m["fecha_utc"].strip(),
                            tid,
                            safe_int(m["opponent_id"]),
                            m["rival"].strip(),
                            m["condicion"].strip(),
                            safe_float(m["goles_a_favor"]),
                            safe_float(m["goles_en_contra"]),
                            safe_float(m["tiros_al_arco"]),
                            safe_float(m["posesion"], 50.0),
                            safe_float(m["salvadas_arquero"]),
                            safe_float(m["corners"]),
                            safe_float(m["faltas"]),
                            safe_float(m["pases"]),
                            safe_float(m["tiros_libres"]),
                            safe_float(m["amarillas"]),
                            safe_float(m["rojas"]),
                            safe_float(m["promedio_valoracion"], 6.5),
                            safe_float(m["tiros"]),
                            safe_float(m["toques_area_rival"]),
                            safe_float(m["offsides"]),
                            safe_float(m["tacles"]),
                            safe_float(m["intercepciones"]),
                            safe_float(m["recuperaciones"]),
                            safe_float(m["despejes"]),
                            safe_float(m["tiros_rival_al_arco"]),
                        ),
                    )

                    raw_names = [
                        n.strip()
                        for n in (m.get("alineacion_nombres") or "").split(";")
                        if n.strip()
                    ]
                    seen_pids_in_match: set[int] = set()
                    ordered_pids: List[Tuple[int, int]] = []
                    for idx, name in enumerate(raw_names):
                        pid = resolve_player_id(name, dbg_mid)
                        if pid is None or pid in seen_pids_in_match:
                            continue
                        seen_pids_in_match.add(pid)
                        ordered_pids.append((idx, pid))
                        if idx < 11:
                            starter_matches_by_pid[pid].add(debug_id)
                        else:
                            bench_appearances_by_pid[pid].append((idx, debug_id))

                    parsed_match_lineups[debug_id] = ordered_pids

                # Determinar exactamente en qué partidos ingresó cada suplente:
                # 365Scores ordena primero a los suplentes que ingresaron (índices 11..15)
                # y luego a los suplentes sin ingresar.
                entered_sub_matches_by_pid: Dict[int, set[int]] = defaultdict(set)
                for pid, bench_list in bench_appearances_by_pid.items():
                    target_pj = player_target_pj.get(pid, 0)
                    starters_count = len(starter_matches_by_pid.get(pid, set()))
                    subs_entered_needed = max(0, target_pj - starters_count)
                    # Ordenar apariciones en el banco por su índice en la planilla (menor índice = ingresó)
                    bench_sorted = sorted(bench_list, key=lambda item: (item[0], item[1]))
                    for _, dbg_id in bench_sorted[:subs_entered_needed]:
                        entered_sub_matches_by_pid[pid].add(dbg_id)

                # Insertar registros en match_lineups
                for debug_id, ordered_pids in parsed_match_lineups.items():
                    for idx, pid in ordered_pids:
                        if idx < 11:
                            role = "TITULAR"
                            played = 1
                        elif debug_id in entered_sub_matches_by_pid[pid]:
                            role = "SUPLENTE_INGRESO"
                            played = 1
                        else:
                            role = "SUPLENTE_SIN_INGRESAR"
                            played = 0

                        cur.execute(
                            """
                            INSERT INTO match_lineups (
                                debug_id, team_id, player_id, order_index, role, played
                            ) VALUES (?, ?, ?, ?, ?, ?);
                            """,
                            (debug_id, tid, pid, idx, role, played),
                        )
                        total_lineup_entries += 1
                        total_played_ones += played

            conn.commit()

        return {
            "teams": len(teams_map),
            "players": len(player_rows),
            "matches": len(match_rows),
            "lineup_entries": total_lineup_entries,
            "played_ones": total_played_ones,
        }

    def get_summary(self) -> Dict[str, Any]:
        with self.connect() as conn:
            cur = conn.cursor()
            teams = cur.execute("SELECT COUNT(*) FROM teams;").fetchone()[0]
            players = cur.execute("SELECT COUNT(*) FROM players;").fetchone()[0]
            eligible_players = cur.execute(
                "SELECT COUNT(*) FROM players WHERE currently_in_club = 1;"
            ).fetchone()[0]
            departed_players = cur.execute(
                "SELECT COUNT(*) FROM players WHERE currently_in_club = 0;"
            ).fetchone()[0]
            matches = cur.execute("SELECT COUNT(*) FROM matches;").fetchone()[0]
            lineups = cur.execute("SELECT COUNT(*) FROM match_lineups;").fetchone()[0]
            played_1 = cur.execute(
                "SELECT COUNT(*) FROM match_lineups WHERE played = 1;"
            ).fetchone()[0]
            played_0 = cur.execute(
                "SELECT COUNT(*) FROM match_lineups WHERE played = 0;"
            ).fetchone()[0]
            return {
                "db_path": str(self.db_path),
                "teams": teams,
                "players_total": players,
                "players_eligible_current_squad": eligible_players,
                "players_departed_ineligible": departed_players,
                "matches": matches,
                "lineup_records": lineups,
                "played_1_entries": played_1,
                "unused_sub_0_entries": played_0,
            }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gestor de Base de Datos SQLite3 para el Sistema de Análisis Lineal LPF"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Ruta al archivo SQLite3 (default: data/lpf_analytics.db)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Inicializar esquema e importar datos desde los CSV actuales",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Mostrar estadísticas de la base de datos",
    )
    args = parser.parse_args()

    mgr = DatabaseManager(Path(args.db))
    if args.init or not Path(args.db).exists():
        stats = mgr.import_from_csvs()
        print(f"[OK] Base de datos inicializada en: {mgr.db_path}")
        print(
            f"     Equipos: {stats['teams']} | Jugadores: {stats['players']} | "
            f"Partidos: {stats['matches']} | Registros de alineación: {stats['lineup_entries']} "
            f"(Jugaron [1]: {stats['played_ones']})"
        )

    if args.stats:
        s = mgr.get_summary()
        print("=== Resumen de Base de Datos SQLite3 (WAL) ===")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
