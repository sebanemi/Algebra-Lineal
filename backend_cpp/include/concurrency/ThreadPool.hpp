#ifndef ALGEBRA_LINEAL_THREAD_POOL_HPP
#define ALGEBRA_LINEAL_THREAD_POOL_HPP

#include <vector>
#include <queue>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <functional>
#include <atomic>
#include <chrono>

namespace lpf::concurrency {

/**
 * @class ThreadPool
 * @brief Pool de hilos concurrentes (Patrón Thread Pool / Reactor Worker) para
 *        atender múltiples usuarios y consultas simultáneas en C++.
 */
class ThreadPool {
private:
    std::vector<std::thread> workers_;
    std::queue<std::function<void()>> tasks_;
    mutable std::mutex queueMutex_;
    std::condition_variable condition_;
    std::atomic<bool> stop_{false};
    std::atomic<std::uint64_t> activeTasks_{0};
    std::atomic<std::uint64_t> completedTasks_{0};
    std::atomic<std::uint64_t> totalMicroseconds_{0};

public:
    explicit ThreadPool(std::size_t numThreads = 0) {
        if (numThreads == 0) {
            numThreads = std::max<std::size_t>(8, std::thread::hardware_concurrency());
        }
        workers_.reserve(numThreads);
        for (std::size_t i = 0; i < numThreads; ++i) {
            workers_.emplace_back([this]() {
                while (true) {
                    std::function<void()> task;
                    {
                        std::unique_lock<std::mutex> lock(this->queueMutex_);
                        this->condition_.wait(lock, [this]() {
                            return this->stop_.load() || !this->tasks_.empty();
                        });
                        if (this->stop_.load() && this->tasks_.empty()) {
                            return;
                        }
                        task = std::move(this->tasks_.front());
                        this->tasks_.pop();
                    }

                    activeTasks_.fetch_add(1, std::memory_order_relaxed);
                    auto t0 = std::chrono::steady_clock::now();
                    try {
                        task();
                    } catch (...) {
                        // Aislamiento de fallos por hilo trabajador
                    }
                    auto t1 = std::chrono::steady_clock::now();
                    auto us = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
                    totalMicroseconds_.fetch_add(static_cast<std::uint64_t>(us), std::memory_order_relaxed);
                    activeTasks_.fetch_sub(1, std::memory_order_relaxed);
                    completedTasks_.fetch_add(1, std::memory_order_relaxed);
                }
            });
        }
    }

    ~ThreadPool() {
        shutdown();
    }

    void enqueue(std::function<void()> task) {
        {
            std::unique_lock<std::mutex> lock(queueMutex_);
            if (stop_.load()) return;
            tasks_.push(std::move(task));
        }
        condition_.notify_one();
    }

    void shutdown() {
        bool expected = false;
        if (stop_.compare_exchange_strong(expected, true)) {
            condition_.notify_all();
            for (std::thread& worker : workers_) {
                if (worker.joinable()) {
                    worker.join();
                }
            }
        }
    }

    std::size_t workerCount() const noexcept { return workers_.size(); }
    std::uint64_t activeTasks() const noexcept { return activeTasks_.load(std::memory_order_relaxed); }
    std::uint64_t completedTasks() const noexcept { return completedTasks_.load(std::memory_order_relaxed); }
    std::uint64_t totalMicroseconds() const noexcept { return totalMicroseconds_.load(std::memory_order_relaxed); }
};

} // namespace lpf::concurrency

#endif // ALGEBRA_LINEAL_THREAD_POOL_HPP
