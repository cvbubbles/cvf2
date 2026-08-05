if(NOT TARGET cvf2::cvf2)
    add_library(cvf2::cvf2 INTERFACE IMPORTED)
    set_target_properties(cvf2::cvf2 PROPERTIES
        INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_LIST_DIR}/.."
    )
endif()
