// Adding a face, then trying it right away. Also opened for a test only.

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog

    required property bool hasFaces

    // intro, scan, failed, testing, result
    property string stage: "intro"
    property string message: ""
    property bool recognized: false
    property real certainty: -1
    readonly property real threshold: parseFloat(kcm.settings["video.certainty"]) || 3.5

    signal improveRequested()

    function start(mode) {
        nameField.text = ""
        if (mode === "test") {
            test()
        } else {
            stage = "intro"
        }
        open()
    }

    function test() {
        stage = "testing"
        kcm.verify()
    }

    function scanHint() {
        switch (kcm.enrollState) {
        case "":
            return i18n("Getting ready…")
        case "searching":
            return i18n("Looking for your face…")
        case "no-face":
            return i18n("Look at the camera")
        case "multiple-faces":
            return i18n("Make sure only your face is in view")
        case "too-far":
            return i18n("Move closer")
        case "too-dark":
            return i18n("It is too dark")
        default:
            return i18n("Hold still…")
        }
    }

    function failureText(result) {
        switch (result) {
        case 10:
            return i18n("No face has been added yet.")
        case 11:
            return i18n("No face was seen in time. Look straight at the camera and try again.")
        case 13:
            return i18n("The camera picture is too dark. If your camera has an infrared light, see Diagnostics.")
        case 14:
            return i18n("The camera could not be used. Another app may be using it.")
        case 252:
            return i18n("Adding a face needs your password.")
        default:
            return i18n("Something went wrong. See Diagnostics for details.")
        }
    }

    title: stage === "testing" || stage === "result" ? i18nc("@title:window", "Test Face Unlock")
         : hasFaces ? i18nc("@title:window", "Improve Recognition") : i18nc("@title:window", "Add Your Face")
    padding: Kirigami.Units.largeSpacing
    preferredWidth: Kirigami.Units.gridUnit * 24
    standardButtons: Kirigami.Dialog.NoButton

    onRejected: kcm.cancel()

    Connections {
        target: kcm

        function onEnrollFinished(result, modelId) {
            if (dialog.stage !== "scan") {
                return
            }
            if (result === 0) {
                // Straight to trying it, like the real thing
                dialog.test()
            } else if (result === 250) {
                dialog.close()
            } else {
                dialog.message = dialog.failureText(result)
                dialog.stage = "failed"
            }
        }

        function onVerifyFinished(result, certainty) {
            if (dialog.stage !== "testing") {
                return
            }
            if (result === 250) {
                dialog.close()
                return
            }
            dialog.recognized = result === 0
            dialog.certainty = certainty
            dialog.message = result === 0 || result === 11 ? "" : dialog.failureText(result)
            dialog.stage = "result"
        }
    }

    customFooterActions: [
        Kirigami.Action {
            visible: dialog.stage === "intro"
            text: i18nc("@action:button", "Start")
            icon.name: "media-playback-start-symbolic"
            onTriggered: {
                dialog.stage = "scan"
                kcm.enroll(nameField.text.trim())
            }
        },
        Kirigami.Action {
            visible: dialog.stage === "failed"
            text: i18nc("@action:button", "Try Again")
            icon.name: "view-refresh-symbolic"
            onTriggered: {
                dialog.stage = "scan"
                kcm.enroll(nameField.text.trim())
            }
        },
        Kirigami.Action {
            visible: dialog.stage === "result" && (!dialog.recognized || dialog.certainty > dialog.threshold * 0.8)
            text: i18nc("@action:button", "Improve Recognition…")
            icon.name: "list-add-symbolic"
            onTriggered: dialog.improveRequested()
        },
        Kirigami.Action {
            visible: dialog.stage === "result"
            text: i18nc("@action:button", "Test Again")
            icon.name: "view-refresh-symbolic"
            onTriggered: dialog.test()
        },
        Kirigami.Action {
            visible: dialog.stage === "result"
            text: i18nc("@action:button", "Done")
            icon.name: "dialog-ok-symbolic"
            onTriggered: dialog.close()
        },
        Kirigami.Action {
            visible: dialog.stage !== "result"
            text: i18nc("@action:button", "Cancel")
            icon.name: "dialog-cancel-symbolic"
            onTriggered: dialog.reject()
        }
    ]

    StackLayout {
        currentIndex: ["intro", "scan", "failed", "testing", "result"].indexOf(dialog.stage)

        // intro
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: dialog.hasFaces
                    ? i18n("Scan your face again to be recognized in more situations, for example in other light or with glasses.")
                    : i18n("Linux Hello keeps a description of your face, not a picture, and only on this computer. Your password keeps working.")
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: i18n("Look at the camera from where you usually sit.")
            }
            Kirigami.FormLayout {
                Layout.fillWidth: true

                QQC2.TextField {
                    id: nameField
                    Kirigami.FormData.label: i18n("Name:")
                    placeholderText: dialog.hasFaces ? i18nc("@info:placeholder example name", "For example: Evening light") : i18nc("@info:placeholder", "Optional")
                }
            }
        }

        // scan
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            FaceFrame {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Kirigami.Units.gridUnit * 14
                face: kcm.enrollFace
                good: kcm.enrollState === "captured"
                preview: dialog.stage === "scan" ? kcm.preview : ""
            }
            Kirigami.Heading {
                Layout.fillWidth: true
                level: 3
                horizontalAlignment: Text.AlignHCenter
                text: dialog.scanHint()
            }
            Row {
                Layout.alignment: Qt.AlignHCenter
                spacing: Kirigami.Units.smallSpacing

                Repeater {
                    model: kcm.enrollSamples || 5

                    Rectangle {
                        required property int index
                        width: Kirigami.Units.gridUnit * 0.6
                        height: width
                        radius: width / 2
                        color: index < kcm.enrollSample ? Kirigami.Theme.positiveTextColor : "transparent"
                        border.color: index < kcm.enrollSample ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
                        border.width: 1

                        Behavior on color {
                            ColorAnimation { duration: Kirigami.Units.shortDuration }
                        }
                    }
                }
            }
        }

        // failed
        ColumnLayout {
            Kirigami.PlaceholderMessage {
                Layout.fillWidth: true
                icon.name: "dialog-warning"
                text: i18n("Your face could not be added")
                explanation: dialog.message
            }
        }

        // testing
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            FaceFrame {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Kirigami.Units.gridUnit * 14
                preview: dialog.stage === "testing" ? kcm.preview : ""
            }
            Kirigami.Heading {
                Layout.fillWidth: true
                level: 3
                horizontalAlignment: Text.AlignHCenter
                text: i18n("Look at the camera")
            }
            QQC2.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: i18n("This works exactly like unlocking.")
                opacity: 0.7
            }
        }

        // result
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            readonly property bool close: dialog.recognized && dialog.certainty > dialog.threshold * 0.8

            Kirigami.PlaceholderMessage {
                Layout.fillWidth: true
                icon.name: dialog.recognized ? "dialog-ok" : "dialog-warning"
                text: dialog.recognized ? i18n("Recognized") : i18n("Not recognized")
                explanation: dialog.message !== "" ? dialog.message
                    : !dialog.recognized ? i18n("Try again facing the camera. If it keeps failing in this light, improve recognition.")
                    : parent.close ? i18n("It worked, but only just. Improving recognition in this light makes it more reliable.")
                    : i18n("A clear match.")
            }
            QQC2.Label {
                Layout.fillWidth: true
                visible: dialog.certainty >= 0
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                // Lower is closer; a match needs a value below the strictness limit
                text: i18n("Difference %1, limit %2", dialog.certainty.toFixed(2), dialog.threshold.toFixed(1))
                font: Kirigami.Theme.smallFont
                opacity: 0.7
            }
        }
    }
}
