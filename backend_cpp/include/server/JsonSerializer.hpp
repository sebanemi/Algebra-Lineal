#ifndef ALGEBRA_LINEAL_JSON_SERIALIZER_HPP
#define ALGEBRA_LINEAL_JSON_SERIALIZER_HPP

#include "domain/Models.hpp"
#include "domain/TeamTransformationModel.hpp"
#include <string>
#include <sstream>
#include <iomanip>
#include <vector>
#include <unordered_map>
#include <cmath>

namespace lpf::server {

/**
 * @class JsonSerializer
 * @brief Serializador JSON y parser de parámetros HTTP ligero sin dependencias externas.
 */
class JsonSerializer {
public:
    static std::string escape(const std::string& s) {
        std::ostringstream oss;
        for (unsigned char c : s) {
            switch (c) {
                case '"':  oss << "\\\""; break;
                case '\\': oss << "\\\\"; break;
                case '\b': oss << "\\b"; break;
                case '\f': oss << "\\f"; break;
                case '\n': oss << "\\n"; break;
                case '\r': oss << "\\r"; break;
                case '\t': oss << "\\t"; break;
                default:
                    if (c < 0x20) {
                        oss << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                    } else {
                        oss << static_cast<char>(c);
                    }
            }
        }
        return oss.str();
    }

    static std::string num(double v, int precision = 3) {
        if (!std::isfinite(v)) return "0.0";
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(precision) << v;
        return oss.str();
    }

    static std::string serializeMetricsObject(const domain::MatchMetrics& m) {
        const auto& cat = domain::getMetricsCatalog();
        std::ostringstream oss;
        oss << "{";
        for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
            if (i > 0) oss << ",";
            oss << "\"" << cat[i].key << "\":" << num(m[i], 2);
        }
        oss << "}";
        return oss.str();
    }

    static std::string serializeMetricsArray(const domain::MatchMetrics& m, const domain::MatchMetrics& ref) {
        const auto& cat = domain::getMetricsCatalog();
        std::ostringstream oss;
        oss << "[";
        for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
            if (i > 0) oss << ",";
            oss << "{"
                << "\"index\":" << i << ","
                << "\"key\":\"" << cat[i].key << "\","
                << "\"name\":\"" << escape(cat[i].displayName) << "\","
                << "\"unit\":\"" << escape(cat[i].unit) << "\","
                << "\"higher_is_better\":" << (cat[i].higherIsBetter ? "true" : "false") << ","
                << "\"value\":" << num(m[i], 2) << ","
                << "\"historical_avg\":" << num(ref[i], 2) << ","
                << "\"delta\":" << num(m[i] - ref[i], 2)
                << "}";
        }
        oss << "]";
        return oss.str();
    }

    static std::string serializeCompetitionsList(const std::vector<domain::Competition>& comps) {
        std::ostringstream oss;
        oss << "{\"status\":\"ok\",\"count\":" << comps.size() << ",\"competitions\":[";
        for (std::size_t i = 0; i < comps.size(); ++i) {
            if (i > 0) oss << ",";
            const auto& c = comps[i];
            oss << "{"
                << "\"competition_id\":" << c.competitionId << ","
                << "\"code\":\"" << escape(c.code) << "\","
                << "\"name\":\"" << escape(c.name) << "\","
                << "\"short_name\":\"" << escape(c.shortName) << "\","
                << "\"country\":\"" << escape(c.country) << "\","
                << "\"confederation\":\"" << escape(c.confederation) << "\","
                << "\"sofascore_id\":" << c.sofascoreId << ","
                << "\"strength_factor\":" << num(c.strengthFactor, 2)
                << "}";
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializePlayerContribution(const domain::PlayerContribution& pc) {
        std::ostringstream oss;
        oss << "{"
            << "\"player_id\":" << pc.player.playerId << ","
            << "\"name\":\"" << escape(pc.player.name) << "\","
            << "\"short_name\":\"" << escape(pc.player.shortName) << "\","
            << "\"position\":\"" << escape(pc.player.position) << "\","
            << "\"position_code\":\"" << escape(pc.player.positionCode) << "\","
            << "\"jersey_number\":\"" << escape(pc.player.jerseyNumber) << "\","
            << "\"convocations\":" << pc.player.convocationsLast20 << ","
            << "\"matches_played\":" << pc.player.matchesPlayed << ","
            << "\"matches_unused_sub\":" << pc.player.matchesUnusedSub << ","
            << "\"total_minutes\":" << pc.player.totalMinutes << ","
            << "\"average_rating\":" << num(pc.player.averageRating, 2) << ","
            << "\"currently_in_club\":" << (pc.player.currentlyInClub ? "true" : "false") << ","
            << "\"is_new_signing\":" << (pc.player.isNewSigning ? "true" : "false") << ","
            << "\"origin_type\":\"" << escape(pc.player.originType) << "\","
            << "\"previous_team_id\":" << pc.player.previousTeamId << ","
            << "\"previous_team_name\":\"" << escape(pc.player.previousTeamName) << "\","
            << "\"previous_matches_played\":" << pc.player.previousMatchesPlayed << ","
            << "\"previous_average_rating\":" << num(pc.player.previousAverageRating, 2) << ","
            << "\"previous_league_strength\":" << num(pc.player.previousLeagueStrength, 2) << ","
            << "\"adaptation_alpha\":" << num(pc.player.adaptationAlpha, 2) << ","
            << "\"input_bit\":" << pc.inputBit << ","
            << "\"tactical_score\":" << num(pc.tacticalScore, 2) << ","
            << "\"column_vector\":[";
        for (std::size_t i = 0; i < pc.columnVector.size(); ++i) {
            if (i > 0) oss << ",";
            oss << num(pc.columnVector[i], 3);
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeTeamsList(const std::vector<domain::TeamTransformationModel>& models) {
        std::ostringstream oss;
        oss << "{\"status\":\"ok\",\"count\":" << models.size() << ",\"teams\":[";
        for (std::size_t i = 0; i < models.size(); ++i) {
            if (i > 0) oss << ",";
            const auto& m = models[i];
            oss << "{"
                << "\"team_id\":" << m.team().teamId << ","
                << "\"name\":\"" << escape(m.team().name) << "\","
                << "\"short_name\":\"" << escape(m.team().shortName) << "\","
                << "\"competition_id\":" << m.team().competitionId << ","
                << "\"competition_name\":\"" << escape(m.team().competitionName) << "\","
                << "\"country\":\"" << escape(m.team().country) << "\","
                << "\"strength_factor\":" << num(m.team().strengthFactor, 2) << ","
                << "\"squad_size\":" << m.team().squad.size() << ","
                << "\"matches_count\":" << m.team().matches.size() << ","
                << "\"matrix_rows\":" << m.transformationMatrix().rows() << ","
                << "\"matrix_cols\":" << m.transformationMatrix().cols() << ","
                << "\"r_squared\":" << num(m.rSquared(), 4) << ","
                << "\"frobenius_residual\":" << num(m.frobeniusResidual(), 2) << ","
                << "\"historical_avg\":" << serializeMetricsObject(m.historicalAverage())
                << "}";
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeTeamDetail(
        const domain::TeamTransformationModel& model,
        const domain::ITacticalStrategy& strategy
    ) {
        std::ostringstream oss;
        const auto& team = model.team();
        const auto& A = model.transformationMatrix();
        auto contributions = model.getAllPlayerContributions(strategy);

        oss << "{\"status\":\"ok\","
            << "\"team_id\":" << team.teamId << ","
            << "\"name\":\"" << escape(team.name) << "\","
            << "\"short_name\":\"" << escape(team.shortName) << "\","
            << "\"competition_id\":" << team.competitionId << ","
            << "\"competition_name\":\"" << escape(team.competitionName) << "\","
            << "\"country\":\"" << escape(team.country) << "\","
            << "\"strength_factor\":" << num(team.strengthFactor, 2) << ","
            << "\"squad_size\":" << team.squad.size() << ","
            << "\"matches_count\":" << team.matches.size() << ","
            << "\"matrix_rows\":" << A.rows() << ","
            << "\"matrix_cols\":" << A.cols() << ","
            << "\"r_squared\":" << num(model.rSquared(), 4) << ","
            << "\"frobenius_residual\":" << num(model.frobeniusResidual(), 2) << ","
            << "\"historical_avg\":" << serializeMetricsObject(model.historicalAverage()) << ","
            << "\"players\":[";

        for (std::size_t j = 0; j < contributions.size(); ++j) {
            if (j > 0) oss << ",";
            oss << serializePlayerContribution(contributions[j]);
        }
        oss << "],\"transformation_matrix\":[";
        for (std::size_t r = 0; r < A.rows(); ++r) {
            if (r > 0) oss << ",";
            oss << "[";
            for (std::size_t c = 0; c < A.cols(); ++c) {
                if (c > 0) oss << ",";
                oss << num(A(r, c), 3);
            }
            oss << "]";
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeBehaviorPrediction(const domain::BehaviorPredictionResult& res) {
        std::ostringstream oss;
        oss << "{\"status\":\"ok\","
            << "\"team_id\":" << res.teamId << ","
            << "\"team_name\":\"" << escape(res.teamName) << "\","
            << "\"competition_id\":" << res.competitionId << ","
            << "\"competition_name\":\"" << escape(res.competitionName) << "\","
            << "\"strength_factor\":" << num(res.leagueStrengthFactor, 2) << ","
            << "\"selected_count\":" << res.selectedPlayerIds.size() << ","
            << "\"tactical_score\":" << num(res.tacticalScore, 2) << ","
            << "\"r_squared\":" << num(res.modelFitR2, 4) << ","
            << "\"frobenius_residual\":" << num(res.frobeniusResidual, 2) << ","
            << "\"binary_vector\":[";
        for (std::size_t i = 0; i < res.binaryAlignmentVector.size(); ++i) {
            if (i > 0) oss << ",";
            oss << res.binaryAlignmentVector[i];
        }
        oss << "],"
            << "\"predicted_metrics\":" << serializeMetricsObject(res.predictedMetrics) << ","
            << "\"raw_linear_output\":" << serializeMetricsObject(res.rawLinearOutput) << ","
            << "\"historical_avg\":" << serializeMetricsObject(res.historicalAverage) << ","
            << "\"metrics_comparison\":" << serializeMetricsArray(res.predictedMetrics, res.historicalAverage) << ","
            << "\"active_players\":[";
        for (std::size_t i = 0; i < res.activeContributions.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.activeContributions[i]);
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeOptimalLineup(const domain::OptimalLineupResult& res) {
        std::ostringstream oss;
        oss << "{\"status\":\"ok\","
            << "\"team_id\":" << res.teamId << ","
            << "\"team_name\":\"" << escape(res.teamName) << "\","
            << "\"competition_id\":" << res.competitionId << ","
            << "\"competition_name\":\"" << escape(res.competitionName) << "\","
            << "\"strength_factor\":" << num(res.leagueStrengthFactor, 2) << ","
            << "\"strategy\":\"" << escape(res.strategyName) << "\","
            << "\"formation\":\"" << escape(res.formationName) << "\","
            << "\"total_tactical_score\":" << num(res.totalTacticalScore, 2) << ","
            << "\"improvement_pct\":" << num(res.improvementPercentage, 2) << ","
            << "\"optimal_binary_vector\":[";
        for (std::size_t i = 0; i < res.optimalBinaryVector.size(); ++i) {
            if (i > 0) oss << ",";
            oss << res.optimalBinaryVector[i];
        }
        oss << "],"
            << "\"expected_metrics\":" << serializeMetricsObject(res.expectedMetrics) << ","
            << "\"historical_avg\":" << serializeMetricsObject(res.historicalAverage) << ","
            << "\"metrics_comparison\":" << serializeMetricsArray(res.expectedMetrics, res.historicalAverage) << ","
            << "\"starters\":[";
        for (std::size_t i = 0; i < res.optimalStarters.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.optimalStarters[i]);
        }
        oss << "],\"bench\":[";
        for (std::size_t i = 0; i < res.recommendedBench.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.recommendedBench.size() ? res.recommendedBench[i] : domain::PlayerContribution{});
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeMatchupPrediction(const domain::MatchupPredictionResult& res) {
        const auto& cat = domain::getMetricsCatalog();
        std::ostringstream oss;
        oss << "{\"status\":\"ok\","
            << "\"is_inter_league\":" << (res.isInterLeague ? "true" : "false") << ","
            << "\"relative_league_ratio\":" << num(res.relativeLeagueRatioAtoB, 3) << ","
            << "\"team_a\":{\"team_id\":" << res.teamAId
            << ",\"name\":\"" << escape(res.teamAName)
            << "\",\"competition_id\":" << res.teamACompetitionId
            << ",\"competition_name\":\"" << escape(res.teamACompetitionName)
            << "\",\"strength_factor\":" << num(res.teamAStrengthFactor, 2)
            << ",\"formation\":\"" << escape(res.teamAFormation)
            << "\",\"expected_goals\":" << num(res.expectedGoalsA, 2)
            << ",\"win_probability\":" << num(res.winProbabilityA, 2)
            << ",\"tactical_edge\":" << num(res.tacticalEdgeScoreA, 2)
            << ",\"standalone_metrics\":" << serializeMetricsObject(res.standaloneMetricsA)
            << ",\"coupled_metrics\":" << serializeMetricsObject(res.coupledMetricsA)
            << ",\"lineup\":[";
        for (std::size_t i = 0; i < res.lineupA.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.lineupA[i]);
        }
        oss << "]},"
            << "\"team_b\":{\"team_id\":" << res.teamBId
            << ",\"name\":\"" << escape(res.teamBName)
            << "\",\"competition_id\":" << res.teamBCompetitionId
            << ",\"competition_name\":\"" << escape(res.teamBCompetitionName)
            << "\",\"strength_factor\":" << num(res.teamBStrengthFactor, 2)
            << ",\"formation\":\"" << escape(res.teamBFormation)
            << "\",\"expected_goals\":" << num(res.expectedGoalsB, 2)
            << ",\"win_probability\":" << num(res.winProbabilityB, 2)
            << ",\"tactical_edge\":" << num(res.tacticalEdgeScoreB, 2)
            << ",\"standalone_metrics\":" << serializeMetricsObject(res.standaloneMetricsB)
            << ",\"coupled_metrics\":" << serializeMetricsObject(res.coupledMetricsB)
            << ",\"lineup\":[";
        for (std::size_t i = 0; i < res.lineupB.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.lineupB[i]);
        }
        oss << "]},"
            << "\"draw_probability\":" << num(res.drawProbability, 2) << ","
            << "\"top_scorelines\":[";
        for (std::size_t i = 0; i < res.topScorelines.size(); ++i) {
            if (i > 0) oss << ",";
            oss << "{\"goals_a\":" << res.topScorelines[i].goalsTeamA
                << ",\"goals_b\":" << res.topScorelines[i].goalsTeamB
                << ",\"probability\":" << num(res.topScorelines[i].probability, 2) << "}";
        }
        oss << "],\"comparison_table\":[";
        for (std::size_t i = 0; i < domain::NUM_METRICS; ++i) {
            if (i > 0) oss << ",";
            oss << "{"
                << "\"index\":" << i << ","
                << "\"key\":\"" << cat[i].key << "\","
                << "\"name\":\"" << escape(cat[i].displayName) << "\","
                << "\"unit\":\"" << escape(cat[i].unit) << "\","
                << "\"higher_is_better\":" << (cat[i].higherIsBetter ? "true" : "false") << ","
                << "\"team_a_standalone\":" << num(res.standaloneMetricsA[i], 2) << ","
                << "\"team_b_standalone\":" << num(res.standaloneMetricsB[i], 2) << ","
                << "\"team_a_coupled\":" << num(res.coupledMetricsA[i], 2) << ","
                << "\"team_b_coupled\":" << num(res.coupledMetricsB[i], 2)
                << "}";
        }
        oss << "]}";
        return oss.str();
    }

    static std::string serializeTransferSimulation(const domain::TransferSimulationResult& res) {
        domain::PlayerContribution simPc;
        simPc.player = res.simulatedPlayer;
        simPc.inputBit = 1;
        simPc.tacticalScore = res.newPlayerTacticalScore;
        simPc.columnVector = res.injectedColumnVector;

        std::ostringstream oss;
        oss << "{\"status\":\"ok\","
            << "\"target_team_id\":" << res.targetTeamId << ","
            << "\"target_team_name\":\"" << escape(res.targetTeamName) << "\","
            << "\"target_competition_name\":\"" << escape(res.targetCompetitionName) << "\","
            << "\"target_league_strength\":" << num(res.targetLeagueStrength, 2) << ","
            << "\"source_team_id\":" << res.sourceTeamId << ","
            << "\"source_team_name\":\"" << escape(res.sourceTeamName) << "\","
            << "\"source_competition_name\":\"" << escape(res.sourceCompetitionName) << "\","
            << "\"source_league_strength\":" << num(res.sourceLeagueStrength, 2) << ","
            << "\"strategy\":\"" << escape(res.strategyName) << "\","
            << "\"formation\":\"" << escape(res.formationName) << "\","
            << "\"league_scaling_ratio\":" << num(res.leagueScalingRatio, 3) << ","
            << "\"adaptation_factor\":" << num(res.adaptationFactor, 2) << ","
            << "\"replaced_player_id\":" << res.replacedPlayerId << ","
            << "\"replaced_player_name\":\"" << escape(res.replacedPlayerName) << "\","
            << "\"replaced_player_score\":" << num(res.replacedPlayerTacticalScore, 2) << ","
            << "\"new_player_score\":" << num(res.newPlayerTacticalScore, 2) << ","
            << "\"baseline_tactical_score\":" << num(res.baselineTacticalScore, 2) << ","
            << "\"post_transfer_tactical_score\":" << num(res.postTransferTacticalScore, 2) << ","
            << "\"tactical_score_delta\":" << num(res.tacticalScoreDelta, 2) << ","
            << "\"improvement_pct\":" << num(res.improvementPercentage, 2) << ","
            << "\"simulated_player\":" << serializePlayerContribution(simPc) << ","
            << "\"positional_prior_vector\":[";
        for (std::size_t i = 0; i < res.positionalPriorVector.size(); ++i) {
            if (i > 0) oss << ",";
            oss << num(res.positionalPriorVector[i], 3);
        }
        oss << "],\"source_differential_vector\":[";
        for (std::size_t i = 0; i < res.sourceDifferentialVec.size(); ++i) {
            if (i > 0) oss << ",";
            oss << num(res.sourceDifferentialVec[i], 3);
        }
        oss << "],"
            << "\"baseline_metrics\":" << serializeMetricsObject(res.baselineMetrics) << ","
            << "\"post_transfer_metrics\":" << serializeMetricsObject(res.postTransferMetrics) << ","
            << "\"metrics_comparison\":" << serializeMetricsArray(res.postTransferMetrics, res.baselineMetrics) << ","
            << "\"post_transfer_starters\":[";
        for (std::size_t i = 0; i < res.postTransferStarters.size(); ++i) {
            if (i > 0) oss << ",";
            oss << serializePlayerContribution(res.postTransferStarters[i]);
        }
        oss << "]}";
        return oss.str();
    }

    static std::unordered_map<std::string, std::string> parseKeyValueParams(const std::string& raw) {
        std::unordered_map<std::string, std::string> params;
        std::size_t start = 0;
        while (start < raw.size()) {
            std::size_t amp = raw.find('&', start);
            if (amp == std::string::npos) amp = raw.size();
            std::string pair = raw.substr(start, amp - start);
            std::size_t eq = pair.find('=');
            if (eq != std::string::npos) {
                std::string k = pair.substr(0, eq);
                std::string v = urlDecode(pair.substr(eq + 1));
                params[k] = v;
            }
            start = amp + 1;
        }
        return params;
    }

    static std::vector<int> parseCsvIntList(const std::string& csv) {
        std::vector<int> out;
        std::stringstream ss(csv);
        std::string item;
        while (std::getline(ss, item, ',')) {
            try {
                if (!item.empty()) {
                    out.push_back(std::stoi(item));
                }
            } catch (...) {}
        }
        return out;
    }

private:
    static std::string urlDecode(const std::string& str) {
        std::string ret;
        ret.reserve(str.size());
        for (std::size_t i = 0; i < str.size(); ++i) {
            if (str[i] == '+') {
                ret += ' ';
            } else if (str[i] == '%' && i + 2 < str.size()) {
                int hexVal = 0;
                std::istringstream iss(str.substr(i + 1, 2));
                if (iss >> std::hex >> hexVal) {
                    ret += static_cast<char>(hexVal);
                    i += 2;
                } else {
                    ret += str[i];
                }
            } else {
                ret += str[i];
            }
        }
        return ret;
    }
};

} // namespace lpf::server

#endif // ALGEBRA_LINEAL_JSON_SERIALIZER_HPP
