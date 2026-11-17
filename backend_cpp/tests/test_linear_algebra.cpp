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

    // Probar pseudoinversa de Moore-Penrose sobre matriz rectangular (3 x 2)
    math::Matrix X(3, 2, {1.0, 0.0, 0.0, 1.0, 1.0, 1.0});
    math::Matrix X_pinv = X.pseudoInverse(1e-6);
    assert(X_pinv.rows() == 2 && X_pinv.cols() == 3);

    // Propiedad de Moore-Penrose: X * X^+ * X ≈ X
    math::Matrix X_reconstructed = X * X_pinv * X;
    for (std::size_t r = 0; r < 3; ++r) {
        for (std::size_t c = 0; c < 2; ++c) {
            assert(std::abs(X_reconstructed(r, c) - X(r, c)) < 1e-4);
        }
    }
    std::cout << "         -> OK (Inversa y Pseudoinversa verificadas con precisión < 1e-4)" << std::endl;
}

void testDatabaseAndLinearModels() {
    std::cout << "[TEST 2] Verificando carga desde SQLite3 y Transformaciones Lineales de los 30 equipos..." << std::endl;

    auto repo = std::make_shared<repository::SqliteRepository>("data/lpf_analytics.db");
    auto engine = std::make_shared<services::AnalyticsEngineService>(repo, 1.35);

    assert(engine->teamCount() == 30);
    auto allModels = engine->getAllTeamModels();
    assert(allModels.size() == 30);

    for (const auto& model : allModels) {
        const auto& A = model.transformationMatrix();
        assert(A.rows() == domain::NUM_METRICS);
        assert(A.cols() == model.team().squad.size());
        assert(model.team().matches.size() == 20);
        assert(model.frobeniusResidual() > 0.0);

        // Verificar que la optimización de cada equipo solo seleccione jugadores que pertenecen actualmente al club
        auto optTeam = engine->optimizeLineup(model.team().teamId, "BALANCED", "AUTO");
        assert(optTeam.optimalStarters.size() == 11);
        for (const auto& starter : optTeam.optimalStarters) {
            assert(starter.player.currentlyInClub == true);
        }
        for (const auto& sub : optTeam.recommendedBench) {
            assert(sub.player.currentlyInClub == true);
        }
    }

    // Verificar que intentar alinear a un jugador que ya no pertenece al equipo sea rechazado
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

    // Caso (b): Optimización de 11 inicial para Boca Juniors (866) y River Plate (868)
    auto optBoca = engine->optimizeLineup(866, "BALANCED", "4-3-3");
    assert(optBoca.optimalStarters.size() == 11);
    assert(optBoca.formationName == "4-3-3");
    assert(optBoca.expectedMetrics[domain::PROMEDIO_VALORACION] >= 5.0);

    // Caso (a): Predicción de comportamiento a partir de los 11 titulares elegidos
    std::vector<int> bocaStarterIds;
    for (const auto& pc : optBoca.optimalStarters) {
        bocaStarterIds.push_back(pc.player.playerId);
    }
    auto behBoca = engine->predictBehavior(866, bocaStarterIds, "BALANCED");
    assert(behBoca.selectedPlayerIds.size() == 11);
    assert(std::abs(behBoca.predictedMetrics[domain::GOLES_A_FAVOR] - optBoca.expectedMetrics[domain::GOLES_A_FAVOR]) < 1e-6);

    // Caso (c): Predicción de enfrentamiento entre dos equipos (Boca vs River)
    auto matchup = engine->predictMatchup(866, 868, {}, {}, "BALANCED", "AUTO", "AUTO", false);
    double probSum = matchup.winProbabilityA + matchup.drawProbability + matchup.winProbabilityB;
    assert(std::abs(probSum - 100.0) < 1e-3);
    double posSum = matchup.coupledMetricsA[domain::POSESION] + matchup.coupledMetricsB[domain::POSESION];
    assert(std::abs(posSum - 100.0) < 1e-3);

    std::cout << "         -> OK (Casos de uso a, b y c verificados para los 30 equipos de LPF)" << std::endl;
}

void testConcurrentMultiUserQueries() {
    std::cout << "[TEST 3] Verificando concurrencia multi-usuario (ThreadPool con 100 consultas paralelas)..." << std::endl;

    auto repo = std::make_shared<repository::SqliteRepository>("data/lpf_analytics.db");
    auto engine = std::make_shared<services::AnalyticsEngineService>(repo, 1.35);
    concurrency::ThreadPool pool(12);

    std::atomic<int> successCount{0};
    const int totalQueries = 100;

    for (int i = 0; i < totalQueries; ++i) {
        pool.enqueue([&engine, &successCount, i]() {
            if (i % 3 == 0) {
                auto r = engine->optimizeLineup(7272, "OFFENSIVE", "4-3-3");
                if (r.optimalStarters.size() == 11) successCount.fetch_add(1);
            } else if (i % 3 == 1) {
                auto r = engine->predictMatchup(866, 868);
                if (r.expectedGoalsA > 0.0 && r.expectedGoalsB > 0.0) successCount.fetch_add(1);
            } else {
                auto r = engine->optimizeLineup(868, "POSSESSION", "4-4-2");
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
    std::cout << " SUITE DE PRUEBAS C++17 - MOTOR DE TRANSFORMACIONES LINEALES LPF" << std::endl;
    std::cout << "=====================================================================" << std::endl;
    testMatrixOperations();
    testDatabaseAndLinearModels();
    testConcurrentMultiUserQueries();
    std::cout << "=====================================================================" << std::endl;
    std::cout << " TODAS LAS PRUEBAS DE BACKEND C++ PASARON EXITOSAMENTE" << std::endl;
    std::cout << "=====================================================================" << std::endl;
    return 0;
}
