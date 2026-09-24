cmake_minimum_required(VERSION 3.18)
project(shimmered_coins_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(SHIMCOINS_OPENMP "Parallelise the search with OpenMP" ON)

add_library(shimcoins_core STATIC src/lp.cpp src/search.cpp)
target_include_directories(shimcoins_core PUBLIC include)
set_target_properties(shimcoins_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(SHIMCOINS_OPENMP)
  find_package(OpenMP)
  if(OpenMP_CXX_FOUND)
    target_link_libraries(shimcoins_core PUBLIC OpenMP::OpenMP_CXX)
  endif()
endif()

# Python module (built by scikit-build-core via `pip install ./cpp`)
if(SKBUILD)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(_core src/bindings.cpp)
  target_link_libraries(_core PRIVATE shimcoins_core)
  install(TARGETS _core LIBRARY DESTINATION shimmered_coins_core)
endif()

# Standalone C++ smoke test: cmake -S cpp -B build && cmake --build build && ./build/core_test
if(NOT SKBUILD)
  enable_testing()
  add_executable(core_test tests/core_test.cpp)
  target_link_libraries(core_test PRIVATE shimcoins_core)
  add_test(NAME core_test COMMAND core_test)
endif()
