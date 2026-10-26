cmake_minimum_required(VERSION 3.26...3.30)
project(trexio LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
#  Locate TREXIO -- auto-discovered (like ./configure):
#    1. pkg-config           (respects PKG_CONFIG_PATH, module-load, etc.)
#    2. find_package-style   (standard CMake convention)
#    3. Standard system paths (/usr/local, /opt)
#    4. Fallback to pkg-config + standard search
# ---------------------------------------------------------------------------

# 1) Try pkg-config first (most robust way)
find_package(PkgConfig QUIET)
if(PKG_CONFIG_FOUND)
  pkg_check_modules(PC_TREXIO QUIET trexio)
endif()

# 2) Try standard CMake find_package approach  
find_path(TREXIO_INCLUDE_DIR 
  NAMES trexio.h
  PATHS /usr/local/include /opt/include
  NO_DEFAULT_PATH
)

# 3) Look for TREXIO using the standard CMake prefix search paths
if(NOT TREXIO_INCLUDE_DIR OR NOT EXISTS "${TREXIO_INCLUDE_DIR}/trexio.h")
  find_path(TREXIO_INCLUDE_DIR NAMES trexio.h)
endif()

# If we found an include directory, determine the prefix
if(TREXIO_INCLUDE_DIR)
  get_filename_component(TREXIO_PREFIX "${TREXIO_INCLUDE_DIR}" DIRECTORY)
else()
  # Fallback - try pkg-config as last resort
  if(PC_TREXIO_FOUND AND PC_TREXIO_PREFIX)
    set(TREXIO_PREFIX "${PC_TREXIO_PREFIX}")
    set(TREXIO_INCLUDE_DIR "${PC_TREXIO_PREFIX}/include")
  else()
    message(FATAL_ERROR "TREXIO not found. Ensure the TREXIO library is installed and visible via pkg-config or standard search paths.")
  endif()
endif()

set(TREXIO_INC "${TREXIO_INCLUDE_DIR}")

# Find the library
find_library(TREXIO_LIB 
  NAMES trexio
  PATHS 
    "${TREXIO_PREFIX}/lib64" 
    "${TREXIO_PREFIX}/lib"
    /usr/local/lib64 
    /usr/local/lib
    /opt/lib64
    /opt/lib
  NO_DEFAULT_PATH)
  
if(NOT TREXIO_LIB)
  # Try standard library search as final fallback
  find_library(TREXIO_LIB NAMES trexio)
endif()

if(NOT TREXIO_LIB)
  message(FATAL_ERROR "libtrexio not found under ${TREXIO_PREFIX}")
endif()

message(STATUS "TREXIO_HOME=${TREXIO_PREFIX}  lib=${TREXIO_LIB}")

# ---------------- version + accessor guards (from installed header) -----------
file(READ "${TREXIO_INC}/trexio.h" _hdr)
if(_hdr MATCHES "TREXIO_VERSION_MAJOR[ \t]+([0-9]+)")
  set(TVMAJ ${CMAKE_MATCH_1})
endif()
if(_hdr MATCHES "TREXIO_VERSION_MINOR[ \t]+([0-9]+)")
  set(TVMIN ${CMAKE_MATCH_1})
endif()
if(_hdr MATCHES "TREXIO_VERSION_PATCH[ \t]+([0-9]+)")
  set(TVPAT ${CMAKE_MATCH_1})
endif()
message(STATUS "TREXIO ${TVMAJ}.${TVMIN}.${TVPAT}")
if(NOT (TVMAJ GREATER 2 OR (TVMAJ EQUAL 2 AND TVMIN GREATER_EQUAL 6)))
  message(FATAL_ERROR "TREXIO >= 2.6 required (found ${TVMAJ}.${TVMIN}.${TVPAT}).")
endif()
if(NOT _hdr MATCHES "trexio_get_back_end")
  message(FATAL_ERROR "Installed TREXIO lacks trexio_get_back_end() (run Plan phase 01, reinstall).")
endif()

# ---------------- nanobind module ---------------------------------------------
# Layout A (user decision 2026-10-09): the module is installed as
# site-packages/trexio/pytrexio.<tag>.so, i.e. importable as `trexio.pytrexio`.
if(SKBUILD)
  find_package(Python 3.10 REQUIRED COMPONENTS
    Interpreter Development.Module ${SKBUILD_SABI_COMPONENT})
else()
  find_package(Python 3.10 REQUIRED COMPONENTS
    Interpreter Development.Module
    OPTIONAL_COMPONENTS Development.SABIModule)
endif()
find_package(nanobind CONFIG REQUIRED)
# core.cpp = hand-written core (lifecycle/state/NAO/bitfield/delete);
# generated_bindings.cpp = per-variable has/read/write (tools/gen_binding.py).
# Split mode keeps the extension on the CPython Stable ABI; the version-specific
# nanobind backend is supplied by the nanobind-backend runtime dependency.
nanobind_add_module(pytrexio src/core.cpp src/generated_bindings.cpp
  BACKEND_MODULE nanobind_backend)
target_include_directories(pytrexio PRIVATE ${TREXIO_INC})
target_compile_definitions(pytrexio PRIVATE TREXIO_PKG_VERSION="${TVMAJ}.${TVMIN}.${TVPAT}")
target_link_libraries(pytrexio PRIVATE ${TREXIO_LIB})
set_target_properties(pytrexio PROPERTIES OUTPUT_NAME "pytrexio")

# ---------------- the trexio/ wheel package ------------------------------------
# Layout A: site-packages/trexio/{__init__.py, _version.py, pytrexio.<tag>.so}.
# api/trexio/ is the tracked package source (symlinks to the canonical vendored
# files -- no content duplication; see api/trexio/__init__.py -> ../trexio.py).
# scikit-build-core (wheel.packages = ["api/trexio"]) copies it into the wheel
# as "trexio/" (prefix stripped).  Its default include mode respects .gitignore,
# so the package dir is tracked; only the compiled module overlay is ignored.
set(WHEEL_PKG "${CMAKE_SOURCE_DIR}/api/trexio")
add_custom_command(TARGET pytrexio POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different $<TARGET_FILE:pytrexio> "${WHEEL_PKG}/"
  COMMENT "Copying pytrexio.<tag>.so into the trexio/ wheel package")

# ---------------- cmake --install layout (non-wheel installs) ------------------
# Relative destinations so that `cmake --install --prefix <dir>` lands them in
# <dir>/trexio/ (absolutes would escape the install prefix).
install(TARGETS pytrexio DESTINATION "trexio")
install(FILES "${CMAKE_SOURCE_DIR}/api/trexio/__init__.py"
              "${CMAKE_SOURCE_DIR}/api/trexio/_version.py"
              "${CMAKE_SOURCE_DIR}/api/trexio/trexio_gen.py"
              DESTINATION "trexio")

# ---------------- generated bindings (per-variable surface) -------------------
# Regenerated from required_api.names + the installed trexio.h + trex.json.
if(EXISTS "${CMAKE_SOURCE_DIR}/tools/gen_binding.py")
  add_custom_command(OUTPUT "${CMAKE_SOURCE_DIR}/src/generated_bindings.cpp"
    COMMAND Python::Interpreter "${CMAKE_SOURCE_DIR}/tools/gen_binding.py"
            --trex-json "${CMAKE_SOURCE_DIR}/third_party/trex.json"
            --out "${CMAKE_SOURCE_DIR}/src/generated_bindings.cpp"
            --trexio-header "${TREXIO_INC}/trexio.h"
    DEPENDS "${CMAKE_SOURCE_DIR}/tools/gen_binding.py"
            "${CMAKE_SOURCE_DIR}/third_party/trex.json"
            "${CMAKE_SOURCE_DIR}/tools/required_api.names"
            "${TREXIO_INC}/trexio.h"
    COMMENT "Generating pytrexio per-variable bindings")
  add_custom_target(gen_bindings DEPENDS "${CMAKE_SOURCE_DIR}/src/generated_bindings.cpp")
  add_dependencies(pytrexio gen_bindings)
endif()

# ---------------- generated PYTHON surface (has_/read_/write_/delete_) ----------
# Regenerated from required_api.names + the installed trexio.h + trex.json.
# (C++ equivalent is the gen_bindings target above; both share tools/trexio_api_model.py.)
if(EXISTS "${CMAKE_SOURCE_DIR}/tools/gen_trexio_py.py")
  add_custom_command(OUTPUT "${CMAKE_SOURCE_DIR}/api/trexio_gen.py"
    COMMAND Python::Interpreter "${CMAKE_SOURCE_DIR}/tools/gen_trexio_py.py"
            --json          "${CMAKE_SOURCE_DIR}/third_party/trex.json"
            --out           "${CMAKE_SOURCE_DIR}/api/trexio_gen.py"
            --names         "${CMAKE_SOURCE_DIR}/tools/required_api.names"
            --golden        "${CMAKE_SOURCE_DIR}/api/trexio_gen.py"
            --trexio-header "${TREXIO_INC}/trexio.h"
    DEPENDS "${CMAKE_SOURCE_DIR}/tools/gen_trexio_py.py"
            "${CMAKE_SOURCE_DIR}/tools/trexio_api_model.py"
            "${CMAKE_SOURCE_DIR}/third_party/trex.json"
            "${CMAKE_SOURCE_DIR}/tools/required_api.names"
            "${TREXIO_INC}/trexio.h"
    COMMENT "Generating api/trexio_gen.py (trexio surface)")
  add_custom_target(gen_trexio_py DEPENDS "${CMAKE_SOURCE_DIR}/api/trexio_gen.py")
  add_dependencies(pytrexio gen_trexio_py)
endif()
