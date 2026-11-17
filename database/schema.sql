-- =============================================================================
-- Esquema Relacional de Base de Datos (3FN) - Sistema Multi-Liga de Análisis Lineal
-- Motor: SQLite3 con soporte de concurrencia Write-Ahead Logging (WAL)
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- 0. Tabla de Ligas y Competiciones (Multi-Liga)
CREATE TABLE IF NOT EXISTS competitions (
    competition_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    country TEXT NOT NULL,
    confederation TEXT NOT NULL,
    sofascore_id INTEGER NOT NULL DEFAULT 0,
    strength_factor REAL NOT NULL DEFAULT 1.00
);

-- 1. Tabla de Equipos Registrados (Multi-Liga)
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    competition_id INTEGER NOT NULL DEFAULT 72,
    competition_name TEXT NOT NULL DEFAULT 'Liga Profesional Argentina',
    country TEXT NOT NULL DEFAULT 'Argentina',
    strength_factor REAL NOT NULL DEFAULT 1.00,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (competition_id) REFERENCES competitions(competition_id) ON DELETE SET DEFAULT
);

-- 2. Tabla de Jugadores Registrados por Equipo (incluyendo soporte Cold-Start y Transferencias)
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
    -- Metadatos para resolución de Jugador Nuevo en el Club (Cold-Start Híbrido):
    is_new_signing INTEGER NOT NULL DEFAULT 0 CHECK(is_new_signing IN (0, 1)),
    origin_type TEXT NOT NULL DEFAULT 'ESTABLISHED' CHECK(origin_type IN ('ESTABLISHED', 'TRANSFERRED', 'YOUTH_OR_EXTERNAL')),
    previous_team_id INTEGER NOT NULL DEFAULT 0,
    previous_team_name TEXT NOT NULL DEFAULT '',
    previous_matches_played INTEGER NOT NULL DEFAULT 0,
    previous_average_rating REAL NOT NULL DEFAULT 0.0,
    previous_league_strength REAL NOT NULL DEFAULT 1.0,
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
    competition_id INTEGER NOT NULL DEFAULT 72,
    competition_name TEXT NOT NULL DEFAULT 'Liga Profesional Argentina',
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
CREATE INDEX IF NOT EXISTS idx_teams_competition ON teams(competition_id);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_players_pid ON players(player_id);
CREATE INDEX IF NOT EXISTS idx_matches_team ON matches(team_id);
CREATE INDEX IF NOT EXISTS idx_matches_comp ON matches(competition_id);
CREATE INDEX IF NOT EXISTS idx_lineups_match ON match_lineups(debug_id);
CREATE INDEX IF NOT EXISTS idx_lineups_team_player ON match_lineups(team_id, player_id);
