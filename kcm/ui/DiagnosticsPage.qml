import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    title: i18nc("@title", "Diagnostics")

    Component.onCompleted: kcm.diagnose()

    actions: [
        Kirigami.Action {
            text: i18nc("@action:button", "Check Again")
            icon.name: "view-refresh-symbolic"
            onTriggered: kcm.refresh()
        },
        Kirigami.Action {
            text: i18nc("@action:button", "Copy Report")
            icon.name: "edit-copy-symbolic"
            onTriggered: {
                kcm.copyReport()
                copied.visible = true
            }
        }
    ]

    header: Kirigami.InlineMessage {
        id: copied
        position: Kirigami.InlineMessage.Position.Header
        type: Kirigami.MessageType.Positive
        text: i18n("The report is in the clipboard.")
        showCloseButton: true
        visible: false
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: !kcm.serviceAvailable
            type: Kirigami.MessageType.Error
            text: kcm.serviceError
        }

        Repeater {
            model: kcm.checks

            delegate: RowLayout {
                required property var modelData

                Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing

                Kirigami.Icon {
                    Layout.alignment: Qt.AlignTop
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: implicitWidth
                    source: ({
                        ok: "dialog-positive",
                        info: "dialog-information",
                        warning: "dialog-warning",
                        error: "dialog-error",
                    })[modelData.state] || "dialog-information"
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    text: modelData.message
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
            }
        }
    }
}
