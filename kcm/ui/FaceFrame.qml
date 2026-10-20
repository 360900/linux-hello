// Where the camera sees the face, drawn like a mirror, while enrolling

import QtQuick

import org.kde.kirigami as Kirigami

Rectangle {
    id: frame

    // left, top, right, bottom as fractions of the camera picture; empty if no face
    property rect face
    // The face is being captured
    property bool good: false

    readonly property bool hasFace: face.width > 0 && face.height > 0

    implicitHeight: width * 3 / 4
    radius: Kirigami.Units.cornerRadius
    color: Kirigami.Theme.alternateBackgroundColor
    border.color: Kirigami.Theme.disabledTextColor
    border.width: 1
    clip: true

    // Where the face should be
    Rectangle {
        anchors.centerIn: parent
        width: parent.width * 0.38
        height: parent.height * 0.7
        radius: width / 2
        color: "transparent"
        border.color: Kirigami.Theme.disabledTextColor
        border.width: 1
        opacity: 0.6
    }

    Kirigami.Icon {
        anchors.centerIn: parent
        width: Kirigami.Units.iconSizes.large
        height: width
        source: "linux-hello-symbolic"
        visible: !frame.hasFace
        opacity: 0.5
    }

    Rectangle {
        visible: frame.hasFace
        // Mirrored: moving left moves the box left
        x: (1 - frame.face.x - frame.face.width) * frame.width
        y: frame.face.y * frame.height
        width: frame.face.width * frame.width
        height: frame.face.height * frame.height
        radius: Kirigami.Units.cornerRadius
        color: "transparent"
        border.width: 2
        border.color: frame.good ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.neutralTextColor

        Behavior on x { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        Behavior on y { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        Behavior on width { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        Behavior on height { NumberAnimation { duration: Kirigami.Units.shortDuration } }
    }
}
