// The main page: your face, where face unlock is used, and how strict it is

import Adw from 'gi://Adw?version=1';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk?version=4.0';

import {EnrollDialog} from './enroll.js';
import {_, format} from './i18n.js';
import {AdvancedPage, DiagnosticsPage, GUIDE, SecurityPage} from './pages.js';

// Certainty values behind the strictness choices; lower is stricter
const PRESETS = [
    {label: _('Convenient'), value: 4.0},
    {label: _('Balanced'), value: 3.5},
    {label: _('Strict'), value: 3.0},
];

function escape(text) {
    return GLib.markup_escape_text(String(text), -1);
}

export const FaceWindow = GObject.registerClass(class FaceWindow extends Adw.ApplicationWindow {
    _init(application, service) {
        super._init({
            application,
            title: _('Face Unlock'),
            default_width: 640,
            default_height: 780,
            width_request: 360,
            height_request: 400,
        });
        this._service = service;
        this._offeredSetup = false;
        this._updating = false;
        this._modelRows = [];
        this._targetRows = [];

        this._toasts = new Adw.ToastOverlay();
        this._navigation = new Adw.NavigationView();
        this._toasts.set_child(this._navigation);
        this.set_content(this._toasts);
        this._navigation.add(this._buildMainPage());

        this._enrollDialog = null;

        service.connect('status-changed', () => this._updateBanner());
        service.connect('checks-changed', () => this._updateBanner());
        service.connect('models-changed', () => this._onModelsChanged());
        service.connect('targets-changed', () => this._updateTargets());
        service.connect('settings-changed', () => this._updateRecognition());
        service.connect('scanning-changed', () => this._updateModels());
        for (const signal of ['action-failed', 'notice']) {
            service.connect(signal, (_service, message) => {
                this._toasts.add_toast(new Adw.Toast({title: message}));
            });
        }
        this._updateBanner();
        this._updateModels();
        this._updateTargets();
        this._updateRecognition();
    }

    // -- layout ------------------------------------------------------------

    _buildMainPage() {
        const page = new Adw.NavigationPage({title: _('Face Unlock'), tag: 'main'});
        const view = new Adw.ToolbarView();
        view.add_top_bar(new Adw.HeaderBar());
        const box = new Gtk.Box({orientation: Gtk.Orientation.VERTICAL});
        this._banner = new Adw.Banner();
        this._banner.connect('button-clicked', () => this._bannerAction?.());
        box.append(this._banner);
        this._preferences = new Adw.PreferencesPage();
        box.append(this._preferences);
        view.set_content(box);
        page.set_child(view);

        this._modelsGroup = new Adw.PreferencesGroup({title: _('Your Face')});
        this._addRow = new Adw.ActionRow({activatable: true});
        this._addRow.add_prefix(new Gtk.Image({icon_name: 'list-add-symbolic'}));
        this._addRow.connect('activated', () => this._enroll('enroll'));
        this._testRow = new Adw.ActionRow({title: _('Test Face Unlock…'), activatable: true});
        this._testRow.add_prefix(new Gtk.Image({icon_name: 'linux-hello-symbolic'}));
        this._testRow.connect('activated', () => this._enroll('test'));
        this._preferences.add(this._modelsGroup);

        this._targetsGroup = new Adw.PreferencesGroup({title: _('Use Face Unlock For')});
        this._preferences.add(this._targetsGroup);

        const recognition = new Adw.PreferencesGroup({
            title: _('Recognition'),
            description: _('Stricter settings are harder to fool with a similar face, but may need a second look in poor light.'),
        });
        this._strictnessRow = new Adw.ComboRow({title: _('Strictness')});
        this._strictnessRow.connect('notify::selected', () => {
            if (this._updating)
                return;
            const index = this._strictnessRow.selected;
            if (index < PRESETS.length)
                this._service.setSetting('video', 'certainty', PRESETS[index].value.toFixed(1));
        });
        recognition.add(this._strictnessRow);
        this._timeoutRow = new Adw.SpinRow({
            title: _('Look for a face for'),
            subtitle: _('Seconds'),
            adjustment: new Gtk.Adjustment({lower: 1, upper: 30, step_increment: 1, page_increment: 5}),
        });
        this._timeoutRow.connect('notify::value', () => {
            if (this._updating)
                return;
            // Ask the service once the value settles, not on every step
            if (this._timeoutPending)
                GLib.source_remove(this._timeoutPending);
            this._timeoutPending = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 800, () => {
                this._timeoutPending = 0;
                this._service.setSetting('video', 'timeout', String(Math.round(this._timeoutRow.value)));
                return GLib.SOURCE_REMOVE;
            });
        });
        recognition.add(this._timeoutRow);
        this._preferences.add(recognition);

        const more = new Adw.PreferencesGroup();
        for (const [title, icon, Subpage] of [
            [_('Security'), 'security-high-symbolic', SecurityPage],
            [_('Diagnostics'), 'emblem-ok-symbolic', DiagnosticsPage],
            [_('Advanced'), 'preferences-other-symbolic', AdvancedPage],
        ]) {
            const row = new Adw.ActionRow({title, activatable: true});
            row.add_prefix(new Gtk.Image({icon_name: icon}));
            row.add_suffix(new Gtk.Image({icon_name: 'go-next-symbolic'}));
            row.connect('activated', () => this._navigation.push(new Subpage(this._service)));
            more.add(row);
        }
        this._preferences.add(more);
        return page;
    }

    // -- the banner: one line on the state of things, or the most important problem

    _updateBanner() {
        const service = this._service;
        const errors = service.checks.filter(check => check.state === 'error');
        let text;
        let button = null;
        let action = null;
        if (!service.available) {
            text = service.error;
        } else if (errors.length > 0) {
            text = errors[0].message;
            button = _('Details');
            action = () => this._navigation.push(new DiagnosticsPage(service));
        } else if (service.status.disabled) {
            text = _('Face unlock is turned off.');
            button = _('Turn On');
            action = () => service.setSetting('core', 'disabled', 'false');
        } else if (!service.hasFaces) {
            text = _('Add your face to start using face unlock.');
            button = _('Add Face…');
            action = () => this._enroll('enroll');
        } else if (service.enabledTargets.length === 0) {
            text = _('Face unlock is not used anywhere yet. Choose where below.');
        } else {
            text = format(_('Face unlock is on for: %s'), service.enabledTargets.map(target => target.label).join(', '));
        }
        this._banner.title = text;
        this._banner.button_label = button ?? '';
        this._bannerAction = action;
        this._banner.revealed = true;
        this._preferences.sensitive = service.available;
    }

    // -- your face -----------------------------------------------------------

    _onModelsChanged() {
        this._updateModels();
        this._updateBanner();
        this._updateTargets();
        // Nothing to set up on this page without a face, so start there, once
        if (!this._offeredSetup) {
            this._offeredSetup = true;
            if (!this._service.hasFaces && !this._service.scanning)
                this._enroll('enroll');
        }
    }

    _updateModels() {
        const service = this._service;
        this._modelRows.forEach(row => this._modelsGroup.remove(row));
        // Behind the faces, so taken out and put back when those change
        for (const row of [this._addRow, this._testRow]) {
            if (row.get_parent())
                this._modelsGroup.remove(row);
        }
        this._modelRows = service.models.map(model => {
            const row = new Adw.ActionRow({title: escape(model.label)});
            if (model.time) {
                const date = GLib.DateTime.new_from_unix_local(model.time).format('%x');
                row.subtitle = escape(format(_('Added %s'), date));
            }
            const rename = new Gtk.Button({icon_name: 'document-edit-symbolic', tooltip_text: _('Rename'), valign: Gtk.Align.CENTER, css_classes: ['flat']});
            rename.connect('clicked', () => this._rename(model));
            row.add_suffix(rename);
            const remove = new Gtk.Button({icon_name: 'user-trash-symbolic', tooltip_text: _('Remove'), valign: Gtk.Align.CENTER, css_classes: ['flat']});
            remove.connect('clicked', () => this._remove(model));
            row.add_suffix(remove);
            this._modelsGroup.add(row);
            return row;
        });
        this._addRow.title = service.hasFaces ? _('Improve Recognition…') : _('Add Face…');
        this._addRow.subtitle = service.hasFaces ? _('Scan your face again, for example in other light or with glasses') : '';
        this._modelsGroup.add(this._addRow);
        this._testRow.visible = service.hasFaces;
        this._modelsGroup.add(this._testRow);
        this._addRow.sensitive = !service.scanning;
        this._testRow.sensitive = !service.scanning;
    }

    _enroll(mode) {
        if (!this._enrollDialog) {
            this._enrollDialog = new EnrollDialog(this._service);
            this._enrollDialog.connect('improve-requested', () => this._enrollDialog.start('enroll'));
            this._enrollDialog.connect('closed', () => {
                this._enrollDialog = null;
            });
            this._enrollDialog.start(mode);
            this._enrollDialog.present(this);
        } else {
            this._enrollDialog.start(mode);
        }
    }

    _rename(model) {
        const dialog = new Adw.AlertDialog({heading: _('Rename Face')});
        const entry = new Gtk.Entry({text: model.label, activates_default: true});
        dialog.set_extra_child(entry);
        dialog.add_response('cancel', _('Cancel'));
        dialog.add_response('rename', _('Rename'));
        dialog.set_response_appearance('rename', Adw.ResponseAppearance.SUGGESTED);
        dialog.default_response = 'rename';
        dialog.close_response = 'cancel';
        dialog.connect('response', (_dialog, response) => {
            const label = entry.text.trim();
            if (response === 'rename' && label !== '' && label !== model.label)
                this._service.renameModel(model.id, label);
        });
        dialog.present(this);
        entry.grab_focus();
    }

    _remove(model) {
        const dialog = new Adw.AlertDialog({
            heading: _('Remove Face'),
            body: this._service.models.length > 1
                ? format(_('“%s” will no longer be recognized.'), model.label)
                : format(_('“%s” is your only face. Face unlock stops working until you add one again; your password keeps working.'), model.label),
        });
        dialog.add_response('cancel', _('Cancel'));
        dialog.add_response('remove', _('Remove'));
        dialog.set_response_appearance('remove', Adw.ResponseAppearance.DESTRUCTIVE);
        dialog.close_response = 'cancel';
        dialog.connect('response', (_dialog, response) => {
            if (response === 'remove')
                this._service.deleteModel(model.id);
        });
        dialog.present(this);
    }

    // -- where face unlock is used --------------------------------------------

    _updateTargets() {
        const service = this._service;
        this._updating = true;
        if (this._targetRows.length !== service.targets.length) {
            this._targetRows.forEach(row => this._targetsGroup.remove(row));
            this._targetRows = service.targets.map(target => {
                const row = new Adw.SwitchRow();
                row.connect('notify::active', () => {
                    if (!this._updating)
                        service.setTarget(target.id, row.active);
                });
                this._targetsGroup.add(row);
                return row;
            });
        }
        service.targets.forEach((target, index) => {
            const row = this._targetRows[index];
            // Keeps the password for the keyring, which an encrypted disk
            // keeps out of reach of a USB stick
            const needsEncryption = target.id === 'auto-login' && !service.status.home_encrypted &&
                !service.settingIsTrue('core.insecure_auto_login');
            const lines = [escape(target.available ? target.description : _('Not available on this system'))];
            if (target.available && needsEncryption) {
                lines.push(format(_('Needs an encrypted disk, so the password kept for the keyring cannot be read from a USB stick. %s'), 
                    `<a href="${GUIDE}">${escape(_('How to set it up'))}</a>`));
            }
            if (target.id === 'auto-login' && target.enabled && service.status.login_secret_missing)
                lines.push(escape(_('Log out, wait a few seconds, and log in with your password once. From then on the keyring opens by itself.')));
            row.title = escape(target.label);
            row.subtitle = lines.join('\n');
            row.active = target.enabled;
            // Turning one on without a face would do nothing
            row.sensitive = target.available && (service.hasFaces || target.enabled) && (!needsEncryption || target.enabled);
        });
        this._updating = false;
    }

    // -- recognition ------------------------------------------------------------

    _updateRecognition() {
        const service = this._service;
        this._updating = true;
        const certainty = parseFloat(service.setting('video.certainty'));
        const preset = PRESETS.findIndex(item => Math.abs(item.value - certainty) < 0.01);
        const labels = PRESETS.map(item => item.label);
        if (preset < 0)
            labels.push(format(_('Custom (%s)'), certainty));
        this._strictnessRow.model = Gtk.StringList.new(labels);
        this._strictnessRow.selected = preset >= 0 ? preset : PRESETS.length;
        this._timeoutRow.value = parseInt(service.setting('video.timeout')) || 4;
        this._updating = false;
    }
});
