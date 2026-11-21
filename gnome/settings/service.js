// Client of the Linux Hello service (io.github.LinuxHello1, see
// linux-hello/src/daemon/io.github.LinuxHello1.xml), the counterpart of the
// KCM's FaceModule. Every change is applied right away by the service, which
// asks polkit, so the pages have no Apply button. Replies land in the fields
// below and a signal says which changed.

import Gdk from 'gi://Gdk?version=4.0';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import {_} from './i18n.js';
import * as ShellExtension from './shellextension.js';

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

const NAME = 'io.github.LinuxHello1';
const PATH = '/io/github/LinuxHello1';
const INTERFACE = 'io.github.LinuxHello1.Manager';
const ERROR_PREFIX = 'io.github.LinuxHello1.Error.';

// Quick reads; the service may have to start first
const QUICK = 30 * 1000;
// Calls that can wait for a polkit prompt, or scan with the camera after one
const INTERACTIVE = 10 * 60 * 1000;

// Result codes of Verify and Enroll
export const Result = {
    SUCCESS: 0,
    NO_FACE_MODEL: 10,
    TIMEOUT: 11,
    ABORTED: 12,
    TOO_DARK: 13,
    CAMERA_UNAVAILABLE: 14,
    RUBBERSTAMP_FAILED: 15,
    CANCELLED: 250,
    // Not from the service: it could not be reached or refused
    FAILED: 251,
    NOT_AUTHORIZED: 252,
};

class ServiceError extends Error {
    constructor(error) {
        const remote = error instanceof GLib.Error && Gio.DBusError.is_remote_error(error)
            ? Gio.DBusError.get_remote_error(error) : '';
        let message = error.message;
        if (remote) {
            const copy = error.copy();
            Gio.DBusError.strip_remote_error(copy);
            message = copy.message;
        }
        super(message);
        this.remote = remote;
        this.unreachable = error instanceof GLib.Error && (
            error.matches(Gio.DBusError, Gio.DBusError.SERVICE_UNKNOWN) ||
            error.matches(Gio.DBusError, Gio.DBusError.NO_REPLY) ||
            error.matches(Gio.DBusError, Gio.DBusError.TIMED_OUT) ||
            error.matches(Gio.DBusError, Gio.DBusError.NAME_HAS_NO_OWNER) ||
            error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CLOSED));
    }

    is(name) {
        return this.remote === ERROR_PREFIX + name;
    }

    describe() {
        if (this.unreachable)
            return _('The Linux Hello service is not responding. Details: journalctl -u linux-hello-daemon');
        if (this.is('PermissionDenied'))
            return _('You are not allowed to make this change.');
        return this.message;
    }
}

export const Service = GObject.registerClass({
    Signals: {
        'status-changed': {},
        'models-changed': {},
        'targets-changed': {},
        'settings-changed': {},
        'checks-changed': {},
        'security-changed': {},
        'cameras-changed': {},
        'scanning-changed': {},
        'enroll-progress': {},
        'preview-changed': {},
        'enroll-finished': {param_types: [GObject.TYPE_INT, GObject.TYPE_UINT]},
        // certainty: the best distance seen times 10, lower is closer; -1 if no face
        'verify-finished': {param_types: [GObject.TYPE_INT, GObject.TYPE_DOUBLE]},
        // A change was refused or failed; the page shows its old state again
        'action-failed': {param_types: [GObject.TYPE_STRING]},
        // Something the user should know after a change that went through
        'notice': {param_types: [GObject.TYPE_STRING]},
    },
}, class Service extends GObject.Object {
    _init() {
        super._init();
        this._bus = Gio.DBus.system;

        // The service answered GetStatus
        this.available = false;
        this.error = '';
        this.status = {};
        this.models = [];
        this.targets = [];
        this.settings = {};
        this.checks = [];
        this.security = {};
        this.cameras = [];
        this.findingCameras = false;

        // Enrolment and verification in progress, one at a time
        this.scanning = false;
        // searching, no-face, multiple-faces, too-far, too-dark, captured;
        // empty until the camera is running
        this.enrollState = '';
        this.enrollSample = 0;
        this.enrollSamples = 0;
        // [left, top, right, bottom] as fractions of the camera picture, or null
        this.enrollFace = null;
        // What the camera sees while scanning, or null
        this.preview = null;

        this._subscription = this._bus.signal_subscribe(NAME, INTERFACE, null, PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, signal, params) => {
                this._onSignal(signal, params);
            });
        this.refresh();
    }

    get user() {
        return this.status.user || GLib.get_user_name();
    }

    get hasFaces() {
        return this.models.length > 0;
    }

    get enabledTargets() {
        return this.targets.filter(target => target.enabled);
    }

    setting(name, fallback = '') {
        return this.settings[name] ?? fallback;
    }

    settingIsTrue(name) {
        return ['true', 'yes', '1', 'on'].includes(String(this.setting(name)).toLowerCase());
    }

    // -- calls --------------------------------------------------------------

    async _call(method, params = null, timeout = QUICK) {
        try {
            return await this._bus.call(NAME, PATH, INTERFACE, method, params, null,
                Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, null);
        } catch (error) {
            throw new ServiceError(error);
        }
    }

    // Reads whose failure only means the page stays as it is
    async _load(method, params, assign) {
        try {
            assign((await this._call(method, params)).recursiveUnpack());
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
        }
    }

    // Shows the error, then reloads what the action may have left stale
    async _change(method, params, reload) {
        try {
            await this._call(method, params, INTERACTIVE);
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
            // A dismissed password prompt needs no message
            if (!error.is('PermissionDenied'))
                this.emit('action-failed', error.describe());
        }
        await reload();
    }

    refresh() {
        this.loadStatus();
        this.loadModels();
        this.loadSettings();
        // The extension checks depend on the lock screen target
        this.loadTargets().then(() => this.diagnose());
    }

    async loadStatus() {
        try {
            this.status = (await this._call('GetStatus')).recursiveUnpack()[0];
            this.available = true;
            this.error = '';
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
            this.available = false;
            this.error = error.describe();
        }
        this.emit('status-changed');
    }

    loadModels() {
        return this._load('ListModels', new GLib.Variant('(s)', [this.user]), ([models]) => {
            this.models = models.map(([id, time, label]) => ({id, time, label}));
            this.emit('models-changed');
        });
    }

    async loadTargets() {
        await this._load('ListTargets', null, ([targets]) => {
            this.targets = targets.map(([id, label, description, available, enabled, services]) =>
                ({id, label, description, available, enabled, services}));
            this.emit('targets-changed');
        });
        // Whether auto-login still waits for a login with the password
        await this.loadStatus();
    }

    async loadSettings() {
        await this._load('GetSettings', null, ([settings]) => {
            this.settings = settings;
            this.emit('settings-changed');
        });
        // The global switch is part of the status
        await this.loadStatus();
    }

    async diagnose() {
        let checks = null;
        await this._load('Diagnose', null, ([reply]) => {
            checks = reply.map(([check, state, message]) => ({check, state, message}));
        });
        if (checks === null)
            return;
        // The service cannot see this user's GNOME Shell
        const lockScreen = this.targets.find(target => target.id === 'lock-screen');
        this.checks = [...checks, ...await ShellExtension.checks(lockScreen)];
        this.emit('checks-changed');
    }

    checkSecurity() {
        return this._load('SecurityStatus', null, ([security]) => {
            this.security = security;
            this.emit('security-changed');
        });
    }

    async findCameras() {
        if (this.findingCameras)
            return;
        this.findingCameras = true;
        this.emit('cameras-changed');
        try {
            const [cameras] = (await this._call('ListCameras', null, INTERACTIVE)).recursiveUnpack();
            this.cameras = cameras.map(([path, node, name, infrared, brightness]) =>
                ({path, node, name, infrared, brightness}));
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
            if (!error.is('PermissionDenied'))
                this.emit('action-failed', error.describe());
        }
        this.findingCameras = false;
        this.emit('cameras-changed');
    }

    // -- enrolment and verification ------------------------------------------

    async enroll(label) {
        if (this.scanning)
            return;
        this.enrollState = '';
        this.enrollSample = 0;
        this.enrollSamples = 0;
        this.enrollFace = null;
        this.emit('enroll-progress');
        this._startScan();
        try {
            const [result, modelId] = (await this._call('Enroll', new GLib.Variant('(ss)', [this.user, label]), INTERACTIVE)).deepUnpack();
            this._finishScan();
            this.emit('enroll-finished', result, modelId);
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
            this._finishScan();
            if (error.is('PermissionDenied')) {
                this.emit('enroll-finished', Result.NOT_AUTHORIZED, 0);
            } else if (error.is('Cancelled')) {
                this.emit('enroll-finished', Result.CANCELLED, 0);
            } else {
                this.emit('action-failed', error.describe());
                this.emit('enroll-finished', Result.FAILED, 0);
            }
        }
    }

    async verify() {
        if (this.scanning)
            return;
        this._startScan();
        try {
            const [result, certainty] = (await this._call('VerifyNow', new GLib.Variant('(s)', [this.user]), INTERACTIVE)).deepUnpack();
            this._finishScan();
            this.emit('verify-finished', result, certainty);
        } catch (error) {
            if (!(error instanceof ServiceError))
                throw error;
            this._finishScan();
            this.emit('action-failed', error.describe());
            this.emit('verify-finished', error.is('Cancelled') ? Result.CANCELLED : Result.FAILED, -1);
        }
    }

    cancel() {
        if (this.scanning)
            this._call('Cancel').catch(() => {});
    }

    _startScan() {
        this.scanning = true;
        // Not the last picture of the previous scan
        this.preview = null;
        this.emit('scanning-changed');
        this.emit('preview-changed');
    }

    _finishScan() {
        this.scanning = false;
        this.emit('scanning-changed');
    }

    _onSignal(signal, params) {
        switch (signal) {
        case 'EnrollProgress': {
            const [user, update] = params.recursiveUnpack();
            if (!this.scanning || user !== this.user)
                return;
            this.enrollState = update.state ?? '';
            this.enrollSample = update.sample ?? 0;
            this.enrollSamples = update.samples ?? 0;
            this.enrollFace = update.face?.length === 4 ? update.face : null;
            this.emit('enroll-progress');
            break;
        }
        case 'Preview': {
            const user = params.get_child_value(0).get_string()[0];
            if (!this.scanning || user !== this.user)
                return;
            try {
                this.preview = Gdk.Texture.new_from_bytes(params.get_child_value(1).get_data_as_bytes());
            } catch {
                return;
            }
            this.emit('preview-changed');
            break;
        }
        case 'ModelsChanged':
            if (params.get_child_value(0).get_string()[0] === this.user) {
                this.loadModels();
                this.loadStatus();
                this.diagnose();
            }
            break;
        case 'TargetsChanged':
            this.loadTargets();
            break;
        case 'SettingsChanged':
            this.loadSettings();
            break;
        }
    }

    // -- changes ---------------------------------------------------------------

    renameModel(id, label) {
        return this._change('RenameModel', new GLib.Variant('(sus)', [this.user, id, label]), () => this.loadModels());
    }

    deleteModel(id) {
        return this._change('DeleteModel', new GLib.Variant('(su)', [this.user, id]), () => this.loadModels());
    }

    setTarget(id, enabled) {
        return this._change('SetTarget', new GLib.Variant('(sb)', [id, enabled]), async () => {
            await this.loadTargets();
            // The GNOME lock screen starts the face stack only through our
            // shell extension: on and off with the target, as the CLI does
            const target = this.targets.find(t => t.id === id);
            if (ShellExtension.usedBy(target) && target.enabled === enabled) {
                const notice = await ShellExtension.setEnabled(enabled);
                if (notice)
                    this.emit('notice', notice);
            }
            await this.diagnose();
        });
    }

    setSetting(section, key, value) {
        return this._change('SetSetting', new GLib.Variant('(sss)', [section, key, value]), async () => {
            await this.loadSettings();
            await this.diagnose();
        });
    }

    // -- diagnostics -----------------------------------------------------------

    report() {
        const lines = [`Linux Hello ${this.status.version ?? ''}`];
        if (!this.available)
            lines.push(`service: ${this.error}`);
        for (const {check, state, message} of this.checks)
            lines.push(`${check} [${state}] ${message}`);
        for (const {id, enabled, services} of this.targets)
            lines.push(`target ${id}: ${enabled ? 'on' : 'off'} (${services.join(',')})`);
        lines.push(`face models: ${this.models.length}`);
        for (const [key, value] of Object.entries(this.settings)) {
            if (key.startsWith('video.') || key.startsWith('core.'))
                lines.push(`${key} = ${value}`);
        }
        return lines.join('\n');
    }
});
