// What the camera sees and where it finds the face, drawn like a mirror

import QtQuick

import org.kde.kirigami as Kirigami

Rectangle {
    id: frame

    // left, top, right, bottom as fractions of the camera picture; empty if no face
    property rect face
    // The face is being captured
    property bool good: false
    // The camera picture (a URL); empty until the camera runs
    property string preview

    readonly property bool hasFace: face.width > 0 && face.height > 0
    readonly property bool hasPicture: preview !== "" && picture.sourceSize.width > 0

    // The picture's shape, so face boxes land where they belong
    implicitHeight: picture.sourceSize.width > 0 ? width * picture.sourceSize.height / picture.sourceSize.width : width * 3 / 4
    radius: Kirigami.Units.cornerRadius
    color: Kirigami.Theme.alternateBackgroundColor
    border.color: Kirigami.Theme.disabledTextColor
    border.width: 1
    clip: true

    Image {
        id: picture
        anchors.fill: parent
        anchors.margins: frame.border.width
        visible: frame.preview !== ""
        source: frame.preview
        cache: false
        // Keeps the last frame up while the next one decodes
        retainWhileLoading: true
        mirror: true
        smooth: true
    }

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
        visible: !frame.hasFace && !frame.hasPicture
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
