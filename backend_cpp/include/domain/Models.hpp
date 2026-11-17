#ifndef ALGEBRA_LINEAL_MODELS_HPP
#define ALGEBRA_LINEAL_MODELS_HPP

#include <string>
#include <vector>
#include <array>
#include <algorithm>
#include <cmath>

namespace lpf::domain {

/**
 * @brief Dimensión del espacio vectorial de salida R^20 (las 20 métricas por partido).
 */
constexpr std::size_t NUM_METRICS = 20;

enum MetricIndex : std::size_t {
    GOLES_A_FAVOR = 0,
    GOLES_EN_CONTRA = 1,
    TIROS_AL_ARCO = 2,
    POSESION = 3,
    SALVADAS = 4,
    CORNERS = 5,
    FALTAS = 6,
    PASES = 7,
    TIROS_LIBRES = 8,
    AMARILLAS = 9,
    ROJAS = 10,
    PROMEDIO_VALORACION = 11,
    TIROS = 12,
    TOQUES_AREA_RIVAL = 13,
    OFFSIDES = 14,
    TACLES = 15,
    INTERCEPCIONES = 16,
    RECUPERACIONES = 17,
    DESPEJES = 18,
    TIROS_RIVAL_AL_ARCO = 19
};

struct MetricMetadata {
    std::size_t index;
    const char* key;
    const char* displayName;
    const char* unit;
    bool higherIsBetter;
};

inline const std::array<MetricMetadata, NUM_METRICS>& getMetricsCatalog() {
    static const std::array<MetricMetadata, NUM_METRICS> catalog = {{
        {0,  "goles_a_favor",       "Goles a favor",                   "goles", true},
        {1,  "goles_en_contra",     "Goles en contra",                 "goles", false},
        {2,  "tiros_al_arco",       "Tiros al arco",                   "tiros", true},
        {3,  "posesion",            "Posesión",                        "%",     true},
        {4,  "salvadas_arquero",    "Salvadas",                        "ataj.", true},
        {5,  "corners",             "Córners",                         "córn.", true},
        {6,  "faltas",              "Faltas",                          "faltas",false},
        {7,  "pases",               "Pases",                           "pases", true},
        {8,  "tiros_libres",        "Tiros libres",                    "t.lib.",true},
        {9,  "amarillas",           "Amarillas",                       "tarj.", false},
        {10, "rojas",               "Rojas",                           "tarj.", false},
        {11, "promedio_valoracion", "Promedio de valoración del equipo","pts",   true},
        {12, "tiros",               "Tiros totales",                   "tiros", true},
        {13, "toques_area_rival",   "Toques en el área rival",         "toques",true},
        {14, "offsides",            "Offsides",                        "offs.", false},
        {15, "tacles",              "Tacles",                          "tacles",true},
        {16, "intercepciones",      "Intercepciones",                  "inter.",true},
        {17, "recuperaciones",      "Recuperaciones",                  "recup.",true},
        {18, "despejes",            "Despejes",                        "desp.", true},
        {19, "tiros_rival_al_arco", "Tiros del rival al arco propio",  "tiros", false}
    }};
    return catalog;
}

/**
 * @struct Competition
 * @brief Entidad que modela una Liga o Competición dentro del sistema Multi-Liga.
 */
struct Competition {
    int competitionId = 72;
    std::string code = "LPF";
    std::string name = "Liga Profesional Argentina";
    std::string shortName = "LPF Argentina";
    std::string country = "Argentina";
    std::string confederation = "CONMEBOL";
    int sofascoreId = 155;
    double strengthFactor = 1.00;
};

/**
 * @class Player
 * @brief Entidad de dominio que modela un jugador registrado en el plantel de un equipo,
 *        incluyendo metadatos de resolución Cold-Start y transferencia entre clubes.
 */
struct Player {
    int playerId = 0;
    int teamId = 0;
    std::string name;
    std::string shortName;
    std::string position;     // Portero, Defensor, Mediocampista, Delantero
    std::string positionCode; // GK, DEF, MID, FWD
    std::string jerseyNumber;
    int convocationsLast20 = 0;
    int matchesPlayed = 0;
    int matchesUnusedSub = 0;
    int totalMinutes = 0;
    double averageRating = 6.5;
    bool currentlyInClub = true; // true si pertenece actualmente al equipo (elegible), false si ya no pertenece

    // Soporte para Jugador Nuevo en un Club (Cold-Start Híbrido: Transferencia + Prior Posicional)
    bool isNewSigning = false;                 // true si pertenece al club y tiene < 3 PJ en el equipo
    std::string originType = "ESTABLISHED";    // ESTABLISHED | TRANSFERRED | YOUTH_OR_EXTERNAL | SIMULATED_SIGNING
    int previousTeamId = 0;                    // ID del club anterior si existe en la BD Multi-Liga
    std::string previousTeamName;              // Nombre del club anterior
    int previousMatchesPlayed = 0;             // Partidos jugados en el club de origen
    double previousAverageRating = 0.0;        // Valoración media en el club de origen
    double previousLeagueStrength = 1.0;       // Coeficiente de fuerza de la liga de origen
    double adaptationAlpha = 1.0;              // Peso empírico alpha = PJ / (PJ + 3) in [0, 1]
};

/**
 * @class MatchMetrics
 * @brief Vector en R^20 con las métricas de rendimiento de un equipo en un partido.
 */
class MatchMetrics {
private:
    std::array<double, NUM_METRICS> values_;

public:
    MatchMetrics() { values_.fill(0.0); }

    explicit MatchMetrics(const std::vector<double>& vec) {
        values_.fill(0.0);
        for (std::size_t i = 0; i < std::min(vec.size(), NUM_METRICS); ++i) {
            values_[i] = vec[i];
        }
    }

    double& operator[](std::size_t idx) { return values_.at(idx); }
    double operator[](std::size_t idx) const { return values_.at(idx); }

    std::vector<double> toVector() const {
        return std::vector<double>(values_.begin(), values_.end());
    }

    /**
     * @brief Acota los valores proyectados dentro de dominios físicamente válidos
     *        (métricas de conteo >= 0, posesión en [15, 85], valoración en [5.0, 9.5]).
     */
    MatchMetrics clampedPhysical() const {
        MatchMetrics out = *this;
        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            if (i == POSESION) {
                out[i] = std::clamp(out[i], 15.0, 85.0);
            } else if (i == PROMEDIO_VALORACION) {
                out[i] = std::clamp(out[i], 5.0, 9.5);
            } else {
                out[i] = std::max(0.0, out[i]);
            }
        }
        // Coherencia física: tiros totales >= tiros al arco >= goles a favor
        if (out[TIROS_AL_ARCO] < out[GOLES_A_FAVOR]) {
            out[TIROS_AL_ARCO] = out[GOLES_A_FAVOR];
        }
        if (out[TIROS] < out[TIROS_AL_ARCO]) {
            out[TIROS] = out[TIROS_AL_ARCO];
        }
        return out;
    }
};

struct MatchLineupSlot {
    int playerId = 0;
    int orderIndex = 0;
    std::string role; // TITULAR, SUPLENTE_INGRESO, SUPLENTE_SIN_INGRESAR
    int played = 0;   // 1 si jugó, 0 en caso contrario
};

struct MatchRecord {
    int debugId = 0;
    std::string debugMatchId;
    int eventId = 0;
    std::string dateUtc;
    int teamId = 0;
    int opponentId = 0;
    std::string opponentName;
    std::string condition; // Local | Visitante
    int competitionId = 72;
    std::string competitionName = "Liga Profesional Argentina";
    MatchMetrics metrics;
    std::vector<MatchLineupSlot> lineup;
};

struct Team {
    int teamId = 0;
    std::string name;
    std::string shortName;
    int competitionId = 72;
    std::string competitionName = "Liga Profesional Argentina";
    std::string country = "Argentina";
    double strengthFactor = 1.00;
    std::vector<Player> squad;
    std::vector<MatchRecord> matches;
};

struct Formation {
    std::string name;
    int defenders;
    int midfielders;
    int forwards;

    static std::vector<Formation> standardFormations() {
        return {
            {"4-3-3", 4, 3, 3},
            {"4-4-2", 4, 4, 2},
            {"4-2-3-1", 4, 5, 1},
            {"3-5-2", 3, 5, 2},
            {"5-3-2", 5, 3, 2},
            {"3-4-3", 3, 4, 3}
        };
    }
};

struct PlayerContribution {
    Player player;
    int inputBit = 0; // 1 o 0
    double tacticalScore = 0.0;
    std::vector<double> columnVector; // Imagen T(e_j) in R^20
};

struct BehaviorPredictionResult {
    int teamId = 0;
    std::string teamName;
    int competitionId = 72;
    std::string competitionName;
    double leagueStrengthFactor = 1.0;
    std::vector<int> selectedPlayerIds;
    std::vector<int> binaryAlignmentVector; // x in {0, 1}^n
    MatchMetrics rawLinearOutput;           // A_E * x (sin acotar)
    MatchMetrics predictedMetrics;          // A_E * x (acotado físicamente)
    MatchMetrics historicalAverage;         // Promedio histórico del equipo
    double tacticalScore = 0.0;
    double modelFitR2 = 0.0;
    double frobeniusResidual = 0.0;
    std::vector<PlayerContribution> activeContributions;
};

struct OptimalLineupResult {
    int teamId = 0;
    std::string teamName;
    int competitionId = 72;
    std::string competitionName;
    double leagueStrengthFactor = 1.0;
    std::string strategyName;
    std::string formationName;
    std::vector<PlayerContribution> optimalStarters;     // 11 titulares óptimos (x_j = 1)
    std::vector<PlayerContribution> recommendedBench;    // 7 suplentes sugeridos (x_j = 0)
    std::vector<int> optimalBinaryVector;                // x* in {0, 1}^n
    MatchMetrics expectedMetrics;                        // T_E(x*) = A_E * x*
    MatchMetrics historicalAverage;
    double totalTacticalScore = 0.0;
    double improvementPercentage = 0.0;
};

struct ScoreProbability {
    int goalsTeamA = 0;
    int goalsTeamB = 0;
    double probability = 0.0;
};

struct MatchupPredictionResult {
    int teamAId = 0;
    std::string teamAName;
    std::string teamAFormation;
    int teamACompetitionId = 72;
    std::string teamACompetitionName;
    double teamAStrengthFactor = 1.0;

    int teamBId = 0;
    std::string teamBName;
    std::string teamBFormation;
    int teamBCompetitionId = 72;
    std::string teamBCompetitionName;
    double teamBStrengthFactor = 1.0;

    bool isInterLeague = false;
    double relativeLeagueRatioAtoB = 1.0;

    MatchMetrics standaloneMetricsA; // T_A(x_A) puro
    MatchMetrics standaloneMetricsB; // T_B(x_B) puro
    MatchMetrics coupledMetricsA;    // Métricas proyectadas en el cruce A vs B
    MatchMetrics coupledMetricsB;    // Métricas proyectadas en el cruce B vs A

    double expectedGoalsA = 0.0;
    double expectedGoalsB = 0.0;
    double winProbabilityA = 0.0;
    double drawProbability = 0.0;
    double winProbabilityB = 0.0;
    double tacticalEdgeScoreA = 0.0;
    double tacticalEdgeScoreB = 0.0;

    std::vector<ScoreProbability> topScorelines;
    std::vector<PlayerContribution> lineupA;
    std::vector<PlayerContribution> lineupB;
};

/**
 * @struct TransferSimulationResult
 * @brief Resultado de simular la incorporación de un jugador nuevo (desde otro club/liga
 *        o debutante externo) dentro de la matriz A_E de un club destino.
 */
struct TransferSimulationResult {
    int targetTeamId = 0;
    std::string targetTeamName;
    std::string targetCompetitionName;
    double targetLeagueStrength = 1.0;

    int sourceTeamId = 0;
    std::string sourceTeamName;
    std::string sourceCompetitionName;
    double sourceLeagueStrength = 1.0;

    Player simulatedPlayer;
    int replacedPlayerId = 0;
    std::string replacedPlayerName;
    std::string strategyName;
    std::string formationName;

    double leagueScalingRatio = 1.0;
    double adaptationFactor = 0.80;

    std::vector<double> injectedColumnVector;   // a_new in R^20
    std::vector<double> positionalPriorVector;  // a_bar_P in R^20 del club destino
    std::vector<double> sourceDifferentialVec;  // delta_j del club origen in R^20

    MatchMetrics baselineMetrics;               // Comportamiento del 11 sin el fichaje
    MatchMetrics postTransferMetrics;           // Comportamiento del 11 con el nuevo jugador
    double baselineTacticalScore = 0.0;
    double postTransferTacticalScore = 0.0;
    double tacticalScoreDelta = 0.0;
    double improvementPercentage = 0.0;
    double newPlayerTacticalScore = 0.0;
    double replacedPlayerTacticalScore = 0.0;

    std::vector<PlayerContribution> postTransferStarters;
};

} // namespace lpf::domain

#endif // ALGEBRA_LINEAL_MODELS_HPP
