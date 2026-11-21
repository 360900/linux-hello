// The Security, Diagnostics and Advanced pages, pushed from the main page

import Adw from 'gi://Adw?version=1';
import Gdk from 'gi://Gdk?version=4.0';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk?version=4.0';

import {_, format} from './i18n.js';

export const GUIDE = 'https://github.com/360900/linux-hello/blob/master/docs/disk-encryption.md';

const STATE_ICONS = {
    ok: 'emblem-ok-symbolic',
    info: 'dialog-information-symbolic',
    warning: 'dialog-warning-symbolic',
    error: 'dialog-error-symbolic',
};

function escape(text) {
    return GLib.markup_escape_text(String(text), -1);
}

// Signal connections that end with the page
function connectWhileAlive(page, service, handlers) {
    const ids = Object.entries(handlers).map(([signal, handler]) => service.connect(signal, handler));
    page.connect('unrealize', () => ids.forEach(id => service.disconnect(id)));
}

class Page extends Adw.NavigationPage {
    static {
        GObject.registerClass(this);
    }

    _init(service, title) {
        super._init({title});
        this._service = service;
        this._view = new Adw.ToolbarView();
        this._header = new Adw.HeaderBar();
        this._view.add_top_bar(this._header);
        this._toasts = new Adw.ToastOverlay();
        this._view.set_content(this._toasts);
        this.set_child(this._view);
    }

    _headerButton(label, icon, onClicked) {
        const button = new Gtk.Button({icon_name: icon, tooltip_text: label});
        button.connect('clicked', onClicked);
        this._header.pack_end(button);
        return button;
    }

    _serviceBanner() {
        const banner = new Adw.Banner();
        const update = () => {
            banner.title = this._service.error;
            banner.revealed = !this._service.available;
        };
        connectWhileAlive(this, this._service, {'status-changed': update});
        update();
        return banner;
    }
}

// -- Security -----------------------------------------------------------------------
// What protects this computer when someone else has it. Linux Hello is fully
// supported only on an encrypted disk; Secure Boot and the TPM make booting
// need no password.
export const SecurityPage = GObject.registerClass(class SecurityPage extends Page {
    _init(service) {
        super._init(service, _('Security'));
        this._headerButton(_('Check Again'), 'view-refresh-symbolic', () => service.checkSecurity());

        const box = new Gtk.Box({orientation: Gtk.Orientation.VERTICAL});
        box.append(this._serviceBanner());
        this._banner = new Adw.Banner();
        box.append(this._banner);
        const page = new Adw.PreferencesPage();
        this._group = new Adw.PreferencesGroup();
        page.add(this._group);
        box.append(page);
        this._toasts.set_child(box);
        this._rows = [];

        connectWhileAlive(this, service, {'security-changed': () => this._update()});
        service.checkSecurity();
    }

    _update() {
        const security = this._service.security;
        if (security.secure_boot === undefined)
            return;
        const encrypted = !!security.home_encrypted;
        const passwordless = encrypted && security.secure_boot === 'on' && !!security.tpm_unlock;
        this._banner.title = !encrypted
            ? _('Partly protected. Logging in automatically needs an encrypted disk.')
            : passwordless
                ? _('Fully protected. This computer boots to the lock screen without a password.')
                : _('Protected. Set up Secure Boot and TPM unlock to boot without typing the disk password.');
        this._banner.revealed = true;

        const secureBoot = security.secure_boot;
        const items = [
            {
                state: encrypted ? 'ok' : 'warning',
                title: _('Disk encryption'),
                text: encrypted
                    ? _('Your home folder is on an encrypted disk.')
                    : _('Your disk is not encrypted. Anyone who has this computer can read your files from a USB stick.'),
                link: encrypted ? '' : GUIDE,
                linkText: _('Encrypt Disk'),
            },
            {
                state: secureBoot === 'on' ? 'ok' : secureBoot === 'setup' || secureBoot === 'unknown' ? 'info' : 'warning',
                title: _('Secure Boot'),
                text: {
                    on: _('On. Only signed bootloaders and kernels start.'),
                    off: _('Off. Any system can start on this computer, also one from a USB stick.'),
                    setup: _('Waiting for keys (Setup Mode).'),
                    legacy: _('Not available: this computer starts without UEFI.'),
                }[secureBoot] ?? _('Unknown.'),
                link: secureBoot === 'on' || secureBoot === 'legacy' ? '' : `${GUIDE}#1-secure-boot`,
                linkText: _('Turn On Secure Boot'),
            },
            {
                state: security.tpm === 2 ? 'ok' : 'warning',
                title: _('Security chip (TPM)'),
                text: security.tpm === 2 ? _('This computer has a TPM 2.0.')
                    : security.tpm === 1 ? _("This computer's TPM is too old (1.2) to unlock the disk.")
                    : _('None found. It may be switched off in the firmware settings, on ThinkPads under Security > Security Chip.'),
                link: '',
                linkText: '',
            },
            {
                state: security.tpm_unlock ? 'ok' : 'info',
                title: _('TPM unlock'),
                text: security.tpm_unlock ? _('The disk unlocks with the TPM at boot.')
                    : encrypted ? _('The disk asks for its password at boot. Let the TPM unlock it to boot without typing anything.')
                    : _('Needs an encrypted disk.'),
                link: !security.tpm_unlock && encrypted ? `${GUIDE}#2-unlock-with-the-tpm` : '',
                linkText: _('Set Up TPM Unlock'),
            },
        ];
        this._rows.forEach(row => this._group.remove(row));
        this._rows = items.map(item => {
            const row = new Adw.ActionRow({title: escape(item.title), subtitle: escape(item.text), icon_name: STATE_ICONS[item.state]});
            if (item.link)
                row.add_suffix(new Gtk.LinkButton({uri: item.link, label: item.linkText, valign: Gtk.Align.CENTER}));
            this._group.add(row);
            return row;
        });
    }
});

// -- Diagnostics -------------------------------------------------------------------
export const DiagnosticsPage = GObject.registerClass(class DiagnosticsPage extends Page {
    _init(service) {
        super._init(service, _('Diagnostics'));
        this._headerButton(_('Copy Report'), 'edit-copy-symbolic', () => {
            this.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(service.report()));
            this._toasts.add_toast(new Adw.Toast({title: _('The report is in the clipboard.')}));
        });
        this._headerButton(_('Check Again'), 'view-refresh-symbolic', () => service.refresh());

        const box = new Gtk.Box({orientation: Gtk.Orientation.VERTICAL});
        box.append(this._serviceBanner());
        const page = new Adw.PreferencesPage();
        this._group = new Adw.PreferencesGroup();
        page.add(this._group);
        box.append(page);
        this._toasts.set_child(box);
        this._rows = [];

        connectWhileAlive(this, service, {'checks-changed': () => this._update()});
        service.diagnose();
        this._update();
    }

    _update() {
        this._rows.forEach(row => this._group.remove(row));
        this._rows = this._service.checks.map(check => {
            const row = new Adw.ActionRow({title: escape(check.message), icon_name: STATE_ICONS[check.state] ?? STATE_ICONS.info});
            this._group.add(row);
            return row;
        });
    }
});

// -- Advanced -----------------------------------------------------------------------

// A yes/no option of the config, changed through the service. A SwitchRow
// with an update() that shows the service's value; the type is final, so no
// subclass.
function settingRow(service, section, key, params, inverted = false) {
    const row = new Adw.SwitchRow(params);
    const name = `${section}.${key}`;
    let updating = false;
    // On means the option is false when inverted
    row.update = () => {
        updating = true;
        row.active = service.settingIsTrue(name) !== inverted;
        updating = false;
    };
    row.connect('notify::active', () => {
        if (updating)
            return;
        service.setSetting(section, key, row.active !== inverted ? 'true' : 'false');
        // Show the service's answer, not the click
        row.update();
    });
    row.update();
    return row;
}

export const AdvancedPage = GObject.registerClass(class AdvancedPage extends Page {
    _init(service) {
        super._init(service, _('Advanced'));
        this._updating = false;

        const box = new Gtk.Box({orientation: Gtk.Orientation.VERTICAL});
        box.append(this._serviceBanner());
        const page = new Adw.PreferencesPage();
        box.append(page);
        this._toasts.set_child(box);
        this._settingRows = [];

        const general = new Adw.PreferencesGroup();
        general.add(this._setting('core', 'disabled', {title: _('Face unlock'), subtitle: _('Turned on')}, true));
        page.add(general);

        const camera = new Adw.PreferencesGroup({title: _('Camera')});
        this._cameraRow = new Adw.ComboRow({title: _('Camera')});
        this._cameraRow.connect('notify::selected', () => {
            if (!this._updating && this._cameraChoices)
                service.setSetting('video', 'device_path', this._cameraChoices[this._cameraRow.selected].path);
        });
        camera.add(this._cameraRow);
        const find = new Adw.ActionRow({
            title: _('Find Cameras'),
            subtitle: _('Opens each camera briefly, so their lights may blink.'),
            activatable: true,
        });
        this._findSpinner = new Gtk.Spinner({valign: Gtk.Align.CENTER});
        find.add_suffix(this._findSpinner);
        find.add_suffix(new Gtk.Image({icon_name: 'view-refresh-symbolic'}));
        find.connect('activated', () => service.findCameras());
        this._findRow = find;
        camera.add(find);
        this._emitterRow = new Adw.ComboRow({
            title: _('Infrared light'),
            model: Gtk.StringList.new([_('Switch on while scanning'), _('Leave as it is')]),
        });
        this._emitterRow.connect('notify::selected', () => {
            if (!this._updating)
                service.setSetting('video', 'ir_emitter', this._emitterRow.selected === 1 ? 'off' : 'auto');
        });
        camera.add(this._emitterRow);
        page.add(camera);

        const behavior = new Adw.PreferencesGroup({title: _('Behavior')});
        this._confirmRow = this._setting('core', 'confirm_elevation', {title: _('Press Enter before scanning for admin prompts and sudo')});
        behavior.add(this._confirmRow);
        this._insecureRow = this._setting('core', 'insecure_auto_login', {title: _('Log in automatically on a disk that is not encrypted')});
        behavior.add(this._insecureRow);
        behavior.add(this._setting('core', 'abort_if_ssh', {title: _('Skip face unlock when signed in remotely (SSH)')}));
        behavior.add(this._setting('core', 'abort_if_lid_closed', {title: _('Skip face unlock when the laptop lid is closed')}));
        page.add(behavior);

        const messages = new Adw.PreferencesGroup({title: _('Messages')});
        messages.add(this._setting('core', 'detection_notice', {title: _('Say when a face scan starts')}));
        messages.add(this._setting('core', 'timeout_notice', {title: _('Say when no face was recognized')}));
        page.add(messages);

        const snapshots = new Adw.PreferencesGroup({
            title: _('Snapshots'),
            description: _('More options are available with: linux-hello-cli config'),
        });
        snapshots.add(this._setting('snapshots', 'save_failed', {title: _('Save a picture of failed attempts')}));
        snapshots.add(this._setting('snapshots', 'save_successful', {title: _('Save a picture of successful attempts')}));
        page.add(snapshots);

        connectWhileAlive(this, service, {
            'settings-changed': () => this._update(),
            'cameras-changed': () => this._update(),
            'scanning-changed': () => this._update(),
        });
        this._update();
    }

    _setting(section, key, params, inverted = false) {
        const row = settingRow(this._service, section, key, params, inverted);
        this._settingRows.push(row);
        return row;
    }

    _update() {
        const service = this._service;
        this._updating = true;
        this._settingRows.forEach(row => row.update());

        this._confirmRow.subtitle = this._confirmRow.active
            ? _("Only a key press on this computer's keyboard starts the scan, so other programs cannot use your face to get administrator rights.")
            : _('Any program you run can get administrator rights while you are in front of the camera.');
        this._insecureRow.subtitle = this._insecureRow.active
            ? _('Anyone who has this computer can read your password from a USB stick, along with all your files.')
            : _('Logging in automatically keeps your password to open the keyring, so it needs an encrypted disk.');

        // Automatic first, then what was found, then the configured camera if it was not
        const device = service.setting('video.device_path') || 'none';
        const automatic = ['', 'none', 'auto'].includes(device);
        const choices = [{text: _('Automatic (infrared preferred)'), path: 'none'}];
        for (const camera of service.cameras)
            choices.push({text: camera.infrared ? format(_('%s (infrared)'), camera.name) : camera.name, path: camera.path});
        if (!automatic && !choices.some(choice => choice.path === device))
            choices.push({text: device, path: device});
        this._cameraChoices = choices;
        this._cameraRow.model = Gtk.StringList.new(choices.map(choice => choice.text));
        this._cameraRow.selected = Math.max(0, choices.findIndex(choice => choice.path === (automatic ? 'none' : device)));
        this._findRow.sensitive = !service.findingCameras && !service.scanning;
        this._findSpinner.spinning = service.findingCameras;
        this._emitterRow.selected = service.setting('video.ir_emitter', 'auto').toLowerCase() === 'off' ? 1 : 0;
        this._updating = false;
    }
});
