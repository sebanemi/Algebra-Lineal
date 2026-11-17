#ifndef ALGEBRA_LINEAL_TACTICAL_STRATEGIES_HPP
#define ALGEBRA_LINEAL_TACTICAL_STRATEGIES_HPP

#include "domain/ITacticalStrategy.hpp"
#include <algorithm>
#include <cctype>

namespace lpf::domain {

/**
 * @class BalancedStrategy
 * @brief Estrategia equilibrada general: maximiza diferencial de goles, valoración colectiva,
 *        generación ofensiva, solidez defensiva y disciplina táctica.
 */
class BalancedStrategy final : public ITacticalStrategy {
public:
    std::string code() const override { return "BALANCED"; }
    std::string name() const override { return "Balanceado General"; }
    std::string description() const override {
        return "Equilibrio integral entre eficacia ofensiva, solidez defensiva, control de balón y valoración.";
    }

    std::array<double, NUM_METRICS> weights() const override {
        std::array<double, NUM_METRICS> w{};
        w[GOLES_A_FAVOR]       =  14.0;
        w[GOLES_EN_CONTRA]     = -13.0;
        w[TIROS_AL_ARCO]       =   3.2;
        w[POSESION]            =   0.35;
        w[SALVADAS]            =   1.5;
        w[CORNERS]             =   1.1;
        w[FALTAS]              =  -0.45;
        w[PASES]               =   0.045;
        w[TIROS_LIBRES]        =   0.30;
        w[AMARILLAS]           =  -1.8;
        w[ROJAS]               = -12.0;
        w[PROMEDIO_VALORACION] =  12.0;
        w[TIROS]               =   1.1;
        w[TOQUES_AREA_RIVAL]   =   0.95;
        w[OFFSIDES]            =  -0.50;
        w[TACLES]              =   0.85;
        w[INTERCEPCIONES]      =   0.95;
        w[RECUPERACIONES]      =   0.35;
        w[DESPEJES]            =   0.25;
        w[TIROS_RIVAL_AL_ARCO] =  -2.8;
        return w;
    }
};

/**
 * @class OffensiveStrategy
 * @brief Prioriza volumen de ataque, goles a favor, remates al arco y presencia en área rival.
 */
class OffensiveStrategy final : public ITacticalStrategy {
public:
    std::string code() const override { return "OFFENSIVE"; }
    std::string name() const override { return "Ofensivo / Vertical"; }
    std::string description() const override {
        return "Prioriza goles a favor, tiros al arco, toques en el área rival, córners y remates totales.";
    }

    std::array<double, NUM_METRICS> weights() const override {
        std::array<double, NUM_METRICS> w{};
        w[GOLES_A_FAVOR]       =  22.0;
        w[GOLES_EN_CONTRA]     =  -7.5;
        w[TIROS_AL_ARCO]       =   5.5;
        w[POSESION]            =   0.40;
        w[SALVADAS]            =   0.80;
        w[CORNERS]             =   2.0;
        w[FALTAS]              =  -0.25;
        w[PASES]               =   0.04;
        w[TIROS_LIBRES]        =   0.55;
        w[AMARILLAS]           =  -1.2;
        w[ROJAS]               = -10.0;
        w[PROMEDIO_VALORACION] =  10.0;
        w[TIROS]               =   2.2;
        w[TOQUES_AREA_RIVAL]   =   1.85;
        w[OFFSIDES]            =  -0.30;
        w[TACLES]              =   0.45;
        w[INTERCEPCIONES]      =   0.50;
        w[RECUPERACIONES]      =   0.30;
        w[DESPEJES]            =   0.10;
        w[TIROS_RIVAL_AL_ARCO] =  -1.5;
        return w;
    }
};

/**
 * @class DefensiveStrategy
 * @brief Prioriza valla invicta, reducción de tiros rivales, recuperaciones, intercepciones y despejes.
 */
class DefensiveStrategy final : public ITacticalStrategy {
public:
    std::string code() const override { return "DEFENSIVE"; }
    std::string name() const override { return "Defensivo / Bloque Sólido"; }
    std::string description() const override {
        return "Minimiza goles en contra y tiros rivales al arco; maximiza recuperaciones, tacles, intercepciones y despejes.";
    }

    std::array<double, NUM_METRICS> weights() const override {
        std::array<double, NUM_METRICS> w{};
        w[GOLES_A_FAVOR]       =   9.0;
        w[GOLES_EN_CONTRA]     = -22.0;
        w[TIROS_AL_ARCO]       =   2.0;
        w[POSESION]            =   0.20;
        w[SALVADAS]            =   2.8;
        w[CORNERS]             =   0.6;
        w[FALTAS]              =  -0.60;
        w[PASES]               =   0.03;
        w[TIROS_LIBRES]        =   0.20;
        w[AMARILLAS]           =  -2.2;
        w[ROJAS]               = -15.0;
        w[PROMEDIO_VALORACION] =  11.0;
        w[TIROS]               =   0.6;
        w[TOQUES_AREA_RIVAL]   =   0.5;
        w[OFFSIDES]            =  -0.4;
        w[TACLES]              =   1.75;
        w[INTERCEPCIONES]      =   1.90;
        w[RECUPERACIONES]      =   0.70;
        w[DESPEJES]            =   0.65;
        w[TIROS_RIVAL_AL_ARCO] =  -5.2;
        return w;
    }
};

/**
 * @class PossessionStrategy
 * @brief Prioriza tenencia de balón, volumen de pases, valoración técnica y control del partido.
 */
class PossessionStrategy final : public ITacticalStrategy {
public:
    std::string code() const override { return "POSSESSION"; }
    std::string name() const override { return "Posesión y Asociativo"; }
    std::string description() const override {
        return "Maximiza porcentaje de posesión, volumen de pases, valoración promedio y recuperaciones altas.";
    }

    std::array<double, NUM_METRICS> weights() const override {
        std::array<double, NUM_METRICS> w{};
        w[GOLES_A_FAVOR]       =  12.0;
        w[GOLES_EN_CONTRA]     = -11.0;
        w[TIROS_AL_ARCO]       =   3.0;
        w[POSESION]            =   1.10;
        w[SALVADAS]            =   1.0;
        w[CORNERS]             =   1.3;
        w[FALTAS]              =  -0.75;
        w[PASES]               =   0.11;
        w[TIROS_LIBRES]        =   0.45;
        w[AMARILLAS]           =  -2.0;
        w[ROJAS]               = -12.0;
        w[PROMEDIO_VALORACION] =  14.0;
        w[TIROS]               =   1.2;
        w[TOQUES_AREA_RIVAL]   =   1.15;
        w[OFFSIDES]            =  -0.60;
        w[TACLES]              =   0.80;
        w[INTERCEPCIONES]      =   0.90;
        w[RECUPERACIONES]      =   0.55;
        w[DESPEJES]            =   0.15;
        w[TIROS_RIVAL_AL_ARCO] =  -2.5;
        return w;
    }
};

/**
 * @class TacticalStrategyFactory
 * @brief Patrón Factory para instanciar polimórficamente estrategias tácticas.
 */
class TacticalStrategyFactory {
public:
    static std::unique_ptr<ITacticalStrategy> create(const std::string& codeOrName) {
        std::string key = codeOrName;
        std::transform(key.begin(), key.end(), key.begin(),
                       [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        if (key.find("OFEN") != std::string::npos || key == "OFFENSIVE") {
            return std::make_unique<OffensiveStrategy>();
        }
        if (key.find("DEF") != std::string::npos || key == "DEFENSIVE") {
            return std::make_unique<DefensiveStrategy>();
        }
        if (key.find("POS") != std::string::npos || key == "POSSESSION") {
            return std::make_unique<PossessionStrategy>();
        }
        return std::make_unique<BalancedStrategy>();
    }
};

} // namespace lpf::domain

#endif // ALGEBRA_LINEAL_TACTICAL_STRATEGIES_HPP
