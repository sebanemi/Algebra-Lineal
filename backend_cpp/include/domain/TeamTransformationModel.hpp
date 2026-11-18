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
 * @brief Modela la Transformación Lineal T_E: R^n -> R^20 asociada a un equipo E
 *        con resolución híbrida para Jugadores Nuevos en el Club (Cold-Start & Cross-Club Transfer).
 *
 * Sea n la cantidad de jugadores registrados del equipo y m la cantidad de partidos (m=20).
 * - Matriz de alineaciones históricas: X in {0, 1}^(n x m), donde X(j, k) = 1 si el jugador j
 *   jugó el partido k (titular o suplente que ingresó) y 0 en caso contrario.
 * - Matriz de métricas observadas: Y in R^(20 x m).
 * - Matriz de la transformación lineal: A_E in R^(20 x n), obtenida mediante Pseudoinversa de
 *   Moore-Penrose regularizada (Tikhonov-Ridge) alrededor de la base canónica del plantel:
 *     A_emp = A_0 + (Y - A_0 * X_norm) * (X_norm)^+
 *
 * Resolución del Problema del Jugador Nuevo en el Club (matchesPlayed < 3 o 0 PJ):
 * Sea alpha_j = PJ_j / (PJ_j + 3.0) in [0, 1] el factor de credibilidad empírica en el club actual.
 * - Caso 1 (TRANSFERRED): Si el jugador proviene de otro club registrado en la BD Multi-Liga,
 *   se proyecta su diferencial de rendimiento relativo del club de origen ajustado por el
 *   Coeficiente de Fuerza de Liga rho = (lambda_orig / lambda_dest)^0.65 sobre el subespacio
 *   posicional a_bar_P del nuevo club.
 * - Caso 2 (YOUTH_OR_EXTERNAL): Si es un debutante o incorporación externa sin club previo en BD,
 *   se inicializa su vector columna a partir del Prior Posicional del club a_bar_P (GK/DEF/MID/FWD)
 *   ponderado por su valoración previa y regularización bayesiana (James-Stein shrinkage).
 * - Combinación convexa progresiva conforme suma partidos en el nuevo club:
 *     A_E(., j) = (1 - alpha_j) * a_j^prior + alpha_j * A_emp(., j)
 */
class TeamTransformationModel {
private:
    Team team_;
    std::unordered_map<int, std::size_t> playerIdToIndex_;
    math::Matrix binaryMatrixX_;       // X in {0, 1}^(n x m)
    math::Matrix normalizedMatrixX_;   // X_norm in R^(n x m), columnas suman 11.0
    math::Matrix metricsMatrixY_;      // Y in R^(20 x m)
    math::Matrix empiricalMatrixA_;    // A_emp = A_0 + Delta_A in R^(20 x n)
    math::Matrix transformationMatrix_;// A_E final con resolución Cold-Start in R^(20 x n)
    MatchMetrics historicalAverage_;   // Promedio histórico en R^20
    std::unordered_map<std::string, std::vector<double>> positionalPriors_;         // GK, DEF, MID, FWD -> R^20
    std::unordered_map<std::string, std::vector<double>> specificPositionalPriors_; // POR, DFC, LI, LD, MCD, MC, MCO, MI, MD, DC, EI, ED, SD -> R^20
    double meanSquadRating_ = 6.75;
    double frobeniusResidual_ = 0.0;
    double rSquared_ = 0.0;

    static double subPositionMetricMultiplier(const std::string& specCode, std::size_t metricIdx) {
        if (specCode == "LI" || specCode == "LD") {
            if (metricIdx == CORNERS || metricIdx == PASES || metricIdx == TOQUES_AREA_RIVAL || metricIdx == RECUPERACIONES) return 1.04;
            if (metricIdx == DESPEJES) return 0.96;
        } else if (specCode == "DFC") {
            if (metricIdx == DESPEJES || metricIdx == TACLES || metricIdx == INTERCEPCIONES) return 1.05;
            if (metricIdx == GOLES_EN_CONTRA || metricIdx == TIROS_RIVAL_AL_ARCO) return 0.96;
        } else if (specCode == "MCD") {
            if (metricIdx == TACLES || metricIdx == INTERCEPCIONES || metricIdx == RECUPERACIONES || metricIdx == PASES) return 1.05;
            if (metricIdx == GOLES_EN_CONTRA || metricIdx == TIROS_RIVAL_AL_ARCO) return 0.96;
        } else if (specCode == "MC") {
            if (metricIdx == PASES || metricIdx == POSESION || metricIdx == RECUPERACIONES) return 1.04;
        } else if (specCode == "MCO") {
            if (metricIdx == GOLES_A_FAVOR || metricIdx == TIROS_AL_ARCO || metricIdx == TOQUES_AREA_RIVAL || metricIdx == TIROS_LIBRES || metricIdx == PASES) return 1.05;
        } else if (specCode == "MI" || specCode == "MD") {
            if (metricIdx == CORNERS || metricIdx == TOQUES_AREA_RIVAL || metricIdx == TIROS || metricIdx == RECUPERACIONES) return 1.04;
        } else if (specCode == "DC") {
            if (metricIdx == GOLES_A_FAVOR || metricIdx == TIROS_AL_ARCO || metricIdx == TIROS || metricIdx == TOQUES_AREA_RIVAL) return 1.06;
        } else if (specCode == "EI" || specCode == "ED") {
            if (metricIdx == CORNERS || metricIdx == TOQUES_AREA_RIVAL || metricIdx == TIROS_AL_ARCO || metricIdx == TIROS_LIBRES) return 1.05;
        } else if (specCode == "SD") {
            if (metricIdx == GOLES_A_FAVOR || metricIdx == TIROS_AL_ARCO || metricIdx == PASES || metricIdx == TOQUES_AREA_RIVAL) return 1.05;
        }
        return 1.00;
    }

    static int tacticalHorizontalOrder(const std::string& specCode) {
        if (specCode == "POR") return 0;
        if (specCode == "LI")  return 10;
        if (specCode == "DFC") return 20;
        if (specCode == "LD")  return 30;
        if (specCode == "MI")  return 40;
        if (specCode == "MCD") return 50;
        if (specCode == "MC")  return 60;
        if (specCode == "MCO") return 70;
        if (specCode == "MD")  return 80;
        if (specCode == "EI")  return 90;
        if (specCode == "SD")  return 100;
        if (specCode == "DC")  return 110;
        if (specCode == "ED")  return 120;
        return 65;
    }

public:
    TeamTransformationModel() = default;

    explicit TeamTransformationModel(const Team& team, double ridgeLambda = 1.35)
        : team_(team) {
        buildModel(ridgeLambda);
    }

    const Team& team() const noexcept { return team_; }
    Team& mutableTeam() noexcept { return team_; }
    const math::Matrix& transformationMatrix() const noexcept { return transformationMatrix_; }
    const math::Matrix& binaryMatrixX() const noexcept { return binaryMatrixX_; }
    const math::Matrix& metricsMatrixY() const noexcept { return metricsMatrixY_; }
    const MatchMetrics& historicalAverage() const noexcept { return historicalAverage_; }
    double meanSquadRating() const noexcept { return meanSquadRating_; }
    double frobeniusResidual() const noexcept { return frobeniusResidual_; }
    double rSquared() const noexcept { return rSquared_; }

    bool hasPlayer(int playerId) const {
        return playerIdToIndex_.find(playerId) != playerIdToIndex_.end();
    }

    std::vector<double> getPlayerColumn(int playerId) const {
        auto it = playerIdToIndex_.find(playerId);
        if (it == playerIdToIndex_.end()) {
            return std::vector<double>(NUM_METRICS, 0.0);
        }
        return transformationMatrix_.getColumn(it->second);
    }

    /**
     * @brief Devuelve el vector Prior Posicional a_bar_P in R^20 para la línea táctica
     *        (GK, DEF, MID, FWD) o sub-posición específica (POR, DFC, LI, LD, MCD, MC,
     *        MCO, MI, MD, DC, EI, ED, SD) en este club.
     */
    std::vector<double> getPositionalPrior(const std::string& positionCode) const {
        auto specIt = specificPositionalPriors_.find(positionCode);
        if (specIt != specificPositionalPriors_.end()) {
            return specIt->second;
        }
        auto it = positionalPriors_.find(positionCode);
        if (it != positionalPriors_.end()) {
            return it->second;
        }
        auto tax = resolvePositionTaxonomy(positionCode);
        auto macroIt = positionalPriors_.find(tax.macroCode);
        if (macroIt != positionalPriors_.end()) {
            std::vector<double> adapted = macroIt->second;
            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                adapted[i] *= subPositionMetricMultiplier(tax.specificCode, i);
            }
            return adapted;
        }
        std::vector<double> fallback(NUM_METRICS, 0.0);
        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            fallback[i] = (historicalAverage_[i] / 11.0) * subPositionMetricMultiplier(tax.specificCode, i);
        }
        return fallback;
    }

    /**
     * @brief Sintetiza la columna a_new in R^20 para un jugador nuevo (transferido desde otro
     *        club con vector sourceCol, o debutante/externo cuando sourceCol está vacío).
     */
    std::vector<double> synthesizeNewPlayerColumn(
        const std::string& positionCode,
        double playerPriorRating,
        double sourceLeagueStrength,
        const std::vector<double>& sourceCol = {},
        const std::vector<double>& sourcePosBaseline = {}
    ) const {
        std::vector<double> posPrior = getPositionalPrior(positionCode);
        const auto& cat = getMetricsCatalog();

        double targetStrength = std::max(0.50, team_.strengthFactor);
        double srcStrength = std::max(0.50, sourceLeagueStrength);
        // Coeficiente de escala inter-liga: rho = (lambda_orig / lambda_dest)^0.65
        double rhoLeague = std::pow(srcStrength / targetStrength, 0.65);
        rhoLeague = std::clamp(rhoLeague, 0.72, 1.38);

        double effRating = (playerPriorRating > 1.0) ? playerPriorRating : meanSquadRating_;
        double ratingDiff = effRating - meanSquadRating_;

        std::vector<double> outCol(NUM_METRICS, 0.0);

        if (!sourceCol.empty() && sourceCol.size() == NUM_METRICS) {
            // RAMA 1 (Transferencia Matricial Inter-Club / Inter-Liga):
            // Proyecta el vector diferencial del club de origen sobre la base posicional del club destino
            constexpr double kappaAdapt = 0.80; // Coeficiente de adaptación táctica al nuevo club
            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                double srcBase = (sourcePosBaseline.size() == NUM_METRICS)
                                 ? sourcePosBaseline[i]
                                 : sourceCol[i];
                double deltaSrc = sourceCol[i] - srcBase;

                if (i == PROMEDIO_VALORACION) {
                    double adjustedRat = effRating * (0.90 + 0.10 * rhoLeague);
                    adjustedRat = std::clamp(adjustedRat, 5.5, 9.2);
                    outCol[i] = (0.55 * posPrior[i]) + (0.45 * (adjustedRat / 11.0));
                } else if (cat[i].higherIsBetter) {
                    double leagueMod = 1.0 + 0.35 * (rhoLeague - 1.0) + 0.08 * ratingDiff;
                    leagueMod = std::clamp(leagueMod, 0.80, 1.28);
                    double scaledDelta = deltaSrc * ((rhoLeague >= 1.0) ? rhoLeague : (0.85 * rhoLeague));
                    double maxShift = std::max(0.20, std::abs(posPrior[i]) * 0.45);
                    scaledDelta = std::clamp(kappaAdapt * scaledDelta, -maxShift, maxShift);
                    outCol[i] = std::max(0.0, posPrior[i] * leagueMod + scaledDelta);
                } else {
                    // Métrica inversa (goles en contra, faltas, tarjetas, tiros rivales): menor es mejor
                    double invMod = 1.0 - 0.30 * (rhoLeague - 1.0) - 0.06 * ratingDiff;
                    invMod = std::clamp(invMod, 0.75, 1.25);
                    double scaledDelta = deltaSrc / std::max(0.75, rhoLeague);
                    double maxShift = std::max(0.15, std::abs(posPrior[i]) * 0.40);
                    scaledDelta = std::clamp(kappaAdapt * scaledDelta, -maxShift, maxShift);
                    outCol[i] = std::max(0.0, posPrior[i] * invMod + scaledDelta);
                }
            }
        } else {
            // RAMA 2 (Debutante / Incorporación Externa sin matriz previa en BD):
            // Prior Posicional del club ponderado por rating previo y contracción de incertidumbre
            double conservativeShrink = 0.98 + 0.09 * ratingDiff + 0.20 * (rhoLeague - 1.0);
            conservativeShrink = std::clamp(conservativeShrink, 0.85, 1.20);

            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                if (i == PROMEDIO_VALORACION) {
                    outCol[i] = (0.65 * posPrior[i]) + (0.35 * (effRating / 11.0));
                } else if (cat[i].higherIsBetter) {
                    outCol[i] = std::max(0.0, posPrior[i] * conservativeShrink);
                } else {
                    outCol[i] = std::max(0.0, posPrior[i] * (2.0 - conservativeShrink));
                }
            }
        }

        return outCol;
    }

    /**
     * @brief Inyecta el vector real del club anterior para un jugador transferido y
     *        recalcula su columna en A_E combinando (1 - alpha_j)*a_prior + alpha_j*A_emp.
     */
    void applyCrossClubTransferVector(
        int playerId,
        const std::vector<double>& sourceCol,
        const std::vector<double>& sourcePosBaseline,
        double sourceLeagueStrength
    ) {
        auto it = playerIdToIndex_.find(playerId);
        if (it == playerIdToIndex_.end()) return;

        std::size_t j = it->second;
        Player& p = team_.squad[j];
        double priorRat = (p.previousAverageRating > 1.0) ? p.previousAverageRating
                        : (p.averageRating > 1.0) ? p.averageRating
                        : meanSquadRating_;
        if (p.averageRating <= 1.0) {
            p.averageRating = priorRat;
        }
        const std::string& effPosCode = !p.specificPositionCode.empty() ? p.specificPositionCode : p.positionCode;
        std::vector<double> transferPrior = synthesizeNewPlayerColumn(
            effPosCode, priorRat, sourceLeagueStrength, sourceCol, sourcePosBaseline
        );

        double pj = static_cast<double>(p.matchesPlayed);
        double alpha = (p.matchesPlayed < 3) ? (pj / (pj + 3.0)) : 1.0;
        p.adaptationAlpha = alpha;

        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            double empVal = empiricalMatrixA_(i, j);
            transformationMatrix_(i, j) = (1.0 - alpha) * transferPrior[i] + alpha * empVal;
        }
        recomputeFitMetrics();
    }

    /**
     * @brief Evalúa el puntaje táctico efectivo s_j de un jugador del plantel (o simulado).
     */
    double evaluatePlayerColumnScore(
        const Player& p,
        const std::vector<double>& col,
        const ITacticalStrategy& strategy
    ) const {
        double rawLinearScore = strategy.evaluateVector(col);

        double targetStrength = std::max(0.50, team_.strengthFactor);
        double srcStrength = std::max(0.50, p.previousLeagueStrength);
        double leagueRatio = std::pow(srcStrength / targetStrength, 0.50);
        leagueRatio = std::clamp(leagueRatio, 0.75, 1.35);

        // Experiencia efectiva: combina los PJ en el club actual con los PJ en su club de origen
        double effectiveMatches = static_cast<double>(p.matchesPlayed) +
                                  0.75 * static_cast<double>(p.previousMatchesPlayed) * leagueRatio;
        double effectiveMinutes = static_cast<double>(p.totalMinutes) +
                                  0.75 * static_cast<double>(p.previousMatchesPlayed * 75) * leagueRatio;

        double matchesFactor = std::min(1.0, effectiveMatches / 20.0);
        double minutesFactor = std::min(1.0, effectiveMinutes / 1100.0);

        double refRating = (p.matchesPlayed > 0 && p.averageRating > 1.0) ? p.averageRating
                         : (p.previousAverageRating > 1.0) ? p.previousAverageRating
                         : (p.averageRating > 1.0) ? p.averageRating
                         : meanSquadRating_;
        double ratingBonus = (refRating - 6.65) * 4.5;

        if (effectiveMatches < 3.0) {
            // Jugador nuevo sin historial previo cargado (Debutante / Incorporación Externa):
            // Regularización Bayesiana suave (sin penalización abrupta) para permitir evaluar
            // su aporte real en el 11 y competir limpiamente según su prior posicional y rating.
            double coldStartWeight = 0.56 + 0.08 * effectiveMatches;
            return rawLinearScore * coldStartWeight + 0.65 * ratingBonus;
        }

        double continuityWeight = 0.35 + 0.35 * matchesFactor + 0.30 * minutesFactor;
        return rawLinearScore * continuityWeight + ratingBonus;
    }

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
        result.competitionId = team_.competitionId;
        result.competitionName = team_.competitionName;
        result.leagueStrengthFactor = team_.strengthFactor;
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

        std::vector<std::size_t> gks, defs, mids, fwds;
        for (std::size_t j = 0; j < n; ++j) {
            if (!team_.squad[j].currentlyInClub) {
                continue;
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

        auto selectBalancedLine = [&](const std::vector<std::size_t>& sortedPool,
                                      int requiredCount,
                                      const std::vector<std::string>& preferredSubRoles) {
            std::vector<std::size_t> selected;
            std::vector<bool> used(sortedPool.size(), false);

            for (const auto& role : preferredSubRoles) {
                if (static_cast<int>(selected.size()) >= requiredCount) break;
                for (std::size_t k = 0; k < sortedPool.size(); ++k) {
                    if (!used[k] && team_.squad[sortedPool[k]].specificPositionCode == role) {
                        used[k] = true;
                        selected.push_back(sortedPool[k]);
                        break;
                    }
                }
            }
            for (std::size_t k = 0; k < sortedPool.size() && static_cast<int>(selected.size()) < requiredCount; ++k) {
                if (!used[k]) {
                    used[k] = true;
                    selected.push_back(sortedPool[k]);
                }
            }
            std::sort(selected.begin(), selected.end(), [&](std::size_t a, std::size_t b) {
                int ordA = tacticalHorizontalOrder(team_.squad[a].specificPositionCode);
                int ordB = tacticalHorizontalOrder(team_.squad[b].specificPositionCode);
                if (ordA != ordB) return ordA < ordB;
                return allContributions[a].tacticalScore > allContributions[b].tacticalScore;
            });
            return selected;
        };

        OptimalLineupResult bestResult;
        double bestFormationScore = -1e18;

        for (const auto& form : candidates) {
            if (gks.empty() ||
                static_cast<int>(defs.size()) < form.defenders ||
                static_cast<int>(mids.size()) < form.midfielders ||
                static_cast<int>(fwds.size()) < form.forwards) {
                continue;
            }

            std::vector<std::string> defRoles;
            if (form.defenders == 4) defRoles = {"LI", "DFC", "DFC", "LD"};
            else if (form.defenders == 5) defRoles = {"LI", "DFC", "DFC", "DFC", "LD"};
            else if (form.defenders == 3) defRoles = {"DFC", "DFC"};

            std::vector<std::string> midRoles;
            if (form.midfielders == 3) midRoles = {"MCD", "MC", "MCO"};
            else if (form.midfielders == 4) midRoles = {"MCD", "MC", "MCO"};
            else if (form.midfielders == 5) midRoles = {"MI", "MCD", "MC", "MCO", "MD"};

            std::vector<std::string> fwdRoles;
            if (form.forwards == 3) fwdRoles = {"EI", "DC", "ED"};
            else if (form.forwards == 2) fwdRoles = {"DC"};
            else if (form.forwards == 1) fwdRoles = {"DC"};

            std::vector<std::size_t> chosenDefs = selectBalancedLine(defs, form.defenders, defRoles);
            std::vector<std::size_t> chosenMids = selectBalancedLine(mids, form.midfielders, midRoles);
            std::vector<std::size_t> chosenFwds = selectBalancedLine(fwds, form.forwards, fwdRoles);

            std::vector<std::size_t> chosenIndices;
            chosenIndices.push_back(gks[0]);
            chosenIndices.insert(chosenIndices.end(), chosenDefs.begin(), chosenDefs.end());
            chosenIndices.insert(chosenIndices.end(), chosenMids.begin(), chosenMids.end());
            chosenIndices.insert(chosenIndices.end(), chosenFwds.begin(), chosenFwds.end());

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
                res.competitionId = team_.competitionId;
                res.competitionName = team_.competitionName;
                res.leagueStrengthFactor = team_.strengthFactor;
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
        return evaluatePlayerColumnScore(team_.squad[playerIdx], col, strategy);
    }

    void buildModel(double ridgeLambda) {
        const std::size_t n = team_.squad.size();
        const std::size_t m = team_.matches.size();

        playerIdToIndex_.clear();
        for (std::size_t j = 0; j < n; ++j) {
            playerIdToIndex_[team_.squad[j].playerId] = j;
        }

        if (n == 0) {
            empiricalMatrixA_ = math::Matrix(NUM_METRICS, 0, 0.0);
            transformationMatrix_ = math::Matrix(NUM_METRICS, 0, 0.0);
            return;
        }

        if (m == 0) {
            // Fallback seguro para equipos sin partidos registrados aún: inicializar con priors canónicos
            const std::array<double, NUM_METRICS> canonicalDefaults = {
                1.35, 1.10, 4.50, 50.0, 2.80, 5.20, 11.5, 420.0, 12.0, 2.10,
                0.08, 6.80, 12.5, 18.0, 1.80, 15.5, 9.20, 48.0, 16.0, 3.90
            };
            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                historicalAverage_[i] = canonicalDefaults[i];
            }
            meanSquadRating_ = 6.80;
            for (const auto& code : std::array<std::string, 4>{"GK", "DEF", "MID", "FWD"}) {
                std::vector<double> col(NUM_METRICS, 0.0);
                for (std::size_t i = 0; i < NUM_METRICS; ++i) col[i] = historicalAverage_[i] / 11.0;
                positionalPriors_[code] = std::move(col);
            }
            empiricalMatrixA_ = math::Matrix(NUM_METRICS, n, 0.0);
            transformationMatrix_ = math::Matrix(NUM_METRICS, n, 0.0);
            for (std::size_t j = 0; j < n; ++j) {
                Player& p = team_.squad[j];
                if (p.averageRating <= 1.0) {
                    p.averageRating = (p.previousAverageRating > 1.0) ? p.previousAverageRating : meanSquadRating_;
                }
                p.adaptationAlpha = 0.0;
                auto col = synthesizeNewPlayerColumn(p.positionCode, p.averageRating, team_.strengthFactor);
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    empiricalMatrixA_(i, j) = col[i];
                    transformationMatrix_(i, j) = col[i];
                }
            }
            frobeniusResidual_ = 0.0;
            rSquared_ = 1.0;
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
            if (p.matchesPlayed > 0 && p.averageRating > 1.0) {
                sumRatings += p.averageRating;
                ratedCount++;
            }
        }
        meanSquadRating_ = (ratedCount > 0) ? (sumRatings / ratedCount) : 6.75;

        for (auto& p : team_.squad) {
            if (p.averageRating <= 1.0) {
                p.averageRating = (p.previousAverageRating > 1.0) ? p.previousAverageRating : meanSquadRating_;
            }
        }

        // 1. Construir matriz base canónica A_0 in R^(20 x n)
        math::Matrix A0(NUM_METRICS, n, 0.0);
        for (std::size_t j = 0; j < n; ++j) {
            const Player& p = team_.squad[j];
            double reliability = static_cast<double>(p.matchesPlayed) / (p.matchesPlayed + 4.0);
            double ratingDiff = (p.matchesPlayed > 0 && p.averageRating > 1.0) ? (p.averageRating - meanSquadRating_) : 0.0;
            double modFactor = 1.0 + 0.12 * reliability * ratingDiff;

            for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                double baseShare = (historicalAverage_[i] / 11.0) * subPositionMetricMultiplier(p.specificPositionCode, i);
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

        // 4. Matriz empírica inicial: A_emp = A_0 + Delta_A
        empiricalMatrixA_ = A0 + deltaA;
        transformationMatrix_ = empiricalMatrixA_;

        // 5. Calcular los vectores Prior Posicionales a_bar_P in R^20 por línea táctica (GK, DEF, MID, FWD)
        //    y por sub-posición específica (POR, DFC, LI, LD, MCD, MC, MCO, MI, MD, DC, EI, ED, SD),
        //    promediando ponderadamente por continuidad a los jugadores establecidos (PJ >= 3).
        const std::array<std::string, 4> posCodes = {"GK", "DEF", "MID", "FWD"};
        for (const auto& code : posCodes) {
            std::vector<double> sumCol(NUM_METRICS, 0.0);
            double totalWeight = 0.0;
            for (std::size_t j = 0; j < n; ++j) {
                const Player& p = team_.squad[j];
                if (p.positionCode == code && p.matchesPlayed >= 3) {
                    double w = static_cast<double>(p.matchesPlayed);
                    for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                        sumCol[i] += w * empiricalMatrixA_(i, j);
                    }
                    totalWeight += w;
                }
            }
            if (totalWeight > 0.0) {
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    sumCol[i] /= totalWeight;
                }
            } else {
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    sumCol[i] = historicalAverage_[i] / 11.0;
                }
            }
            positionalPriors_[code] = std::move(sumCol);
        }

        const std::array<std::string, 13> specCodes = {
            "POR", "DFC", "LI", "LD", "MCD", "MC", "MCO", "MI", "MD", "DC", "EI", "ED", "SD"
        };
        for (const auto& scode : specCodes) {
            auto tax = resolvePositionTaxonomy(scode);
            const auto& macroPrior = positionalPriors_[tax.macroCode];
            std::vector<double> sumCol(NUM_METRICS, 0.0);
            double totalWeight = 0.0;
            for (std::size_t j = 0; j < n; ++j) {
                const Player& p = team_.squad[j];
                if (p.specificPositionCode == scode && p.matchesPlayed >= 3) {
                    double w = static_cast<double>(p.matchesPlayed);
                    for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                        sumCol[i] += w * empiricalMatrixA_(i, j);
                    }
                    totalWeight += w;
                }
            }
            if (totalWeight > 0.0) {
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    double empMean = sumCol[i] / totalWeight;
                    double modMacro = macroPrior[i] * subPositionMetricMultiplier(scode, i);
                    sumCol[i] = 0.70 * empMean + 0.30 * modMacro;
                }
            } else {
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    sumCol[i] = macroPrior[i] * subPositionMetricMultiplier(scode, i);
                }
            }
            specificPositionalPriors_[scode] = std::move(sumCol);
        }

        // 6. Aplicar Resolución Cold-Start Híbrida para jugadores nuevos en el club (PJ < 3):
        //    A_E(., j) = (1 - alpha_j) * a_j^prior + alpha_j * A_emp(., j)
        for (std::size_t j = 0; j < n; ++j) {
            Player& p = team_.squad[j];
            double pj = static_cast<double>(p.matchesPlayed);

            if (p.matchesPlayed < 3) {
                double alpha = pj / (pj + 3.0);
                p.adaptationAlpha = alpha;
                double priorRat = (p.previousAverageRating > 1.0) ? p.previousAverageRating
                                : (p.averageRating > 1.0) ? p.averageRating
                                : meanSquadRating_;
                double srcLeague = (p.previousLeagueStrength > 0.1) ? p.previousLeagueStrength : team_.strengthFactor;
                const std::string& effPosCode = !p.specificPositionCode.empty() ? p.specificPositionCode : p.positionCode;
                std::vector<double> priorCol = synthesizeNewPlayerColumn(
                    effPosCode, priorRat, srcLeague
                );
                for (std::size_t i = 0; i < NUM_METRICS; ++i) {
                    transformationMatrix_(i, j) = (1.0 - alpha) * priorCol[i] + alpha * empiricalMatrixA_(i, j);
                }
            } else {
                p.adaptationAlpha = 1.0;
            }
        }

        // 7. Métricas de bondad de ajuste matricial sobre los partidos jugados (Norma de Frobenius y R^2)
        recomputeFitMetrics();
    }

    void recomputeFitMetrics() {
        const std::size_t m = team_.matches.size();
        if (m == 0 || normalizedMatrixX_.cols() == 0) {
            frobeniusResidual_ = 0.0;
            rSquared_ = 1.0;
            return;
        }
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
