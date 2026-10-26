#pragma once
// bind_helpers.hpp — shared helpers for src/core.cpp and src/generated_bindings.cpp.
//
// TrexioHandle: RAII wrapper around trexio_t*. nanobind invokes the C++
// destructor of a bound type when the Python object is released / GC'd, so
// the still-open file is closed on GC -- dropping the handle can never leak.
// An explicit trexio_close() nulls the pointer first and becomes a no-op
// (closes exactly once).
//
// cast_vec / vec_list: sequence conversion between Python iterables (list,
// tuple, or numpy array, including nested / multi-dimensional for drop-in
// compatibility with the SWIG bindings). Sparse arrays bypass these helpers:
// they use typed nb::ndarray views and capsule-owned NumPy output buffers.
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <trexio.h>

#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace nb = nanobind;

class TrexioHandle {
 public:
  explicit TrexioHandle(trexio_t* f) noexcept : ptr_(f) {}
  TrexioHandle(TrexioHandle&& o) noexcept : ptr_(o.ptr_) { o.ptr_ = nullptr; }
  TrexioHandle(const TrexioHandle&) = delete;
  TrexioHandle& operator=(const TrexioHandle&) = delete;
  ~TrexioHandle() { close(); }

  bool open() const noexcept { return ptr_ != nullptr; }
  trexio_t* raw() const noexcept { return ptr_; }

  // int by contract: 0 = closed successfully, -1 (TREXIO_FAILURE) = already
  // closed/invalid.
  int close() noexcept {
    if (!ptr_) return static_cast<int>(TREXIO_FAILURE);
    int rc = static_cast<int>(trexio_close(ptr_));
    ptr_ = nullptr;
    return rc;
  }

  int back_end() const {
    back_end_t be = TREXIO_INVALID_BACK_END;
    if (ptr_ && trexio_get_back_end(ptr_, &be) == TREXIO_SUCCESS)
      return static_cast<int>(be);
    return static_cast<int>(TREXIO_FAILURE);
  }

 private:
  trexio_t* ptr_ = nullptr;
};

namespace {
template <typename T>
size_t checked_array_size(int64_t size, const char* name) {
  if (size < 0)
    throw nb::value_error((std::string(name) + " must be non-negative").c_str());

  const auto usize = static_cast<uint64_t>(size);
  if (usize > static_cast<uint64_t>(std::numeric_limits<size_t>::max() / sizeof(T)))
    throw std::overflow_error(std::string(name) + " is too large");
  return static_cast<size_t>(usize);
}

template <typename T>
nb::object owned_numpy_1d(std::unique_ptr<T[]> data, size_t size) {
  T* ptr = data.get();
  nb::capsule owner(ptr, [](void* p) noexcept { delete[] static_cast<T*>(p); });
  data.release();
  return nb::ndarray<nb::numpy, T, nb::ndim<1>, nb::c_contig, nb::device::cpu>(
             ptr, {size}, owner)
      .cast();
}

inline int64_t checked_ndarray_size(size_t size, const char* name) {
  if (size > static_cast<size_t>(std::numeric_limits<int64_t>::max()))
    throw std::overflow_error(std::string(name) + " is too large");
  return static_cast<int64_t>(size);
}

// Recursively flatten an iterable of Python scalars into a C vector of T.
// Nested lists / multi-dimensional numpy arrays are supported (the SWIG
// bindings flattened them too).
//
// NOTE: the sequence must NOT be iterated through nanobind's generic object
// iterator (`for (auto& y : nb_object)`) -- under nanobind 3.1.0 on aarch64
// its `iterator::operator++` corrupts the cached element and jumps to NULL.
// `nb::list` (PySequence_List) + index-based list iteration is safe.
template <typename T>
void append_vec(std::vector<T>& v, const nb::object& x) {
  bool scalar = false;
  if constexpr (std::is_floating_point_v<T>) {
    try { v.push_back(static_cast<T>(nb::cast<double>(x))); scalar = true; }
    catch (nb::python_error&) { /* not a number -- fall through */ }
    catch (std::bad_cast&) { /* not a number -- fall through */ }
  } else {
    try { v.push_back(static_cast<T>(nb::cast<std::int64_t>(x))); scalar = true; }
    catch (nb::python_error&) { /* not a number -- fall through */ }
    catch (std::bad_cast&) { /* not a number -- fall through */ }
  }
  if (scalar) return;
  const nb::list l(x);  // any sequence/iterable -> plain list (raises if neither)
  for (const auto& y : l) append_vec<T>(v, nb::borrow(y));
}

template <typename T>
std::vector<T> cast_vec(const nb::object& o) {
  std::vector<T> v;
  if (o.is_valid()) append_vec<T>(v, o);
  return v;
}

// Vector of T -> a fresh Python list (lists are the ABI container).
template <typename T>
nb::object vec_list(const std::vector<T>& v) {
  nb::list l;
  for (auto x : v) {
    if constexpr (std::is_floating_point_v<T>)
      l.append(nb::float_(x));
    else
      l.append(nb::int_(x));
  }
  return l;
}
}  // namespace
