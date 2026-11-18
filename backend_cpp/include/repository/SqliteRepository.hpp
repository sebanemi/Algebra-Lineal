#ifndef ALGEBRA_LINEAL_SQLITE_REPOSITORY_HPP
#define ALGEBRA_LINEAL_SQLITE_REPOSITORY_HPP

#include "repository/IDataRepository.hpp"
#include <mutex>
#include <stdexcept>
#include <unordered_map>
#include <iostream>
#include <filesystem>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace lpf::repository {

// Tipos opacos y constantes estándar de la API C de SQLite3
struct sqlite3;
struct sqlite3_stmt;

constexpr int SQLITE_OK_CODE = 0;
constexpr int SQLITE_ROW_CODE = 100;
constexpr int SQLITE_DONE_CODE = 101;

/**
 * @class SqliteDynamicDriver
 * @brief Conector nativo C++ a SQLite3 mediante enlace dinámico (sqlite3.dll).
 *        Permite ejecutar consultas SQL reales desde C++ sin requerir headers ni
 *        librerías estáticas externas en el entorno de compilación.
 */
class SqliteDynamicDriver {
public:
    using fn_initialize = int (*)();
    using fn_open = int (*)(const char*, sqlite3**);
    using fn_close = int (*)(sqlite3*);
    using fn_exec = int (*)(sqlite3*, const char*, int (*)(void*, int, char**, char**), void*, char**);
    using fn_prepare_v2 = int (*)(sqlite3*, const char*, int, sqlite3_stmt**, const char**);
    using fn_step = int (*)(sqlite3_stmt*);
    using fn_finalize = int (*)(sqlite3_stmt*);
    using fn_column_int = int (*)(sqlite3_stmt*, int);
    using fn_column_double = double (*)(sqlite3_stmt*, int);
    using fn_column_text = const unsigned char* (*)(sqlite3_stmt*, int);
    using fn_errmsg = const char* (*)(sqlite3*);

    fn_initialize initialize = nullptr;
    fn_open open = nullptr;
    fn_close close = nullptr;
    fn_exec exec = nullptr;
    fn_prepare_v2 prepare_v2 = nullptr;
    fn_step step = nullptr;
    fn_finalize finalize = nullptr;
    fn_column_int column_int = nullptr;
    fn_column_double column_double = nullptr;
    fn_column_text column_text = nullptr;
    fn_errmsg errmsg = nullptr;

private:
#ifdef _WIN32
    HMODULE hModule_ = nullptr;
#endif

public:
    SqliteDynamicDriver() {
#ifdef _WIN32
        std::vector<std::string> candidatePaths;

        char exePath[MAX_PATH] = {0};
        DWORD len = GetModuleFileNameA(nullptr, exePath, MAX_PATH);
        if (len > 0 && len < MAX_PATH) {
            std::string fullExe(exePath, len);
            std::size_t lastSlash = fullExe.find_last_of("\\/");
            if (lastSlash != std::string::npos) {
                std::string exeDir = fullExe.substr(0, lastSlash);
                candidatePaths.push_back(exeDir + "\\sqlite3.dll");
                candidatePaths.push_back(exeDir + "\\..\\bin\\sqlite3.dll");
                candidatePaths.push_back(exeDir + "\\..\\sqlite3.dll");
            }
        }

        candidatePaths.push_back("sqlite3.dll");
        candidatePaths.push_back("bin\\sqlite3.dll");
        candidatePaths.push_back("..\\bin\\sqlite3.dll");

        for (const auto& path : candidatePaths) {
            hModule_ = LoadLibraryA(path.c_str());
            if (hModule_ != nullptr) break;
        }
        if (!hModule_) {
            throw std::runtime_error(
                "No se pudo cargar sqlite3.dll dinamicamente. Asegurese de que sqlite3.dll este en la carpeta bin/ o en el PATH."
            );
        }

        initialize = reinterpret_cast<fn_initialize>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_initialize")));
        open = reinterpret_cast<fn_open>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_open")));
        close = reinterpret_cast<fn_close>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_close")));
        exec = reinterpret_cast<fn_exec>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_exec")));
        prepare_v2 = reinterpret_cast<fn_prepare_v2>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_prepare_v2")));
        step = reinterpret_cast<fn_step>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_step")));
        finalize = reinterpret_cast<fn_finalize>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_finalize")));
        column_int = reinterpret_cast<fn_column_int>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_column_int")));
        column_double = reinterpret_cast<fn_column_double>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_column_double")));
        column_text = reinterpret_cast<fn_column_text>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_column_text")));
        errmsg = reinterpret_cast<fn_errmsg>(reinterpret_cast<void*>(GetProcAddress(hModule_, "sqlite3_errmsg")));

        if (!open || !close || !prepare_v2 || !step || !finalize ||
            !column_int || !column_double || !column_text) {
            throw std::runtime_error("Funciones de API C de SQLite3 incompletas en sqlite3.dll.");
        }
        if (initialize) {
            initialize();
        }
#endif
    }

    ~SqliteDynamicDriver() {
#ifdef _WIN32
        if (hModule_) {
            FreeLibrary(hModule_);
        }
#endif
    }

    SqliteDynamicDriver(const SqliteDynamicDriver&) = delete;
    SqliteDynamicDriver& operator=(const SqliteDynamicDriver&) = delete;
};

/**
 * @class SqliteRepository
 * @brief Implementación concreta de IDataRepository que consulta de forma segura para
 *        múltiples hilos la base de datos SQLite3 Multi-Liga (data/lpf_analytics.db en modo WAL).
 */
class SqliteRepository final : public IDataRepository {
private:
    std::string dbPath_;
    SqliteDynamicDriver driver_;
    mutable std::mutex dbMutex_;

    static std::string safeText(const unsigned char* raw) {
        return raw ? reinterpret_cast<const char*>(raw) : "";
    }

    static std::string resolveDynamicDbPath(const std::string& requestedPath) {
        namespace fs = std::filesystem;
        if (fs::exists(requestedPath)) {
            return fs::absolute(requestedPath).string();
        }
#ifdef _WIN32
        char exePath[MAX_PATH] = {0};
        DWORD len = GetModuleFileNameA(nullptr, exePath, MAX_PATH);
        if (len > 0 && len < MAX_PATH) {
            fs::path exeDir = fs::path(std::string(exePath, len)).parent_path();
            const fs::path bases[] = {exeDir, exeDir.parent_path(), exeDir.parent_path().parent_path()};
            for (const auto& base : bases) {
                if (base.empty()) continue;
                fs::path candidate = base / requestedPath;
                if (fs::exists(candidate)) {
                    return fs::absolute(candidate).string();
                }
            }
        }
#endif
        return requestedPath;
    }

public:
    explicit SqliteRepository(std::string dbPath)
        : dbPath_(resolveDynamicDbPath(dbPath)), driver_() {}

    std::string connectionInfo() const override {
        return "SQLite3 Native C++ Driver (WAL Mode) -> " + dbPath_;
    }

    std::vector<domain::Competition> loadAllCompetitions() override {
        std::lock_guard<std::mutex> lock(dbMutex_);

        sqlite3* db = nullptr;
        if (driver_.open(dbPath_.c_str(), &db) != SQLITE_OK_CODE || !db) {
            throw std::runtime_error("Error al abrir la base de datos SQLite: " + dbPath_);
        }

        std::vector<domain::Competition> competitions;
        sqlite3_stmt* stmt = nullptr;
        const char* sql =
            "SELECT competition_id, code, name, short_name, country, confederation, sofascore_id, strength_factor "
            "FROM competitions ORDER BY strength_factor DESC, name ASC;";
        if (driver_.prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK_CODE) {
            while (driver_.step(stmt) == SQLITE_ROW_CODE) {
                domain::Competition c;
                c.competitionId = driver_.column_int(stmt, 0);
                c.code = safeText(driver_.column_text(stmt, 1));
                c.name = safeText(driver_.column_text(stmt, 2));
                c.shortName = safeText(driver_.column_text(stmt, 3));
                c.country = safeText(driver_.column_text(stmt, 4));
                c.confederation = safeText(driver_.column_text(stmt, 5));
                c.sofascoreId = driver_.column_int(stmt, 6);
                c.strengthFactor = driver_.column_double(stmt, 7);
                competitions.push_back(std::move(c));
            }
            driver_.finalize(stmt);
        }

        driver_.close(db);
        return competitions;
    }

    std::vector<domain::Team> loadAllTeams() override {
        std::lock_guard<std::mutex> lock(dbMutex_);

        sqlite3* db = nullptr;
        if (driver_.open(dbPath_.c_str(), &db) != SQLITE_OK_CODE || !db) {
            throw std::runtime_error("Error al abrir la base de datos SQLite: " + dbPath_);
        }

        if (driver_.exec) {
            driver_.exec(db, "PRAGMA synchronous=NORMAL;", nullptr, nullptr, nullptr);
        }

        std::vector<domain::Team> teams;
        std::unordered_map<int, std::size_t> teamIdToIdx;

        // 1. Cargar Equipos (con metadatos de Liga / Competición y Fuerza de Liga)
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql =
                "SELECT team_id, name, short_name, competition_id, competition_name, country, strength_factor "
                "FROM teams ORDER BY competition_id ASC, name ASC;";
            if (driver_.prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK_CODE) {
                while (driver_.step(stmt) == SQLITE_ROW_CODE) {
                    domain::Team t;
                    t.teamId = driver_.column_int(stmt, 0);
                    t.name = safeText(driver_.column_text(stmt, 1));
                    t.shortName = safeText(driver_.column_text(stmt, 2));
                    t.competitionId = driver_.column_int(stmt, 3);
                    t.competitionName = safeText(driver_.column_text(stmt, 4));
                    t.country = safeText(driver_.column_text(stmt, 5));
                    t.strengthFactor = driver_.column_double(stmt, 6);
                    if (t.strengthFactor <= 0.0) t.strengthFactor = 1.00;
                    teamIdToIdx[t.teamId] = teams.size();
                    teams.push_back(std::move(t));
                }
                driver_.finalize(stmt);
            }
        }

        // 2. Cargar Jugadores Registrados por Equipo (incluyendo sub-posición táctica, soporte Cold-Start y Transferencias)
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql =
                "SELECT player_id, team_id, name, short_name, position, position_code, "
                "specific_position, specific_position_code, "
                "jersey_number, convocations_last_20, matches_played, matches_unused_sub, "
                "total_minutes, average_rating, currently_in_club, "
                "is_new_signing, origin_type, previous_team_id, previous_team_name, "
                "previous_matches_played, previous_average_rating, previous_league_strength "
                "FROM players ORDER BY team_id ASC, currently_in_club DESC, matches_played DESC, total_minutes DESC, player_id ASC;";
            if (driver_.prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK_CODE) {
                while (driver_.step(stmt) == SQLITE_ROW_CODE) {
                    domain::Player p;
                    p.playerId = driver_.column_int(stmt, 0);
                    p.teamId = driver_.column_int(stmt, 1);
                    p.name = safeText(driver_.column_text(stmt, 2));
                    p.shortName = safeText(driver_.column_text(stmt, 3));
                    p.position = safeText(driver_.column_text(stmt, 4));
                    p.positionCode = safeText(driver_.column_text(stmt, 5));
                    p.specificPosition = safeText(driver_.column_text(stmt, 6));
                    p.specificPositionCode = safeText(driver_.column_text(stmt, 7));
                    if (p.specificPositionCode.empty()) {
                        auto tax = domain::resolvePositionTaxonomy(p.positionCode);
                        p.specificPositionCode = tax.specificCode;
                        p.specificPosition = tax.specificName;
                    }
                    p.jerseyNumber = safeText(driver_.column_text(stmt, 8));
                    p.convocationsLast20 = driver_.column_int(stmt, 9);
                    p.matchesPlayed = driver_.column_int(stmt, 10);
                    p.matchesUnusedSub = driver_.column_int(stmt, 11);
                    p.totalMinutes = driver_.column_int(stmt, 12);
                    p.averageRating = driver_.column_double(stmt, 13);
                    p.currentlyInClub = (driver_.column_int(stmt, 14) != 0);
                    p.isNewSigning = (driver_.column_int(stmt, 15) != 0);
                    p.originType = safeText(driver_.column_text(stmt, 16));
                    if (p.originType.empty()) {
                        p.originType = p.isNewSigning ? "YOUTH_OR_EXTERNAL" : "ESTABLISHED";
                    }
                    p.previousTeamId = driver_.column_int(stmt, 17);
                    p.previousTeamName = safeText(driver_.column_text(stmt, 18));
                    p.previousMatchesPlayed = driver_.column_int(stmt, 19);
                    p.previousAverageRating = driver_.column_double(stmt, 20);
                    p.previousLeagueStrength = driver_.column_double(stmt, 21);
                    if (p.previousLeagueStrength <= 0.0) p.previousLeagueStrength = 1.0;

                    auto it = teamIdToIdx.find(p.teamId);
                    if (it != teamIdToIdx.end()) {
                        teams[it->second].squad.push_back(std::move(p));
                    }
                }
                driver_.finalize(stmt);
            }
        }

        // 3. Cargar Alineaciones por Partido (1 = jugó, 0 = no jugó)
        std::unordered_map<int, std::vector<domain::MatchLineupSlot>> lineupsByDebugId;
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql =
                "SELECT debug_id, player_id, order_index, role, played "
                "FROM match_lineups ORDER BY debug_id ASC, order_index ASC;";
            if (driver_.prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK_CODE) {
                while (driver_.step(stmt) == SQLITE_ROW_CODE) {
                    int debugId = driver_.column_int(stmt, 0);
                    domain::MatchLineupSlot slot;
                    slot.playerId = driver_.column_int(stmt, 1);
                    slot.orderIndex = driver_.column_int(stmt, 2);
                    slot.role = safeText(driver_.column_text(stmt, 3));
                    slot.played = driver_.column_int(stmt, 4);
                    lineupsByDebugId[debugId].push_back(std::move(slot));
                }
                driver_.finalize(stmt);
            }
        }

        // 4. Cargar Partidos y las 20 Métricas de Comportamiento
        {
            sqlite3_stmt* stmt = nullptr;
            const char* sql =
                "SELECT debug_id, debug_match_id, event_id, fecha_utc, team_id, "
                "opponent_id, opponent_name, condicion, "
                "goles_a_favor, goles_en_contra, tiros_al_arco, posesion, "
                "salvadas_arquero, corners, faltas, pases, tiros_libres, "
                "amarillas, rojas, promedio_valoracion, tiros, "
                "toques_area_rival, offsides, tacles, intercepciones, "
                "recuperaciones, despejes, tiros_rival_al_arco, "
                "competition_id, competition_name "
                "FROM matches ORDER BY debug_id ASC;";
            if (driver_.prepare_v2(db, sql, -1, &stmt, nullptr) == SQLITE_OK_CODE) {
                while (driver_.step(stmt) == SQLITE_ROW_CODE) {
                    domain::MatchRecord m;
                    m.debugId = driver_.column_int(stmt, 0);
                    m.debugMatchId = safeText(driver_.column_text(stmt, 1));
                    m.eventId = driver_.column_int(stmt, 2);
                    m.dateUtc = safeText(driver_.column_text(stmt, 3));
                    m.teamId = driver_.column_int(stmt, 4);
                    m.opponentId = driver_.column_int(stmt, 5);
                    m.opponentName = safeText(driver_.column_text(stmt, 6));
                    m.condition = safeText(driver_.column_text(stmt, 7));

                    for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
                        m.metrics[i] = driver_.column_double(stmt, static_cast<int>(8 + i));
                    }
                    m.competitionId = driver_.column_int(stmt, 28);
                    m.competitionName = safeText(driver_.column_text(stmt, 29));

                    auto lIt = lineupsByDebugId.find(m.debugId);
                    if (lIt != lineupsByDebugId.end()) {
                        m.lineup = lIt->second;
                    }

                    auto tIt = teamIdToIdx.find(m.teamId);
                    if (tIt != teamIdToIdx.end()) {
                        teams[tIt->second].matches.push_back(std::move(m));
                    }
                }
                driver_.finalize(stmt);
            }
        }

        driver_.close(db);
        return teams;
    }

    std::optional<domain::Team> loadTeamById(int teamId) override {
        auto all = loadAllTeams();
        for (auto& t : all) {
            if (t.teamId == teamId) {
                return t;
            }
        }
        return std::nullopt;
    }
};

} // namespace lpf::repository

#endif // ALGEBRA_LINEAL_SQLITE_REPOSITORY_HPP
