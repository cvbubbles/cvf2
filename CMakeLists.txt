cmake_minimum_required(VERSION 3.23)

project(cvf2 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include_directories(F:/dev2/cvf2)

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

add_executable(test1 ${TOOLS_SOURCES})

target_compile_definitions(test1 PRIVATE
    USE_OPENCV3_LIB
)

target_include_directories(test1 PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    ${OpenCV_INCLUDE_DIRS}
)

target_link_libraries(test1 PRIVATE
    assimp::assimp
    glm::glm
    nlohmann_json::nlohmann_json
    ${FREEGLUT_TARGET}
    ${OpenCV_LIBS}
)

set_target_properties(test1 PROPERTIES
    OUTPUT_NAME "test1"
)
