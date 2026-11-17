#ifndef ALGEBRA_LINEAL_HTTP_JSON_SERVER_HPP
#define ALGEBRA_LINEAL_HTTP_JSON_SERVER_HPP

#include "concurrency/ThreadPool.hpp"
#include "services/AnalyticsEngineService.hpp"
#include "server/JsonSerializer.hpp"

#include <string>
#include <memory>
#include <atomic>
#include <iostream>
#include <sstream>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#ifdef _MSC_VER
#pragma comment(lib, "ws2_32.lib")
#endif
using SocketType = SOCKET;
constexpr SocketType INVALID_SOCKET_VAL = INVALID_SOCKET;
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
using SocketType = int;
constexpr SocketType INVALID_SOCKET_VAL = -1;
#endif

namespace lpf::server {

/**
 * @class HttpJsonServer
 * @brief Servidor HTTP/JSON concurrente en C++ apoyado en ThreadPool.
 *        Separa completamente el Backend (lógica de negocio y álgebra lineal en C++)
 *        del Frontend (interfaz de usuario TUI / clientes concurrentes).
 */
class HttpJsonServer {
private:
    std::shared_ptr<services::AnalyticsEngineService> engine_;
    concurrency::ThreadPool threadPool_;
    int port_;
    std::atomic<bool> running_{false};
    SocketType listenSocket_ = INVALID_SOCKET_VAL;

    static void closeSocket(SocketType s) {
#ifdef _WIN32
        closesocket(s);
#else
        close(s);
#endif
    }

public:
    HttpJsonServer(
        std::shared_ptr<services::AnalyticsEngineService> engine,
        int port = 8085,
        std::size_t workerThreads = 12
    ) : engine_(std::move(engine)), threadPool_(workerThreads), port_(port) {}

    ~HttpJsonServer() {
        stop();
    }

    void stop() {
        bool expected = true;
        if (running_.compare_exchange_strong(expected, false)) {
            if (listenSocket_ != INVALID_SOCKET_VAL) {
                closeSocket(listenSocket_);
                listenSocket_ = INVALID_SOCKET_VAL;
            }
            threadPool_.shutdown();
#ifdef _WIN32
            WSACleanup();
#endif
        }
    }

    void startBlocking() {
#ifdef _WIN32
        WSADATA wsaData;
        if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
            throw std::runtime_error("Error al inicializar Winsock2 (WSAStartup).");
        }
#endif

        listenSocket_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listenSocket_ == INVALID_SOCKET_VAL) {
            throw std::runtime_error("No se pudo crear el socket TCP del servidor C++.");
        }

        int opt = 1;
#ifdef _WIN32
        setsockopt(listenSocket_, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&opt), sizeof(opt));
#else
        setsockopt(listenSocket_, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
#endif

        sockaddr_in serverAddr{};
        serverAddr.sin_family = AF_INET;
        serverAddr.sin_addr.s_addr = htonl(INADDR_LOOPBACK); // 127.0.0.1
        serverAddr.sin_port = htons(static_cast<unsigned short>(port_));

        if (bind(listenSocket_, reinterpret_cast<sockaddr*>(&serverAddr), sizeof(serverAddr)) < 0) {
            closeSocket(listenSocket_);
            throw std::runtime_error("No se pudo enlazar (bind) el puerto TCP " + std::to_string(port_));
        }

        if (listen(listenSocket_, SOMAXCONN) < 0) {
            closeSocket(listenSocket_);
            throw std::runtime_error("Error en listen() sobre el puerto TCP " + std::to_string(port_));
        }

        running_.store(true);
        std::cout << "[C++ Backend Server] Escuchando en http://127.0.0.1:" << port_
                  << " | Worker Threads: " << threadPool_.workerCount()
                  << " | Equipos cargados: " << engine_->teamCount() << std::endl;

        while (running_.load()) {
            sockaddr_in clientAddr{};
#ifdef _WIN32
            int clientLen = sizeof(clientAddr);
#else
            socklen_t clientLen = sizeof(clientAddr);
#endif
            SocketType clientSocket = accept(
                listenSocket_,
                reinterpret_cast<sockaddr*>(&clientAddr),
                &clientLen
            );

            if (clientSocket == INVALID_SOCKET_VAL) {
                if (!running_.load()) break;
                continue;
            }

            threadPool_.enqueue([this, clientSocket]() {
                this->handleClientConnection(clientSocket);
            });
        }
    }

    /**
     * @brief Procesa una petición de ruta y parámetros y devuelve el cuerpo JSON.
     *        También puede invocarse directamente en modo CLI / pruebas unitarias.
     */
    std::string dispatchRequest(
        const std::string& path,
        const std::unordered_map<std::string, std::string>& params,
        int& outStatusCode
    ) {
        outStatusCode = 200;
        try {
            if (path == "/api/status") {
                std::ostringstream oss;
                oss << "{\"status\":\"ok\","
                    << "\"engine\":\"LPF Linear Transformation C++17 Backend\","
                    << "\"worker_threads\":" << threadPool_.workerCount() << ","
                    << "\"active_tasks\":" << threadPool_.activeTasks() << ","
                    << "\"completed_tasks\":" << threadPool_.completedTasks() << ","
                    << "\"total_compute_us\":" << threadPool_.totalMicroseconds() << ","
                    << "\"teams_loaded\":" << engine_->teamCount() << ","
                    << "\"database\":\"" << JsonSerializer::escape(engine_->repositoryInfo()) << "\"}";
                return oss.str();
            }

            if (path == "/api/teams") {
                return JsonSerializer::serializeTeamsList(engine_->getAllTeamModels());
            }

            if (path == "/api/team") {
                int teamId = getIntParam(params, "team_id", 0);
                std::string stratCode = getStrParam(params, "strategy", "BALANCED");
                auto model = engine_->getTeamModel(teamId);
                auto strat = domain::TacticalStrategyFactory::create(stratCode);
                return JsonSerializer::serializeTeamDetail(model, *strat);
            }

            if (path == "/api/predict-behavior") {
                int teamId = getIntParam(params, "team_id", 0);
                std::string playersRaw = getStrParam(params, "players", "");
                std::string stratCode = getStrParam(params, "strategy", "BALANCED");
                std::vector<int> pids = JsonSerializer::parseCsvIntList(playersRaw);
                auto res = engine_->predictBehavior(teamId, pids, stratCode);
                return JsonSerializer::serializeBehaviorPrediction(res);
            }

            if (path == "/api/optimize-lineup") {
                int teamId = getIntParam(params, "team_id", 0);
                std::string stratCode = getStrParam(params, "strategy", "BALANCED");
                std::string formation = getStrParam(params, "formation", "AUTO");
                auto res = engine_->optimizeLineup(teamId, stratCode, formation);
                return JsonSerializer::serializeOptimalLineup(res);
            }

            if (path == "/api/predict-matchup") {
                int teamAId = getIntParam(params, "team_a_id", 0);
                int teamBId = getIntParam(params, "team_b_id", 0);
                std::vector<int> pidsA = JsonSerializer::parseCsvIntList(getStrParam(params, "players_a", ""));
                std::vector<int> pidsB = JsonSerializer::parseCsvIntList(getStrParam(params, "players_b", ""));
                std::string stratCode = getStrParam(params, "strategy", "BALANCED");
                std::string formA = getStrParam(params, "formation_a", "AUTO");
                std::string formB = getStrParam(params, "formation_b", "AUTO");
                bool neutral = (getIntParam(params, "neutral", 0) != 0);

                auto res = engine_->predictMatchup(
                    teamAId, teamBId, pidsA, pidsB, stratCode, formA, formB, neutral
                );
                return JsonSerializer::serializeMatchupPrediction(res);
            }

            if (path == "/api/reload") {
                engine_->reloadModels();
                return "{\"status\":\"ok\",\"message\":\"Modelos recargados exitosamente desde SQLite3.\"}";
            }

            outStatusCode = 404;
            return "{\"status\":\"error\",\"message\":\"Endpoint no encontrado: " + JsonSerializer::escape(path) + "\"}";
        } catch (const std::exception& ex) {
            outStatusCode = 400;
            return "{\"status\":\"error\",\"message\":\"" + JsonSerializer::escape(ex.what()) + "\"}";
        }
    }

private:
    static int getIntParam(
        const std::unordered_map<std::string, std::string>& params,
        const std::string& key,
        int defaultVal
    ) {
        auto it = params.find(key);
        if (it == params.end() || it->second.empty()) return defaultVal;
        try {
            return std::stoi(it->second);
        } catch (...) {
            return defaultVal;
        }
    }

    static std::string getStrParam(
        const std::unordered_map<std::string, std::string>& params,
        const std::string& key,
        const std::string& defaultVal
    ) {
        auto it = params.find(key);
        return (it != params.end() && !it->second.empty()) ? it->second : defaultVal;
    }

    void handleClientConnection(SocketType clientSocket) {
        std::string rawRequest;
        char buffer[8192];
        while (true) {
            int bytesRead = recv(clientSocket, buffer, sizeof(buffer) - 1, 0);
            if (bytesRead <= 0) break;
            buffer[bytesRead] = '\0';
            rawRequest.append(buffer, static_cast<std::size_t>(bytesRead));
            if (rawRequest.find("\r\n\r\n") != std::string::npos) {
                break;
            }
        }

        if (rawRequest.empty()) {
            closeSocket(clientSocket);
            return;
        }

        // Extraer primera línea HTTP (ej: "GET /api/teams?x=1 HTTP/1.1")
        std::istringstream iss(rawRequest);
        std::string method, target, httpVersion;
        iss >> method >> target >> httpVersion;

        std::string path = target;
        std::string queryStr;
        std::size_t qPos = target.find('?');
        if (qPos != std::string::npos) {
            path = target.substr(0, qPos);
            queryStr = target.substr(qPos + 1);
        }

        // Si hay cuerpo POST urlencoded, combinarlo con queryStr
        std::size_t headerEnd = rawRequest.find("\r\n\r\n");
        if (headerEnd != std::string::npos && headerEnd + 4 < rawRequest.size()) {
            std::string body = rawRequest.substr(headerEnd + 4);
            if (!body.empty()) {
                if (!queryStr.empty()) queryStr += "&";
                queryStr += body;
            }
        }

        auto params = JsonSerializer::parseKeyValueParams(queryStr);
        int statusCode = 200;
        std::string jsonBody = dispatchRequest(path, params, statusCode);

        std::string statusText = (statusCode == 200) ? "200 OK"
                               : (statusCode == 404) ? "404 Not Found"
                                                     : "400 Bad Request";

        std::ostringstream resp;
        resp << "HTTP/1.1 " << statusText << "\r\n"
             << "Content-Type: application/json; charset=utf-8\r\n"
             << "Access-Control-Allow-Origin: *\r\n"
             << "Connection: close\r\n"
             << "Content-Length: " << jsonBody.size() << "\r\n\r\n"
             << jsonBody;

        std::string wire = resp.str();
        std::size_t totalSent = 0;
        while (totalSent < wire.size()) {
            int sent = send(
                clientSocket,
                wire.data() + totalSent,
                static_cast<int>(wire.size() - totalSent),
                0
            );
            if (sent <= 0) break;
            totalSent += static_cast<std::size_t>(sent);
        }

        closeSocket(clientSocket);
    }
};

} // namespace lpf::server

#endif // ALGEBRA_LINEAL_HTTP_JSON_SERVER_HPP
