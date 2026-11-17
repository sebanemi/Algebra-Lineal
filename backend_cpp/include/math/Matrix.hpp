#ifndef ALGEBRA_LINEAL_MATRIX_HPP
#define ALGEBRA_LINEAL_MATRIX_HPP

#include <vector>
#include <cmath>
#include <stdexcept>
#include <iomanip>
#include <sstream>
#include <algorithm>

namespace lpf::math {

/**
 * @class Matrix
 * @brief Representa una matriz densa en R^(rows x cols) y provee operaciones de Álgebra Lineal
 *        para Transformaciones Lineales, Inversión Gauss-Jordan y Pseudoinversa de Moore-Penrose.
 *
 * Diseño Orientado a Objetos (Pressman / Ghezzi):
 * - Encapsulamiento estricto del almacenamiento contiguo en memoria (row-major).
 * - Inmutabilidad opcional en operaciones algebraicas (devuelven nuevas instancias).
 * - Manejo robusto de singularidades numéricas mediante pivoteo parcial escalado y
 *   regularización de Tikhonov (Ridge) para sistemas subdeterminados/sobredeterminados.
 */
class Matrix {
private:
    std::size_t rows_;
    std::size_t cols_;
    std::vector<double> data_;

public:
    Matrix() : rows_(0), cols_(0), data_() {}

    Matrix(std::size_t rows, std::size_t cols, double initialValue = 0.0)
        : rows_(rows), cols_(cols), data_(rows * cols, initialValue) {}

    Matrix(std::size_t rows, std::size_t cols, const std::vector<double>& values)
        : rows_(rows), cols_(cols), data_(values) {
        if (values.size() != rows * cols) {
            throw std::invalid_argument("Dimensiones incompatibles al inicializar Matrix.");
        }
    }

    std::size_t rows() const noexcept { return rows_; }
    std::size_t cols() const noexcept { return cols_; }
    bool empty() const noexcept { return rows_ == 0 || cols_ == 0; }

    double& operator()(std::size_t r, std::size_t c) {
        if (r >= rows_ || c >= cols_) {
            throw std::out_of_range("Índice de Matrix fuera de rango.");
        }
        return data_[r * cols_ + c];
    }

    double operator()(std::size_t r, std::size_t c) const {
        if (r >= rows_ || c >= cols_) {
            throw std::out_of_range("Índice de Matrix fuera de rango.");
        }
        return data_[r * cols_ + c];
    }

    const std::vector<double>& rawData() const noexcept { return data_; }

    static Matrix identity(std::size_t n) {
        Matrix I(n, n, 0.0);
        for (std::size_t i = 0; i < n; ++i) {
            I(i, i) = 1.0;
        }
        return I;
    }

    Matrix transpose() const {
        Matrix result(cols_, rows_, 0.0);
        for (std::size_t r = 0; r < rows_; ++r) {
            for (std::size_t c = 0; c < cols_; ++c) {
                result(c, r) = (*this)(r, c);
            }
        }
        return result;
    }

    Matrix operator+(const Matrix& other) const {
        if (rows_ != other.rows_ || cols_ != other.cols_) {
            throw std::invalid_argument("Dimensiones incompatibles en suma de matrices.");
        }
        Matrix result(rows_, cols_, 0.0);
        for (std::size_t i = 0; i < data_.size(); ++i) {
            result.data_[i] = data_[i] + other.data_[i];
        }
        return result;
    }

    Matrix operator-(const Matrix& other) const {
        if (rows_ != other.rows_ || cols_ != other.cols_) {
            throw std::invalid_argument("Dimensiones incompatibles en resta de matrices.");
        }
        Matrix result(rows_, cols_, 0.0);
        for (std::size_t i = 0; i < data_.size(); ++i) {
            result.data_[i] = data_[i] - other.data_[i];
        }
        return result;
    }

    Matrix operator*(double scalar) const {
        Matrix result(rows_, cols_, 0.0);
        for (std::size_t i = 0; i < data_.size(); ++i) {
            result.data_[i] = data_[i] * scalar;
        }
        return result;
    }

    Matrix operator*(const Matrix& other) const {
        if (cols_ != other.rows_) {
            throw std::invalid_argument("Dimensiones incompatibles en multiplicación de matrices.");
        }
        Matrix result(rows_, other.cols_, 0.0);
        for (std::size_t i = 0; i < rows_; ++i) {
            for (std::size_t k = 0; k < cols_; ++k) {
                double aik = (*this)(i, k);
                if (std::abs(aik) < 1e-15) continue;
                for (std::size_t j = 0; j < other.cols_; ++j) {
                    result(i, j) += aik * other(k, j);
                }
            }
        }
        return result;
    }

    /**
     * @brief Aplica la transformación lineal y = A * x sobre un vector x in R^cols.
     */
    std::vector<double> multiplyVector(const std::vector<double>& x) const {
        if (x.size() != cols_) {
            throw std::invalid_argument("Dimensión de vector incompatible con columnas de la matriz.");
        }
        std::vector<double> y(rows_, 0.0);
        for (std::size_t i = 0; i < rows_; ++i) {
            double sum = 0.0;
            for (std::size_t j = 0; j < cols_; ++j) {
                sum += (*this)(i, j) * x[j];
            }
            y[i] = sum;
        }
        return y;
    }

    std::vector<double> getColumn(std::size_t colIdx) const {
        if (colIdx >= cols_) {
            throw std::out_of_range("Índice de columna fuera de rango.");
        }
        std::vector<double> col(rows_, 0.0);
        for (std::size_t r = 0; r < rows_; ++r) {
            col[r] = (*this)(r, colIdx);
        }
        return col;
    }

    std::vector<double> getRow(std::size_t rowIdx) const {
        if (rowIdx >= rows_) {
            throw std::out_of_range("Índice de fila fuera de rango.");
        }
        std::vector<double> row(cols_, 0.0);
        for (std::size_t c = 0; c < cols_; ++c) {
            row[c] = (*this)(rowIdx, c);
        }
        return row;
    }

    double frobeniusNorm() const noexcept {
        double sumSq = 0.0;
        for (double v : data_) {
            sumSq += v * v;
        }
        return std::sqrt(sumSq);
    }

    /**
     * @brief Calcula la matriz inversa A^(-1) mediante eliminación de Gauss-Jordan
     *        con pivoteo parcial.
     */
    Matrix inverse() const {
        if (rows_ != cols_ || rows_ == 0) {
            throw std::invalid_argument("Solo se pueden invertir matrices cuadradas no vacías.");
        }
        const std::size_t n = rows_;
        Matrix aug(n, 2 * n, 0.0);
        for (std::size_t i = 0; i < n; ++i) {
            for (std::size_t j = 0; j < n; ++j) {
                aug(i, j) = (*this)(i, j);
            }
            aug(i, n + i) = 1.0;
        }

        for (std::size_t col = 0; col < n; ++col) {
            std::size_t pivotRow = col;
            double maxAbs = std::abs(aug(col, col));
            for (std::size_t r = col + 1; r < n; ++r) {
                double val = std::abs(aug(r, col));
                if (val > maxAbs) {
                    maxAbs = val;
                    pivotRow = r;
                }
            }

            if (maxAbs < 1e-12) {
                // Regularización diagonal mínima de emergencia para evitar división por cero
                aug(col, col) += 1e-8;
                pivotRow = col;
            }

            if (pivotRow != col) {
                for (std::size_t j = 0; j < 2 * n; ++j) {
                    std::swap(aug(col, j), aug(pivotRow, j));
                }
            }

            double pivotVal = aug(col, col);
            for (std::size_t j = 0; j < 2 * n; ++j) {
                aug(col, j) /= pivotVal;
            }

            for (std::size_t r = 0; r < n; ++r) {
                if (r == col) continue;
                double factor = aug(r, col);
                if (std::abs(factor) < 1e-15) continue;
                for (std::size_t j = 0; j < 2 * n; ++j) {
                    aug(r, j) -= factor * aug(col, j);
                }
            }
        }

        Matrix inv(n, n, 0.0);
        for (std::size_t i = 0; i < n; ++i) {
            for (std::size_t j = 0; j < n; ++j) {
                inv(i, j) = aug(i, n + j);
            }
        }
        return inv;
    }

    /**
     * @brief Calcula la Pseudoinversa de Moore-Penrose regularizada (Tikhonov / Ridge):
     *        X^+ = X^T * (X * X^T + lambda * I)^(-1)  si rows <= cols
     *        X^+ = (X^T * X + lambda * I)^(-1) * X^T  si rows > cols
     *
     * @param lambda Factor de amortiguamiento de Tikhonov (estabiliza colinealidades).
     * @return Matriz pseudoinversa de dimensión (cols x rows).
     */
    Matrix pseudoInverse(double lambda = 0.5) const {
        Matrix Xt = this->transpose();
        if (rows_ <= cols_) {
            Matrix XXt = (*this) * Xt;
            for (std::size_t i = 0; i < rows_; ++i) {
                XXt(i, i) += lambda;
            }
            return Xt * XXt.inverse();
        } else {
            Matrix XtX = Xt * (*this);
            for (std::size_t i = 0; i < cols_; ++i) {
                XtX(i, i) += lambda;
            }
            return XtX.inverse() * Xt;
        }
    }
};

} // namespace lpf::math

#endif // ALGEBRA_LINEAL_MATRIX_HPP
