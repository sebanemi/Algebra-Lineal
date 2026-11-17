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

namespace lpf::services {

/**
 * @class AnalyticsEngineService
 * @brief Capa de Servicio de Dominio (Service Layer - Pressman / Ghezzi).
 *        Gestiona de forma concurrente (thread-safe mediante std::shared_mutex)
 *        las Transformaciones Lineales de todos los equipos y resuelve los casos
 *        de uso (a), (b) y (c).
 */
class AnalyticsEngineService {
private:
    std::shared_ptr<repository::IDataRepository> repository_;
    mutable std::shared_mutex mutex_;
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
     * @brief Recarga desde la base de datos SQLite todos los equipos y recalcula
     *        sus matrices de transformación lineal A_E in R^(20 x n).
     *        Adquiere un bloqueo exclusivo (unique_lock).
     */
    void reloadModels() {
        auto teams = repository_->loadAllTeams();
        std::unordered_map<int, domain::TeamTransformationModel> newModels;
        std::vector<int> ids;
        ids.reserve(teams.size());

        for (const auto& t : teams) {
            newModels.emplace(t.teamId, domain::TeamTransformationModel(t, ridgeLambda_));
            ids.push_back(t.teamId);
        }

        std::unique_lock<std::shared_mutex> writeLock(mutex_);
        modelsByTeamId_ = std::move(newModels);
        sortedTeamIds_ = std::move(ids);
    }

    std::size_t teamCount() const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        return modelsByTeamId_.size();
    }

    std::string repositoryInfo() const {
        return repository_->connectionInfo();
    }

    std::vector<domain::TeamTransformationModel> getAllTeamModels() const {
        std::shared_lock<std::shared_mutex> readLock(mutex_);
        std::vector<domain::TeamTransformationModel> out;
        out.reserve(sortedTeamIds_.size());
        for (int id : sortedTeamIds_) {
            auto it = modelsByTeamId_.find(id);
            if (it != modelsByTeamId_.end()) {
                out.push_back(it->second);
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
     * @brief Caso de Uso (c): Cálculos y predicciones entre dos equipos (Equipo A vs Equipo B)
     *        en base a los rendimientos proyectados por las transformaciones lineales de cada uno.
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
        res.teamBId = modelB.team().teamId;
        res.teamBName = modelB.team().name;

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

        // Acoplamiento bilateral de vectores de rendimiento y_A y y_B
        const auto& yA = res.standaloneMetricsA;
        const auto& yB = res.standaloneMetricsB;
        domain::MatchMetrics cA = yA;
        domain::MatchMetrics cB = yB;

        double homeBoostA = neutralVenue ? 1.0 : 1.05;
        double awayFactorB = neutralVenue ? 1.0 : 0.96;

        // 1. Posesión normalizada al 100% entre ambos equipos
        double posSum = (yA[domain::POSESION] * homeBoostA) + (yB[domain::POSESION] * awayFactorB);
        if (posSum > 1e-6) {
            cA[domain::POSESION] = 100.0 * (yA[domain::POSESION] * homeBoostA) / posSum;
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
                      (0.45 * yB[domain::TIROS_RIVAL_AL_ARCO]);
        double sotB = (0.55 * yB[domain::TIROS_AL_ARCO] * awayFactorB) +
                      (0.45 * yA[domain::TIROS_RIVAL_AL_ARCO]);
        cA[domain::TIROS_AL_ARCO] = std::max(0.5, sotA);
        cB[domain::TIROS_AL_ARCO] = std::max(0.5, sotB);

        // Reciprocidad: los tiros del rival al arco propio de A son los tiros al arco de B
        cA[domain::TIROS_RIVAL_AL_ARCO] = cB[domain::TIROS_AL_ARCO];
        cB[domain::TIROS_RIVAL_AL_ARCO] = cA[domain::TIROS_AL_ARCO];

        // 4. Índice defensivo y cálculo de Goles Esperados (xG acoplado)
        double defIndexA = (yA[domain::INTERCEPCIONES] + yA[domain::TACLES] + 0.4 * yA[domain::DESPEJES] + 0.25 * yA[domain::RECUPERACIONES]) / 38.0;
        double defIndexB = (yB[domain::INTERCEPCIONES] + yB[domain::TACLES] + 0.4 * yB[domain::DESPEJES] + 0.25 * yB[domain::RECUPERACIONES]) / 38.0;
        defIndexA = std::clamp(defIndexA, 0.70, 1.35);
        defIndexB = std::clamp(defIndexB, 0.70, 1.35);

        double baseXgA = (0.50 * yA[domain::GOLES_A_FAVOR] + 0.50 * yB[domain::GOLES_EN_CONTRA]) * homeBoostA;
        double shotFactorA = (cA[domain::TIROS_AL_ARCO] / 4.0) * 0.30 + (yA[domain::TOQUES_AREA_RIVAL] / 12.0) * 0.15;
        double lambdaA = (0.65 * baseXgA + 0.35 * (baseXgA * (0.65 + shotFactorA))) / std::sqrt(defIndexB);

        double baseXgB = (0.50 * yB[domain::GOLES_A_FAVOR] + 0.50 * yA[domain::GOLES_EN_CONTRA]) * awayFactorB;
        double shotFactorB = (cB[domain::TIROS_AL_ARCO] / 4.0) * 0.30 + (yB[domain::TOQUES_AREA_RIVAL] / 12.0) * 0.15;
        double lambdaB = (0.65 * baseXgB + 0.35 * (baseXgB * (0.65 + shotFactorB))) / std::sqrt(defIndexA);

        lambdaA = std::clamp(lambdaA, 0.20, 4.20);
        lambdaB = std::clamp(lambdaB, 0.20, 4.20);

        cA[domain::GOLES_A_FAVOR] = lambdaA;
        cA[domain::GOLES_EN_CONTRA] = lambdaB;
        cB[domain::GOLES_A_FAVOR] = lambdaB;
        cB[domain::GOLES_EN_CONTRA] = lambdaA;

        // Ajustar salvadas del arquero coherentemente con los tiros al arco rivales y goles recibidos
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
};

} // namespace lpf::services

#endif // ALGEBRA_LINEAL_ANALYTICS_ENGINE_SERVICE_HPP
