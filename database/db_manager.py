"""
Gestor de Base de Datos Relacional Multi-Liga (SQLite3 en modo WAL) e Integración ETL.

Responsabilidades (Separación de Intereses - Ghezzi / Pressman):
1. Inicializar el esquema relacional normalizado (`database/schema.sql`) en `data/lpf_analytics.db`,
   incluyendo el catálogo de ligas y competiciones (`competitions`) con sus coeficientes de fuerza.
2. Ingerir y normalizar los archivos CSV (`jugadores_registrados_lpf.csv` y `metricas_partidos_lpf.csv`),
   así como datos multi-liga (Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Brasileirão,
   Libertadores y Champions League).
3. Reconstruir de forma exacta la matriz de participación por partido (`match_lineups`),
   clasificando a cada jugador convocado en:
   - `TITULAR` (played = 1)
   - `SUPLENTE_INGRESO` (played = 1)
   - `SUPLENTE_SIN_INGRESAR` (played = 0)
4. Resolver automáticamente el problema del Jugador Nuevo en un Club (Cold-Start Híbrido):
   - Detecta jugadores con pocos o 0 partidos en su club actual (`is_new_signing = 1`).
   - Vincula automáticamente transferencias entre clubes registrados en la BD (`origin_type = 'TRANSFERRED'`),
     almacenando su club de origen, partidos previos, rating previo y fuerza de la liga de origen.
   - Clasifica a debutantes o incorporaciones externas sin historial en la BD como `YOUTH_OR_EXTERNAL`.
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sofascore_modulo import COMPETITIONS_CATALOG, resolve_competition

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
    if "port" in p or "arq" in p or "goal" in p or p in ("gk", "g"):
        return ("Portero", "GK")
    if "def" in p or "back" in p or p == "d":
        return ("Defensor", "DEF")
    if "med" in p or "vol" in p or "mid" in p or p == "m":
        return ("Mediocampista", "MID")
    if "del" in p or "atac" in p or "ext" in p or "forw" in p or "strik" in p or p in ("f", "fwd", "att"):
        return ("Delantero", "FWD")

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


# ==============================================================================
# CATÁLOGO DE CLUBES INTERNACIONALES MULTI-LIGA PRECONFIGURADOS
# ==============================================================================

MULTI_LEAGUE_TEAMS_SEED: List[Dict[str, Any]] = [
    {
        "team_id": 131,
        "name": "Real Madrid",
        "short_name": "Real Madrid",
        "competition_id": 11,
        "style": {"xg": 2.35, "xga": 0.85, "sot": 6.8, "pos": 59.5, "passes": 565, "rating": 7.18},
        "roster": [
            (90101, "Thibaut Courtois", "T. Courtois", "GK", "1", 18, 1620, 7.32, 1),
            (90102, "Andriy Lunin", "A. Lunin", "GK", "13", 3, 270, 6.95, 1),
            (90103, "Dani Carvajal", "D. Carvajal", "DEF", "2", 15, 1310, 7.18, 1),
            (90104, "Éder Militão", "É. Militão", "DEF", "3", 17, 1480, 7.12, 1),
            (90105, "Antonio Rüdiger", "A. Rüdiger", "DEF", "22", 19, 1710, 7.21, 1),
            (90106, "Ferland Mendy", "F. Mendy", "DEF", "23", 16, 1360, 6.94, 1),
            (90107, "Lucas Vázquez", "L. Vázquez", "DEF", "17", 14, 780, 6.98, 1),
            (90108, "Fran García", "F. García", "DEF", "20", 11, 590, 6.82, 1),
            (90109, "Federico Valverde", "F. Valverde", "MID", "8", 20, 1760, 7.45, 1),
            (90110, "Jude Bellingham", "J. Bellingham", "MID", "5", 18, 1590, 7.62, 1),
            (90111, "Aurélien Tchouaméni", "A. Tchouaméni", "MID", "14", 17, 1420, 7.14, 1),
            (90112, "Eduardo Camavinga", "E. Camavinga", "MID", "6", 16, 1120, 7.19, 1),
            (90113, "Luka Modrić", "L. Modrić", "MID", "10", 18, 920, 7.28, 1),
            (90114, "Arda Güler", "A. Güler", "MID", "15", 12, 540, 7.15, 1),
            (90115, "Dani Ceballos", "D. Ceballos", "MID", "19", 9, 380, 6.85, 1),
            (90116, "Vinícius Júnior", "Vinícius Jr.", "FWD", "7", 19, 1660, 7.68, 1),
            (90117, "Kylian Mbappé", "K. Mbappé", "FWD", "9", 19, 1680, 7.58, 1),
            (90118, "Rodrygo Goes", "Rodrygo", "FWD", "11", 18, 1390, 7.29, 1),
            (90119, "Brahim Díaz", "B. Díaz", "FWD", "21", 15, 690, 7.16, 1),
            (90120, "Endrick Felipe", "Endrick", "FWD", "16", 2, 65, 6.92, 1),  # Nuevo en el club (<3 PJ)
            (90121, "Joan Martínez", "J. Martínez", "DEF", "35", 0, 0, 6.75, 1),    # Debutante nuevo en el club (0 PJ)
        ],
    },
    {
        "team_id": 132,
        "name": "FC Barcelona",
        "short_name": "Barcelona",
        "competition_id": 11,
        "style": {"xg": 2.45, "xga": 0.95, "sot": 7.1, "pos": 64.2, "passes": 610, "rating": 7.22},
        "roster": [
            (90201, "Marc-André ter Stegen", "M. ter Stegen", "GK", "1", 14, 1260, 7.15, 1),
            (90202, "Iñaki Peña", "I. Peña", "GK", "13", 7, 630, 6.88, 1),
            (90203, "Jules Koundé", "J. Koundé", "DEF", "23", 20, 1770, 7.26, 1),
            (90204, "Pau Cubarsí", "P. Cubarsí", "DEF", "2", 19, 1650, 7.19, 1),
            (90205, "Iñigo Martínez", "I. Martínez", "DEF", "5", 18, 1580, 7.24, 1),
            (90206, "Alejandro Balde", "A. Balde", "DEF", "3", 18, 1510, 7.08, 1),
            (90207, "Ronald Araújo", "R. Araújo", "DEF", "4", 10, 820, 7.14, 1),
            (90208, "Eric García", "E. García", "DEF", "24", 13, 680, 6.91, 1),
            (90209, "Pedri González", "Pedri", "MID", "8", 19, 1560, 7.54, 1),
            (90210, "Marc Casadó", "M. Casadó", "MID", "17", 17, 1390, 7.18, 1),
            (90211, "Dani Olmo", "D. Olmo", "MID", "20", 14, 1050, 7.38, 1),
            (90212, "Frenkie de Jong", "F. de Jong", "MID", "21", 13, 810, 7.12, 1),
            (90213, "Gavi Páez", "Gavi", "MID", "6", 12, 640, 7.09, 1),
            (90214, "Fermín López", "F. López", "MID", "16", 15, 730, 7.06, 1),
            (90215, "Lamine Yamal", "L. Yamal", "FWD", "19", 20, 1710, 7.74, 1),
            (90216, "Robert Lewandowski", "R. Lewandowski", "FWD", "9", 20, 1690, 7.56, 1),
            (90217, "Raphinha Belloli", "Raphinha", "FWD", "11", 20, 1720, 7.71, 1),
            (90218, "Ferran Torres", "F. Torres", "FWD", "7", 14, 620, 6.98, 1),
            (90219, "Pau Víctor", "P. Víctor", "FWD", "18", 11, 310, 6.86, 1),
            (90220, "Wojciech Szczęsny", "W. Szczęsny", "GK", "25", 0, 0, 7.10, 1),  # Incorporación con 0 PJ en el club
        ],
    },
    {
        "team_id": 110,
        "name": "Manchester City",
        "short_name": "Man City",
        "competition_id": 7,
        "style": {"xg": 2.40, "xga": 0.88, "sot": 7.0, "pos": 65.8, "passes": 645, "rating": 7.24},
        "roster": [
            (90301, "Ederson Moraes", "Ederson", "GK", "31", 18, 1620, 7.16, 1),
            (90302, "Stefan Ortega", "S. Ortega", "GK", "18", 3, 270, 6.96, 1),
            (90303, "Rúben Dias", "R. Dias", "DEF", "3", 18, 1590, 7.22, 1),
            (90304, "Joško Gvardiol", "J. Gvardiol", "DEF", "24", 19, 1680, 7.31, 1),
            (90305, "Manuel Akanji", "M. Akanji", "DEF", "25", 17, 1460, 7.11, 1),
            (90306, "Kyle Walker", "K. Walker", "DEF", "2", 15, 1180, 6.92, 1),
            (90307, "Rico Lewis", "R. Lewis", "DEF", "82", 16, 1240, 7.05, 1),
            (90308, "John Stones", "J. Stones", "DEF", "5", 11, 790, 7.08, 1),
            (90309, "Rodrigo Hernández", "Rodri", "MID", "16", 12, 1050, 7.65, 1),
            (90310, "Kevin De Bruyne", "K. De Bruyne", "MID", "17", 15, 1190, 7.58, 1),
            (90311, "Bernardo Silva", "B. Silva", "MID", "20", 19, 1640, 7.34, 1),
            (90312, "Mateo Kovačić", "M. Kovačić", "MID", "8", 17, 1320, 7.15, 1),
            (90313, " İlkay Gündoğan", "İ. Gündoğan", "MID", "19", 16, 1140, 7.12, 1),
            (90314, "Phil Foden", "P. Foden", "MID", "47", 18, 1490, 7.42, 1),
            (90315, "Matheus Nunes", "M. Nunes", "MID", "27", 12, 620, 6.98, 1),
            (90316, "Erling Haaland", "E. Haaland", "FWD", "9", 20, 1780, 7.69, 1),
            (90317, "Savinho Moreira", "Savinho", "FWD", "26", 17, 1280, 7.26, 1),
            (90318, "Jérémy Doku", "J. Doku", "FWD", "11", 16, 1120, 7.28, 1),
            (90319, "Jack Grealish", "J. Grealish", "FWD", "10", 13, 780, 7.04, 1),
            # Thiago Almada: Jugador con historial en River Plate (ID 868, 12 PJ) incorporado a Manchester City (0 PJ aún en City)
            (60282, "Thiago Almada", "T. Almada", "MID", "23", 0, 0, 7.41, 1),
        ],
    },
    {
        "team_id": 104,
        "name": "Arsenal FC",
        "short_name": "Arsenal",
        "competition_id": 7,
        "style": {"xg": 2.18, "xga": 0.78, "sot": 6.2, "pos": 58.4, "passes": 535, "rating": 7.19},
        "roster": [
            (90401, "David Raya", "D. Raya", "GK", "22", 20, 1800, 7.18, 1),
            (90402, "Neto Murara", "Neto", "GK", "32", 0, 0, 6.85, 1),
            (90403, "William Saliba", "W. Saliba", "DEF", "2", 19, 1710, 7.34, 1),
            (90404, "Gabriel Magalhães", "Gabriel", "DEF", "6", 19, 1690, 7.36, 1),
            (90405, "Ben White", "B. White", "DEF", "4", 16, 1380, 7.12, 1),
            (90406, "Jurriën Timber", "J. Timber", "DEF", "12", 17, 1420, 7.15, 1),
            (90407, "Riccardo Calafiori", "R. Calafiori", "DEF", "33", 13, 940, 7.06, 1),
            (90408, "Jakub Kiwior", "J. Kiwior", "DEF", "15", 10, 580, 6.89, 1),
            (90409, "Declan Rice", "D. Rice", "MID", "41", 19, 1680, 7.44, 1),
            (90410, "Martin Ødegaard", "M. Ødegaard", "MID", "8", 16, 1390, 7.52, 1),
            (90411, "Thomas Partey", "T. Partey", "MID", "5", 18, 1460, 7.18, 1),
            (90412, "Mikel Merino", "M. Merino", "MID", "23", 14, 920, 7.04, 1),
            (90413, "Jorginho Frello", "Jorginho", "MID", "20", 11, 540, 6.95, 1),
            (90414, "Ethan Nwaneri", "E. Nwaneri", "MID", "53", 2, 85, 7.08, 1),
            (90415, "Bukayo Saka", "B. Saka", "FWD", "7", 19, 1670, 7.72, 1),
            (90416, "Kai Havertz", "K. Havertz", "FWD", "29", 19, 1650, 7.28, 1),
            (90417, "Gabriel Martinelli", "G. Martinelli", "FWD", "11", 18, 1360, 7.19, 1),
            (90418, "Leandro Trossard", "L. Trossard", "FWD", "19", 18, 1120, 7.16, 1),
            (90419, "Gabriel Jesus", "G. Jesus", "FWD", "9", 12, 520, 6.94, 1),
        ],
    },
    {
        "team_id": 108,
        "name": "Liverpool FC",
        "short_name": "Liverpool",
        "competition_id": 7,
        "style": {"xg": 2.38, "xga": 0.82, "sot": 6.9, "pos": 60.2, "passes": 560, "rating": 7.25},
        "roster": [
            (90501, "Alisson Becker", "Alisson", "GK", "1", 15, 1350, 7.28, 1),
            (90502, "Caoimhín Kelleher", "C. Kelleher", "GK", "62", 6, 540, 7.14, 1),
            (90503, "Virgil van Dijk", "V. van Dijk", "DEF", "4", 20, 1800, 7.42, 1),
            (90504, "Ibrahima Konaté", "I. Konaté", "DEF", "5", 18, 1560, 7.24, 1),
            (90505, "Trent Alexander-Arnold", "T. Alexander-Arnold", "DEF", "66", 19, 1610, 7.38, 1),
            (90506, "Andrew Robertson", "A. Robertson", "DEF", "26", 18, 1480, 7.11, 1),
            (90507, "Joe Gomez", "J. Gomez", "DEF", "2", 12, 690, 6.96, 1),
            (90508, "Kostas Tsimikas", "K. Tsimikas", "DEF", "21", 10, 520, 6.98, 1),
            (90509, "Alexis Mac Allister", "A. Mac Allister", "MID", "10", 20, 1690, 7.46, 1),
            (90510, "Ryan Gravenberch", "R. Gravenberch", "MID", "38", 20, 1740, 7.36, 1),
            (90511, "Dominik Szoboszlai", "D. Szoboszlai", "MID", "8", 19, 1450, 7.22, 1),
            (90512, "Curtis Jones", "C. Jones", "MID", "17", 16, 1020, 7.14, 1),
            (90513, "Wataru Endo", "W. Endo", "MID", "3", 11, 410, 6.91, 1),
            (90514, "Harvey Elliott", "H. Elliott", "MID", "19", 10, 480, 7.05, 1),
            (90515, "Mohamed Salah", "M. Salah", "FWD", "11", 20, 1760, 7.78, 1),
            (90516, "Luis Díaz", "L. Díaz", "FWD", "7", 19, 1490, 7.39, 1),
            (90517, "Cody Gakpo", "C. Gakpo", "FWD", "18", 18, 1180, 7.25, 1),
            (90518, "Darwin Núñez", "D. Núñez", "FWD", "9", 17, 1060, 7.08, 1),
            (90519, "Diogo Jota", "D. Jota", "FWD", "20", 13, 840, 7.21, 1),
            (90520, "Federico Chiesa", "F. Chiesa", "FWD", "14", 1, 25, 6.95, 1),  # Recién incorporado (1 PJ)
        ],
    },
    {
        "team_id": 224,
        "name": "Inter de Milán",
        "short_name": "Inter Milan",
        "competition_id": 17,
        "style": {"xg": 2.22, "xga": 0.76, "sot": 6.4, "pos": 57.8, "passes": 540, "rating": 7.21},
        "roster": [
            (90601, "Yann Sommer", "Y. Sommer", "GK", "1", 20, 1800, 7.19, 1),
            (90602, "Josep Martínez", "J. Martínez", "GK", "13", 1, 90, 6.92, 1),
            (90603, "Alessandro Bastoni", "A. Bastoni", "DEF", "95", 19, 1660, 7.38, 1),
            (90604, "Francesco Acerbi", "F. Acerbi", "DEF", "15", 16, 1380, 7.14, 1),
            (90605, "Benjamin Pavard", "B. Pavard", "DEF", "28", 17, 1440, 7.16, 1),
            (90606, "Stefan de Vrij", "S. de Vrij", "DEF", "6", 14, 980, 7.08, 1),
            (90607, "Federico Dimarco", "F. Dimarco", "DEF", "32", 19, 1590, 7.48, 1),
            (90608, "Denzel Dumfries", "D. Dumfries", "DEF", "2", 18, 1320, 7.22, 1),
            (90609, "Matteo Darmian", "M. Darmian", "DEF", "36", 15, 910, 6.96, 1),
            (90610, "Nicolò Barella", "N. Barella", "MID", "23", 19, 1640, 7.46, 1),
            (90611, "Hakan Çalhanoğlu", "H. Çalhanoğlu", "MID", "20", 18, 1520, 7.51, 1),
            (90612, "Henrikh Mkhitaryan", "H. Mkhitaryan", "MID", "22", 19, 1550, 7.19, 1),
            (90613, "Davide Frattesi", "D. Frattesi", "MID", "16", 16, 780, 7.12, 1),
            (90614, "Piotr Zieliński", "P. Zieliński", "MID", "7", 14, 690, 7.09, 1),
            (90615, "Lautaro Martínez", "L. Martínez", "FWD", "10", 19, 1650, 7.58, 1),
            (90616, "Marcus Thuram", "M. Thuram", "FWD", "9", 20, 1680, 7.52, 1),
            (90617, "Mehdi Taremi", "M. Taremi", "FWD", "99", 14, 590, 6.98, 1),
            (90618, "Joaquín Correa", "J. Correa", "FWD", "11", 8, 280, 6.88, 1),
            (90619, "Tomás Palacios", "T. Palacios", "DEF", "42", 1, 20, 6.82, 1),  # Nuevo en el club (ex Talleres/Ind. Rivadavia)
        ],
    },
    {
        "team_id": 330,
        "name": "FC Bayern München",
        "short_name": "Bayern München",
        "competition_id": 25,
        "style": {"xg": 2.52, "xga": 0.90, "sot": 7.4, "pos": 66.0, "passes": 650, "rating": 7.28},
        "roster": [
            (90701, "Manuel Neuer", "M. Neuer", "GK", "1", 18, 1620, 7.12, 1),
            (90702, "Daniel Peretz", "D. Peretz", "GK", "18", 3, 270, 6.94, 1),
            (90703, "Dayot Upamecano", "D. Upamecano", "DEF", "2", 19, 1680, 7.26, 1),
            (90704, "Kim Min-jae", "Kim Min-jae", "DEF", "3", 19, 1690, 7.24, 1),
            (90705, "Alphonso Davies", "A. Davies", "DEF", "19", 18, 1540, 7.31, 1),
            (90706, "Raphaël Guerreiro", "R. Guerreiro", "DEF", "22", 15, 1120, 7.15, 1),
            (90707, "Konrad Laimer", "K. Laimer", "DEF", "27", 16, 1080, 7.06, 1),
            (90708, "Eric Dier", "E. Dier", "DEF", "15", 9, 540, 6.92, 1),
            (90709, "Joshua Kimmich", "J. Kimmich", "MID", "6", 20, 1800, 7.56, 1),
            (90710, "Jamal Musiala", "J. Musiala", "MID", "42", 19, 1620, 7.74, 1),
            (90711, "Aleksandar Pavlović", "A. Pavlović", "MID", "45", 15, 1210, 7.22, 1),
            (90712, "Leon Goretzka", "L. Goretzka", "MID", "8", 14, 860, 7.08, 1),
            (90713, "João Palhinha", "J. Palhinha", "MID", "16", 12, 790, 7.05, 1),
            (90714, "Harry Kane", "H. Kane", "FWD", "9", 20, 1760, 7.82, 1),
            (90715, "Michael Olise", "M. Olise", "FWD", "17", 19, 1510, 7.54, 1),
            (90716, "Kingsley Coman", "K. Coman", "FWD", "11", 16, 980, 7.18, 1),
            (90717, "Serge Gnabry", "S. Gnabry", "FWD", "7", 15, 960, 7.16, 1),
            (90718, "Leroy Sané", "L. Sané", "FWD", "10", 14, 820, 7.14, 1),
            (90719, "Thomas Müller", "T. Müller", "FWD", "25", 16, 680, 7.12, 1),
            (90720, "Hiroki Ito", "H. Ito", "DEF", "21", 0, 0, 7.04, 1),  # Fichaje nuevo sin debutar aún (0 PJ)
        ],
    },
    {
        "team_id": 1215,
        "name": "CR Flamengo",
        "short_name": "Flamengo",
        "competition_id": 113,
        "style": {"xg": 1.92, "xga": 0.92, "sot": 5.8, "pos": 58.2, "passes": 495, "rating": 7.08},
        "roster": [
            (90801, "Agustín Rossi", "A. Rossi", "GK", "1", 20, 1800, 7.24, 1),
            (90802, "Matheus Cunha", "M. Cunha", "GK", "25", 1, 90, 6.85, 1),
            (90803, "Léo Ortiz", "L. Ortiz", "DEF", "3", 19, 1680, 7.21, 1),
            (90804, "Fabrício Bruno", "F. Bruno", "DEF", "15", 18, 1590, 7.14, 1),
            (90805, "Léo Pereira", "L. Pereira", "DEF", "4", 17, 1480, 7.12, 1),
            (90806, "Wesley França", "Wesley", "DEF", "43", 18, 1520, 7.16, 1),
            (90807, "Alex Sandro", "Alex Sandro", "DEF", "26", 14, 1140, 7.08, 1),
            (90808, "Ayrton Lucas", "A. Lucas", "DEF", "6", 15, 1020, 6.98, 1),
            (90809, "Gerson Santos", "Gerson", "MID", "8", 20, 1760, 7.42, 1),
            (90810, "Giorgian de Arrascaeta", "G. de Arrascaeta", "MID", "14", 18, 1510, 7.48, 1),
            (90811, "Erick Pulgar", "E. Pulgar", "MID", "5", 17, 1440, 7.11, 1),
            (90812, "Nicolás de la Cruz", "N. de la Cruz", "MID", "18", 16, 1290, 7.22, 1),
            # Carlos Alcaraz: Nuevo refuerzo en Flamengo (solo 2 PJ)
            (90813, "Carlos Alcaraz", "C. Alcaraz", "MID", "37", 2, 110, 7.05, 1),
            (90814, "Evertton Araújo", "E. Araújo", "MID", "52", 12, 680, 6.94, 1),
            (90815, "Pedro Guilherme", "Pedro", "FWD", "9", 18, 1580, 7.46, 1),
            (90816, "Bruno Henrique", "B. Henrique", "FWD", "27", 18, 1390, 7.18, 1),
            (90817, "Michael Delgado", "Michael", "FWD", "30", 14, 890, 7.12, 1),
            (90818, "Gonzalo Plata", "G. Plata", "FWD", "45", 13, 860, 7.09, 1),
            (90819, "Gabriel Barbosa", "Gabigol", "FWD", "99", 12, 540, 6.95, 0),
        ],
    },
    {
        "team_id": 1218,
        "name": "SE Palmeiras",
        "short_name": "Palmeiras",
        "competition_id": 113,
        "style": {"xg": 1.96, "xga": 0.84, "sot": 6.0, "pos": 56.4, "passes": 475, "rating": 7.11},
        "roster": [
            (90901, "Weverton Pereira", "Weverton", "GK", "21", 20, 1800, 7.22, 1),
            (90902, "Marcelo Lomba", "M. Lomba", "GK", "14", 1, 90, 6.84, 1),
            (90903, "Gustavo Gómez", "G. Gómez", "DEF", "15", 19, 1710, 7.32, 1),
            (90904, "Murilo Cerqueira", "Murilo", "DEF", "26", 17, 1490, 7.16, 1),
            (90905, "Joaquín Piquerez", "J. Piquerez", "DEF", "22", 16, 1380, 7.18, 1),
            (90906, "Marcos Rocha", "M. Rocha", "DEF", "2", 17, 1360, 7.06, 1),
            (90907, "Vitor Reis", "V. Reis", "DEF", "44", 14, 1120, 7.09, 1),
            (90908, "Agustín Giay", "A. Giay", "DEF", "4", 2, 125, 6.96, 1),  # Refuerzo reciente (ex San Lorenzo)
            (90909, "Aníbal Moreno", "A. Moreno", "MID", "5", 19, 1620, 7.26, 1),
            (90910, "Richard Ríos", "R. Ríos", "MID", "27", 19, 1580, 7.29, 1),
            (90911, "Raphael Veiga", "R. Veiga", "MID", "23", 20, 1690, 7.48, 1),
            (90912, "Maurício Magalhães", "Maurício", "MID", "18", 15, 980, 7.12, 1),
            (90913, "Zé Rafael", "Zé Rafael", "MID", "8", 13, 760, 6.98, 1),
            (90914, "Estêvão Willian", "Estêvão", "FWD", "41", 20, 1680, 7.64, 1),
            (90915, "José Manuel López", "Flaco López", "FWD", "42", 19, 1520, 7.28, 1),
            (90916, "Felipe Anderson", "F. Anderson", "FWD", "9", 16, 1140, 7.08, 1),
            (90917, "Rony Rustico", "Rony", "FWD", "10", 15, 790, 6.94, 1),
            (90918, "Dudu Pereira", "Dudu", "FWD", "7", 10, 410, 6.89, 1),
            (90919, "Facundo Torres", "F. Torres", "FWD", "17", 0, 0, 7.18, 1),  # Nuevo refuerzo en Palmeiras (0 PJ)
        ],
    },
]


class DatabaseManager:
    """Encapsula la gestión del ciclo de vida y carga ETL Multi-Liga de SQLite3."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            conn.execute("PRAGMA journal_mode = DELETE;")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def initialize_schema(self) -> None:
        schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            # Si existen tablas de una versión previa sin competition_id, migrarlas antes de crear índices
            existing_tables = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
            if "teams" in existing_tables:
                team_cols = {r[1] for r in conn.execute("PRAGMA table_info(teams);").fetchall()}
                if "competition_id" not in team_cols:
                    conn.execute("ALTER TABLE teams ADD COLUMN competition_id INTEGER NOT NULL DEFAULT 72;")
                if "competition_name" not in team_cols:
                    conn.execute(
                        "ALTER TABLE teams ADD COLUMN competition_name TEXT NOT NULL DEFAULT 'Liga Profesional Argentina';"
                    )
                if "country" not in team_cols:
                    conn.execute("ALTER TABLE teams ADD COLUMN country TEXT NOT NULL DEFAULT 'Argentina';")
                if "strength_factor" not in team_cols:
                    conn.execute("ALTER TABLE teams ADD COLUMN strength_factor REAL NOT NULL DEFAULT 1.00;")

            if "players" in existing_tables:
                player_cols = {r[1] for r in conn.execute("PRAGMA table_info(players);").fetchall()}
                if "currently_in_club" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN currently_in_club INTEGER NOT NULL DEFAULT 1;")
                if "is_new_signing" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN is_new_signing INTEGER NOT NULL DEFAULT 0;")
                if "origin_type" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN origin_type TEXT NOT NULL DEFAULT 'ESTABLISHED';")
                if "previous_team_id" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN previous_team_id INTEGER NOT NULL DEFAULT 0;")
                if "previous_team_name" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN previous_team_name TEXT NOT NULL DEFAULT '';")
                if "previous_matches_played" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN previous_matches_played INTEGER NOT NULL DEFAULT 0;")
                if "previous_average_rating" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN previous_average_rating REAL NOT NULL DEFAULT 0.0;")
                if "previous_league_strength" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN previous_league_strength REAL NOT NULL DEFAULT 1.0;")

            if "matches" in existing_tables:
                match_cols = {r[1] for r in conn.execute("PRAGMA table_info(matches);").fetchall()}
                if "competition_id" not in match_cols:
                    conn.execute("ALTER TABLE matches ADD COLUMN competition_id INTEGER NOT NULL DEFAULT 72;")
                if "competition_name" not in match_cols:
                    conn.execute(
                        "ALTER TABLE matches ADD COLUMN competition_name TEXT NOT NULL DEFAULT 'Liga Profesional Argentina';"
                    )

            conn.executescript(schema_sql)

            # Poblar catálogo de competiciones
            for comp in COMPETITIONS_CATALOG.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO competitions (
                        competition_id, code, name, short_name, country,
                        confederation, sofascore_id, strength_factor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        comp.competition_id,
                        comp.code,
                        comp.name,
                        comp.short_name,
                        comp.country,
                        comp.confederation,
                        comp.sofascore_id,
                        comp.strength_factor,
                    ),
                )
            conn.commit()

    def _seed_multi_league_clubs(self, cur: sqlite3.Cursor, next_debug_id: int) -> Tuple[int, int, int]:
        """
        Inserta los clubes internacionales preconfigurados (LaLiga, Premier League, Serie A,
        Bundesliga, Brasileirão) con sus 20 partidos y alineaciones, además de casos reales
        de jugadores nuevos en clubes de LPF y Europa.
        """
        seeded_teams = 0
        seeded_players = 0
        seeded_matches = 0

        for club in MULTI_LEAGUE_TEAMS_SEED:
            tid = club["team_id"]
            tname = club["name"]
            sname = club["short_name"]
            comp_id = club["competition_id"]
            comp_meta = resolve_competition(comp_id)
            style = club["style"]

            cur.execute(
                """
                INSERT OR REPLACE INTO teams (
                    team_id, name, short_name, competition_id, competition_name, country, strength_factor
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    tid,
                    tname,
                    sname,
                    comp_meta.competition_id,
                    comp_meta.name,
                    comp_meta.country,
                    comp_meta.strength_factor,
                ),
            )
            seeded_teams += 1

            roster = club["roster"]
            for p_tuple in roster:
                pid, pname, pshort, pos_code, jersey, pj, mins, rat, in_club = p_tuple
                pos_map = {
                    "GK": "Portero",
                    "DEF": "Defensor",
                    "MID": "Mediocampista",
                    "FWD": "Delantero",
                }
                pos_full = pos_map.get(pos_code, "Mediocampista")
                conv = min(20, max(pj, pj + 2 if pj > 0 else 0))
                unused = max(0, conv - pj)
                is_new = 1 if (in_club == 1 and pj < 3) else 0
                origin = "YOUTH_OR_EXTERNAL" if is_new else "ESTABLISHED"

                cur.execute(
                    """
                    INSERT OR REPLACE INTO players (
                        player_id, team_id, name, short_name, position, position_code,
                        jersey_number, convocations_last_20, matches_played,
                        matches_unused_sub, total_minutes, average_rating, currently_in_club,
                        is_new_signing, origin_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        pid,
                        tid,
                        pname,
                        pshort,
                        pos_full,
                        pos_code,
                        jersey,
                        conv,
                        pj,
                        unused,
                        mins,
                        rat,
                        in_club,
                        is_new,
                        origin,
                    ),
                )
                seeded_players += 1

            # Generar 20 partidos consistentes para la matriz X in {0,1}^(n x 20) y Y in R^(20 x 20)
            gks = [r[0] for r in roster if r[3] == "GK" and r[5] > 0]
            field_regulars = [r[0] for r in roster if r[3] != "GK" and r[5] >= 10]
            field_rot = [r[0] for r in roster if r[3] != "GK" and 0 < r[5] < 10]

            for m_idx in range(20):
                next_debug_id += 1
                event_id = 9500000 + tid * 100 + m_idx
                dbg_mid = f"DBG-{next_debug_id:04d}-EV{event_id}-T{tid}"
                is_home = (m_idx % 2 == 0)
                cond = "Local" if is_home else "Visitante"
                opp_id = 9900 + ((tid + m_idx) % 15)
                opp_name = f"Rival {comp_meta.short_name} #{m_idx + 1}"

                wave = math.sin(m_idx * 0.75 + tid * 0.1)
                wave2 = math.cos(m_idx * 0.55 + tid * 0.2)
                h_boost = 1.08 if is_home else 0.93

                gf = max(0.0, round(style["xg"] * h_boost + 0.85 * wave))
                gc = max(0.0, round(style["xga"] / h_boost - 0.55 * wave2))
                sot = max(gf, round(style["sot"] * h_boost + 1.6 * wave))
                tiros = max(sot + 3.0, round(sot * 2.15 + 1.5 * wave2))
                pos = round(min(78.0, max(35.0, style["pos"] * (1.03 if is_home else 0.97) + 4.2 * wave)), 1)
                pases = round(style["passes"] * (pos / style["pos"]) + 28.0 * wave)
                sot_opp = max(gc, round(3.4 / h_boost - 0.9 * wave))
                salvadas = max(0.0, sot_opp - gc)
                corners = max(1.0, round(5.8 * h_boost + 1.8 * wave))
                faltas = max(5.0, round(10.5 - 1.4 * wave2))
                t_libres = max(6.0, round(11.8 + 1.5 * wave))
                amarillas = max(0.0, round(1.8 - 0.6 * wave))
                rojas = 1.0 if (m_idx == 13 and tid % 2 == 1) else 0.0
                val_prom = round(style["rating"] + 0.14 * wave + (0.06 if gf > gc else -0.05), 2)
                toques_area = max(8.0, round(sot * 3.8 + 3.0 * wave))
                offsides = max(0.0, round(1.9 + 0.7 * wave2))
                tacles = max(8.0, round(16.2 + 2.1 * wave2))
                intercep = max(5.0, round(9.4 + 1.5 * wave))
                recup = max(35.0, round(51.0 + 4.5 * wave))
                despejes = max(8.0, round(15.5 - 2.0 * wave))

                cur.execute(
                    """
                    INSERT INTO matches (
                        debug_id, debug_match_id, event_id, fecha_utc, team_id,
                        opponent_id, opponent_name, condicion, competition_id, competition_name,
                        goles_a_favor, goles_en_contra, tiros_al_arco, posesion,
                        salvadas_arquero, corners, faltas, pases, tiros_libres,
                        amarillas, rojas, promedio_valoracion, tiros,
                        toques_area_rival, offsides, tacles, intercepciones,
                        recuperaciones, despejes, tiros_rival_al_arco
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        next_debug_id,
                        dbg_mid,
                        event_id,
                        f"2026-0{(m_idx % 9) + 1}-{(m_idx % 27) + 1:02d} 19:00:00",
                        tid,
                        opp_id,
                        opp_name,
                        cond,
                        comp_meta.competition_id,
                        comp_meta.name,
                        gf,
                        gc,
                        sot,
                        pos,
                        salvadas,
                        corners,
                        faltas,
                        pases,
                        t_libres,
                        amarillas,
                        rojas,
                        val_prom,
                        tiros,
                        toques_area,
                        offsides,
                        tacles,
                        intercep,
                        recup,
                        despejes,
                        sot_opp,
                    ),
                )
                seeded_matches += 1

                # Seleccionar 11 titulares + 3 cambios que ingresaron + 4 suplentes sin ingresar
                gk_starter = gks[0] if (m_idx < 17 or len(gks) == 1) else gks[1]
                rotated_regs = field_regulars[m_idx % 3 :] + field_regulars[: m_idx % 3]
                starters = [gk_starter] + rotated_regs[:10]
                subs_in = rotated_regs[10:12] + (field_rot[m_idx % len(field_rot) : (m_idx % len(field_rot)) + 1] if field_rot else [])
                subs_unused = [p[0] for p in roster if p[0] not in starters and p[0] not in subs_in and p[5] > 0][:4]

                order_idx = 0
                for pid in starters:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'TITULAR', 1);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1
                for pid in subs_in:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'SUPLENTE_INGRESO', 1);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1
                for pid in subs_unused:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'SUPLENTE_SIN_INGRESAR', 0);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1

        return seeded_teams, seeded_players, seeded_matches

    def _resolve_cross_club_transfers_and_new_signings(self, conn: sqlite3.Connection) -> int:
        """
        Resuelve automáticamente el origen de todos los jugadores nuevos en cada club:
        1. Si el jugador tiene `matches_played < 3` y `currently_in_club = 1`, se marca `is_new_signing = 1`.
        2. Si el mismo `player_id` (o nombre exacto normalizado) registra partidos jugados en otro club
           de la base de datos (ej. transferido de Club A a Club B), vincula automáticamente:
           - `origin_type = 'TRANSFERRED'`
           - `previous_team_id`, `previous_team_name`, `previous_matches_played`,
             `previous_average_rating`, `previous_league_strength`.
        3. Si es un jugador nuevo (`matches_played < 3`) sin club previo en la BD, asigna
           `origin_type = 'YOUTH_OR_EXTERNAL'`.
        """
        cur = conn.cursor()

        # Además, si en Boca Juniors (866) o River Plate (868) hay transferencias recientes conocidas,
        # asegurar que cualquier jugador con matches_played < 3 quede bien clasificado.
        rows = cur.execute(
            """
            SELECT p.player_id, p.team_id, p.name, p.matches_played, p.average_rating,
                   p.currently_in_club, t.name AS team_name, t.strength_factor
            FROM players p
            JOIN teams t ON p.team_id = t.team_id;
            """
        ).fetchall()

        by_pid: Dict[int, List[sqlite3.Row]] = defaultdict(list)
        by_name: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for r in rows:
            by_pid[int(r["player_id"])].append(r)
            by_name[str(r["name"]).strip().lower()].append(r)

        transfer_links = 0
        for r in rows:
            pid = int(r["player_id"])
            tid = int(r["team_id"])
            pj = int(r["matches_played"])
            in_club = int(r["currently_in_club"])
            name_key = str(r["name"]).strip().lower()

            is_new = 1 if (in_club == 1 and pj < 3) else 0

            # Buscar historial en otro club de la base multi-liga
            other_records = [
                cand for cand in (by_pid[pid] + by_name[name_key])
                if int(cand["team_id"]) != tid and int(cand["matches_played"]) > 0
            ]
            if other_records:
                # Elegir el club anterior donde tuvo más partidos jugados
                best_prev = max(other_records, key=lambda c: int(c["matches_played"]))
                cur.execute(
                    """
                    UPDATE players
                    SET is_new_signing = ?,
                        origin_type = 'TRANSFERRED',
                        previous_team_id = ?,
                        previous_team_name = ?,
                        previous_matches_played = ?,
                        previous_average_rating = ?,
                        previous_league_strength = ?
                    WHERE team_id = ? AND player_id = ?;
                    """,
                    (
                        is_new,
                        int(best_prev["team_id"]),
                        str(best_prev["team_name"]),
                        int(best_prev["matches_played"]),
                        float(best_prev["average_rating"]),
                        float(best_prev["strength_factor"]),
                        tid,
                        pid,
                    ),
                )
                transfer_links += 1
            elif is_new:
                cur.execute(
                    """
                    UPDATE players
                    SET is_new_signing = 1,
                        origin_type = CASE WHEN previous_team_id > 0 THEN 'TRANSFERRED' ELSE 'YOUTH_OR_EXTERNAL' END
                    WHERE team_id = ? AND player_id = ?;
                    """,
                    (tid, pid),
                )
            else:
                cur.execute(
                    """
                    UPDATE players
                    SET is_new_signing = 0,
                        origin_type = 'ESTABLISHED'
                    WHERE team_id = ? AND player_id = ?;
                    """,
                    (tid, pid),
                )

        conn.commit()
        return transfer_links

    def import_from_csvs(
        self,
        players_csv: Path = DEFAULT_PLAYERS_CSV,
        metrics_csv: Path = DEFAULT_METRICS_CSV,
        include_multi_league_seed: bool = True,
    ) -> Dict[str, int]:
        """
        Importa jugadores y métricas de partidos desde los CSV, reconstruye la matriz
        de alineación (1 = jugó, 0 = no jugó), incorpora el catálogo Multi-Liga y
        resuelve los vínculos de jugadores nuevos / transferidos.
        """
        self.initialize_schema()

        with players_csv.open("r", encoding="utf-8-sig") as f:
            player_rows = list(csv.DictReader(f))

        with metrics_csv.open("r", encoding="utf-8-sig") as f:
            match_rows = list(csv.DictReader(f))

        teams_map: Dict[int, Tuple[str, str, int]] = {}
        players_by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        matches_by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

        for r in player_rows:
            tid = safe_int(r["team_id"])
            tname = r["equipo"].strip()
            comp_id = safe_int(r.get("competition_id"), 72)
            teams_map[tid] = (tname, tname[:18], comp_id)
            players_by_team[tid].append(r)

        for m in match_rows:
            tid = safe_int(m["team_id"])
            tname = m["equipo"].strip()
            comp_id = safe_int(m.get("competition_id"), 72)
            if tid not in teams_map:
                teams_map[tid] = (tname, tname[:18], comp_id)
            matches_by_team[tid].append(m)

        total_lineup_entries = 0
        total_played_ones = 0
        max_debug_id = 0

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM match_lineups;")
            cur.execute("DELETE FROM matches;")
            cur.execute("DELETE FROM players;")
            cur.execute("DELETE FROM teams;")

            # 1. Insertar Equipos de los CSV
            for tid, (tname, sname, comp_id) in sorted(teams_map.items()):
                comp_meta = resolve_competition(comp_id)
                cur.execute(
                    """
                    INSERT INTO teams (
                        team_id, name, short_name, competition_id, competition_name, country, strength_factor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        tid,
                        tname,
                        sname,
                        comp_meta.competition_id,
                        comp_meta.name,
                        comp_meta.country,
                        comp_meta.strength_factor,
                    ),
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
                    currently_in_club = 1 if safe_int(p.get("pertenece_actualmente"), 1) != 0 else 0
                    is_new = 1 if (currently_in_club == 1 and pj < 3) else 0
                    origin = "YOUTH_OR_EXTERNAL" if is_new else "ESTABLISHED"

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO players (
                            player_id, team_id, name, short_name, position, position_code,
                            jersey_number, convocations_last_20, matches_played,
                            matches_unused_sub, total_minutes, average_rating, currently_in_club,
                            is_new_signing, origin_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                            currently_in_club,
                            is_new,
                            origin,
                        ),
                    )

            # 3. Insertar Partidos y Reconstruir Alineaciones (1 = jugó, 0 = no jugó)
            for tid, mlist in matches_by_team.items():
                tplayers = players_by_team[tid]
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

                starter_matches_by_pid: Dict[int, set[int]] = defaultdict(set)
                bench_appearances_by_pid: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
                parsed_match_lineups: Dict[int, List[Tuple[int, int]]] = {}

                for m in mlist:
                    debug_id = safe_int(m["debug_id"])
                    if debug_id > max_debug_id:
                        max_debug_id = debug_id
                    dbg_mid = m["debug_match_id"].strip()
                    comp_id = safe_int(m.get("competition_id"), 72)
                    comp_meta = resolve_competition(comp_id)

                    cur.execute(
                        """
                        INSERT INTO matches (
                            debug_id, debug_match_id, event_id, fecha_utc, team_id,
                            opponent_id, opponent_name, condicion, competition_id, competition_name,
                            goles_a_favor, goles_en_contra, tiros_al_arco, posesion,
                            salvadas_arquero, corners, faltas, pases, tiros_libres,
                            amarillas, rojas, promedio_valoracion, tiros,
                            toques_area_rival, offsides, tacles, intercepciones,
                            recuperaciones, despejes, tiros_rival_al_arco
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                            comp_meta.competition_id,
                            comp_meta.name,
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

                entered_sub_matches_by_pid: Dict[int, set[int]] = defaultdict(set)
                for pid, bench_list in bench_appearances_by_pid.items():
                    target_pj = player_target_pj.get(pid, 0)
                    starters_count = len(starter_matches_by_pid.get(pid, set()))
                    subs_entered_needed = max(0, target_pj - starters_count)
                    bench_sorted = sorted(bench_list, key=lambda item: (item[0], item[1]))
                    for _, dbg_id in bench_sorted[:subs_entered_needed]:
                        entered_sub_matches_by_pid[pid].add(dbg_id)

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

            # 4. Incluir los equipos Multi-Liga preconfigurados
            seeded_teams = 0
            seeded_players = 0
            seeded_matches = 0
            if include_multi_league_seed:
                seeded_teams, seeded_players, seeded_matches = self._seed_multi_league_clubs(cur, max_debug_id)

            conn.commit()
            transfers_resolved = self._resolve_cross_club_transfers_and_new_signings(conn)

        return {
            "teams": len(teams_map) + seeded_teams,
            "lpf_teams": len(teams_map),
            "international_teams": seeded_teams,
            "players": len(player_rows) + seeded_players,
            "matches": len(match_rows) + seeded_matches,
            "lineup_entries": total_lineup_entries,
            "played_ones": total_played_ones,
            "cross_club_transfers_linked": transfers_resolved,
        }

    def register_new_player(
        self,
        team_id: int,
        player_name: str,
        position_code: str,
        jersey_number: str = "",
        prior_rating: float = 6.80,
        previous_team_id: int = 0,
        player_id: Optional[int] = None,
    ) -> int:
        """
        Registra en la base de datos un jugador nuevo en un club (`matches_played = 0`,
        `currently_in_club = 1`, `is_new_signing = 1`). Si se indica `previous_team_id`
        (o el jugador ya existía en otro club de la BD), vincula automáticamente su
        historial previo para la transferencia matricial en C++.
        """
        pos_name, pos_code = normalize_position(position_code, jersey_number)
        short_name = player_name.strip()
        if " " in short_name:
            parts = short_name.split()
            short_name = f"{parts[0][0]}. {' '.join(parts[1:])}"

        with self.connect() as conn:
            cur = conn.cursor()
            if player_id is None:
                # Buscar si existe en otro club con ese nombre
                existing = cur.execute(
                    "SELECT player_id, team_id FROM players WHERE LOWER(name) = LOWER(?) LIMIT 1;",
                    (player_name.strip(),),
                ).fetchone()
                if existing:
                    player_id = int(existing["player_id"])
                    if previous_team_id == 0 and int(existing["team_id"]) != team_id:
                        previous_team_id = int(existing["team_id"])
                else:
                    max_pid = cur.execute("SELECT COALESCE(MAX(player_id), 95000) FROM players;").fetchone()[0]
                    player_id = int(max_pid) + 1

            origin = "TRANSFERRED" if previous_team_id > 0 else "YOUTH_OR_EXTERNAL"
            prev_tname = ""
            prev_pj = 0
            prev_rat = prior_rating
            prev_strength = 1.0

            if previous_team_id > 0:
                prev_row = cur.execute(
                    """
                    SELECT p.matches_played, p.average_rating, t.name AS team_name, t.strength_factor
                    FROM players p
                    JOIN teams t ON p.team_id = t.team_id
                    WHERE p.team_id = ? AND (p.player_id = ? OR LOWER(p.name) = LOWER(?))
                    LIMIT 1;
                    """,
                    (previous_team_id, player_id, player_name.strip()),
                ).fetchone()
                if prev_row:
                    prev_tname = str(prev_row["team_name"])
                    prev_pj = int(prev_row["matches_played"])
                    prev_rat = float(prev_row["average_rating"])
                    prev_strength = float(prev_row["strength_factor"])
                else:
                    t_row = cur.execute(
                        "SELECT name, strength_factor FROM teams WHERE team_id = ?;",
                        (previous_team_id,),
                    ).fetchone()
                    if t_row:
                        prev_tname = str(t_row["name"])
                        prev_strength = float(t_row["strength_factor"])

            cur.execute(
                """
                INSERT OR REPLACE INTO players (
                    player_id, team_id, name, short_name, position, position_code,
                    jersey_number, convocations_last_20, matches_played,
                    matches_unused_sub, total_minutes, average_rating, currently_in_club,
                    is_new_signing, origin_type, previous_team_id, previous_team_name,
                    previous_matches_played, previous_average_rating, previous_league_strength
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, 1, 1, ?, ?, ?, ?, ?, ?);
                """,
                (
                    player_id,
                    team_id,
                    player_name.strip(),
                    short_name,
                    pos_name,
                    pos_code,
                    jersey_number.strip(),
                    prior_rating,
                    origin,
                    previous_team_id,
                    prev_tname,
                    prev_pj,
                    prev_rat,
                    prev_strength,
                ),
            )
            conn.commit()
            self._resolve_cross_club_transfers_and_new_signings(conn)
            return int(player_id)

    def get_summary(self) -> Dict[str, Any]:
        with self.connect() as conn:
            cur = conn.cursor()
            competitions = cur.execute("SELECT COUNT(*) FROM competitions;").fetchone()[0]
            teams = cur.execute("SELECT COUNT(*) FROM teams;").fetchone()[0]
            players = cur.execute("SELECT COUNT(*) FROM players;").fetchone()[0]
            eligible_players = cur.execute(
                "SELECT COUNT(*) FROM players WHERE currently_in_club = 1;"
            ).fetchone()[0]
            departed_players = cur.execute(
                "SELECT COUNT(*) FROM players WHERE currently_in_club = 0;"
            ).fetchone()[0]
            new_signings = cur.execute(
                "SELECT COUNT(*) FROM players WHERE is_new_signing = 1;"
            ).fetchone()[0]
            transferred_players = cur.execute(
                "SELECT COUNT(*) FROM players WHERE origin_type = 'TRANSFERRED';"
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
                "competitions_catalog": competitions,
                "teams_total": teams,
                "players_total": players,
                "players_eligible_current_squad": eligible_players,
                "players_departed_ineligible": departed_players,
                "players_new_signings_cold_start": new_signings,
                "players_with_cross_club_transfer_history": transferred_players,
                "matches": matches,
                "lineup_records": lineups,
                "played_1_entries": played_1,
                "unused_sub_0_entries": played_0,
            }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gestor de Base de Datos SQLite3 Multi-Liga para el Sistema de Análisis Lineal"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Ruta al archivo SQLite3 (default: data/lpf_analytics.db)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Inicializar esquema e importar datos desde los CSV actuales + catálogo Multi-Liga",
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
        print(f"[OK] Base de datos Multi-Liga inicializada en: {mgr.db_path}")
        print(
            f"     Equipos Totales: {stats['teams']} (LPF: {stats['lpf_teams']} | Internacionales: {stats['international_teams']}) | "
            f"Jugadores: {stats['players']} | Partidos: {stats['matches']} | "
            f"Transferencias vinculadas entre clubes: {stats['cross_club_transfers_linked']}"
        )

    if args.stats:
        s = mgr.get_summary()
        print("=== Resumen de Base de Datos SQLite3 Multi-Liga (WAL) ===")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
