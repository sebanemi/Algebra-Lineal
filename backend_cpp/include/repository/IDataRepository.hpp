#ifndef ALGEBRA_LINEAL_IDATA_REPOSITORY_HPP
#define ALGEBRA_LINEAL_IDATA_REPOSITORY_HPP

#include "domain/Models.hpp"
#include <vector>
#include <optional>
#include <string>

namespace lpf::repository {

/**
 * @class IDataRepository
 * @brief Interfaz abstracta de acceso a datos (Patrón Repository / DAO - Pressman & Ghezzi).
 *        Desacopla completamente el motor matemático y de negocio en C++ del motor físico
 *        de persistencia (SQLite3 en modo WAL).
 */
class IDataRepository {
public:
    virtual ~IDataRepository() = default;

    /**
     * @brief Carga todos los equipos con sus planteles completos, partidos y alineaciones.
     */
    virtual std::vector<domain::Team> loadAllTeams() = 0;

    /**
     * @brief Carga un equipo específico por su ID.
     */
    virtual std::optional<domain::Team> loadTeamById(int teamId) = 0;

    /**
     * @brief Devuelve la ruta o identificador de conexión activa.
     */
    virtual std::string connectionInfo() const = 0;
};

} // namespace lpf::repository

#endif // ALGEBRA_LINEAL_IDATA_REPOSITORY_HPP
