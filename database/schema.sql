-- =============================================================================
-- Esquema Relacional de Base de Datos (3FN) - Sistema de Análisis Lineal LPF
-- Motor: SQLite3 con soporte de concurrencia Write-Ahead Logging (WAL)
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- 1. Tabla de Equipos de la Liga Profesional
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 2. Tabla de Jugadores Registrados por Equipo
CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    position TEXT NOT NULL,          -- Portero, Defensor, Mediocampista, Delantero
    position_code TEXT NOT NULL,     -- GK, DEF, MID, FWD
    jersey_number TEXT DEFAULT '',
    convocations_last_20 INTEGER DEFAULT 0,
    matches_played INTEGER DEFAULT 0,
    matches_unused_sub INTEGER DEFAULT 0,
    total_minutes INTEGER DEFAULT 0,
    average_rating REAL DEFAULT 6.5,
    currently_in_club INTEGER NOT NULL DEFAULT 1 CHECK(currently_in_club IN (0, 1)),
    PRIMARY KEY (team_id, player_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
);

-- 3. Tabla de Partidos y las 20 Métricas de Comportamiento por Equipo
CREATE TABLE IF NOT EXISTS matches (
    debug_id INTEGER PRIMARY KEY,
    debug_match_id TEXT NOT NULL UNIQUE,
    event_id INTEGER NOT NULL,
    fecha_utc TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    opponent_id INTEGER NOT NULL,
    opponent_name TEXT NOT NULL,
    condicion TEXT NOT NULL,         -- 'Local' | 'Visitante'
    -- Las 20 métricas de interés del modelo lineal:
    goles_a_favor REAL NOT NULL DEFAULT 0,
    goles_en_contra REAL NOT NULL DEFAULT 0,
    tiros_al_arco REAL NOT NULL DEFAULT 0,
    posesion REAL NOT NULL DEFAULT 50.0,
    salvadas_arquero REAL NOT NULL DEFAULT 0,
    corners REAL NOT NULL DEFAULT 0,
    faltas REAL NOT NULL DEFAULT 0,
    pases REAL NOT NULL DEFAULT 0,
    tiros_libres REAL NOT NULL DEFAULT 0,
    amarillas REAL NOT NULL DEFAULT 0,
    rojas REAL NOT NULL DEFAULT 0,
    promedio_valoracion REAL NOT NULL DEFAULT 6.5,
    tiros REAL NOT NULL DEFAULT 0,
    toques_area_rival REAL NOT NULL DEFAULT 0,
    offsides REAL NOT NULL DEFAULT 0,
    tacles REAL NOT NULL DEFAULT 0,
    intercepciones REAL NOT NULL DEFAULT 0,
    recuperaciones REAL NOT NULL DEFAULT 0,
    despejes REAL NOT NULL DEFAULT 0,
    tiros_rival_al_arco REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
);

-- 4. Tabla de Alineaciones por Partido (Vector de Entrada x in {0, 1}^n)
-- Registra los jugadores iniciales (TITULAR -> played=1),
-- sustitutos que ingresaron (SUPLENTE_INGRESO -> played=1)
-- y convocados que no formaron parte del encuentro (SUPLENTE_SIN_INGRESAR -> played=0).
-- Los jugadores del plantel no presentes en un partido tienen implícitamente played=0.
CREATE TABLE IF NOT EXISTS match_lineups (
    debug_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    order_index INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('TITULAR', 'SUPLENTE_INGRESO', 'SUPLENTE_SIN_INGRESAR')),
    played INTEGER NOT NULL CHECK(played IN (0, 1)),
    PRIMARY KEY (debug_id, player_id),
    FOREIGN KEY (debug_id) REFERENCES matches(debug_id) ON DELETE CASCADE,
    FOREIGN KEY (team_id, player_id) REFERENCES players(team_id, player_id) ON DELETE CASCADE
);

-- Índices para optimizar consultas concurrentes del Backend C++
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_matches_team ON matches(team_id);
CREATE INDEX IF NOT EXISTS idx_lineups_match ON match_lineups(debug_id);
CREATE INDEX IF NOT EXISTS idx_lineups_team_player ON match_lineups(team_id, player_id);
