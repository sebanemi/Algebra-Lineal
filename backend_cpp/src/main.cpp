#include "repository/SqliteRepository.hpp"
#include "services/AnalyticsEngineService.hpp"
#include "server/HttpJsonServer.hpp"

#include <iostream>
#include <string>
#include <memory>

int main(int argc, char* argv[]) {
    std::string dbPath = "data/lpf_analytics.db";
    int port = 8085;
    std::size_t threads = 12;
    std::string directQuery;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--db" && i + 1 < argc) {
            dbPath = argv[++i];
        } else if (arg == "--port" && i + 1 < argc) {
            port = std::stoi(argv[++i]);
        } else if (arg == "--threads" && i + 1 < argc) {
            threads = static_cast<std::size_t>(std::stoul(argv[++i]));
        } else if (arg == "--query" && i + 1 < argc) {
            directQuery = argv[++i];
        }
    }

    try {
        auto repo = std::make_shared<lpf::repository::SqliteRepository>(dbPath);
        auto engine = std::make_shared<lpf::services::AnalyticsEngineService>(repo, 1.35);
        lpf::server::HttpJsonServer server(engine, port, threads);

        if (!directQuery.empty()) {
            std::string path = directQuery;
            std::string qStr;
            std::size_t qPos = directQuery.find('?');
            if (qPos != std::string::npos) {
                path = directQuery.substr(0, qPos);
                qStr = directQuery.substr(qPos + 1);
            }
            auto params = lpf::server::JsonSerializer::parseKeyValueParams(qStr);
            int statusCode = 200;
            std::cout << server.dispatchRequest(path, params, statusCode) << std::endl;
            return (statusCode == 200) ? 0 : 1;
        }

        server.startBlocking();
    } catch (const std::exception& ex) {
        std::cerr << "{\"status\":\"error\",\"message\":\"" << ex.what() << "\"}" << std::endl;
        return 1;
    }

    return 0;
}
