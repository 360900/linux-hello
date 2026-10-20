import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    id: root

    readonly property bool hasFaces: kcm.models.length > 0
    readonly property var enabledTargets: kcm.targets.filter(target => target.enabled)
    readonly property var errors: kcm.checks.filter(check => check.state === "error")
    property bool offeredSetup: false

    // Certainty values behind the strictness choices; lower is stricter
    readonly property var presets: [
        { label: i18nc("@item:inlistbox strictness", "Convenient"), value: 4.0 },
        { label: i18nc("@item:inlistbox strictness", "Balanced"), value: 3.5 },
        { label: i18nc("@item:inlistbox strictness", "Strict"), value: 3.0 },
    ]

    function enroll() {
        enrollDialog.start("enroll")
    }

    actions: [
        Kirigami.Action {
            text: i18nc("@action:button", "Diagnostics")
            icon.name: "checkmark-symbolic"
            onTriggered: kcm.push("DiagnosticsPage.qml")
        },
        Kirigami.Action {
            text: i18nc("@action:button", "Advanced")
            icon.name: "configure-symbolic"
            onTriggered: kcm.push("AdvancedPage.qml")
        }
    ]

    header: ColumnLayout {
        spacing: 0

        // One line on the state of things, or the most important problem
        Kirigami.InlineMessage {
            id: banner
            Layout.fillWidth: true
            position: Kirigami.InlineMessage.Position.Header
            visible: text !== ""
            showCloseButton: false

            readonly property var summary: {
                if (!kcm.serviceAvailable) {
                    return { type: Kirigami.MessageType.Error, text: kcm.serviceError, action: null }
                }
                if (root.errors.length > 0) {
                    return { type: Kirigami.MessageType.Error, text: root.errors[0].message, action: diagnosticsAction }
                }
                if (kcm.status.disabled) {
                    return { type: Kirigami.MessageType.Warning, text: i18n("Face unlock is turned off."), action: turnOnAction }
                }
                if (!root.hasFaces) {
                    return { type: Kirigami.MessageType.Information, text: i18n("Add your face to start using face unlock."), action: addFaceAction }
                }
                if (root.enabledTargets.length === 0) {
                    return { type: Kirigami.MessageType.Warning, text: i18n("Face unlock is not used anywhere yet. Choose where below."), action: null }
                }
                return {
                    type: Kirigami.MessageType.Positive,
                    text: i18n("Face unlock is on for: %1", root.enabledTargets.map(target => target.label).join(", ")),
                    action: null,
                }
            }

            type: summary.type
            text: summary.text
            actions: summary.action ? [summary.action] : []

            Kirigami.Action {
                id: diagnosticsAction
                text: i18nc("@action:button", "Details")
                onTriggered: kcm.push("DiagnosticsPage.qml")
            }
            Kirigami.Action {
                id: turnOnAction
                text: i18nc("@action:button", "Turn On")
                onTriggered: kcm.setSetting("core", "disabled", "false")
            }
            Kirigami.Action {
                id: addFaceAction
                text: i18nc("@action:button", "Add Face…")
                icon.name: "list-add-symbolic"
                onTriggered: root.enroll()
            }
        }

        Kirigami.InlineMessage {
            id: failure
            Layout.fillWidth: true
            position: Kirigami.InlineMessage.Position.Header
            type: Kirigami.MessageType.Error
            showCloseButton: true
            visible: false
        }
    }

    Connections {
        target: kcm
        function onActionFailed(message) {
            failure.text = message
            failure.visible = true
        }
    }

    // Nothing to set up on this page without a face, so start there, once
    Connections {
        target: kcm
        enabled: !root.offeredSetup
        function onModelsChanged() {
            root.offeredSetup = true
            if (!root.hasFaces && !kcm.scanning) {
                root.enroll()
            }
        }
    }

    Kirigami.FormLayout {
        enabled: kcm.serviceAvailable

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18nc("@title:group", "Your Face")
        }

        ColumnLayout {
            Kirigami.FormData.label: i18n("Faces:")
            Kirigami.FormData.labelAlignment: Qt.AlignTop
            spacing: 0

            QQC2.Label {
                visible: !root.hasFaces
                text: i18n("None added yet")
            }

            Repeater {
                model: kcm.models

                delegate: RowLayout {
                    required property var modelData

                    spacing: Kirigami.Units.smallSpacing

                    QQC2.Label {
                        text: modelData.label
                    }
                    QQC2.Label {
                        visible: !!modelData.time
                        text: modelData.time ? i18nc("@info added on date", "added %1", Qt.formatDate(modelData.time, Qt.locale().dateFormat(Locale.ShortFormat))) : ""
                        opacity: 0.7
                    }
                    QQC2.ToolButton {
                        icon.name: "edit-rename-symbolic"
                        display: QQC2.AbstractButton.IconOnly
                        text: i18nc("@action:button", "Rename")
                        QQC2.ToolTip.text: text
                        QQC2.ToolTip.visible: hovered
                        onClicked: renameDialog.open(modelData)
                    }
                    QQC2.ToolButton {
                        icon.name: "edit-delete-symbolic"
                        display: QQC2.AbstractButton.IconOnly
                        text: i18nc("@action:button", "Remove")
                        QQC2.ToolTip.text: text
                        QQC2.ToolTip.visible: hovered
                        onClicked: removeDialog.open(modelData)
                    }
                }
            }
        }

        RowLayout {
            spacing: Kirigami.Units.smallSpacing

            QQC2.Button {
                text: root.hasFaces ? i18nc("@action:button", "Improve Recognition…") : i18nc("@action:button", "Add Face…")
                icon.name: "list-add-symbolic"
                enabled: !kcm.scanning
                onClicked: root.enroll()

                QQC2.ToolTip.text: i18n("Scan your face again, for example in other light or with glasses")
                QQC2.ToolTip.visible: root.hasFaces && hovered
            }
            QQC2.Button {
                visible: root.hasFaces
                text: i18nc("@action:button", "Test…")
                icon.name: "linux-hello-symbolic"
                enabled: !kcm.scanning
                onClicked: enrollDialog.start("test")
            }
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18nc("@title:group", "Use Face Unlock For")
        }

        ColumnLayout {
            Kirigami.FormData.label: i18n("Sign in to:")
            Kirigami.FormData.labelAlignment: Qt.AlignTop
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            spacing: Kirigami.Units.smallSpacing

            Repeater {
                model: kcm.targets

                delegate: ColumnLayout {
                    required property var modelData

                    Layout.fillWidth: true
                    spacing: 0

                    QQC2.CheckBox {
                        id: targetBox
                        text: modelData.label
                        checked: modelData.enabled
                        // Turning one on without a face would do nothing
                        enabled: modelData.available && (root.hasFaces || modelData.enabled)
                        onToggled: {
                            kcm.setTarget(modelData.id, checked)
                            // Show the service's answer, not the click
                            checked = Qt.binding(() => modelData.enabled)
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        leftPadding: targetBox.indicator.width + targetBox.spacing
                        text: modelData.available ? modelData.description : i18n("Not available on this system")
                        wrapMode: Text.Wrap
                        font: Kirigami.Theme.smallFont
                        opacity: 0.7
                    }
                }
            }
        }

        Item {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: i18nc("@title:group", "Recognition")
        }

        QQC2.ComboBox {
            id: strictness
            Kirigami.FormData.label: i18n("Strictness:")

            readonly property real certainty: parseFloat(kcm.settings["video.certainty"])
            readonly property int preset: root.presets.findIndex(item => Math.abs(item.value - certainty) < 0.01)

            model: preset >= 0 ? root.presets.map(item => item.label)
                               : root.presets.map(item => item.label).concat([i18nc("@item:inlistbox strictness", "Custom (%1)", certainty)])
            currentIndex: preset >= 0 ? preset : root.presets.length
            onActivated: index => {
                if (index < root.presets.length) {
                    kcm.setSetting("video", "certainty", root.presets[index].value.toFixed(1))
                }
                currentIndex = Qt.binding(() => preset >= 0 ? preset : root.presets.length)
            }
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            text: i18n("Stricter settings are harder to fool with a similar face, but may need a second look in poor light.")
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }

        QQC2.SpinBox {
            id: timeout
            Kirigami.FormData.label: i18n("Look for a face for:")
            from: 1
            to: 30
            value: parseInt(kcm.settings["video.timeout"]) || 4
            textFromValue: (value, locale) => i18np("%1 second", "%1 seconds", value)
            valueFromText: (text, locale) => parseInt(text) || 4
            onValueModified: applyTimeout.restart()

            // Ask the service once the value settles, not on every step
            Timer {
                id: applyTimeout
                interval: 800
                onTriggered: kcm.setSetting("video", "timeout", String(timeout.value))
            }
        }
    }

    EnrollDialog {
        id: enrollDialog
        hasFaces: root.hasFaces
        onImproveRequested: start("enroll")
    }

    Kirigami.PromptDialog {
        id: renameDialog

        property var face: null

        function open(face) {
            renameDialog.face = face
            nameField.text = face.label
            visible = true
            nameField.forceActiveFocus()
            nameField.selectAll()
        }

        title: i18nc("@title:window", "Rename Face")
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        onAccepted: {
            if (nameField.text.trim() !== "" && nameField.text !== face.label) {
                kcm.renameModel(face.id, nameField.text.trim())
            }
        }

        QQC2.TextField {
            id: nameField
            onAccepted: renameDialog.accept()
        }
    }

    Kirigami.PromptDialog {
        id: removeDialog

        property var face: null

        function open(face) {
            removeDialog.face = face
            visible = true
        }

        title: i18nc("@title:window", "Remove Face")
        subtitle: !face ? "" : (kcm.models.length > 1
            ? i18n("“%1” will no longer be recognized.", face.label)
            : i18n("“%1” is your only face. Face unlock stops working until you add one again; your password keeps working.", face.label))
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [
            Kirigami.Action {
                text: i18nc("@action:button", "Remove")
                icon.name: "edit-delete-symbolic"
                onTriggered: {
                    kcm.deleteModel(removeDialog.face.id)
                    removeDialog.close()
                }
            }
        ]
    }
}
