// Linux Hello for GNOME Shell: face unlock on the lock screen.
//
// When the unlock dialog shows, gnome-shell starts the "gdm-password" PAM
// stack and, for users with a fingerprint enrolled, "gdm-fingerprint" next to
// it. This extension starts Linux Hello's "gdm-face" stack the same way
// (created by "linux-hello-cli enable lock-screen"), so the password field
// and the camera work at the same time. A scan that found no face is started
// again for as long as the dialog is up; the Linux Hello service pauses the
// camera after a few of those until there is input activity.
//
// Nothing here decides anything: GDM runs the stack as root and reports the
// outcome, like for a fingerprint.

import GLib from 'gi://GLib';

import {Extension, InjectionManager, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

const FACE_SERVICE = 'gdm-face';
// Only the lock-screen target creates it; without it there is nothing to start
const FACE_STACK = '/etc/pam.d/gdm-face';

function faceStackInstalled() {
    return GLib.file_test(FACE_STACK, GLib.FileTest.IS_REGULAR);
}

// Whether a verifier (or, on GNOME 51, its legacy authentication services)
// should start the face stack now: a known user, the stack installed and not
// already running or refused by GDM
function wantsFace(verifier) {
    return !!verifier._userName &&
        faceStackInstalled() &&
        !verifier._unavailableServices?.has(FACE_SERVICE) &&
        !verifier._activeServices?.has(FACE_SERVICE);
}

export default class LinuxHelloExtension extends Extension {
    enable() {
        // Shown under the password field while the camera looks, like the
        // fingerprint hint
        this._hint = _('(or look at the camera)');
        this._injections = new InjectionManager();
        this._patch().catch(error => console.error(`Linux Hello: ${error}`));
    }

    disable() {
        this._injections?.clear();
        this._injections = null;
    }

    async _patch() {
        // GNOME 51 moved the PAM services out of ShellUserVerifier into
        // authentication service classes; the modules tell the layouts apart
        let legacy = null;
        try {
            legacy = await import('resource:///org/gnome/shell/gdm/authServicesLegacy.js');
        } catch {
            legacy = null;
        }
        if (!this._injections)
            return;
        if (legacy)
            this._patchAuthServices(legacy, await import('resource:///org/gnome/shell/gdm/userVerifier.js'));
        else
            this._patchUserVerifier(await import('resource:///org/gnome/shell/gdm/util.js'));
    }

    // GNOME 46 to 50: ShellUserVerifier starts the services itself and
    // retries a stopped one that is not in the foreground
    _patchUserVerifier(Util) {
        const proto = Util.ShellUserVerifier.prototype;
        const {MessageType} = Util;
        const hint = this._hint;

        this._injections.overrideMethod(proto, '_beginVerification',
            original => function (...args) {
                original.call(this, ...args);
                if (wantsFace(this))
                    this._startService(FACE_SERVICE);
            });

        this._injections.overrideMethod(proto, '_onConversationStarted',
            original => function (client, serviceName) {
                original.call(this, client, serviceName);
                if (serviceName === FACE_SERVICE)
                    this._queueMessage(serviceName, hint, MessageType.HINT);
            });

        // A scan without a match is no failure of the dialog: the password
        // keeps its state, the camera looks again
        this._injections.overrideMethod(proto, '_onConversationStopped',
            original => function (client, serviceName) {
                if (serviceName !== FACE_SERVICE) {
                    original.call(this, client, serviceName);
                    return;
                }
                this._activeServices.delete(serviceName);
                this._filterServiceMessages(serviceName, MessageType.ERROR);
                if (!this._unavailableServices.has(serviceName) && this._canRetry())
                    this._retry(serviceName);
            });
    }

    // GNOME 51 and later: AuthServicesLegacy owns gdm-password and
    // gdm-fingerprint
    _patchAuthServices(Legacy, Verifier) {
        const proto = Legacy.AuthServicesLegacy.prototype;
        const {MessageType} = Verifier;
        const hint = this._hint;
        const start = services => {
            services._startService(FACE_SERVICE, services._cancellable)
                .catch(error => console.debug(`Linux Hello: ${FACE_SERVICE} not started: ${error}`));
        };

        this._injections.overrideMethod(proto, '_handleBeginVerification',
            original => function (...args) {
                original.call(this, ...args);
                if (wantsFace(this))
                    start(this);
            });

        this._injections.overrideMethod(proto, '_handleOnConversationStarted',
            original => function (serviceName) {
                original.call(this, serviceName);
                if (serviceName === FACE_SERVICE)
                    this.emit('queue-message', {serviceName, message: hint, messageType: MessageType.HINT});
            });

        // Not through _verificationFailed: that clears the password field
        // and restarts every service
        this._injections.overrideMethod(proto, '_handleOnConversationStopped',
            original => function (serviceName) {
                if (serviceName !== FACE_SERVICE) {
                    original.call(this, serviceName);
                    return;
                }
                if (this._userVerifier && !this._unavailableServices.has(serviceName) && this._canRetry())
                    start(this);
            });
    }
}
