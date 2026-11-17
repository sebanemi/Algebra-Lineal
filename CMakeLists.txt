cmake_minimum_required(VERSION 3.16)
project(LPF_LinearAlgebra_System VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(backend_cpp/include)

add_executable(lpf_backend
    backend_cpp/src/main.cpp
)

add_executable(test_linear_algebra
    backend_cpp/tests/test_linear_algebra.cpp
)

if(WIN32)
    target_link_libraries(lpf_backend PRIVATE ws2_32)
    target_link_libraries(test_linear_algebra PRIVATE ws2_32)
endif()
