#include "math/Matrix.hpp"
#include "repository/SqliteRepository.hpp"
#include "services/AnalyticsEngineService.hpp"
#include "concurrency/ThreadPool.hpp"

#include <iostream>
#include <cassert>
#include <cmath>
#include <atomic>
#include <vector>

using namespace lpf;

void testMatrixOperations() {
    std::cout << "[TEST 1] Verificando operaciones de Matrix, Gauss-Jordan y Pseudoinversa de Moore-Penrose..." << std::endl;

    math::Matrix A(2, 2, {4.0, 7.0, 2.0, 6.0});
    math::Matrix A_inv = A.inverse();
    math::Matrix I = A * A_inv;

    assert(std::abs(I(0, 0) - 1.0) < 1e-9);
    assert(std::abs(I(1, 1) - 1.0) < 1e-9);
    assert(std::abs(I(0, 1)) < 1e-9);
    assert(std::abs(I(1, 0)) < 1e-9);

    math::Matrix X(3, 2, {1.0, 0.0, 0.0, 1.0, 1.0, 1.0});
    math::Matrix X_pinv = X.pseudoInverse(1e-6);
    assert(X_pinv.rows() == 2 && X_pinv.cols() == 3);

    math::Matrix X_reconstructed = X * X_pinv * X;
    for (std::size_t r = 0; r < 3; ++r) {
        for (std::size_t c = 0; c < 2; ++c) {
            assert(std::abs(X_reconstructed(r, c) - X(r, c)) < 1e-4);
        }
    }
    std::cout << "         -> OK (Inversa y Pseudoinversa verificadas con precisión < 1e-4)" << std::endl;
}

void testDatabaseAndLinearModels() {
    std::cout << "[TEST 2] Verificando carga Multi-Liga desde SQLite3 y Transformaciones Lineales..." << std::endl;

    auto repo = std::make_shared<repository::SqliteRepository>("data/lpf_analytics.db");
    auto engine = std::make_shared<services::AnalyticsEngineService>(repo, 1.35);

    auto comps = engine->getAllCompetitions();
    assert(comps.size() >= 10);

    // Deben existir exactamente 30 equipos en la Liga Profesional Argentina (competition_id = 72)
    auto lpfModels = engine->getAllTeamModels(72);
    assert(lpfModels.size() == 30);

    // Y además los equipos internacionales preconfigurados (LaLiga, Premier League, Serie A, Bundesliga, Brasileirão)
    auto allModels = engine->getAllTeamModels();
    assert(allModels.size() >= 38);

    for (const auto& model : allModels) {
        const auto& A = model.transformationMatrix();
        assert(A.rows() == domain::NUM_METRICS);
        assert(A.cols() == model.team().squad.size());
        assert(model.team().matches.size() == 20);
        assert(model.frobeniusResidual() > 0.0);

        auto optTeam = engine->optimizeLineup(model.team().teamId, "BALANCED", "AUTO");
        assert(optTeam.optimalStarters.size() == 11);
        for (const auto& starter : optTeam.optimalStarters) {
            assert(starter.player.currentlyInClub == true);
        }
        for (const auto& sub : optTeam.recommendedBench) {
            assert(sub.player.currentlyInClub == true);
        }
    }

    // Verificar rechazo de ex-jugadores dados de baja (currentlyInClub == false)
    const auto& instModel = engine->getTeamModel(7272);
    int departedPlayerId = -1;
    for (const auto& p : instModel.team().squad) {
        if (!p.currentlyInClub) {
            departedPlayerId = p.playerId;
            break;
        }
    }
    if (departedPlayerId != -1) {
        bool rejected = false;
        try {
            engine->predictBehavior(7272, {departedPlayerId}, "BALANCED");
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        assert(rejected == true);
    }

    std::cout << "         -> OK (" << allModels.size() << " equipos en " << comps.size()
              << " competiciones verificados exitosamente)" << std::endl;
}

void testColdStartNewPlayerAndTransfers() {
    std::cout << "[TEST 3] Verificando resolución de Jugador Nuevo en un Club (Cold-Start Híbrido y Simulador de Fichajes)..." << std::endl;

    auto repo = std::make_shared<repository::SqliteRepository>("data/lpf_analytics.db");
    auto engine = std::make_shared<services::AnalyticsEngineService>(repo, 1.35);

    // 1. Verificar jugador nuevo transferido entre clubes de la BD (Thiago Almada ID 60282: River Plate -> Manchester City con 0 PJ en City)
    const auto& cityModel = engine->getTeamModel(110);
    bool foundTransferred = false;
    for (std::size_t j = 0; j < cityModel.team().squad.size(); ++j) {
        const auto& p = cityModel.team().squad[j];
        if (p.playerId == 60282) { // Thiago Almada
            foundTransferred = true;
            assert(p.matchesPlayed == 0);
            assert(p.isNewSigning == true);
            assert(p.originType == "TRANSFERRED");
            assert(p.previousTeamId == 868); // River Plate
            assert(std::abs(p.adaptationAlpha - 0.0) < 1e-6);

            auto col = cityModel.transformationMatrix().getColumn(j);
            assert(col[domain::GOLES_A_FAVOR] > 0.01);
            assert(col[domain::PASES] > 10.0);
            assert(col[domain::PROMEDIO_VALORACION] > 0.4);
            break;
        }
    }
    assert(foundTransferred == true);

    // 2. Verificar jugador nuevo debutante / externo con 0 PJ sin club previo en BD (Joan Martínez en Real Madrid, ID 90121)
    const auto& madridModel = engine->getTeamModel(131);
    bool foundDebutant = false;
    for (std::size_t j = 0; j < madridModel.team().squad.size(); ++j) {
        const auto& p = madridModel.team().squad[j];
        if (p.playerId == 90121) {
            foundDebutant = true;
            assert(p.matchesPlayed == 0);
            assert(p.isNewSigning == true);
            assert(p.originType == "YOUTH_OR_EXTERNAL");
            auto col = madridModel.transformationMatrix().getColumn(j);
            // Debe recibir el Prior Posicional de DEF del Real Madrid (no nulo)
            assert(col[domain::INTERCEPCIONES] > 0.1);
            assert(col[domain::PASES] > 15.0);
            break;
        }
    }
    assert(foundDebutant == true);

    // 3. Probar Simulador de Fichajes Inter-Liga (ej: ¿Qué pasa si Boca Juniors ficha a Kylian Mbappé del Real Madrid?)
    auto simTransfer = engine->simulateTransfer(866, 131, 90117, 0, "OFFENSIVE", "4-3-3");
    assert(simTransfer.targetTeamId == 866);
    assert(simTransfer.sourceTeamId == 131);
    assert(simTransfer.leagueScalingRatio > 1.0); // LaLiga (1.26) -> LPF (1.00)
    assert(simTransfer.injectedColumnVector.size() == domain::NUM_METRICS);
    assert(simTransfer.postTransferStarters.size() == 11);
    assert(simTransfer.postTransferMetrics[domain::GOLES_A_FAVOR] >= simTransfer.baselineMetrics[domain::GOLES_A_FAVOR]);

    // 4. Probar Simulador de Incorporación de un Jugador Nuevo Externo / Inferiores (sin club origen en BD)
    auto simYouth = engine->simulateTransfer(868, 0, 0, 0, "BALANCED", "4-3-3", "Juvenil Promesa", "FWD", 7.15, 1.00);
    assert(simYouth.targetTeamId == 868);
    assert(simYouth.simulatedPlayer.originType == "YOUTH_OR_EXTERNAL");
    assert(simYouth.injectedColumnVector[domain::TIROS_AL_ARCO] > 0.05);

    // 5. Probar Predicción Inter-Liga (Real Madrid vs River Plate)
    auto interMatch = engine->predictMatchup(131, 868, {}, {}, "BALANCED", "4-3-3", "4-3-3", true);
    assert(interMatch.isInterLeague == true);
    assert(interMatch.relativeLeagueRatioAtoB > 1.20);
    double probSum = interMatch.winProbabilityA + interMatch.drawProbability + interMatch.winProbabilityB;
    assert(std::abs(probSum - 100.0) < 1e-3);

    std::cout << "         -> OK (Cold-Start Híbrido, Transferencia Matricial Inter-Club y Cruce Inter-Liga verificados)" << std::endl;
}

void testConcurrentMultiUserQueries() {
    std::cout << "[TEST 4] Verificando concurrencia multi-usuario (ThreadPool con 100 consultas paralelas Multi-Liga)..." << std::endl;

    auto repo = std::make_shared<repository::SqliteRepository>("data/lpf_analytics.db");
    auto engine = std::make_shared<services::AnalyticsEngineService>(repo, 1.35);
    concurrency::ThreadPool pool(12);

    std::atomic<int> successCount{0};
    const int totalQueries = 100;

    for (int i = 0; i < totalQueries; ++i) {
        pool.enqueue([&engine, &successCount, i]() {
            if (i % 4 == 0) {
                auto r = engine->optimizeLineup(7272, "OFFENSIVE", "4-3-3");
                if (r.optimalStarters.size() == 11) successCount.fetch_add(1);
            } else if (i % 4 == 1) {
                auto r = engine->predictMatchup(131, 868); // Real Madrid vs River Plate
                if (r.expectedGoalsA > 0.0 && r.expectedGoalsB > 0.0) successCount.fetch_add(1);
            } else if (i % 4 == 2) {
                auto r = engine->simulateTransfer(866, 110, 90316); // Haaland -> Boca Juniors
                if (r.postTransferStarters.size() == 11) successCount.fetch_add(1);
            } else {
                auto r = engine->optimizeLineup(132, "POSSESSION", "4-3-3"); // FC Barcelona
                if (r.optimalStarters.size() == 11) successCount.fetch_add(1);
            }
        });
    }

    pool.shutdown();
    assert(successCount.load() == totalQueries);
    std::cout << "         -> OK (" << successCount.load() << "/" << totalQueries
              << " consultas concurrentes completadas sin condiciones de carrera)" << std::endl;
}

int main() {
    std::cout << "=====================================================================" << std::endl;
    std::cout << " SUITE DE PRUEBAS C++17 - MOTOR MULTI-LIGA Y COLD-START HIBRIDO" << std::endl;
    std::cout << "=====================================================================" << std::endl;
    testMatrixOperations();
    testDatabaseAndLinearModels();
    testColdStartNewPlayerAndTransfers();
    testConcurrentMultiUserQueries();
    std::cout << "=====================================================================" << std::endl;
    std::cout << " TODAS LAS PRUEBAS DE BACKEND C++ PASARON EXITOSAMENTE" << std::endl;
    std::cout << "=====================================================================" << std::endl;
    return 0;
}
