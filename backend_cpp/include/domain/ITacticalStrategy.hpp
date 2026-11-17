#ifndef ALGEBRA_LINEAL_ITACTICAL_STRATEGY_HPP
#define ALGEBRA_LINEAL_ITACTICAL_STRATEGY_HPP

#include "domain/Models.hpp"
#include <string>
#include <array>
#include <memory>

namespace lpf::domain {

/**
 * @class ITacticalStrategy
 * @brief Interfaz polimórfica (Patrón Strategy - GoF / Pressman) que define una
 *        funcional lineal J: R^20 -> R sobre el espacio vectorial de métricas de partido.
 *
 * Por linealidad de la composición:
 *   J(T_E(x)) = w^T * (A_E * x) = (w^T * A_E) * x = sum_{j=1}^n s_j * x_j
 * donde s_j = (w^T * A_E)_j es el aporte táctico escalar del jugador j.
 */
class ITacticalStrategy {
public:
    virtual ~ITacticalStrategy() = default;

    virtual std::string code() const = 0;
    virtual std::string name() const = 0;
    virtual std::string description() const = 0;

    /**
     * @brief Devuelve el vector de pesos normalizados w in R^20 de la funcional lineal.
     */
    virtual std::array<double, NUM_METRICS> weights() const = 0;

    /**
     * @brief Evalúa el funcional lineal J(y) = w^T * y para un vector de 20 métricas.
     */
    double evaluate(const MatchMetrics& metrics) const {
        const auto w = weights();
        double score = 0.0;
        for (std::size_t i = 0; i < NUM_METRICS; ++i) {
            score += w[i] * metrics[i];
        }
        return score;
    }

    double evaluateVector(const std::vector<double>& y) const {
        const auto w = weights();
        double score = 0.0;
        for (std::size_t i = 0; i < std::min(y.size(), NUM_METRICS); ++i) {
            score += w[i] * y[i];
        }
        return score;
    }
};

} // namespace lpf::domain

#endif // ALGEBRA_LINEAL_ITACTICAL_STRATEGY_HPP
