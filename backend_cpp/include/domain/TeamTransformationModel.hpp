#ifndef ALGEBRA_LINEAL_TEAM_TRANSFORMATION_MODEL_HPP
#define ALGEBRA_LINEAL_TEAM_TRANSFORMATION_MODEL_HPP

#include "math/Matrix.hpp"
#include "domain/Models.hpp"
#include "domain/ITacticalStrategy.hpp"
#include "domain/TacticalStrategies.hpp"
#include <unordered_map>
#include <numeric>
#include <algorithm>
#include <cmath>

namespace lpf::domain {

/**
 * @class TeamTransformationModel
 * @brief Modela la Transformación Lineal T_E: R^n -> R^20 asociada a un equipo E.
 *
 * Sea n la cantidad de jugadores registrados del equipo y m la cantidad de partidos (m=20).
 * - Matriz de alineaciones históricas: X in {0, 1}^(n x m), donde X(j, k) = 1 si el jugador j
 *   jugó el partido k (titular o suplente que ingresó) y 0 en caso contrario.
 * - Matriz de métricas observadas: Y in R^(20 x m).
 * - Matriz de la transformación lineal: A_E in R^(20 x n), obtenida mediante Pseudoinversa de
 *   Moore-Penrose regularizada (Tikhonov-Ridge) alrededor de la base canónica del plantel:
 *     A_E = A_0 + (Y - A_0 * X_norm) * (X_norm)^+
 *
 * Para cualquier vector de alineación inicial x in {0, 1}^n (con ||x||_1 = 11 titulares):
 *     y_hat = T_E(x) = A_E * x  in R^20
 */
class TeamTransformationModel {
private:
    Team team_;
    std::unordered_map<int, std::size_t> playerIdToIndex_;
    math::Matrix binaryMatrixX_;       // X in {0, 1}^(n x m)
    math::Matrix normalizedMatrixX_;   // X_norm in R^(n x m), columnas suman 11.0
    math::Matrix metricsMatrixY_;      // Y in R^(20 x m)
    math::Matrix transformationMatrix_;// A_E in R^(20 x n)
    MatchMetrics historicalAverage_;   // Promedio histórico en R^20
    double frobeniusResidual_ = 0.0;
    double rSquared_ = 0.0;

public:
    TeamTransformationModel() = default;

    explicit TeamTransformationModel(const Team& team, double ridgeLambda = 1.35)
        : team_(team) {
        buildModel(ridgeLambda);
    }

    const Team& team() const noexcept { return team_; }
    const math::Matrix& transformationMatrix() const noexcept { return transformationMatrix_; }
    const math::Matrix& binaryMatrixX() const noexcept { return binaryMatrixX_; }
    const math::Matrix& metricsMatrixY() const noexcept { return metricsMatrixY_; }
    const MatchMetrics& historicalAverage() const noexcept { return historicalAverage_; }
    double frobeniusResidual() const noexcept { return frobeniusResidual_; }
    double rSquared() const noexcept { return rSquared_; }

    /**
     * @brief Caso de Uso (a): Dado un conjunto de jugadores seleccionados (equipo inicial),
     *        construye el vector x in {0, 1}^n y aplica la transformación lineal y_hat = A_E * x.
     */
    BehaviorPredictionResult predictBehavior(
        const std::vector<int>& selectedPlayerIds,
        const ITacticalStrategy& strategy = BalancedStrategy()
    ) const {
        const std::size_t n = team_.squad.size();
        std::vector<int> binaryVec(n, 0);
        std::vector<double> inputVec(n, 0.0);
        std::vector<int> validSelectedIds;

        for (int pid : selectedPlayerIds) {
            auto it = playerIdToIndex_.find(pid);
            if (it != playerIdToIndex_.end() && binaryVec[it->second] == 0) {
                const Player& p = team_.squad[it->second];
                if (!p.currentlyInClub) {
                    throw std::invalid_argument(
                        "El jugador '" + p.name + "' (ID " + std::to_string(p.playerId) +
                        ") ya no pertenece actualmente al plantel de " + team_.name + " y no es elegible."
                    );
                }
                binaryVec[it->second] = 1;
                inputVec[it->second] = 1.0;
                validSelectedIds.push_back(pid);
            }
        }

        // Si el usuario ingresó K jugadores (ej. K=11 titulares, o K>11 incluyendo suplentes),
        // normalizamos la energía de entrada a 11 unidades activas (para K=11 el factor es 1.0 exacto).
        double activeSum = std::accumulate(inputVec.begin(), inputVec.end(), 0.0);
        double scaleFactor = (activeSum > 0.0) ? (11.0 / activeSum) : 1.0;
        std::vector<double> scaledInput(n, 0.0);
        for (std::size_t j = 0; j < n; ++j) {
            scaledInput[j] = inputVec[j] * scaleFactor;
        }

        // Aplicación directa de la Transformación Lineal: y_raw = A_E * x
        std::vector<double> rawY = transformationMatrix_.multiplyVector(scaledInput);
        MatchMetrics rawMetrics(rawY);
        MatchMetrics clampedMetrics = rawMetrics.clampedPhysical();

        BehaviorPredictionResult result;
        result.teamId = team_.teamId;
        result.teamName = team_.name;
        result.selectedPlayerIds = validSelectedIds;
        result.binaryAlignmentVector = binaryVec;
        result.rawLinearOutput = rawMetrics;
        result.predictedMetrics = clampedMetrics;
        result.historicalAverage = historicalAverage_;
        result.tacticalScore = strategy.evaluate(clampedMetrics);
        result.modelFitR2 = rSquared_;
        result.frobeniusResidual = frobeniusResidual_;

        for (std::size_t j = 0; j < n; ++j) {
            if (binaryVec[j] == 1) {
                PlayerContribution pc;
                pc.player = team_.squad[j];
                pc.inputBit = 1;
                pc.columnVector = transformationMatrix_.getColumn(j);
                pc.tacticalScore = computeEffectivePlayerScore(j, strategy);
                result.activeContributions.push_back(pc);
            }
        }

        return result;
    }

    /**
     * @brief Caso de Uso (b): Obtiene el equipo inicial (11 jugadores) que maximiza el
     *        rendimiento esperado según la transformación lineal A_E y la estrategia táctica.
     */
    OptimalLineupResult findOptimalLineup(
        const ITacticalStrategy& strategy = BalancedStrategy(),
        const std::string& requestedFormation = "AUTO"
    ) const {
        std::vector<Formation> candidates;
        if (requestedFormation.empty() || requestedFormation == "AUTO") {
            candidates = Formation::standardFormations();
        } else {
            for (const auto& f : Formation::standardFormations()) {
                if (f.name == requestedFormation) {
                    candidates.push_back(f);
                    break;
                }
            }
            if (candidates.empty()) {
                candidates = Formation::standardFormations();
            }
        }

        const std::size_t n = team_.squad.size();
        std::vector<PlayerContribution> allContributions(n);
        for (std::size_t j = 0; j < n; ++j) {
            allContributions[j].player = team_.squad[j];
            allContributions[j].inputBit = 0;
            allContributions[j].columnVector = transformationMatrix_.getColumn(j);
            allContributions[j].tacticalScore = computeEffectivePlayerScore(j, strategy);
        }

        // Separar candidatos ELEGIBLES (que pertenecen actualmente al club) por línea posicional
        // y ordenar de mayor a menor aporte lineal s_j.
        std::vector<std::size_t> gks, defs, mids, fwds;
        for (std::size_t j = 0; j < n; ++j) {
            if (!team_.squad[j].currentlyInClub) {
                continue; // Jugador transferido / dado de baja: no elegible (x_j = 0 fijo)
            }
            const std::string& pos = team_.squad[j].positionCode;
            if (pos == "GK") gks.push_back(j);
            else if (pos == "DEF") defs.push_back(j);
            else if (pos == "FWD") fwds.push_back(j);
            else mids.push_back(j);
        }

        auto sortByScoreDesc = [&](std::size_t a, std::size_t b) {
            return allContributions[a].tacticalScore > allContributions[b].tacticalScore;
        };
        std::sort(gks.begin(), gks.end(), sortByScoreDesc);
        std::sort(defs.begin(), defs.end(), sortByScoreDesc);
        std::sort(mids.begin(), mids.end(), sortByScoreDesc);
        std::sort(fwds.begin(), fwds.end(), sortByScoreDesc);

        OptimalLineupResult bestResult;
        double bestFormationScore = -1e18;

        for (const auto& form : candidates) {
            if (gks.empty() ||
                static_cast<int>(defs.size()) < form.defenders ||
                static_cast<int>(mids.size()) < form.midfielders ||
                static_cast<int>(fwds.size()) < form.forwards) {
                continue;
            }

            std::vector<std::size_t> chosenIndices;
            chosenIndices.push_back(gks[0]);
            for (int i = 0; i < form.defenders; ++i) chosenIndices.push_back(defs[i]);
            for (int i = 0; i < form.midfielders; ++i) chosenIndices.push_back(mids[i]);
            for (int i = 0; i < form.forwards; ++i) chosenIndices.push_back(fwds[i]);

            std::vector<int> chosenPids;
            double sumPlayerScores = 0.0;
            for (std::size_t idx : chosenIndices) {
                chosenPids.push_back(team_.squad[idx].playerId);
                sumPlayerScores += allContributions[idx].tacticalScore;
            }

            BehaviorPredictionResult pred = predictBehavior(chosenPids, strategy);
            double combinedObjective = sumPlayerScores + 0.35 * pred.tacticalScore;

            if (combinedObjective > bestFormationScore) {
                bestFormationScore = combinedObjective;

                OptimalLineupResult res;
                res.teamId = team_.teamId;
                res.teamName = team_.name;
                res.strategyName = strategy.name();
                res.formationName = form.name;
                res.optimalBinaryVector = pred.binaryAlignmentVector;
                res.expectedMetrics = pred.predictedMetrics;
                res.historicalAverage = historicalAverage_;
                res.totalTacticalScore = pred.tacticalScore;

                double histScore = strategy.evaluate(historicalAverage_);
                if (std::abs(histScore) > 1e-6) {
                    res.improvementPercentage = ((pred.tacticalScore - histScore) / std::abs(histScore)) * 100.0;
                }

                for (std::size_t idx : chosenIndices) {
                    PlayerContribution pc = allContributions[idx];
                    pc.inputBit = 1;
                    res.optimalStarters.push_back(pc);
                }

                // Seleccionar los mejores 7 suplentes (1 arquero suplente + 6 de campo)
                std::vector<std::size_t> remainingField;
                if (gks.size() > 1) {
                    PlayerContribution gkSub = allContributions[gks[1]];
                    gkSub.inputBit = 0;
                    res.recommendedBench.push_back(gkSub);
                }
                for (std::size_t j = 0; j < n; ++j) {
                    if (team_.squad[j].currentlyInClub &&
                        res.optimalBinaryVector[j] == 0 &&
                        (gks.size() <= 1 || j != gks[1]) &&
                        team_.squad[j].positionCode != "GK") {
                        remainingField.push_back(j);
                    }
                }
                std::sort(remainingField.begin(), remainingField.end(), sortByScoreDesc);
                for (std::size_t k = 0; k < std::min<std::size_t>(6, remainingField.size()); ++k) {
                    PlayerContribution subPc = allContributions[remainingField[k]];
                    subPc.inputBit = 0;
                    res.recommendedBench.push_back(subPc);
                }

                bestResult = res;
            }
        }

        return bestResult;
    }

    /**
     * @brief Obtiene todas las columnas de A_E junto con la información de cada jugador.
     */
    std::vector<PlayerContribution> getAllPlayerContributions(
        const ITacticalStrategy& strategy = BalancedStrategy()
    ) const {
        const std::size_t n = team_.squad.size();
        std::vector<PlayerContribution> list(n);
        for (std::size_t j = 0; j < n; ++j) {
            list[j].player = team_.squad[j];
            list[j].inputBit = 0;
            list[j].columnVector = transformationMatrix_.getColumn(j);
            list[j].tacticalScore = computeEffectivePlayerScore(j, strategy);
        }
        return list;
    }

private:
    double computeEffectivePlayerScore(std::size_t playerIdx, const ITacticalStrategy& strategy) const {
        std::vector<double> col = transformationMatrix_.getColumn(playerIdx);
        double rawLinearScore = strategy.evaluateVector(col);

        const Player& p = team_.squad[playerIdx];
        // Factor de regularización empírica según continuidad (partidos jugados y minutos)
        // para garantizar que el 11 óptimo priorice jugadores titulares con rodaje comprobado.
        double matchesFactor = static_cast<double>(p.matchesPlayed) / 20.0;
        double minutesFactor = std::min(1.0, static_cast<double>(p.totalMinutes) / 1100.0);
        double ratingBonus = (p.averageRating - 6.65) * 4.5;
        double continuityWeight = 0.35 + 0.35 * matchesFactor + 0.30 * minutesFactor;

        if (p.matchesPlayed < 3) {
            return rawLinearScore * 0.25 - 6.0;
        }
        return rawLinearScore * continuityWeight + ratingBonus;
    }

    void buildModel(double ridgeLambda) {
        const std::size_t n = team_.squad.size();
        const std::size_t m = team_.matches.size();

        playerIdToIndex_.clear();
        for (std::size_t j = 0; j < n; ++j) {
            playerIdToIndex_[team_.squad[j].playerId] = j;
        }

        if (n == 0 || m == 0) {
            transformationMatrix_ = math::Matrix(NUM_METRICS, n, 0.0);
            return;
        }

        binaryMatrixX_ = math::Matrix(n, m, 0.0);
        normalizedMatrixX_ = math::Matrix(n, m, 0.0);
        metricsMatrixY_ = math::Matrix(NUM_METRICS, m, 0.0);

        std::array<double, NUM_METRICS> sumMetrics{};
        sumMetrics.fill(0.0);

        for (std::size_t k = 0; k < m; ++k) {
            const auto& match = team_.matches[k];
            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                double val = match.metrics[i];
                metricsMatrixY_(i, k) = val;
                sumMetrics[i] += val;
            }

            double activePlayersInMatch = 0.0;
            for (const auto& slot : match.lineup) {
                if (slot.played == 1) {
                    auto it = playerIdToIndex_.find(slot.playerId);
                    if (it != playerIdToIndex_.end()) {
                        binaryMatrixX_(it->second, k) = 1.0;
                        activePlayersInMatch += 1.0;
                    }
                }
            }

            double normFactor = (activePlayersInMatch > 0.0) ? (11.0 / activePlayersInMatch) : 1.0;
            for (std::size_t j = 0; j < n; ++j) {
                normalizedMatrixX_(j, k) = binaryMatrixX_(j, k) * normFactor;
            }
        }

        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            historicalAverage_[i] = sumMetrics[i] / static_cast<double>(m);
        }

        // Calcular valoración media del plantel para desambiguar jugadores colineales
        double sumRatings = 0.0;
        int ratedCount = 0;
        for (const auto& p : team_.squad) {
            if (p.matchesPlayed > 0 && p.averageRating > 0.0) {
                sumRatings += p.averageRating;
                ratedCount++;
            }
        }
        double meanSquadRating = (ratedCount > 0) ? (sumRatings / ratedCount) : 6.75;

        // 1. Construir matriz base canónica A_0 in R^(20 x n)
        math::Matrix A0(NUM_METRICS, n, 0.0);
        for (std::size_t j = 0; j < n; ++j) {
            const Player& p = team_.squad[j];
            double reliability = static_cast<double>(p.matchesPlayed) / (p.matchesPlayed + 4.0);
            double ratingDiff = (p.matchesPlayed > 0) ? (p.averageRating - meanSquadRating) : -0.15;
            double modFactor = 1.0 + 0.12 * reliability * ratingDiff;

            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                double baseShare = historicalAverage_[i] / 11.0;
                if (i == PROMEDIO_VALORACION) {
                    double playerRat = (p.matchesPlayed > 0 && p.averageRating > 0.0)
                                       ? p.averageRating
                                       : historicalAverage_[PROMEDIO_VALORACION];
                    A0(i, j) = (0.65 * baseShare) + (0.35 * (playerRat / 11.0));
                } else if (getMetricsCatalog()[i].higherIsBetter) {
                    A0(i, j) = baseShare * modFactor;
                } else {
                    A0(i, j) = baseShare * (2.0 - modFactor);
                }
            }
        }

        // 2. Calcular residuo histórico Delta_Y = Y - A_0 * X_norm
        math::Matrix Y0 = A0 * normalizedMatrixX_;
        math::Matrix deltaY = metricsMatrixY_ - Y0;

        // 3. Resolver transformación diferencial por Pseudoinversa de Moore-Penrose:
        //    Delta_A = Delta_Y * (X_norm)^+ con estabilización de James-Stein por muestra
        math::Matrix X_pinv = normalizedMatrixX_.pseudoInverse(ridgeLambda);
        math::Matrix deltaA = deltaY * X_pinv;
        for (std::size_t j = 0; j < n; ++j) {
            double pj = static_cast<double>(team_.squad[j].matchesPlayed);
            double shrink = (pj / (pj + 5.0)) * 0.55;
            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                double maxDelta = std::max(0.25, std::abs(historicalAverage_[i]) * 0.065);
                double val = std::clamp(deltaA(i, j) * shrink, -maxDelta, maxDelta);
                deltaA(i, j) = val;
            }
        }

        // 4. Matriz final de la Transformación Lineal del Equipo: A_E = A_0 + Delta_A
        transformationMatrix_ = A0 + deltaA;

        // 5. Métricas de bondad de ajuste matricial (Norma de Frobenius y R^2)
        math::Matrix Y_hat = transformationMatrix_ * normalizedMatrixX_;
        math::Matrix residualMat = metricsMatrixY_ - Y_hat;
        frobeniusResidual_ = residualMat.frobeniusNorm();

        double ssRes = frobeniusResidual_ * frobeniusResidual_;
        double ssTot = 0.0;
        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            for (std::size_t k = 0; k < m; ++k) {
                double diff = metricsMatrixY_(i, k) - historicalAverage_[i];
                ssTot += diff * diff;
            }
        }
        rSquared_ = (ssTot > 1e-9) ? std::clamp(1.0 - (ssRes / ssTot), 0.0, 1.0) : 1.0;
    }
};

} // namespace lpf::domain

#endif // ALGEBRA_LINEAL_TEAM_TRANSFORMATION_MODEL_HPP
