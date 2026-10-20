# System Settings page "Face Unlock". A D-Bus client of the Linux Hello
# service; it needs no Python or camera libraries.

cmake_minimum_required(VERSION 3.22)
file(STRINGS ${CMAKE_CURRENT_SOURCE_DIR}/../VERSION LINUX_HELLO_VERSION LIMIT_COUNT 1)
project(kcm_linuxhello VERSION ${LINUX_HELLO_VERSION} LANGUAGES CXX)

set(QT_MIN_VERSION 6.7.0)
set(KF_MIN_VERSION 6.0.0)

find_package(ECM ${KF_MIN_VERSION} REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)
include(ECMQmlModule)

find_package(Qt6 ${QT_MIN_VERSION} REQUIRED COMPONENTS Core DBus Gui Qml Quick)
find_package(KF6 ${KF_MIN_VERSION} REQUIRED COMPONENTS Config CoreAddons I18n KCMUtils)

add_definitions(-DTRANSLATION_DOMAIN=\"kcm_linuxhello\")

# Also generates the hidden kcm_linuxhello.desktop, like every KDE settings page
kcmutils_add_qml_kcm(kcm_linuxhello SOURCES
    facemodule.cpp
    lockscreen.cpp
    replies.cpp
)
target_link_libraries(kcm_linuxhello PRIVATE
    Qt6::Core
    Qt6::DBus
    Qt6::Gui
    Qt6::Quick
    KF6::ConfigCore
    KF6::CoreAddons
    KF6::I18n
    KF6::KCMUtilsQuick
)

# Fallbacks; icon themes can provide their own "linux-hello"
install(FILES icons/linux-hello.svg DESTINATION ${KDE_INSTALL_ICONDIR}/hicolor/scalable/apps)
install(FILES icons/linux-hello-symbolic.svg DESTINATION ${KDE_INSTALL_ICONDIR}/hicolor/symbolic/apps)
