"""
Tests unitarios y demostración de integración para `sofascore_modulo.py`.
Verifica la generación de los dos archivos CSV (`jugadores_registrados_lpf.csv` y
`metricas_partidos_lpf.csv`), el filtrado de los últimos 20 partidos de LPF,
el registro de jugadores hayan jugado o no, y el mapeo de todas las métricas.
"""

from pathlib import Path
import csv
import tempfile
import unittest

from sofascore_modulo import (
    COMPETITIONS_CATALOG,
    LPF_365SCORES_COMPETITION_ID,
    LPF_UNIQUE_TOURNAMENT_ID,
    LigaProfesional365Extractor,
    LigaProfesionalExtractor,
    Public365ScoresClient,
    SofaScoreClient,
    resolve_competition,
)


def build_mock_transport():
    """Simula respuestas reales de la API v1 de SofaScore para pruebas offline."""
    events_page_0 = []
    # Creamos 22 partidos de LPF + 1 de Copa Argentina para verificar el tope de 20 y el filtro de torneo
    for idx in range(1, 23):
        event_id = 12000000 + idx
        is_home = idx % 2 == 1
        home_team = {"id": 3211, "name": "River Plate"} if is_home else {"id": 3202, "name": "Boca Juniors"}
        away_team = {"id": 3202, "name": "Boca Juniors"} if is_home else {"id": 3211, "name": "River Plate"}
        events_page_0.append(
            {
                "id": event_id,
                "startTimestamp": 1750000000 + idx * 86400,
                "status": {"code": 100, "type": "finished"},
                "tournament": {
                    "name": "Liga Profesional de Fútbol",
                    "uniqueTournament": {"id": LPF_UNIQUE_TOURNAMENT_ID, "name": "Liga Profesional de Fútbol"},
                },
                "homeTeam": home_team,
                "awayTeam": away_team,
                "homeScore": {"current": 2, "display": 2},
                "awayScore": {"current": 1, "display": 1},
            }
        )

    # Partido de otro torneo (no debe incluirse)
    events_page_0.append(
        {
            "id": 99999999,
            "startTimestamp": 1760000000,
            "status": {"code": 100, "type": "finished"},
            "tournament": {
                "name": "Copa Argentina",
                "uniqueTournament": {"id": 1033, "name": "Copa Argentina"},
            },
            "homeTeam": {"id": 3211, "name": "River Plate"},
            "awayTeam": {"id": 3205, "name": "Racing Club"},
            "homeScore": {"current": 3, "display": 3},
            "awayScore": {"current": 0, "display": 0},
        }
    )

    def transport(url: str, headers: dict) -> dict:
        if url.endswith(f"/unique-tournament/{LPF_UNIQUE_TOURNAMENT_ID}/seasons"):
            return {"seasons": [{"id": 70001, "name": "Liga Profesional 2026", "year": "2026"}]}

        if url.endswith(f"/unique-tournament/{LPF_UNIQUE_TOURNAMENT_ID}/season/70001/standings/total"):
            return {
                "standings": [
                    {
                        "rows": [
                            {"team": {"id": 3211, "name": "River Plate"}},
                        ]
                    }
                ]
            }

        if "/team/3211/events/last/0" in url:
            return {"events": events_page_0, "hasNextPage": False}

        if "/team/3211/players" in url:
            return {
                "players": [
                    {"player": {"id": 101, "name": "Franco Armani", "shortName": "F. Armani", "position": "G", "jerseyNumber": "1"}},
                    {"player": {"id": 102, "name": "Paulo Díaz", "shortName": "P. Díaz", "position": "D", "jerseyNumber": "17"}},
                    {"player": {"id": 103, "name": "Jeremías Ledesma", "shortName": "J. Ledesma", "position": "G", "jerseyNumber": "25"}},
                    # Jugador nuevo en el club sin convocatorias en los últimos 20 partidos (Cold-Start 0 PJ):
                    {"player": {"id": 999, "name": "Refuerzo Nuevo", "shortName": "R. Nuevo", "position": "F", "jerseyNumber": "9"}},
                ]
            }

        if "/lineups" in url:
            return {
                "confirmed": True,
                "home": {
                    "players": [
                        {
                            "player": {"id": 101, "name": "Franco Armani", "shortName": "F. Armani", "position": "G"},
                            "shirtNumber": 1,
                            "substitute": False,
                            "statistics": {"minutesPlayed": 90, "rating": 7.4},
                        },
                        {
                            "player": {"id": 102, "name": "Paulo Díaz", "shortName": "P. Díaz", "position": "D"},
                            "shirtNumber": 17,
                            "substitute": False,
                            "statistics": {"minutesPlayed": 90, "rating": 7.0},
                        },
                        {
                            "player": {"id": 103, "name": "Jeremías Ledesma", "shortName": "J. Ledesma", "position": "G"},
                            "shirtNumber": 25,
                            "substitute": True,
                            "statistics": {},  # Suplente que NO ingresó (0 minutos)
                        },
                    ]
                },
                "away": {
                    "players": [
                        {
                            "player": {"id": 101, "name": "Franco Armani", "shortName": "F. Armani", "position": "G"},
                            "shirtNumber": 1,
                            "substitute": False,
                            "statistics": {"minutesPlayed": 90, "rating": 7.2},
                        },
                        {
                            "player": {"id": 103, "name": "Jeremías Ledesma", "shortName": "J. Ledesma", "position": "G"},
                            "shirtNumber": 25,
                            "substitute": True,
                            "statistics": {},  # Suplente que NO ingresó
                        },
                    ]
                },
            }

        if "/statistics" in url:
            return {
                "statistics": [
                    {
                        "period": "ALL",
                        "groups": [
                            {
                                "groupName": "Match overview",
                                "statisticsItems": [
                                    {"key": "ballPossession", "name": "Ball possession", "homeValue": 60, "awayValue": 40},
                                    {"key": "totalShotsOnGoal", "name": "Total shots", "homeValue": 15, "awayValue": 9},
                                    {"key": "shotsOnGoal", "name": "Shots on target", "homeValue": 6, "awayValue": 3},
                                    {"key": "cornerKicks", "name": "Corner kicks", "homeValue": 7, "awayValue": 4},
                                    {"key": "fouls", "name": "Fouls", "homeValue": 11, "awayValue": 14},
                                    {"key": "passes", "name": "Passes", "homeValue": 480, "awayValue": 310},
                                    {"key": "freeKicks", "name": "Free kicks", "homeValue": 13, "awayValue": 10},
                                    {"key": "yellowCards", "name": "Yellow cards", "homeValue": 2, "awayValue": 3},
                                    {"key": "redCards", "name": "Red cards", "homeValue": 0, "awayValue": 1},
                                    {"key": "goalkeeperSaves", "name": "Goalkeeper saves", "homeValue": 2, "awayValue": 4},
                                    {"key": "touchesInOppBox", "name": "Touches in penalty area", "homeValue": 24, "awayValue": 12},
                                    {"key": "offsides", "name": "Offsides", "homeValue": 2, "awayValue": 1},
                                    {"key": "totalTackle", "name": "Tackles", "homeValue": 18, "awayValue": 16},
                                    {"key": "interceptionWon", "name": "Interceptions", "homeValue": 9, "awayValue": 11},
                                    {"key": "ballRecovery", "name": "Recoveries", "homeValue": 52, "awayValue": 47},
                                    {"key": "totalClearance", "name": "Clearances", "homeValue": 14, "awayValue": 22},
                                ],
                            }
                        ],
                    }
                ]
            }

        return {}

    return transport


class TestSofaScoreModulo(unittest.TestCase):
    def test_full_pipeline_and_csv_generation(self):
        client = SofaScoreClient(delay_seconds=0, transport=build_mock_transport())
        extractor = LigaProfesionalExtractor(client=client, matches_per_team=20)

        with tempfile.TemporaryDirectory() as tmpdir:
            players_csv = Path(tmpdir) / "jugadores_registrados_lpf.csv"
            metrics_csv = Path(tmpdir) / "metricas_partidos_lpf.csv"

            p_out, m_out = extractor.run_all_teams(
                players_csv_path=players_csv,
                metrics_csv_path=metrics_csv,
            )

            self.assertTrue(p_out.exists())
            self.assertTrue(m_out.exists())

            with p_out.open("r", encoding="utf-8-sig") as f:
                players_rows = list(csv.DictReader(f))

            with m_out.open("r", encoding="utf-8-sig") as f:
                metrics_rows = list(csv.DictReader(f))

            # Deben haberse procesado exactamente 20 partidos de LPF (excluyendo Copa Argentina y los excedentes)
            self.assertEqual(len(metrics_rows), 20)

            # Verificar que el suplente sin minutos (Jeremías Ledesma, ID 103) esté registrado
            ledesma = next(r for r in players_rows if r["player_id"] == "103")
            self.assertEqual(ledesma["convocatorias_ultimos_20"], "20")
            self.assertEqual(ledesma["partidos_jugados"], "0")
            self.assertEqual(ledesma["partidos_suplente_sin_ingresar"], "20")

            # Verificar que el jugador nuevo en el club sin convocatorias previas (ID 999) se registre con 0 PJ y pertenece_actualmente = 1
            nuevo = next(r for r in players_rows if r["player_id"] == "999")
            self.assertEqual(nuevo["convocatorias_ultimos_20"], "0")
            self.assertEqual(nuevo["partidos_jugados"], "0")
            self.assertEqual(nuevo["pertenece_actualmente"], "1")

            # Verificar catálogo Multi-Liga
            self.assertGreaterEqual(len(COMPETITIONS_CATALOG), 10)
            epl = resolve_competition("EPL")
            self.assertEqual(epl.competition_id, 7)
            self.assertGreater(epl.strength_factor, 1.2)

            # Verificar métricas del primer partido procesado (idx=22 -> Visitante)
            first_match = metrics_rows[0]
            self.assertEqual(first_match["debug_id"], "1")
            self.assertTrue(first_match["debug_match_id"].startswith("DBG-0001-EV12000022-T3211"))
            self.assertEqual(first_match["condicion"], "Visitante")
            self.assertEqual(first_match["goles_a_favor"], "1")
            self.assertEqual(first_match["goles_en_contra"], "2")
            self.assertEqual(first_match["tiros_al_arco"], "3")
            self.assertEqual(first_match["tiros_rival_al_arco"], "6")
            self.assertIn("Jeremías Ledesma", first_match["alineacion_nombres"])


if __name__ == "__main__":
    unittest.main()
