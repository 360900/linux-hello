// The Linux Hello extension for GNOME Shell (gnome/extension), which starts
// the gdm-face stack next to the password on the lock screen. Installed
// system-wide with the package, turned on per user; the counterpart of
// linux-hello/src/integration/gnomeshell.py, which the CLI uses.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {_} from './i18n.js';

const UUID = 'linux-hello@360900.github.io';
const SCHEMA = 'org.gnome.shell';
// The shell runs what is in ENABLED unless it is also in DISABLED, which takes
// precedence, or USER_OFF switches every listed extension off (the switch at
// the top of the Extensions app); see _getEnabledExtensions in gnome-shell's
// js/ui/extensionSystem.js
const ENABLED = 'enabled-extensions';
const DISABLED = 'disabled-extensions';
const USER_OFF = 'disable-user-extensions';
// ExtensionState.UNINSTALLED: an extension the shell has not found
const NOT_LOADED = 99;

// Whether the lock screen target scans through gdm-face, which only the
// extension starts
export function usedBy(target) {
    return target?.id === 'lock-screen' && target.services.includes('gdm-face');
}

function inGnome() {
    return (GLib.getenv('XDG_CURRENT_DESKTOP') ?? '').split(':').includes('GNOME');
}

function installed() {
    return GLib.get_system_data_dirs().some(dir =>
        GLib.file_test(GLib.build_filenamev([dir, 'gnome-shell', 'extensions', UUID, 'metadata.json']),
            GLib.FileTest.IS_REGULAR));
}

function shellSettings() {
    const schema = Gio.SettingsSchemaSource.get_default()?.lookup(SCHEMA, true);
    return schema ? new Gio.Settings({settings_schema: schema}) : null;
}

// The state the running shell reports, or null without a shell
async function shellState() {
    try {
        const reply = await Gio.DBus.session.call('org.gnome.Shell', '/org/gnome/Shell',
            'org.gnome.Shell.Extensions', 'GetExtensionInfo', new GLib.Variant('(s)', [UUID]),
            null, Gio.DBusCallFlags.NONE, 10 * 1000, null);
        const [info] = reply.recursiveUnpack();
        return info.state ?? NOT_LOADED;
    } catch {
        return null;
    }
}

/**
 * Turns the extension on or off for this user, as the Extensions app does,
 * except for the switch over all extensions, which stays the user's. The shell
 * picks up a change at once when it knows the extension, else at the next
 * login. Returns a message for the user, or null.
 */
export async function setEnabled(enabled) {
    if (!inGnome())
        return null;
    if (enabled && !installed())
        return _('The Linux Hello extension for GNOME Shell is not installed; the lock screen cannot use your face without it.');
    const settings = shellSettings();
    if (!settings)
        return null;
    if (enabled) {
        const disabled = settings.get_strv(DISABLED);
        if (disabled.includes(UUID))
            settings.set_strv(DISABLED, disabled.filter(uuid => uuid !== UUID));
    }
    const uuids = settings.get_strv(ENABLED);
    if (enabled && !uuids.includes(UUID))
        settings.set_strv(ENABLED, [...uuids, UUID]);
    else if (!enabled && uuids.includes(UUID))
        settings.set_strv(ENABLED, uuids.filter(uuid => uuid !== UUID));
    Gio.Settings.sync();
    if (!enabled)
        return null;
    if (settings.get_boolean(USER_OFF))
        return _('Extensions are switched off in the Extensions app, so the lock screen cannot use your face. Switch them on there.');
    if (await shellState() === NOT_LOADED)
        return _('Log out and in once for GNOME Shell to load the Linux Hello extension.');
    return null;
}

/**
 * In a GNOME session with the lock screen on: whether the extension runs,
 * as checks like the service's Diagnose gives. The service runs as root and
 * cannot see the user's shell.
 */
export async function checks(lockScreen) {
    if (!inGnome() || !lockScreen?.enabled || !usedBy(lockScreen) || !installed())
        return [];
    const settings = shellSettings();
    if (!settings)
        return [];
    const warning = message => [{check: 'gnome-extension', state: 'warning', message}];
    if (settings.get_boolean(USER_OFF))
        return warning(_('Extensions are switched off in the Extensions app, so the lock screen cannot use your face. Switch them on there.'));
    if (!settings.get_strv(ENABLED).includes(UUID) || settings.get_strv(DISABLED).includes(UUID))
        return warning(_('The Linux Hello extension for GNOME Shell is off for you, so the lock screen cannot use your face. Turn the lock screen off and on again here.'));
    if (await shellState() === NOT_LOADED)
        return warning(_('GNOME Shell started before the Linux Hello extension was installed. Log out and in once.'));
    return [];
}
