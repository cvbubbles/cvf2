cmake_minimum_required(VERSION 3.23)

project(cvf2 LANGUAGES CXX)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

file(GLOB TOOLS_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/test1/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/test1/*.hpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/test1/*.h"
)


find_package(OpenCV REQUIRED)
find_package(assimp CONFIG REQUIRED)
find_package(glm CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)

find_package(FreeGLUT CONFIG QUIET)
if(TARGET FreeGLUT::freeglut)
    set(FREEGLUT_TARGET FreeGLUT::freeglut)
else()
    find_package(unofficial-freeglut CONFIG REQUIRED)
    if(TARGET unofficial::freeglut::freeglut)
        set(FREEGLUT_TARGET unofficial::freeglut::freeglut)
    else()
        message(FATAL_ERROR "Unable to locate a usable freeglut target from vcpkg.")
    endif()
endif()

add_library(cvf2 INTERFACE)
add_library(cvf2::cvf2 ALIAS cvf2)

target_include_directories(cvf2 INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)

add_executable(test1 ${TOOLS_SOURCES})

target_compile_definitions(test1 PRIVATE
    USE_OPENCV3_LIB
)

target_link_libraries(test1 PRIVATE
    cvf2::cvf2
    assimp::assimp
    glm::glm
    nlohmann_json::nlohmann_json
    ${FREEGLUT_TARGET}
    ${OpenCV_LIBS}
)

set_target_properties(test1 PROPERTIES
    OUTPUT_NAME "test1"
)

install(TARGETS cvf2
    EXPORT cvf2Targets
)

install(DIRECTORY
    BFC
    CVRender
    CVX
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}"
    FILES_MATCHING
        PATTERN "*.h"
        PATTERN "*.hpp"
)

install(FILES ffdef.h
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}"
)

install(EXPORT cvf2Targets
    NAMESPACE cvf2::
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/cvf2"
)

configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/cvf2Config.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/cvf2Config.cmake"
    INSTALL_DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/cvf2"
)

write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/cvf2ConfigVersion.cmake"
    VERSION 0.1.0
    COMPATIBILITY SameMajorVersion
)

install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/cvf2Config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/cvf2ConfigVersion.cmake"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/cvf2"
)

export(EXPORT cvf2Targets
    NAMESPACE cvf2::
    FILE "${CMAKE_CURRENT_BINARY_DIR}/cvf2Targets.cmake"
)
