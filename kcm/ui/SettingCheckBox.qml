// A yes/no option of the config, changed through the service

import QtQuick
import QtQuick.Controls as QQC2

QQC2.CheckBox {
    id: box

    required property string section
    required property string key
    // Checked means the option is false
    property bool inverted: false

    readonly property bool value: ["true", "yes", "1", "on"].includes(String(kcm.settings[section + "." + key]).toLowerCase())

    checked: value !== inverted
    onToggled: {
        kcm.setSetting(section, key, checked !== inverted ? "true" : "false")
        // Show the service's answer, not the click
        checked = Qt.binding(() => box.value !== box.inverted)
    }
}
