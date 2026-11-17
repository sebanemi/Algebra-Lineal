#ifndef ALGEBRA_LINEAL_ANALYTICS_ENGINE_SERVICE_HPP
#define ALGEBRA_LINEAL_ANALYTICS_ENGINE_SERVICE_HPP

#include "domain/Models.hpp"
#include "domain/TeamTransformationModel.hpp"
#include "domain/TacticalStrategies.hpp"
#include "repository/IDataRepository.hpp"

#include <shared_mutex>
#include <unordered_map>
#include <memory>
#include <cmath>
#include <algorithm>
#include <stdexcept>
#include <numeric>

namespace lpf::services {

/**
 * @class AnalyticsEngineService
 * @brief Capa de Servicio de Dominio Multi-Liga (Service Layer - Pressman / Ghezzi).
 *        Gestiona de forma concurrente (thread-safe mediante std::shared_mutex)
 *        las Transformaciones Lineales de todos los equipos de múltiples ligas,
 *        resuelve el problema de jugadores nuevos (Cold-Start Híbrido con transferencia
 *        matricial entre clubes) y ejecuta los casos de uso (a), (b), (c) y Simulación de Fichajes.
 */
class AnalyticsEngineService {
private:
    std::shared_ptr<repository::IDataRepository> repository_;
    mutable std::shared_mutex mutex_;
    std::vector<domain::Competition> competitions_;
    std::unordered_map<int, domain::TeamTransformationModel> modelsByTeamId_;
    std::vector<int> sortedTeamIds_;
    double ridgeLambda_ = 1.35;

    static double poissonPmf(int k, double lambda) {
        if (lambda <= 0.0) return (k == 0) ? 1.0 : 0.0;
        double logP = k * std::log(lambda) - lambda - std::lgamma(k + 1.0);
        return std::exp(logP);
    }

public:
    explicit AnalyticsEngineService(
        std::shared_ptr<repository::IDataRepository> repository,
        double ridgeLambda = 1.35
    ) : repository_(std::move(repository)), ridgeLambda_(ridgeLambda) {
        reloadModels();
    }

    /**
     * @brief Recarga desde la base de datos SQLite todas las competiciones y equipos,
     *        calcula sus matrices A_E in R^(20 x n) en dos pasadas (Pasada 1: modelo local;
     *        Pasada 2: transferencia matricial inter-club para jugadores nuevos transferidos).
     */
    void reloadModels() {
        auto competitions = repository_->loadAllCompetitions();
        auto teams = repository_->loadAllTeams();
        std::unordered_map<int, domain::TeamTransformationModel> newModels;
        std::vector<int> ids;
        ids.reserve(teams.size());

        // Pasada 1: Construir modelo lineal base de cada equipo
        for (const auto& t : teams) {
            newModels.emplace(t.teamId, domain::TeamTransformationModel(t, ridgeLambda_));
            ids.push_back(t.teamId);
        }

        // Pasada 2: Transferencia Matricial Inter-Club para jugadores nuevos con historial en otro club de la BD
        for (auto& kv : newModels) {
            auto& targetModel = kv.second;
            for (const auto& p : targetModel.team().squad) {
                if (p.matchesPlayed < 5 && p.previousTeamId > 0) {
                    auto srcIt = newModels.find(p.previousTeamId);
                    if (srcIt != newModels.end() && srcIt->second.hasPlayer(p.playerId)) {
                        const auto& srcModel = srcIt->second;
                        std::vector<double> srcCol = srcModel.getPlayerColumn(p.playerId);
                        std::vector<double> srcPosPrior = srcModel.getPositionalPrior(p.positionCode);
                        targetModel.applyCrossClubTransferVector(
                            p.playerId,
                            srcCol,
                            srcPosPrior,
                            srcModel.team().strengthFactor
                        );
                    }
                }
            }
        }

        std::unique_lock<std::shared_mutex> writeLock(mutex_);
        competitions_ = std::move(competitions);
        modelsByTeamId_ = std::move(newModels);
        sortedTeamIds_ = std::move(ids);
    }

    std::size_t teamCount() const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        return modelsByTeamId_.size();
    }

    std::size_t competitionCount() const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        return competitions_.size();
    }

    std::string repositoryInfo() const {
        return repository_->connectionInfo();
    }

    std::vector<domain::Competition> getAllCompetitions() const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        return competitions_;
    }

    std::vector<domain::TeamTransformationModel> getAllTeamModels(int competitionIdFilter = 0) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        std::vector<domain::TeamTransformationModel> out;
        out.reserve(sortedTeamIds_.size());
        for (int id : sortedTeamIds_) {
            auto it = modelsByTeamId_.find(id);
            if (it != modelsByTeamId_.end()) {
                if (competitionIdFilter == 0 || it->second.team().competitionId == competitionIdFilter) {
                    out.push_back(it->second);
                }
            }
        }
        return out;
    }

    domain::TeamTransformationModel getTeamModel(int teamId) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        auto it = modelsByTeamId_.find(teamId);
        if (it == modelsByTeamId_.end()) {
            throw std::invalid_argument("Equipo no encontrado con ID: " + std::to_string(teamId));
        }
        return it->second;
    }

    /**
     * @brief Caso de Uso (a): Un equipo ingresa su equipo inicial y el sistema devuelve
     *        el comportamiento que debería tener en las 20 métricas mediante T_E(x) = A_E * x.
     */
    domain::BehaviorPredictionResult predictBehavior(
        int teamId,
        const std::vector<int>& playerIds,
        const std::string& strategyCode = "BALANCED"
    ) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        auto it = modelsByTeamId_.find(teamId);
        if (it == modelsByTeamId_.end()) {
            throw std::invalid_argument("Equipo no encontrado con ID: " + std::to_string(teamId));
        }
        auto strategy = domain::TacticalStrategyFactory::create(strategyCode);
        return it->second.predictBehavior(playerIds, *strategy);
    }

    /**
     * @brief Caso de Uso (b): Se ingresa un equipo y el sistema devuelve el equipo inicial
     *        (11 titulares) que le da el mejor rendimiento posible.
     */
    domain::OptimalLineupResult optimizeLineup(
        int teamId,
        const std::string& strategyCode = "BALANCED",
        const std::string& formation = "AUTO"
    ) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        auto it = modelsByTeamId_.find(teamId);
        if (it == modelsByTeamId_.end()) {
            throw std::invalid_argument("Equipo no encontrado con ID: " + std::to_string(teamId));
        }
        auto strategy = domain::TacticalStrategyFactory::create(strategyCode);
        return it->second.findOptimalLineup(*strategy, formation);
    }

    /**
     * @brief Caso de Uso (c): Cálculos y predicciones entre dos equipos (Equipo A vs Equipo B),
     *        soportando tanto duelos de la misma liga como cruces Inter-Liga ajustados por
     *        el Coeficiente de Fuerza de Liga (League Strength Factor).
     */
    domain::MatchupPredictionResult predictMatchup(
        int teamAId,
        int teamBId,
        const std::vector<int>& customLineupA = {},
        const std::vector<int>& customLineupB = {},
        const std::string& strategyCode = "BALANCED",
        const std::string& formationA = "AUTO",
        const std::string& formationB = "AUTO",
        bool neutralVenue = false
    ) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        auto itA = modelsByTeamId_.find(teamAId);
        auto itB = modelsByTeamId_.find(teamBId);
        if (itA == modelsByTeamId_.end()) {
            throw std::invalid_argument("Equipo A no encontrado con ID: " + std::to_string(teamAId));
        }
        if (itB == modelsByTeamId_.end()) {
            throw std::invalid_argument("Equipo B no encontrado con ID: " + std::to_string(teamBId));
        }

        const auto& modelA = itA->second;
        const auto& modelB = itB->second;
        auto strategy = domain::TacticalStrategyFactory::create(strategyCode);

        domain::MatchupPredictionResult res;
        res.teamAId = modelA.team().teamId;
        res.teamAName = modelA.team().name;
        res.teamACompetitionId = modelA.team().competitionId;
        res.teamACompetitionName = modelA.team().competitionName;
        res.teamAStrengthFactor = modelA.team().strengthFactor;

        res.teamBId = modelB.team().teamId;
        res.teamBName = modelB.team().name;
        res.teamBCompetitionId = modelB.team().competitionId;
        res.teamBCompetitionName = modelB.team().competitionName;
        res.teamBStrengthFactor = modelB.team().strengthFactor;

        res.isInterLeague = (res.teamACompetitionId != res.teamBCompetitionId);
        double strA = std::max(0.50, res.teamAStrengthFactor);
        double strB = std::max(0.50, res.teamBStrengthFactor);
        res.relativeLeagueRatioAtoB = strA / strB;

        // Escala de ajuste inter-liga (para equipos de la misma liga vale exactamente 1.0)
        double leagueBoostA = std::pow(strA / strB, 0.55);
        double leagueBoostB = std::pow(strB / strA, 0.55);
        leagueBoostA = std::clamp(leagueBoostA, 0.72, 1.38);
        leagueBoostB = std::clamp(leagueBoostB, 0.72, 1.38);

        // Obtener rendimiento individual de A (con alineación personalizada o 11 óptimo)
        if (!customLineupA.empty()) {
            auto predA = modelA.predictBehavior(customLineupA, *strategy);
            res.standaloneMetricsA = predA.predictedMetrics;
            res.lineupA = predA.activeContributions;
            res.teamAFormation = "Personalizada (" + std::to_string(predA.selectedPlayerIds.size()) + " jug.)";
        } else {
            auto optA = modelA.findOptimalLineup(*strategy, formationA);
            res.standaloneMetricsA = optA.expectedMetrics;
            res.lineupA = optA.optimalStarters;
            res.teamAFormation = optA.formationName;
        }

        // Obtener rendimiento individual de B (con alineación personalizada o 11 óptimo)
        if (!customLineupB.empty()) {
            auto predB = modelB.predictBehavior(customLineupB, *strategy);
            res.standaloneMetricsB = predB.predictedMetrics;
            res.lineupB = predB.activeContributions;
            res.teamBFormation = "Personalizada (" + std::to_string(predB.selectedPlayerIds.size()) + " jug.)";
        } else {
            auto optB = modelB.findOptimalLineup(*strategy, formationB);
            res.standaloneMetricsB = optB.expectedMetrics;
            res.lineupB = optB.optimalStarters;
            res.teamBFormation = optB.formationName;
        }

        // Acoplamiento bilateral de vectores de rendimiento y_A y y_B con factor de liga
        const auto& yA = res.standaloneMetricsA;
        const auto& yB = res.standaloneMetricsB;
        domain::MatchMetrics cA = yA;
        domain::MatchMetrics cB = yB;

        double homeBoostA = (neutralVenue ? 1.0 : 1.05) * leagueBoostA;
        double awayFactorB = (neutralVenue ? 1.0 : 0.96) * leagueBoostB;

        // 1. Posesión normalizada al 100% entre ambos equipos
        double posWeightA = yA[domain::POSESION] * (neutralVenue ? 1.0 : 1.05) * std::pow(strA / strB, 0.25);
        double posWeightB = yB[domain::POSESION] * (neutralVenue ? 1.0 : 0.96) * std::pow(strB / strA, 0.25);
        double posSum = posWeightA + posWeightB;
        if (posSum > 1e-6) {
            cA[domain::POSESION] = 100.0 * posWeightA / posSum;
            cB[domain::POSESION] = 100.0 - cA[domain::POSESION];
        } else {
            cA[domain::POSESION] = 50.0;
            cB[domain::POSESION] = 50.0;
        }

        // 2. Pases escalados según la posesión acoplada
        double posRatioA = cA[domain::POSESION] / std::max(15.0, yA[domain::POSESION]);
        double posRatioB = cB[domain::POSESION] / std::max(15.0, yB[domain::POSESION]);
        cA[domain::PASES] = yA[domain::PASES] * (0.65 + 0.35 * posRatioA);
        cB[domain::PASES] = yB[domain::PASES] * (0.65 + 0.35 * posRatioB);

        // 3. Cruce de Tiros al Arco vs Tiros Rivales Permitidos
        double sotA = (0.55 * yA[domain::TIROS_AL_ARCO] * homeBoostA) +
                      (0.45 * yB[domain::TIROS_RIVAL_AL_ARCO] * leagueBoostA);
        double sotB = (0.55 * yB[domain::TIROS_AL_ARCO] * awayFactorB) +
                      (0.45 * yA[domain::TIROS_RIVAL_AL_ARCO] * leagueBoostB);
        cA[domain::TIROS_AL_ARCO] = std::max(0.5, sotA);
        cB[domain::TIROS_AL_ARCO] = std::max(0.5, sotB);

        cA[domain::TIROS_RIVAL_AL_ARCO] = cB[domain::TIROS_AL_ARCO];
        cB[domain::TIROS_RIVAL_AL_ARCO] = cA[domain::TIROS_AL_ARCO];

        // 4. Índice defensivo y cálculo de Goles Esperados (xG acoplado con Fuerza de Liga)
        double defIndexA = ((yA[domain::INTERCEPCIONES] + yA[domain::TACLES] + 0.4 * yA[domain::DESPEJES] + 0.25 * yA[domain::RECUPERACIONES]) / 38.0) * std::pow(strA / strB, 0.30);
        double defIndexB = ((yB[domain::INTERCEPCIONES] + yB[domain::TACLES] + 0.4 * yB[domain::DESPEJES] + 0.25 * yB[domain::RECUPERACIONES]) / 38.0) * std::pow(strB / strA, 0.30);
        defIndexA = std::clamp(defIndexA, 0.65, 1.45);
        defIndexB = std::clamp(defIndexB, 0.65, 1.45);

        double baseXgA = (0.50 * yA[domain::GOLES_A_FAVOR] + 0.50 * yB[domain::GOLES_EN_CONTRA]) * homeBoostA;
        double shotFactorA = (cA[domain::TIROS_AL_ARCO] / 4.0) * 0.30 + (yA[domain::TOQUES_AREA_RIVAL] / 12.0) * 0.15;
        double lambdaA = (0.65 * baseXgA + 0.35 * (baseXgA * (0.65 + shotFactorA))) / std::sqrt(defIndexB);

        double baseXgB = (0.50 * yB[domain::GOLES_A_FAVOR] + 0.50 * yA[domain::GOLES_EN_CONTRA]) * awayFactorB;
        double shotFactorB = (cB[domain::TIROS_AL_ARCO] / 4.0) * 0.30 + (yB[domain::TOQUES_AREA_RIVAL] / 12.0) * 0.15;
        double lambdaB = (0.65 * baseXgB + 0.35 * (baseXgB * (0.65 + shotFactorB))) / std::sqrt(defIndexA);

        lambdaA = std::clamp(lambdaA, 0.20, 4.50);
        lambdaB = std::clamp(lambdaB, 0.20, 4.50);

        cA[domain::GOLES_A_FAVOR] = lambdaA;
        cA[domain::GOLES_EN_CONTRA] = lambdaB;
        cB[domain::GOLES_A_FAVOR] = lambdaB;
        cB[domain::GOLES_EN_CONTRA] = lambdaA;

        cA[domain::SALVADAS] = std::max(0.0, cB[domain::TIROS_AL_ARCO] - lambdaB * 0.85);
        cB[domain::SALVADAS] = std::max(0.0, cA[domain::TIROS_AL_ARCO] - lambdaA * 0.85);

        res.coupledMetricsA = cA.clampedPhysical();
        res.coupledMetricsB = cB.clampedPhysical();
        res.expectedGoalsA = lambdaA;
        res.expectedGoalsB = lambdaB;
        res.tacticalEdgeScoreA = strategy->evaluate(res.coupledMetricsA);
        res.tacticalEdgeScoreB = strategy->evaluate(res.coupledMetricsB);

        // 5. Distribución conjunta de probabilidades de marcador (Poisson truncada en 0..7 goles)
        double pWinA = 0.0;
        double pDraw = 0.0;
        double pWinB = 0.0;
        std::vector<domain::ScoreProbability> matrixProbs;

        for (int gA = 0; gA <= 7; ++gA) {
            double pA = poissonPmf(gA, lambdaA);
            for (int gB = 0; gB <= 7; ++gB) {
                double pB = poissonPmf(gB, lambdaB);
                double pJoint = pA * pB;
                if (gA > gB) pWinA += pJoint;
                else if (gA == gB) pDraw += pJoint;
                else pWinB += pJoint;
                matrixProbs.push_back({gA, gB, pJoint * 100.0});
            }
        }

        double totalP = pWinA + pDraw + pWinB;
        if (totalP > 0.0) {
            res.winProbabilityA = (pWinA / totalP) * 100.0;
            res.drawProbability = (pDraw / totalP) * 100.0;
            res.winProbabilityB = (pWinB / totalP) * 100.0;
        }

        std::sort(matrixProbs.begin(), matrixProbs.end(),
                  [](const domain::ScoreProbability& a, const domain::ScoreProbability& b) {
                      return a.probability > b.probability;
                  });
        if (matrixProbs.size() > 6) {
            matrixProbs.resize(6);
        }
        res.topScorelines = std::move(matrixProbs);

        return res;
    }

    /**
     * @brief Simulador de Fichajes / Incorporación de Jugador Nuevo en un Club:
     *        Calcula el vector columna a_new in R^20 que tendría un jugador (proveniente de otro
     *        club/liga de la BD o un debutante/incorporación externa) al integrarse a la matriz
     *        A_E del club destino, y proyecta el impacto sobre las 20 métricas del 11 inicial.
     */
    domain::TransferSimulationResult simulateTransfer(
        int targetTeamId,
        int sourceTeamId = 0,
        int playerId = 0,
        int replacePlayerId = 0,
        const std::string& strategyCode = "BALANCED",
        const std::string& formation = "AUTO",
        const std::string& customPlayerName = "Nuevo Jugador",
        const std::string& customPositionCode = "FWD",
        double customPriorRating = 7.00,
        double customSourceLeagueStrength = 1.00
    ) const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        auto targetIt = modelsByTeamId_.find(targetTeamId);
        if (targetIt == modelsByTeamId_.end()) {
            throw std::invalid_argument("Club destino no encontrado con ID: " + std::to_string(targetTeamId));
        }

        const auto& targetModel = targetIt->second;
        auto strategy = domain::TacticalStrategyFactory::create(strategyCode);

        // 1. Obtener el 11 óptimo actual del club destino (línea base antes de la incorporación)
        auto baseOpt = targetModel.findOptimalLineup(*strategy, formation);

        domain::TransferSimulationResult res;
        res.targetTeamId = targetModel.team().teamId;
        res.targetTeamName = targetModel.team().name;
        res.targetCompetitionName = targetModel.team().competitionName;
        res.targetLeagueStrength = targetModel.team().strengthFactor;
        res.strategyName = strategy->name();
        res.formationName = baseOpt.formationName;
        res.baselineMetrics = baseOpt.expectedMetrics;
        res.baselineTacticalScore = baseOpt.totalTacticalScore;

        domain::Player simPlayer;
        std::vector<double> sourceCol;
        std::vector<double> sourcePosPrior;
        double srcStrength = customSourceLeagueStrength;

        if (sourceTeamId > 0 && playerId > 0) {
            auto srcIt = modelsByTeamId_.find(sourceTeamId);
            if (srcIt == modelsByTeamId_.end()) {
                throw std::invalid_argument("Club de origen no encontrado con ID: " + std::to_string(sourceTeamId));
            }
            const auto& srcModel = srcIt->second;
            res.sourceTeamId = srcModel.team().teamId;
            res.sourceTeamName = srcModel.team().name;
            res.sourceCompetitionName = srcModel.team().competitionName;
            res.sourceLeagueStrength = srcModel.team().strengthFactor;
            srcStrength = srcModel.team().strengthFactor;

            bool found = false;
            for (const auto& sp : srcModel.team().squad) {
                if (sp.playerId == playerId) {
                    simPlayer = sp;
                    found = true;
                    break;
                }
            }
            if (!found) {
                throw std::invalid_argument(
                    "El jugador ID " + std::to_string(playerId) + " no pertenece al club de origen " + srcModel.team().name
                );
            }

            sourceCol = srcModel.getPlayerColumn(playerId);
            sourcePosPrior = srcModel.getPositionalPrior(simPlayer.positionCode);

            simPlayer.previousTeamId = srcModel.team().teamId;
            simPlayer.previousTeamName = srcModel.team().name;
            simPlayer.previousMatchesPlayed = std::max(3, simPlayer.matchesPlayed);
            simPlayer.previousAverageRating = simPlayer.averageRating;
            simPlayer.previousLeagueStrength = srcStrength;
            simPlayer.teamId = targetModel.team().teamId;
            simPlayer.matchesPlayed = 0;
            simPlayer.convocationsLast20 = 0;
            simPlayer.totalMinutes = 0;
            simPlayer.currentlyInClub = true;
            simPlayer.isNewSigning = true;
            simPlayer.originType = "SIMULATED_SIGNING";
            simPlayer.adaptationAlpha = 0.0;
        } else {
            // Jugador nuevo externo / debutante sin club origen en la BD
            res.sourceTeamId = 0;
            res.sourceTeamName = "Incorporación Externa / Inferiores";
            res.sourceCompetitionName = "Prior Posicional del Club";
            res.sourceLeagueStrength = srcStrength;

            std::string posCode = customPositionCode;
            if (posCode != "GK" && posCode != "DEF" && posCode != "MID" && posCode != "FWD") {
                posCode = "FWD";
            }
            std::string posFull = (posCode == "GK") ? "Portero"
                                : (posCode == "DEF") ? "Defensor"
                                : (posCode == "MID") ? "Mediocampista" : "Delantero";

            simPlayer.playerId = 999001;
            simPlayer.teamId = targetModel.team().teamId;
            simPlayer.name = customPlayerName.empty() ? "Nuevo Jugador" : customPlayerName;
            simPlayer.shortName = simPlayer.name;
            simPlayer.position = posFull;
            simPlayer.positionCode = posCode;
            simPlayer.jerseyNumber = "99";
            simPlayer.convocationsLast20 = 0;
            simPlayer.matchesPlayed = 0;
            simPlayer.totalMinutes = 0;
            simPlayer.averageRating = customPriorRating;
            simPlayer.currentlyInClub = true;
            simPlayer.isNewSigning = true;
            simPlayer.originType = "YOUTH_OR_EXTERNAL";
            simPlayer.previousLeagueStrength = srcStrength;
            simPlayer.previousAverageRating = customPriorRating;
            simPlayer.adaptationAlpha = 0.0;
        }

        // 2. Sintetizar el vector columna a_new in R^20 en el espacio del club destino
        res.positionalPriorVector = targetModel.getPositionalPrior(simPlayer.positionCode);
        res.sourceDifferentialVec.assign(domain::NUM_METRICS, 0.0);
        if (!sourceCol.empty() && sourcePosPrior.size() == domain::NUM_METRICS) {
            for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
                res.sourceDifferentialVec[i] = sourceCol[i] - sourcePosPrior[i];
            }
        }

        res.injectedColumnVector = targetModel.synthesizeNewPlayerColumn(
            simPlayer.positionCode,
            simPlayer.averageRating,
            srcStrength,
            sourceCol,
            sourcePosPrior
        );

        res.leagueScalingRatio = std::pow(
            std::max(0.50, srcStrength) / std::max(0.50, res.targetLeagueStrength),
            0.65
        );
        res.simulatedPlayer = simPlayer;
        res.newPlayerTacticalScore = targetModel.evaluatePlayerColumnScore(
            simPlayer, res.injectedColumnVector, *strategy
        );

        // 3. Determinar a qué titular reemplaza en el 11 (por ID explícito o el de menor score de su misma línea)
        std::size_t replaceIdxInStarters = 0;
        bool foundReplace = false;
        if (replacePlayerId > 0) {
            for (std::size_t idx = 0; idx < baseOpt.optimalStarters.size(); ++idx) {
                if (baseOpt.optimalStarters[idx].player.playerId == replacePlayerId) {
                    replaceIdxInStarters = idx;
                    foundReplace = true;
                    break;
                }
            }
        }
        if (!foundReplace) {
            double minPosScore = 1e18;
            for (std::size_t idx = 0; idx < baseOpt.optimalStarters.size(); ++idx) {
                const auto& st = baseOpt.optimalStarters[idx];
                if (st.player.positionCode == simPlayer.positionCode && st.tacticalScore < minPosScore) {
                    minPosScore = st.tacticalScore;
                    replaceIdxInStarters = idx;
                    foundReplace = true;
                }
            }
        }
        if (!foundReplace && !baseOpt.optimalStarters.empty()) {
            replaceIdxInStarters = baseOpt.optimalStarters.size() - 1;
        }

        // 4. Calcular el nuevo comportamiento del 11 inicial:
        //    y_post = y_base_raw - a_replaced + a_new
        res.postTransferStarters = baseOpt.optimalStarters;
        std::vector<double> sumPostVec(domain::NUM_METRICS, 0.0);

        for (std::size_t idx = 0; idx < res.postTransferStarters.size(); ++idx) {
            if (idx == replaceIdxInStarters) {
                res.replacedPlayerId = res.postTransferStarters[idx].player.playerId;
                res.replacedPlayerName = res.postTransferStarters[idx].player.name;
                res.replacedPlayerTacticalScore = res.postTransferStarters[idx].tacticalScore;

                domain::PlayerContribution newPc;
                newPc.player = simPlayer;
                newPc.inputBit = 1;
                newPc.columnVector = res.injectedColumnVector;
                newPc.tacticalScore = res.newPlayerTacticalScore;
                res.postTransferStarters[idx] = newPc;
            }
            for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
                sumPostVec[i] += res.postTransferStarters[idx].columnVector[i];
            }
        }

        res.postTransferMetrics = domain::MatchMetrics(sumPostVec).clampedPhysical();
        res.postTransferTacticalScore = strategy->evaluate(res.postTransferMetrics);
        res.tacticalScoreDelta = res.postTransferTacticalScore - res.baselineTacticalScore;
        if (std::abs(res.baselineTacticalScore) > 1e-6) {
            res.improvementPercentage = (res.tacticalScoreDelta / std::abs(res.baselineTacticalScore)) * 100.0;
        }

        return res;
    }
};

} // namespace lpf::services

#endif // ALGEBRA_LINEAL_ANALYTICS_ENGINE_SERVICE_HPP
