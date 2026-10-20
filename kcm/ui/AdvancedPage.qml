import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    id: page

    title: i18nc("@title", "Advanced")

    readonly property string device: kcm.settings["video.device_path"] || "none"
    readonly property bool automatic: ["", "none", "auto"].includes(device)

    // Automatic first, then what was found, then the configured camera if it was not
    readonly property var cameraChoices: {
        const choices = [{ text: i18nc("@item:inlistbox camera", "Automatic (infrared preferred)"), path: "none" }]
        for (const camera of kcm.cameras) {
            choices.push({
                text: camera.infrared ? i18nc("@item:inlistbox camera name", "%1 (infrared)", camera.name) : camera.name,
                path: camera.path,
            })
        }
        if (!automatic && !choices.some(choice => choice.path === device)) {
            choices.push({ text: device, path: device })
        }
        return choices
    }

    header: Kirigami.InlineMessage {
        id: failure
        position: Kirigami.InlineMessage.Position.Header
        type: Kirigami.MessageType.Error
        showCloseButton: true
        visible: false
    }

    Connections {
        target: kcm
        function onActionFailed(message) {
            failure.text = message
            failure.visible = true
        }
    }

    Kirigami.FormLayout {
        enabled: kcm.serviceAvailable

        SettingCheckBox {
            Kirigami.FormData.label: i18n("Face unlock:")
            section: "core"
            key: "disabled"
            inverted: true
            text: i18nc("@option:check", "Turned on")
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18nc("@title:group", "Camera")
        }

        RowLayout {
            Kirigami.FormData.label: i18n("Camera:")

            QQC2.ComboBox {
                id: camera
                textRole: "text"
                valueRole: "path"
                model: page.cameraChoices
                currentIndex: Math.max(0, page.cameraChoices.findIndex(choice => choice.path === (page.automatic ? "none" : page.device)))
                onActivated: index => {
                    kcm.setSetting("video", "device_path", page.cameraChoices[index].path)
                    currentIndex = Qt.binding(() => Math.max(0, page.cameraChoices.findIndex(choice => choice.path === (page.automatic ? "none" : page.device))))
                }
            }
            QQC2.Button {
                text: i18nc("@action:button", "Find Cameras")
                icon.name: "view-refresh-symbolic"
                enabled: !kcm.findingCameras && !kcm.scanning
                onClicked: kcm.findCameras()
            }
            QQC2.BusyIndicator {
                visible: kcm.findingCameras
                running: visible
                Layout.preferredHeight: Kirigami.Units.iconSizes.medium
            }
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            text: i18n("Finding cameras opens each one briefly, so their lights may blink.")
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }

        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Infrared light:")
            textRole: "text"
            valueRole: "value"
            model: [
                { text: i18nc("@item:inlistbox", "Switch on while scanning"), value: "auto" },
                { text: i18nc("@item:inlistbox", "Leave as it is"), value: "off" },
            ]
            readonly property string current: (kcm.settings["video.ir_emitter"] || "auto").toLowerCase() === "off" ? "off" : "auto"
            currentIndex: indexOfValue(current)
            onActivated: index => {
                kcm.setSetting("video", "ir_emitter", model[index].value)
                currentIndex = Qt.binding(() => indexOfValue(current))
            }
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18nc("@title:group", "Behavior")
        }

        SettingCheckBox {
            Kirigami.FormData.label: i18n("Skip face unlock:")
            section: "core"
            key: "abort_if_ssh"
            text: i18nc("@option:check", "When signed in remotely (SSH)")
        }
        SettingCheckBox {
            section: "core"
            key: "abort_if_lid_closed"
            text: i18nc("@option:check", "When the laptop lid is closed")
        }

        SettingCheckBox {
            Kirigami.FormData.label: i18n("Messages:")
            section: "core"
            key: "detection_notice"
            text: i18nc("@option:check", "Say when a face scan starts")
        }
        SettingCheckBox {
            section: "core"
            key: "timeout_notice"
            text: i18nc("@option:check", "Say when no face was recognized")
        }

        SettingCheckBox {
            Kirigami.FormData.label: i18n("Snapshots:")
            section: "snapshots"
            key: "save_failed"
            text: i18nc("@option:check", "Save a picture of failed attempts")
        }
        SettingCheckBox {
            section: "snapshots"
            key: "save_successful"
            text: i18nc("@option:check", "Save a picture of successful attempts")
        }

        QQC2.Label {
            Kirigami.FormData.isSection: true
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            text: i18n("More options are available with: linux-hello-cli config")
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
    }
}
