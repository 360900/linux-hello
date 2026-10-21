import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

// What protects this computer when someone else has it. Linux Hello is
// fully supported only on an encrypted disk; Secure Boot and the TPM make
// booting need no password.
KCM.SimpleKCM {
    id: page

    title: i18nc("@title", "Security")

    readonly property string guide: "https://github.com/360900/linux-hello/blob/master/docs/disk-encryption.md"
    readonly property var security: kcm.security
    readonly property bool loaded: security.secure_boot !== undefined
    readonly property bool encrypted: !!security.home_encrypted
    readonly property bool passwordless: encrypted && security.secure_boot === "on" && !!security.tpm_unlock

    Component.onCompleted: kcm.checkSecurity()

    actions: [
        Kirigami.Action {
            text: i18nc("@action:button", "Check Again")
            icon.name: "view-refresh-symbolic"
            onTriggered: kcm.checkSecurity()
        }
    ]

    header: Kirigami.InlineMessage {
        position: Kirigami.InlineMessage.Position.Header
        visible: page.loaded
        showCloseButton: false
        type: !page.encrypted ? Kirigami.MessageType.Warning
            : page.passwordless ? Kirigami.MessageType.Positive : Kirigami.MessageType.Information
        text: !page.encrypted
            ? i18n("Partly protected. Logging in automatically needs an encrypted disk.")
            : page.passwordless
                ? i18n("Fully protected. This computer boots to the lock screen without a password.")
                : i18n("Protected. Set up Secure Boot and TPM unlock to boot without typing the disk password.")
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
            model: page.loaded ? [
                {
                    state: page.encrypted ? "ok" : "warning",
                    title: i18nc("@title:group", "Disk encryption"),
                    text: page.encrypted
                        ? i18n("Your home folder is on an encrypted disk.")
                        : i18n("Your disk is not encrypted. Anyone who has this computer can read your files from a USB stick."),
                    link: page.encrypted ? "" : page.guide,
                    linkText: i18nc("@action:button", "Encrypt Disk"),
                },
                {
                    state: page.security.secure_boot === "on" ? "ok" : (page.security.secure_boot === "setup" || page.security.secure_boot === "unknown") ? "info" : "warning",
                    title: i18nc("@title:group", "Secure Boot"),
                    text: ({
                        on: i18n("On. Only signed bootloaders and kernels start."),
                        off: i18n("Off. Any system can start on this computer, also one from a USB stick."),
                        setup: i18n("Waiting for keys (Setup Mode)."),
                        legacy: i18n("Not available: this computer starts without UEFI."),
                    })[page.security.secure_boot] || i18n("Unknown."),
                    link: page.security.secure_boot === "on" || page.security.secure_boot === "legacy" ? "" : page.guide + "#1-secure-boot",
                    linkText: i18nc("@action:button", "Turn On Secure Boot"),
                },
                {
                    state: page.security.tpm === 2 ? "ok" : "warning",
                    title: i18nc("@title:group", "Security chip (TPM)"),
                    text: page.security.tpm === 2 ? i18n("This computer has a TPM 2.0.")
                        : page.security.tpm === 1 ? i18n("This computer's TPM is too old (1.2) to unlock the disk.")
                        : i18n("None found. It may be switched off in the firmware settings, on ThinkPads under Security > Security Chip."),
                    link: "",
                    linkText: "",
                },
                {
                    state: page.security.tpm_unlock ? "ok" : "info",
                    title: i18nc("@title:group", "TPM unlock"),
                    text: page.security.tpm_unlock ? i18n("The disk unlocks with the TPM at boot.")
                        : page.encrypted ? i18n("The disk asks for its password at boot. Let the TPM unlock it to boot without typing anything.")
                        : i18n("Needs an encrypted disk."),
                    link: !page.security.tpm_unlock && page.encrypted ? page.guide + "#2-unlock-with-the-tpm" : "",
                    linkText: i18nc("@action:button", "Set Up TPM Unlock"),
                },
            ] : []

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
                    })[modelData.state]
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    Kirigami.Heading {
                        level: 4
                        text: modelData.title
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        text: modelData.text
                        wrapMode: Text.Wrap
                    }
                    QQC2.Button {
                        visible: modelData.link !== ""
                        text: modelData.linkText
                        icon.name: "help-contents-symbolic"
                        onClicked: Qt.openUrlExternally(modelData.link)
                    }
                }
            }
        }
    }
}
