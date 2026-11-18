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
import json
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
MULTI_LEAGUE_CATALOG_JSON = PROJECT_ROOT / "data" / "multi_league_catalog.json"


SPECIFIC_POSITION_CATALOG: Dict[str, Tuple[str, str, str]] = {
    # código_específico -> (nombre_específico, nombre_macro, código_macro)
    "POR": ("Portero", "Portero", "GK"),
    "DFC": ("Defensor Central", "Defensor", "DEF"),
    "LI":  ("Lateral Izquierdo", "Defensor", "DEF"),
    "LD":  ("Lateral Derecho", "Defensor", "DEF"),
    "MCD": ("Mediocentro Defensivo", "Mediocampista", "MID"),
    "MC":  ("Mediocentro", "Mediocampista", "MID"),
    "MCO": ("Mediocentro Ofensivo", "Mediocampista", "MID"),
    "MI":  ("Mediocampista Izquierdo", "Mediocampista", "MID"),
    "MD":  ("Mediocampista Derecho", "Mediocampista", "MID"),
    "DC":  ("Delantero Centro", "Delantero", "FWD"),
    "EI":  ("Extremo Izquierdo", "Delantero", "FWD"),
    "ED":  ("Extremo Derecho", "Delantero", "FWD"),
    "SD":  ("Segundo Delantero", "Delantero", "FWD"),
}

# Mapa curado de sub-posiciones tácticas reales para jugadores destacados (LPF + Internacionales)
KNOWN_PLAYER_SUBPOSITIONS: Dict[str, str] = {
    # Laterales Izquierdos (LI)
    "lautaro blanco": "LI", "marcos acuna": "LI", "francisco ortega": "LI", "malcom braida": "LI",
    "teo pagano": "LI", "mathias de ritis": "LI", "lucas carrizo": "LI", "matias vina": "LI",
    "diego sosa": "LI", "elias pereyra": "LI", "dario caceres": "LI", "fernando roman": "LI",
    "juan elordi": "LI", "nicolas colazo": "LI", "nahuel banegas": "LI", "braian cufre": "LI",
    "pedro silva torrejon": "LI", "matias melluso": "LI", "cesar ibanez": "LI", "milton valenzuela": "LI",
    "mateo del blanco": "LI", "ferland mendy": "LI", "fran garcia": "LI", "alejandro balde": "LI",
    "josko gvardiol": "LI", "riccardo calafiori": "LI", "andrew robertson": "LI", "kostas tsimikas": "LI",
    "federico dimarco": "LI", "alphonso davies": "LI", "raphael guerreiro": "LI", "hiroki ito": "LI",
    "alex sandro": "LI", "ayrton lucas": "LI", "joaquin piquerez": "LI", "marc cucurella": "LI",
    "alvaro carreras": "LI", "jorrel hato": "LI", "pep chavarria": "LI",
    # Laterales Derechos (LD)
    "leandro lozano": "LD", "dylan gorosito": "LD", "marcelo weigandt": "LD", "juan barinaga": "LD",
    "gonzalo montiel": "LD", "fabricio bustos": "LD", "giovanni gonzalez": "LD", "ezequiel cannavo": "LD",
    "nahuel arias": "LD", "ezequiel herrera": "LD", "alex vigo": "LD", "lucas blondel": "LD",
    "federico vera": "LD", "martin garay": "LD", "guillermo soto": "LD", "elias lopez": "LD",
    "rodrigo gonzalez": "LD", "alejo osella": "LD", "luciano gomez": "LD", "ezequiel bonifacio": "LD",
    "santiago moyano": "LD", "joaquin garcia": "LD", "gaston martirena": "LD", "alan nunez": "LD",
    "dani carvajal": "LD", "lucas vazquez": "LD", "jules kounde": "LD", "kyle walker": "LD",
    "rico lewis": "LD", "ben white": "LD", "jurrien timber": "LD", "trent alexander-arnold": "LD",
    "denzel dumfries": "LD", "matteo darmian": "LD", "konrad laimer": "LD", "wesley franca": "LD",
    "wesley": "LD", "marcos rocha": "LD", "agustin giay": "LD", "reece james": "LD",
    "malo gusto": "LD", "marco palestra": "LD", "daniel munoz": "LD", "andoni gorosabel": "LD",
    # Defensores Centrales (DFC)
    "lautaro di lollo": "DFC", "nicolas figal": "DFC", "marco pellegrino": "DFC", "ayrton costa": "DFC",
    "facundo herrera": "DFC", "matias satas": "DFC", "lucas martinez quarta": "DFC", "nicolas otamendi": "DFC",
    "german pezzella": "DFC", "lautaro rivero": "DFC", "facundo gonzalez": "DFC", "tobias ramirez": "DFC",
    "juan carlos portillo": "DFC", "emiliano amor": "DFC", "danilo arboleda": "DFC", "guzman corujo": "DFC",
    "marcos rojo": "DFC", "kevin lomonaco": "DFC", "fernando alarcon": "DFC", "leonel mosevich": "DFC",
    "jonatan galvan": "DFC", "gonzalo escudero": "DFC", "mateo martinez": "DFC", "nazareno colombo": "DFC",
    "agustin garcia basso": "DFC", "sergio vittor": "DFC", "nehuen paz": "DFC", "fabio pereyra": "DFC",
    "hugo nervo": "DFC", "german conti": "DFC", "enzo martinez": "DFC", "renzo giampaoli": "DFC",
    "joaquin laso": "DFC", "alan barrionuevo": "DFC", "leonard costa": "DFC", "ivan villalba": "DFC",
    "sheyko studer": "DFC", "leonardo sigali": "DFC", "nestor breitenbruch": "DFC", "carlos quintana": "DFC",
    "miguel barbieri": "DFC", "cristian paz": "DFC", "eder militao": "DFC", "antonio rudiger": "DFC",
    "joan martinez": "DFC", "raul asencio": "DFC", "dean huijsen": "DFC", "pau cubarsi": "DFC",
    "inigo martinez": "DFC", "ronald araujo": "DFC", "eric garcia": "DFC", "ruben dias": "DFC",
    "manuel akanji": "DFC", "john stones": "DFC", "william saliba": "DFC", "gabriel magalhaes": "DFC",
    "jakub kiwior": "DFC", "virgil van dijk": "DFC", "ibrahima konate": "DFC", "joe gomez": "DFC",
    "alessandro bastoni": "DFC", "francesco acerbi": "DFC", "benjamin pavard": "DFC", "stefan de vrij": "DFC",
    "tomas palacios": "DFC", "dayot upamecano": "DFC", "kim min-jae": "DFC", "eric dier": "DFC",
    "leo ortiz": "DFC", "fabricio bruno": "DFC", "leo pereira": "DFC", "gustavo gomez": "DFC",
    "murilo cerqueira": "DFC", "vitor reis": "DFC", "aaron anselmino": "DFC", "wesley fofana": "DFC",
    "levi colwill": "DFC", "maxence lacroix": "DFC", "jose maria gimenez": "DFC", "david alaba": "DFC",
    # Mediocentros Defensivos (MCD)
    "leandro paredes": "MCD", "milton delgado": "MCD", "rodrigo battaglia": "MCD", "camilo rey domenech": "MCD",
    "fausto vera": "MCD", "anibal moreno": "MCD", "matias kranevitter": "MCD", "federico fattori": "MCD",
    "rodrigo fernandez cedres": "MCD", "esteban rolon": "MCD", "franco leys": "MCD", "gustavo abregu": "MCD",
    "franco moyano": "MCD", "ignacio miramon": "MCD", "mateo seoane": "MCD", "augusto max": "MCD",
    "bruno leyes": "MCD", "joaquin mosqueira": "MCD", "tomas bottari": "MCD", "nicolas linares": "MCD",
    "roberto bochi": "MCD", "martin rio": "MCD", "bruno zuculini": "MCD", "santiago sosa": "MCD",
    "aurelien tchouameni": "MCD", "marc casado": "MCD", "rodrigo hernandez": "MCD", "rodri": "MCD",
    "thomas partey": "MCD", "declan rice": "MCD", "ryan gravenberch": "MCD", "wataru endo": "MCD",
    "hakan calhanoglu": "MCD", "joshua kimmich": "MCD", "aleksandar pavlovic": "MCD", "joao palhinha": "MCD",
    "erick pulgar": "MCD", "romeo lavia": "MCD", "moises caicedo": "MCD",
    # Mediocentros Mixtos / Interiores (MC)
    "santiago ascacibar": "MC", "tomas belmonte": "MC", "williams alarcon": "MC", "ander herrera": "MC",
    "mauro arambarri": "MC", "nicolas tripichio": "MC", "manuel insaurralde": "MC", "gonzalo abrego": "MC",
    "gaston lodico": "MC", "ulises ortegoza": "MC", "leonel perez": "MC", "leonardo gil": "MC",
    "facundo waller": "MC", "emmanuel ojeda": "MC", "jose florentin": "MC", "leonel bucca": "MC",
    "ever banega": "MC", "kevin castano": "MC", "jalil elias": "MC", "federico mancuello": "MC",
    "federico valverde": "MC", "eduardo camavinga": "MC", "luka modric": "MC", "dani ceballos": "MC",
    "pedri gonzalez": "MC", "pedri": "MC", "frenkie de jong": "MC", "gavi paez": "MC",
    "mateo kovacic": "MC", "ilkay gundogan": "MC", "matheus nunes": "MC", "mikel merino": "MC",
    "jorginho frello": "MC", "alexis mac allister": "MC", "curtis jones": "MC", "nicolo barella": "MC",
    "henrikh mkhitaryan": "MC", "davide frattesi": "MC", "piotr zielinski": "MC", "leon goretzka": "MC",
    "gerson santos": "MC", "nicolas de la cruz": "MC", "evertton araujo": "MC", "richard rios": "MC",
    "ze rafael": "MC", "enzo fernandez": "MC", "jordan henderson": "MC", "khephren thuram": "MC",
    # Mediocentros Ofensivos / Enganches (MCO)
    "tomas aranda": "MCO", "carlos palacios": "MCO", "thiago almada": "MCO", "tomas galvan": "MCO",
    "juan fernando quintero": "MCO", "facundo gulli": "MCO", "facundo farias": "MCO", "alan lescano": "MCO",
    "ruben botta": "MCO", "nacho fernandez": "MCO", "nicolas barros schelotto": "MCO", "gonzalo martinez": "MCO",
    "oscar romero": "MCO", "matias fernandez": "MCO", "luis sequeira": "MCO", "nico gaitan": "MCO",
    "lucas castro": "MCO", "baltasar rodriguez": "MCO", "matias gallardo": "MCO", "franco vazquez": "MCO",
    "jude bellingham": "MCO", "arda guler": "MCO", "dani olmo": "MCO", "fermin lopez": "MCO",
    "kevin de bruyne": "MCO", "phil foden": "MCO", "bernardo silva": "MCO", "martin odegaard": "MCO",
    "ethan nwaneri": "MCO", "dominik szoboszlai": "MCO", "harvey elliott": "MCO", "jamal musiala": "MCO",
    "giorgian de arrascaeta": "MCO", "carlos alcaraz": "MCO", "raphael veiga": "MCO", "mauricio magalhaes": "MCO",
    "cole palmer": "MCO", "morgan rogers": "MCO", "xavi simons": "MCO", "nicolas paz": "MCO",
    # Mediocampistas Izquierdos (MI) y Derechos (MD)
    "kevin zenon": "MI", "ignacio rodriguez": "MI", "ignacio malcorra": "MI", "valentin barco": "MI",
    "gonzalo rios": "MI", "tiago serrago": "MI", "thaiel peralta": "MI", "juan pablo alvarez": "MI",
    "matias zaracho": "MD", "tobias andrada": "MD", "juan cruz meza": "MD", "giuliano galoppo": "MD",
    "santiago gonzalez": "MD", "jabes saralegui": "MD", "maximiliano meza": "MD", "martin garcia": "MD",
    # Delanteros Centro (DC)
    "miguel merentiel": "DC", "milton gimenez": "DC", "adam bareiro": "DC", "enner valencia": "DC",
    "lucas beltran": "DC", "sebastian driussi": "DC", "rafael borre": "DC", "agustin ruberto": "DC",
    "adrian martinez": "DC", "alexis cuello": "DC", "rodrigo auzmendi": "DC", "gabriel avalos": "DC",
    "bruno sepulveda": "DC", "facundo bruera": "DC", "ivan morales": "DC", "ronaldo martinez": "DC",
    "mauro mendez": "DC", "ignacio russo": "DC", "alfio oviedo": "DC", "jordy caicedo": "DC",
    "agustin auzmendi": "DC", "marcelo torres": "DC", "andres vombergar": "DC", "nicolas cordero": "DC",
    "andres chavez": "DC", "bautista dadin": "DC", "alex arce": "DC", "jonathan herrera": "DC",
    "tiziano perrotta": "DC", "franco jara": "DC", "michael hoyos": "DC", "florian monzon": "DC",
    "kylian mbappe": "DC", "endrick felipe": "DC", "endrick": "DC", "robert lewandowski": "DC",
    "erling haaland": "DC", "kai havertz": "DC", "gabriel jesus": "DC", "darwin nunez": "DC",
    "diogo jota": "DC", "lautaro martinez": "DC", "marcus thuram": "DC", "mehdi taremi": "DC",
    "harry kane": "DC", "pedro guilherme": "DC", "gabriel barbosa": "DC", "jose manuel lopez": "DC",
    "rony rustico": "DC", "hulk": "DC", "jonathan david": "DC", "ezequiel ponce": "DC",
    "alejo veliz": "DC", "joao pedro": "DC",
    # Extremos Izquierdos (EI)
    "sebastian villa": "EI", "exequiel zeballos": "EI", "lucas janson": "EI", "facundo colidio": "EI",
    "ian subiabre": "EI", "tomas conechny": "EI", "matias reali": "EI", "agustin palavecino": "EI",
    "bruno barticciotto": "EI", "santiago lopez": "EI", "vinicius junior": "EI", "vinicius": "EI",
    "raphinha belloli": "EI", "ferran torres": "EI", "jeremy doku": "EI", "jack grealish": "EI",
    "gabriel martinelli": "EI", "leandro trossard": "EI", "luis diaz": "EI", "cody gakpo": "EI",
    "kingsley coman": "EI", "serge gnabry": "EI", "bruno henrique": "EI", "michael delgado": "EI",
    "felipe anderson": "EI", "dudu pereira": "EI", "facundo torres": "EI", "jamie gittens": "EI",
    # Extremos Derechos (ED)
    "leonel flores": "ED", "brahim diaz": "ED", "rodrygo goes": "ED", "rodrygo": "ED",
    "lamine yamal": "ED", "savinho moreira": "ED", "bukayo saka": "ED", "mohamed salah": "ED",
    "federico chiesa": "ED", "michael olise": "ED", "leroy sane": "ED", "gonzalo plata": "ED",
    "estevao willian": "ED", "estevao": "ED", "pedro neto": "ED", "yan diomande": "ED",
    "oscar cortes": "ED", "juan bisanz": "ED", "ignacio pussetto": "ED", "santiago solari": "ED",
    "tomas fernandez": "ED", "alan sosa": "ED", "victorio ramis": "ED",
    # Segundos Delanteros / Media Punta (SD)
    "alan velasco": "SD", "angel romero": "SD", "angel correa": "SD", "maximiliano salas": "SD",
    "joaquin correa": "SD", "lautaro diaz": "SD", "eric ramirez": "SD", "leonardo sequeira": "SD",
    "tomas gonzalez": "SD", "agustin colazo": "SD", "jeronimo domina": "SD", "rodrigo atencio": "SD",
    "thomas muller": "SD", "pau victor": "SD", "iliman ndiaye": "SD",
}


def _clean_lookup_name(text: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def normalize_position_detailed(
    raw_pos: str,
    jersey: str = "",
    player_name: str = "",
    player_id: int = 0,
    avg_rel_slot: Optional[float] = None,
) -> Tuple[str, str, str, str]:
    """
    Normaliza la posición del jugador a 4 valores:
      (Nombre Macro, Código Macro, Nombre Específico, Código Específico)
    - Macro: GK (Portero), DEF (Defensor), MID (Mediocampista), FWD (Delantero)
    - Específico:
      * GK:  POR
      * DEF: DFC, LI, LD
      * MID: MCD, MC, MCO, MI, MD
      * FWD: DC, EI, ED, SD
    """
    p_up = (raw_pos or "").strip().upper()
    if p_up in SPECIFIC_POSITION_CATALOG:
        spec_name, macro_name, macro_code = SPECIFIC_POSITION_CATALOG[p_up]
        return (macro_name, macro_code, spec_name, p_up)

    p = (raw_pos or "").strip().lower()
    # 1. Detectar si raw_pos ya contiene una sub-posición explícita
    if any(x in p for x in ("lat. izq", "lateral izq", "carrilero izq", "left back", "left wing-back")) or p in ("li", "lb", "lwb", "cai"):
        return ("Defensor", "DEF", "Lateral Izquierdo", "LI")
    if any(x in p for x in ("lat. der", "lateral der", "carrilero der", "right back", "right wing-back")) or p in ("ld", "rb", "rwb", "cad"):
        return ("Defensor", "DEF", "Lateral Derecho", "LD")
    if any(x in p for x in ("central", "zaguero", "centre-back", "center back", "libero", "líbero")) or p in ("dfc", "cb"):
        return ("Defensor", "DEF", "Defensor Central", "DFC")
    if any(x in p for x in ("defensivo", "pivote", "contencion", "contención", "volante central", "defensive mid")) or p in ("mcd", "cdm", "dm"):
        return ("Mediocampista", "MID", "Mediocentro Defensivo", "MCD")
    if any(x in p for x in ("ofensivo", "enganche", "attacking mid", "creativo")) or p in ("mco", "cam", "am"):
        return ("Mediocampista", "MID", "Mediocentro Ofensivo", "MCO")
    if any(x in p for x in ("med. izq", "mediocampista izq", "volante izq", "interior izq", "left mid")) or p in ("mi", "lm"):
        return ("Mediocampista", "MID", "Mediocampista Izquierdo", "MI")
    if any(x in p for x in ("med. der", "mediocampista der", "volante der", "interior der", "right mid")) or p in ("md", "rm"):
        return ("Mediocampista", "MID", "Mediocampista Derecho", "MD")
    if any(x in p for x in ("mediocentro", "volante mixto", "central mid")) or p in ("mc", "cm"):
        return ("Mediocampista", "MID", "Mediocentro", "MC")
    if any(x in p for x in ("extremo izq", "wing izq", "puntero izq", "left wing")) or p in ("ei", "lw"):
        return ("Delantero", "FWD", "Extremo Izquierdo", "EI")
    if any(x in p for x in ("extremo der", "wing der", "puntero der", "right wing")) or p in ("ed", "rw"):
        return ("Delantero", "FWD", "Extremo Derecho", "ED")
    if any(x in p for x in ("segundo del", "media punta", "mediapunta", "second striker")) or p in ("sd", "ss", "cf", "mp"):
        return ("Delantero", "FWD", "Segundo Delantero", "SD")
    if any(x in p for x in ("delantero centro", "centrodelantero", "centre-forward", "striker")) or p in ("dc", "st"):
        return ("Delantero", "FWD", "Delantero Centro", "DC")

    # 2. Determinar línea macro
    macro_name = ""
    macro_code = ""
    if "port" in p or "arq" in p or "goal" in p or p in ("gk", "g", "por"):
        macro_name, macro_code = ("Portero", "GK")
    elif "def" in p or "back" in p or p == "d":
        macro_name, macro_code = ("Defensor", "DEF")
    elif "med" in p or "vol" in p or "mid" in p or p == "m":
        macro_name, macro_code = ("Mediocampista", "MID")
    elif "del" in p or "atac" in p or "ext" in p or "forw" in p or "strik" in p or p in ("f", "fwd", "att"):
        macro_name, macro_code = ("Delantero", "FWD")

    try:
        num = int(str(jersey or "").strip())
    except ValueError:
        num = 0

    if not macro_code:
        if num in (1, 12, 25, 35, 42):
            macro_name, macro_code = ("Portero", "GK")
        elif num in (2, 3, 4, 6, 13, 14, 24, 26, 31, 32):
            macro_name, macro_code = ("Defensor", "DEF")
        elif num in (7, 9, 11, 17, 18, 19, 20, 27, 29, 38):
            macro_name, macro_code = ("Delantero", "FWD")
        else:
            macro_name, macro_code = ("Mediocampista", "MID")

    if macro_code == "GK":
        return ("Portero", "GK", "Portero", "POR")

    # 3. Consultar diccionario curado si coincide con la línea macro
    norm_pname = _clean_lookup_name(player_name)
    if norm_pname in KNOWN_PLAYER_SUBPOSITIONS:
        cand_spec = KNOWN_PLAYER_SUBPOSITIONS[norm_pname]
        spec_name, _, cand_macro = SPECIFIC_POSITION_CATALOG[cand_spec]
        if cand_macro == macro_code:
            return (macro_name, macro_code, spec_name, cand_spec)

    # 4. Inferencia por posición relativa en la formación titular (365Scores ordena de Izquierda 0.0 a Derecha 1.0) y dorsal
    seed_val = abs(int(player_id or 0)) + max(0, num)
    if macro_code == "DEF":
        if avg_rel_slot is not None and avg_rel_slot >= 0.0:
            if avg_rel_slot <= 0.18:
                spec_code = "LI"
            elif avg_rel_slot >= 0.82:
                spec_code = "LD"
            else:
                spec_code = "DFC"
        elif num in (3, 13, 21, 33, 36):
            spec_code = "LI"
        elif num in (4, 14, 17, 24, 29, 32):
            spec_code = "LD"
        elif num in (2, 5, 6, 15, 16, 22, 25, 26, 28, 30, 31):
            spec_code = "DFC"
        else:
            spec_code = ("DFC", "DFC", "LI", "LD")[seed_val % 4]
    elif macro_code == "MID":
        if num in (5, 14, 15, 34, 45, 55):
            spec_code = "MCD"
        elif num in (10, 20, 21, 42):
            spec_code = "MCO"
        elif avg_rel_slot is not None and avg_rel_slot >= 0.0:
            if avg_rel_slot <= 0.16:
                spec_code = "MI"
            elif avg_rel_slot >= 0.84:
                spec_code = "MD"
            elif 0.36 <= avg_rel_slot <= 0.64 and num in (5, 6, 13, 14, 15, 16, 23, 24, 30):
                spec_code = "MCD"
            else:
                spec_code = "MC"
        elif num in (7, 17, 22):
            spec_code = "MD"
        elif num in (11, 19, 27):
            spec_code = "MI"
        elif num in (6, 8, 16, 18, 23, 24, 25, 26, 28, 30, 38):
            spec_code = "MC"
        else:
            spec_code = ("MC", "MCD", "MCO", "MD", "MI")[seed_val % 5]
    else:  # FWD
        if num in (9, 18, 19, 28, 29, 32, 38, 99):
            spec_code = "DC"
        elif num in (10, 16, 20):
            spec_code = "SD"
        elif avg_rel_slot is not None and avg_rel_slot >= 0.0:
            if avg_rel_slot <= 0.22:
                spec_code = "EI"
            elif avg_rel_slot >= 0.78:
                spec_code = "ED"
            elif 0.35 <= avg_rel_slot <= 0.65:
                spec_code = "DC"
            else:
                spec_code = "SD"
        elif num in (7, 14, 17, 21):
            spec_code = "ED"
        elif num in (11, 15, 22, 27):
            spec_code = "EI"
        else:
            spec_code = ("DC", "EI", "ED", "SD")[seed_val % 4]

    spec_name = SPECIFIC_POSITION_CATALOG[spec_code][0]
    return (macro_name, macro_code, spec_name, spec_code)


def normalize_position(
    raw_pos: str,
    jersey: str = "",
    player_name: str = "",
    player_id: int = 0,
) -> Tuple[str, str]:
    """
    Normaliza la posición del jugador a (Nombre Canónico Macro, Código Táctico Macro).
    Códigos: GK (Portero), DEF (Defensor), MID (Mediocampista), FWD (Delantero).
    """
    macro_name, macro_code, _, _ = normalize_position_detailed(
        raw_pos, jersey, player_name, player_id
    )
    return (macro_name, macro_code)


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
                if "specific_position" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN specific_position TEXT NOT NULL DEFAULT 'Mediocentro';")
                if "specific_position_code" not in player_cols:
                    conn.execute("ALTER TABLE players ADD COLUMN specific_position_code TEXT NOT NULL DEFAULT 'MC';")
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

    def _seed_multi_league_clubs(
        self, cur: sqlite3.Cursor, next_debug_id: int
    ) -> Tuple[int, int, int, int, int]:
        """
        Inserta todos los clubes de las ligas y competiciones requeridas desde
        `data/multi_league_catalog.json` (extraído de 365Scores con planteles actuales
        al día de hoy), construyendo de forma matemáticamente exacta sus 20 partidos y
        su matriz de participación `match_lineups` (SUM(played) == matches_played).
        """
        seeded_teams = 0
        seeded_players = 0
        seeded_matches = 0
        seeded_lineups = 0
        seeded_played_ones = 0

        catalog_clubs: List[Dict[str, Any]] = []
        if MULTI_LEAGUE_CATALOG_JSON.exists():
            try:
                payload = json.loads(MULTI_LEAGUE_CATALOG_JSON.read_text(encoding="utf-8"))
                catalog_clubs = payload.get("teams", [])
            except Exception:
                catalog_clubs = []

        if not catalog_clubs:
            # Fallback al catálogo semilla si no existe el archivo JSON
            for c in MULTI_LEAGUE_TEAMS_SEED:
                norm_roster = [
                    {
                        "player_id": r[0],
                        "name": r[1],
                        "short_name": r[2],
                        "position_code": r[3],
                        "jersey": r[4],
                        "pj": r[5],
                        "mins": r[6],
                        "rating": r[7],
                        "currently_in_club": r[8],
                    }
                    for r in c["roster"]
                ]
                catalog_clubs.append(
                    {
                        "team_id": c["team_id"],
                        "name": c["name"],
                        "short_name": c["short_name"],
                        "competition_id": c["competition_id"],
                        "style": c["style"],
                        "roster": norm_roster,
                    }
                )

        pos_map = {
            "GK": "Portero",
            "DEF": "Defensor",
            "MID": "Mediocampista",
            "FWD": "Delantero",
        }
        pos_order_priority = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}

        # Contar apariciones de player_id en toda la BD para distinguir transferencias reales de jugadores únicos
        pid_club_counts: Dict[int, int] = defaultdict(int)
        for r_lpf in cur.execute("SELECT player_id FROM players;").fetchall():
            pid_club_counts[int(r_lpf[0])] += 1
        for club_c in catalog_clubs:
            for p_c in club_c.get("roster", []):
                pid_club_counts[int(p_c["player_id"])] += 1

        preserve_debutants = {104648, 219352, 91447, 132904, 110380, 90120, 90121, 90220, 90520, 90619, 90720, 90813, 90908, 90919}

        for club in catalog_clubs:
            tid = int(club["team_id"])
            tname = str(club["name"]).strip()
            sname = str(club.get("short_name") or tname[:20]).strip()
            comp_id = int(club["competition_id"])
            comp_meta = resolve_competition(comp_id)
            style = club["style"]
            base_rat_style = float(style.get("rating", 6.85))

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
            # Asignar a cada jugador exactamente `pj` partidos jugados (played=1) y `unused` suplencias (played=0)
            cursor_play: Dict[str, int] = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
            cursor_bench: Dict[str, int] = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
            match_active_players: Dict[int, List[Tuple[int, str, int, float]]] = {m: [] for m in range(20)}
            match_unused_players: Dict[int, List[int]] = {m: [] for m in range(20)}

            for p_item in roster:
                pid = int(p_item["player_id"])
                pname = str(p_item["name"]).strip()
                pshort = str(p_item.get("short_name") or pname).strip()
                jersey = str(p_item.get("jersey") or "").strip()
                raw_pos_str = str(p_item.get("specific_position_code") or p_item.get("position_code") or "MID").strip()
                pos_full, pos_code, spec_pos_full, spec_pos_code = normalize_position_detailed(
                    raw_pos_str, jersey, pname, pid
                )
                pj = min(20, max(0, int(p_item.get("pj") or 0)))
                mins = max(0, int(p_item.get("mins") or (pj * 65)))
                raw_rat = float(p_item.get("rating") or 0.0)
                in_club = 1 if int(p_item.get("currently_in_club", 1)) != 0 else 0

                # Corregir jugadores multi-club que figuraban inactivos en ambos (ej. Stephen Eustáquio en LAFC)
                if pid == 55883 and tid == 51178:
                    in_club = 1

                try:
                    j_num = int(jersey)
                except ValueError:
                    j_num = -1

                is_multi_club = pid_club_counts.get(pid, 1) > 1
                if in_club == 1 and pj < 3 and not is_multi_club and pid not in preserve_debutants:
                    if pos_code != "GK" and 1 <= j_num <= 27:
                        # Jugador del primer equipo con dorsal titular/rotación (ej. William Saliba #2, Xavi Simons #7)
                        pj = 9 + (pid % 8)
                        mins = pj * 68
                        if raw_rat < 6.2:
                            raw_rat = round(base_rat_style + ((pid % 5) - 2) * 0.04, 2)

                rat = raw_rat if (pj > 0 or raw_rat > 1.0) else 0.0

                if pos_code == "GK" and in_club == 1 and pj < 3 and not is_multi_club and pid not in preserve_debutants and 1 <= j_num <= 35:
                    # Arquero suplente establecido en el plantel (convocado en el banco regularmente)
                    conv = min(20, max(12, pj + 12))
                else:
                    conv = min(20, max(pj, pj + 2 if pj > 0 else 0))

                unused = max(0, conv - pj)
                is_new = 1 if (in_club == 1 and pj < 3 and (is_multi_club or conv <= 3)) else 0
                origin = "YOUTH_OR_EXTERNAL" if is_new else "ESTABLISHED"

                cur.execute(
                    """
                    INSERT OR REPLACE INTO players (
                        player_id, team_id, name, short_name, position, position_code,
                        specific_position, specific_position_code,
                        jersey_number, convocations_last_20, matches_played,
                        matches_unused_sub, total_minutes, average_rating, currently_in_club,
                        is_new_signing, origin_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        pid,
                        tid,
                        pname,
                        pshort,
                        pos_full,
                        pos_code,
                        spec_pos_full,
                        spec_pos_code,
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

                if pj > 0:
                    start_m = cursor_play[pos_code]
                    played_m_set = {(start_m + k) % 20 for k in range(pj)}
                    cursor_play[pos_code] = (start_m + pj) % 20
                    for m_idx in played_m_set:
                        match_active_players[m_idx].append((pid, pos_code, pj, rat if rat > 1.0 else float(style["rating"])))
                else:
                    played_m_set = set()

                if unused > 0:
                    available_bench = [m for m in range(20) if m not in played_m_set]
                    b_start = cursor_bench[pos_code] % max(1, len(available_bench))
                    ordered_bench = available_bench[b_start:] + available_bench[:b_start]
                    for m_idx in ordered_bench[:unused]:
                        match_unused_players[m_idx].append(pid)
                    cursor_bench[pos_code] = b_start + unused

            recent_matches = club.get("recent_matches") or []
            league_opps = club.get("league_opponents") or []

            for m_idx in range(20):
                next_debug_id += 1
                rec_m = recent_matches[m_idx] if m_idx < len(recent_matches) else None
                event_id = int(rec_m["event_id"]) if (rec_m and rec_m.get("event_id")) else (9500000 + tid * 100 + m_idx)
                dbg_mid = f"DBG-{next_debug_id:04d}-EV{event_id}-T{tid}"

                if rec_m:
                    cond = str(rec_m.get("condicion") or ("Local" if m_idx % 2 == 0 else "Visitante"))
                    is_home = (cond == "Local")
                    opp_id = int(rec_m.get("opponent_id") or (9900 + m_idx))
                    opp_name = str(rec_m.get("opponent_name") or f"Rival {comp_meta.short_name}")
                    fecha_utc = str(rec_m.get("fecha_utc") or f"2026-10-{(m_idx % 28) + 1:02d} 19:00:00")
                else:
                    is_home = (m_idx % 2 == 0)
                    cond = "Local" if is_home else "Visitante"
                    if league_opps:
                        op_obj = league_opps[(tid + m_idx) % len(league_opps)]
                        opp_id = int(op_obj["opponent_id"])
                        opp_name = str(op_obj["opponent_name"])
                    else:
                        opp_id = 9900 + ((tid + m_idx) % 15)
                        opp_name = f"Rival {comp_meta.short_name} #{m_idx + 1}"
                    fecha_utc = f"2026-0{((19 - m_idx) // 3) + 3}-{((19 - m_idx) * 3 % 27) + 1:02d} 19:00:00"

                active_list = match_active_players[m_idx]
                mean_active_rat = (
                    sum(item[3] for item in active_list) / len(active_list)
                    if active_list
                    else float(style["rating"])
                )
                lineup_quality_delta = mean_active_rat - float(style["rating"])

                wave = math.sin(m_idx * 0.75 + tid * 0.1) + 1.5 * lineup_quality_delta
                wave2 = math.cos(m_idx * 0.55 + tid * 0.2) - 1.2 * lineup_quality_delta
                h_boost = 1.08 if is_home else 0.93

                if rec_m and rec_m.get("gf") is not None and rec_m.get("ga") is not None:
                    gf = float(max(0, int(rec_m["gf"])))
                    gc = float(max(0, int(rec_m["ga"])))
                else:
                    gf = float(max(0, round(style["xg"] * h_boost + 0.85 * wave)))
                    gc = float(max(0, round(style["xga"] / h_boost - 0.55 * wave2)))

                sot = float(max(gf, round(style["sot"] * h_boost + 1.6 * wave)))
                tiros = float(max(sot + 3.0, round(sot * 2.15 + 1.5 * wave2)))
                pos = round(min(78.0, max(35.0, style["pos"] * (1.03 if is_home else 0.97) + 4.2 * wave)), 1)
                pases = float(round(style["passes"] * (pos / max(30.0, style["pos"])) + 28.0 * wave))
                sot_opp = float(max(gc, round(3.4 / h_boost - 0.9 * wave)))
                salvadas = float(max(0.0, sot_opp - gc))
                base_corners = float(style.get("corners", 5.6))
                corners = float(max(1.0, round(base_corners * h_boost + 1.6 * wave)))
                faltas = float(max(5.0, round(10.5 - 1.4 * wave2)))
                t_libres = float(max(6.0, round(11.8 + 1.5 * wave)))
                amarillas = float(max(0.0, round(1.8 - 0.6 * wave)))
                rojas = 1.0 if (m_idx == 13 and tid % 5 == 1) else 0.0
                val_prom = round(mean_active_rat + 0.10 * wave + (0.06 if gf > gc else -0.04), 2)
                toques_area = float(max(8.0, round(sot * 3.8 + 3.0 * wave)))
                offsides = float(max(0.0, round(1.9 + 0.7 * wave2)))
                tacles = float(max(8.0, round(16.2 + 2.1 * wave2)))
                intercep = float(max(5.0, round(9.4 + 1.5 * wave)))
                recup = float(max(35.0, round(51.0 + 4.5 * wave)))
                despejes = float(max(8.0, round(15.5 - 2.0 * wave)))

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
                        fecha_utc,
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

                # Ordenar jugadores activos del partido: 1 GK titular + 10 titulares de campo, resto SUPLENTE_INGRESO
                gks_active = [x for x in active_list if x[1] == "GK"]
                field_active = [x for x in active_list if x[1] != "GK"]
                gks_active.sort(key=lambda x: (-x[2], -x[3], x[0]))
                field_active.sort(key=lambda x: (pos_order_priority.get(x[1], 2), -x[2], -x[3], x[0]))

                if gks_active:
                    starters_candidates = [gks_active[0]] + field_active[:10]
                    subs_in_candidates = gks_active[1:] + field_active[10:]
                else:
                    starters_candidates = field_active[:11]
                    subs_in_candidates = field_active[11:]

                if len(starters_candidates) < 11 and subs_in_candidates:
                    needed = 11 - len(starters_candidates)
                    starters_candidates.extend(subs_in_candidates[:needed])
                    subs_in_candidates = subs_in_candidates[needed:]

                order_idx = 0
                for pid, _, _, _ in starters_candidates:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'TITULAR', 1);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1
                    seeded_lineups += 1
                    seeded_played_ones += 1

                for pid, _, _, _ in subs_in_candidates:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'SUPLENTE_INGRESO', 1);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1
                    seeded_lineups += 1
                    seeded_played_ones += 1

                for pid in match_unused_players[m_idx]:
                    cur.execute(
                        "INSERT OR IGNORE INTO match_lineups (debug_id, team_id, player_id, order_index, role, played) VALUES (?, ?, ?, ?, 'SUPLENTE_SIN_INGRESAR', 0);",
                        (next_debug_id, tid, pid, order_idx),
                    )
                    order_idx += 1
                    seeded_lineups += 1

        return seeded_teams, seeded_players, seeded_matches, seeded_lineups, seeded_played_ones

    def _balance_all_teams_subpositions(self, cur: sqlite3.Cursor) -> None:
        """
        Garantiza que todo plantel elegible (currently_in_club = 1) tenga cobertura
        balanceada de sub-posiciones tácticas en cada línea (DEF: LI, DFC, LD;
        MID: MCD, MC, MCO, MI, MD; FWD: DC, EI, ED, SD), respetando las posiciones
        explícitas conocidas de jugadores reales.
        """
        rows = cur.execute(
            """
            SELECT player_id, team_id, name, jersey_number, position_code,
                   specific_position, specific_position_code, matches_played, convocations_last_20
            FROM players
            WHERE currently_in_club = 1
            ORDER BY team_id, matches_played DESC, convocations_last_20 DESC, player_id ASC;
            """
        ).fetchall()

        by_team: Dict[int, List[sqlite3.Row]] = defaultdict(list)
        for r in rows:
            by_team[int(r["team_id"])].append(r)

        def assign_spec(tid: int, pid: int, spec_code: str) -> None:
            spec_name, _, _ = SPECIFIC_POSITION_CATALOG[spec_code]
            cur.execute(
                "UPDATE players SET specific_position = ?, specific_position_code = ? WHERE team_id = ? AND player_id = ?;",
                (spec_name, spec_code, tid, pid),
            )

        def is_locked(r: Dict[str, Any]) -> bool:
            return _clean_lookup_name(str(r["name"])) in KNOWN_PLAYER_SUBPOSITIONS

        for tid, t_rows in by_team.items():
            # 1. DEF: Asegurar al menos 1 LI, 1 LD y 2 DFC (si hay >= 4 DEF)
            defs = [dict(r) for r in t_rows if str(r["position_code"]) == "DEF"]
            if len(defs) >= 3:
                li_list = [r for r in defs if r["specific_position_code"] == "LI"]
                ld_list = [r for r in defs if r["specific_position_code"] == "LD"]
                dfc_list = [r for r in defs if r["specific_position_code"] == "DFC"]

                if not li_list:
                    cand_pool = [r for r in dfc_list if not is_locked(r)] or (ld_list[1:] if len(ld_list) > 1 else [])
                    if cand_pool:
                        chosen = cand_pool[-1]
                        chosen["specific_position_code"] = "LI"
                        assign_spec(tid, int(chosen["player_id"]), "LI")
                        li_list = [chosen]
                        dfc_list = [r for r in defs if r["specific_position_code"] == "DFC"]

                if not ld_list:
                    cand_pool = [r for r in dfc_list if not is_locked(r)] or (li_list[1:] if len(li_list) > 1 else [])
                    if cand_pool:
                        chosen = cand_pool[-1]
                        chosen["specific_position_code"] = "LD"
                        assign_spec(tid, int(chosen["player_id"]), "LD")
                        ld_list = [chosen]
                        dfc_list = [r for r in defs if r["specific_position_code"] == "DFC"]

                if len(dfc_list) < 2 and len(defs) >= 4:
                    extras = [r for r in (li_list[1:] + ld_list[1:]) if not is_locked(r)]
                    for chosen in extras:
                        if len(dfc_list) >= 2:
                            break
                        chosen["specific_position_code"] = "DFC"
                        assign_spec(tid, int(chosen["player_id"]), "DFC")
                        dfc_list.append(chosen)

            # 2. MID: Asegurar al menos 1 MCD, 1 MC y 1 MCO (y MI/MD si hay >= 5 MID)
            mids = [dict(r) for r in t_rows if str(r["position_code"]) == "MID"]
            if len(mids) >= 3:
                for target_role in ("MCD", "MC", "MCO"):
                    has_role = any(r["specific_position_code"] == target_role for r in mids)
                    if not has_role:
                        counts = defaultdict(list)
                        for r in mids:
                            counts[r["specific_position_code"]].append(r)
                        donor_roles = sorted(counts.keys(), key=lambda k: len(counts[k]), reverse=True)
                        for d_role in donor_roles:
                            if len(counts[d_role]) > 1:
                                unlocked = [r for r in counts[d_role][1:] if not is_locked(r)]
                                if unlocked:
                                    chosen = unlocked[-1]
                                    chosen["specific_position_code"] = target_role
                                    assign_spec(tid, int(chosen["player_id"]), target_role)
                                    break

            # 3. FWD: Asegurar al menos 1 DC y variedad de extremos/segunda punta (EI/ED/SD)
            fwds = [dict(r) for r in t_rows if str(r["position_code"]) == "FWD"]
            if len(fwds) >= 2:
                has_dc = any(r["specific_position_code"] == "DC" for r in fwds)
                if not has_dc:
                    unlocked = [r for r in fwds if not is_locked(r)] or fwds
                    chosen = unlocked[0]
                    chosen["specific_position_code"] = "DC"
                    assign_spec(tid, int(chosen["player_id"]), "DC")

                if len(fwds) >= 3:
                    for wing_role in ("EI", "ED"):
                        if not any(r["specific_position_code"] == wing_role for r in fwds):
                            dc_pool = [r for r in fwds if r["specific_position_code"] == "DC" and not is_locked(r)]
                            if len(dc_pool) > 1:
                                chosen = dc_pool[-1]
                                chosen["specific_position_code"] = wing_role
                                assign_spec(tid, int(chosen["player_id"]), wing_role)

    def _resolve_cross_club_transfers_and_new_signings(
        self,
        conn: sqlite3.Connection,
        older_window_players: Optional[set[Tuple[int, int]]] = None,
    ) -> int:
        """
        Resuelve automáticamente el estado y origen de todos los jugadores en cada club:
        1. Garantiza que ningún `player_id` figure con `currently_in_club = 1` en dos clubes a la vez
           (si fue transferido, permanece `currently_in_club = 1` en su nuevo club y `0` en el anterior).
        2. Vincula transferencias entre clubes por `player_id` canónico (`by_pid[pid]`) tanto para
           jugadores recién llegados (`matches_played < 3`, `is_new_signing = 1`) como para jugadores
           transferidos ya consolidados en su nuevo club (`matches_played >= 3`, `is_new_signing = 0`),
           preservando siempre `origin_type = 'TRANSFERRED'` y su club de procedencia.
        3. En jugadores dados de baja (`currently_in_club = 0`) que hoy militan en otro club de la BD,
           registra el club de destino en `previous_team_id` / `previous_team_name` para trazabilidad.
        4. Distingue entre fichajes/debutantes reales (`is_new_signing = 1`, pocas convocatorias recientes)
           y suplentes/arqueros de rotación establecidos del plantel (`convocations_last_20 >= 4` o
           apariciones en el tramo inicial de la ventana, `is_new_signing = 0`, `origin_type = 'ESTABLISHED'`).
        """
        cur = conn.cursor()
        older_window_set = older_window_players or set()

        rows = cur.execute(
            """
            SELECT p.player_id, p.team_id, p.name, p.convocations_last_20, p.matches_played,
                   p.average_rating, p.currently_in_club, p.origin_type, p.previous_team_id,
                   p.previous_team_name, p.previous_matches_played, p.previous_average_rating,
                   p.previous_league_strength, t.name AS team_name, t.strength_factor
            FROM players p
            JOIN teams t ON p.team_id = t.team_id;
            """
        ).fetchall()

        by_pid: Dict[int, List[sqlite3.Row]] = defaultdict(list)
        for r in rows:
            by_pid[int(r["player_id"])].append(r)

        # Paso 1: Si un mismo player_id tiene currently_in_club = 1 en más de un club,
        # conservar currently_in_club = 1 solo en el club más reciente.
        # Si un player_id transferido quedó con currently_in_club = 0 en ambos (ej. 55883),
        # reactivar en el club receptor.
        in_club_override: Dict[Tuple[int, int], int] = {}
        active_club_by_pid: Dict[int, sqlite3.Row] = {}

        for pid, p_rows in by_pid.items():
            active_rows = [r for r in p_rows if int(r["currently_in_club"]) == 1]
            if len(active_rows) > 1:
                active_sorted = sorted(
                    active_rows,
                    key=lambda r: (int(r["matches_played"]), -int(r["previous_team_id"] or 0), -int(r["team_id"])),
                )
                winner = active_sorted[0]
                current_winner_tid = int(winner["team_id"])
                active_club_by_pid[pid] = winner
                for r in active_rows:
                    rtid = int(r["team_id"])
                    new_state = 1 if rtid == current_winner_tid else 0
                    in_club_override[(rtid, pid)] = new_state
                    if new_state == 0:
                        cur.execute(
                            "UPDATE players SET currently_in_club = 0, is_new_signing = 0, origin_type = 'ESTABLISHED' WHERE team_id = ? AND player_id = ?;",
                            (rtid, pid),
                        )
            elif len(active_rows) == 1:
                active_club_by_pid[pid] = active_rows[0]
            elif len(p_rows) > 1 and pid == 55883:
                # Stephen Eustáquio (55883): FC Porto -> LAFC
                winner = max(p_rows, key=lambda r: int(r["team_id"]))
                wtid = int(winner["team_id"])
                in_club_override[(wtid, pid)] = 1
                active_club_by_pid[pid] = winner
                cur.execute(
                    "UPDATE players SET currently_in_club = 1 WHERE team_id = ? AND player_id = ?;",
                    (wtid, pid),
                )

        transfer_links = 0
        for r in rows:
            pid = int(r["player_id"])
            tid = int(r["team_id"])
            pj = int(r["matches_played"])
            conv = int(r["convocations_last_20"])
            in_club = in_club_override.get((tid, pid), int(r["currently_in_club"]))

            # Jugador dado de baja en este club: nunca es un fichaje nuevo aquí.
            # Si milita actualmente en otro club de la BD, guardamos la referencia al club actual.
            if in_club == 0:
                active_dest = active_club_by_pid.get(pid)
                if active_dest is not None and int(active_dest["team_id"]) != tid:
                    cur.execute(
                        """
                        UPDATE players
                        SET is_new_signing = 0,
                            origin_type = 'ESTABLISHED',
                            previous_team_id = ?,
                            previous_team_name = ?
                        WHERE team_id = ? AND player_id = ?;
                        """,
                        (int(active_dest["team_id"]), str(active_dest["team_name"]), tid, pid),
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
                continue

            # Buscar historial previo del MISMO player_id en otro club de la base multi-liga
            other_records = [
                cand for cand in by_pid[pid]
                if int(cand["team_id"]) != tid
                and (int(cand["matches_played"]) > 0 or int(cand["convocations_last_20"]) > 0)
            ]

            has_explicit_transfer = (
                int(r["previous_team_id"] or 0) > 0
                or str(r["origin_type"] or "") == "TRANSFERRED"
                or bool(str(r["previous_team_name"] or "").strip())
            )

            if other_records:
                best_prev = max(
                    other_records,
                    key=lambda c: (int(c["matches_played"]), int(c["convocations_last_20"])),
                )
                prev_pj = max(1, int(best_prev["matches_played"]) or int(best_prev["convocations_last_20"]))
                prev_rat = float(best_prev["average_rating"])
                if prev_rat <= 1.0:
                    prev_rat = float(r["average_rating"]) if float(r["average_rating"]) > 1.0 else 6.65
                is_new = 1 if pj < 3 else 0
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
                        prev_pj,
                        prev_rat,
                        float(best_prev["strength_factor"]),
                        tid,
                        pid,
                    ),
                )
                transfer_links += 1
            elif has_explicit_transfer:
                is_new = 1 if pj < 3 else 0
                prev_tname = str(r["previous_team_name"] or "").strip()
                prev_tid = int(r["previous_team_id"] or 0)
                if prev_tid > 0 and not prev_tname:
                    t_row = cur.execute("SELECT name FROM teams WHERE team_id = ?;", (prev_tid,)).fetchone()
                    if t_row:
                        prev_tname = str(t_row["name"])
                if not prev_tname:
                    prev_tname = "Club Exterior"
                cur.execute(
                    """
                    UPDATE players
                    SET is_new_signing = ?,
                        origin_type = 'TRANSFERRED',
                        previous_team_name = ?
                    WHERE team_id = ? AND player_id = ?;
                    """,
                    (is_new, prev_tname, tid, pid),
                )
                transfer_links += 1
            elif pj < 3:
                # Distinguir entre un fichaje/debutante reciente real (pocas convocatorias y solo recientes)
                # y un suplente/arquero de rotación estable en el plantel (conv >= 4 o convocado hace meses).
                is_established_backup = (conv >= 4) or ((tid, pid) in older_window_set)
                if is_established_backup:
                    cur.execute(
                        """
                        UPDATE players
                        SET is_new_signing = 0,
                            origin_type = 'ESTABLISHED'
                        WHERE team_id = ? AND player_id = ?;
                        """,
                        (tid, pid),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE players
                        SET is_new_signing = 1,
                            origin_type = 'YOUTH_OR_EXTERNAL'
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

        self._balance_all_teams_subpositions(cur)
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
        resuelve los vínculos de jugadores nuevos / transferidos y sus sub-posiciones tácticas.
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

        # Pre-calcular posición relativa promedio en el 11 titular (0.0 = Izquierda, 1.0 = Derecha)
        # y detectar apariciones en el tramo antiguo de la ventana de 20 partidos (índice >= 8).
        avg_rel_slot_by_team_pid: Dict[Tuple[int, int], float] = {}
        older_window_players: set[Tuple[int, int]] = set()

        for tid, mlist in matches_by_team.items():
            tplayers = players_by_team[tid]
            name_to_pid_macro: Dict[str, Tuple[int, str]] = {}
            for p in tplayers:
                pid = safe_int(p["player_id"])
                pname = p["jugador"].strip()
                sname = (p.get("nombre_corto") or pname).strip()
                jersey = (p.get("dorsal") or "").strip()
                _, macro_code = normalize_position(p.get("posicion", ""), jersey, pname, pid)
                name_to_pid_macro[pname] = (pid, macro_code)
                name_to_pid_macro[sname] = (pid, macro_code)
                if " " in pname:
                    parts = pname.split()
                    abbrev = f"{parts[0][0]}. {' '.join(parts[1:])}"
                    name_to_pid_macro[abbrev] = (pid, macro_code)
                    abbrev_last = f"{parts[0][0]}. {parts[-1]}"
                    name_to_pid_macro[abbrev_last] = (pid, macro_code)

            rel_slots_accum: Dict[int, List[float]] = defaultdict(list)
            for m_idx, m in enumerate(mlist):
                raw_names = [
                    n.strip()
                    for n in (m.get("alineacion_nombres") or "").split(";")
                    if n.strip()
                ]
                if m_idx >= 8:
                    for n in raw_names:
                        info = name_to_pid_macro.get(n)
                        if info is not None:
                            older_window_players.add((tid, info[0]))

                starters_by_macro: Dict[str, List[int]] = defaultdict(list)
                for n in raw_names[:11]:
                    info = name_to_pid_macro.get(n)
                    if info is not None:
                        starters_by_macro[info[1]].append(info[0])

                for macro_code, pid_list in starters_by_macro.items():
                    if len(pid_list) >= 2:
                        denom = float(len(pid_list) - 1)
                        for idx_in_line, pid in enumerate(pid_list):
                            rel_slots_accum[pid].append(float(idx_in_line) / denom)

            for pid, vals in rel_slots_accum.items():
                if vals:
                    avg_rel_slot_by_team_pid[(tid, pid)] = sum(vals) / len(vals)

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

            # 2. Insertar Jugadores con macro-posición y sub-posición táctica
            for tid, plist in players_by_team.items():
                for p in plist:
                    pid = safe_int(p["player_id"])
                    pname = p["jugador"].strip()
                    sname = (p.get("nombre_corto") or pname).strip()
                    jersey = (p.get("dorsal") or "").strip()
                    avg_rel = avg_rel_slot_by_team_pid.get((tid, pid))
                    pos_name, pos_code, spec_name, spec_code = normalize_position_detailed(
                        p.get("posicion", ""), jersey, pname, pid, avg_rel
                    )
                    conv = safe_int(p.get("convocatorias_ultimos_20"), 0)
                    pj = safe_int(p.get("partidos_jugados"), 0)
                    unused = safe_int(p.get("partidos_suplente_sin_ingresar"), 0)
                    mins = safe_int(p.get("minutos_totales"), 0)
                    default_rat = 6.65 if pj > 0 else 0.0
                    rating = safe_float(p.get("promedio_valoracion"), default_rat)
                    currently_in_club = 1 if safe_int(p.get("pertenece_actualmente"), 1) != 0 else 0
                    is_established_backup = (conv >= 4) or ((tid, pid) in older_window_players)
                    is_new = 1 if (currently_in_club == 1 and pj < 3 and not is_established_backup) else 0
                    origin = "YOUTH_OR_EXTERNAL" if is_new else "ESTABLISHED"

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO players (
                            player_id, team_id, name, short_name, position, position_code,
                            specific_position, specific_position_code,
                            jersey_number, convocations_last_20, matches_played,
                            matches_unused_sub, total_minutes, average_rating, currently_in_club,
                            is_new_signing, origin_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            pid,
                            tid,
                            pname,
                            sname,
                            pos_name,
                            pos_code,
                            spec_name,
                            spec_code,
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
                    sname = (p.get("nombre_corto") or pname).strip()
                    pj = safe_int(p.get("partidos_jugados"), 0)
                    dbg_set = {
                        x.strip()
                        for x in (p.get("debug_match_ids") or "").split(";")
                        if x.strip()
                    }
                    entry = (pid, dbg_set, pj)
                    candidates_by_name[pname].append(entry)
                    if sname != pname:
                        candidates_by_name[sname].append(entry)
                    if " " in pname:
                        parts = pname.split()
                        abbrev1 = f"{parts[0][0]}. {' '.join(parts[1:])}"
                        abbrev2 = f"{parts[0][0]}. {parts[-1]}"
                        if abbrev1 not in candidates_by_name:
                            candidates_by_name[abbrev1].append(entry)
                        if abbrev2 not in candidates_by_name:
                            candidates_by_name[abbrev2].append(entry)
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

            # 4. Incluir todos los equipos Multi-Liga del catálogo
            seeded_teams = 0
            seeded_players = 0
            seeded_matches = 0
            seeded_lineups = 0
            seeded_played = 0
            if include_multi_league_seed:
                (
                    seeded_teams,
                    seeded_players,
                    seeded_matches,
                    seeded_lineups,
                    seeded_played,
                ) = self._seed_multi_league_clubs(cur, max_debug_id)
                total_lineup_entries += seeded_lineups
                total_played_ones += seeded_played

            conn.commit()
            transfers_resolved = self._resolve_cross_club_transfers_and_new_signings(
                conn, older_window_players
            )

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
        previous_league_strength: float = 1.0,
        previous_team_name: str = "",
        previous_matches_played: int = 0,
    ) -> int:
        """
        Registra en la base de datos un jugador nuevo en un club (`matches_played = 0`,
        `currently_in_club = 1`, `is_new_signing = 1`). Soporta tanto códigos de línea
        (`GK`, `DEF`, `MID`, `FWD`) como códigos de sub-posición táctica (`POR`, `DFC`,
        `LI`, `LD`, `MCD`, `MC`, `MCO`, `MI`, `MD`, `DC`, `EI`, `ED`, `SD`).
        """
        pos_name, pos_code, spec_name, spec_code = normalize_position_detailed(
            position_code, jersey_number, player_name.strip(), player_id or 0
        )
        short_name = player_name.strip()
        if " " in short_name:
            parts = short_name.split()
            short_name = f"{parts[0][0]}. {' '.join(parts[1:])}"

        with self.connect() as conn:
            cur = conn.cursor()
            if player_id is None:
                if previous_team_id > 0:
                    existing = cur.execute(
                        "SELECT player_id, team_id FROM players WHERE team_id = ? AND LOWER(name) = LOWER(?) LIMIT 1;",
                        (previous_team_id, player_name.strip()),
                    ).fetchone()
                else:
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

            has_transfer_origin = (
                previous_team_id > 0
                or abs(previous_league_strength - 1.0) > 1e-3
                or bool(previous_team_name.strip())
                or previous_matches_played > 0
            )
            origin = "TRANSFERRED" if has_transfer_origin else "YOUTH_OR_EXTERNAL"
            prev_tname = previous_team_name.strip()
            prev_pj = max(0, int(previous_matches_played))
            prev_rat = prior_rating
            prev_strength = max(0.50, float(previous_league_strength))

            if previous_team_id > 0:
                prev_row = cur.execute(
                    """
                    SELECT p.player_id, p.matches_played, p.average_rating, t.name AS team_name, t.strength_factor
                    FROM players p
                    JOIN teams t ON p.team_id = t.team_id
                    WHERE p.team_id = ? AND (p.player_id = ? OR LOWER(p.name) = LOWER(?))
                    LIMIT 1;
                    """,
                    (previous_team_id, player_id, player_name.strip()),
                ).fetchone()
                if prev_row:
                    player_id = int(prev_row["player_id"])
                    prev_tname = str(prev_row["team_name"])
                    prev_pj = int(prev_row["matches_played"])
                    if float(prev_row["average_rating"]) > 1.0:
                        prev_rat = float(prev_row["average_rating"])
                    prev_strength = float(prev_row["strength_factor"])
                    # Dar de baja en el club anterior al concretarse la transferencia
                    cur.execute(
                        "UPDATE players SET currently_in_club = 0, is_new_signing = 0 WHERE team_id = ? AND player_id = ?;",
                        (previous_team_id, player_id),
                    )
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
                    specific_position, specific_position_code,
                    jersey_number, convocations_last_20, matches_played,
                    matches_unused_sub, total_minutes, average_rating, currently_in_club,
                    is_new_signing, origin_type, previous_team_id, previous_team_name,
                    previous_matches_played, previous_average_rating, previous_league_strength
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, 1, 1, ?, ?, ?, ?, ?, ?);
                """,
                (
                    player_id,
                    team_id,
                    player_name.strip(),
                    short_name,
                    pos_name,
                    pos_code,
                    spec_name,
                    spec_code,
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
