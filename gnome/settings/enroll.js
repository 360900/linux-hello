// Adding a face, then trying it right away. Also opened for a test only.

import Adw from 'gi://Adw?version=1';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk?version=4.0';

import {FaceFrame} from './faceframe.js';
import {_, format} from './i18n.js';
import {Result} from './service.js';

const SAMPLES = 5;

export const EnrollDialog = GObject.registerClass({
    Signals: {'improve-requested': {}},
}, class EnrollDialog extends Adw.Dialog {
    _init(service) {
        super._init({content_width: 440, follows_content_size: true});
        this._service = service;
        // intro, scan, failed, testing, result
        this._stage = 'intro';
        this._message = '';
        this._recognized = false;
        this._certainty = -1;
        this._build();

        const ids = [
            service.connect('enroll-finished', (_service, result) => this._onEnrollFinished(result)),
            service.connect('verify-finished', (_service, result, certainty) => this._onVerifyFinished(result, certainty)),
            service.connect('enroll-progress', () => this._updateScan()),
            service.connect('preview-changed', () => this._updatePreview()),
        ];
        this.connect('closed', () => {
            ids.forEach(id => service.disconnect(id));
            if (this._stage === 'scan' || this._stage === 'testing')
                service.cancel();
        });
    }

    get _threshold() {
        return parseFloat(this._service.setting('video.certainty')) || 3.5;
    }

    start(mode) {
        this._name.text = '';
        if (mode === 'test')
            this._test();
        else
            this._setStage('intro');
    }

    _test() {
        this._setStage('testing');
        this._service.verify();
    }

    _enroll() {
        this._setStage('scan');
        this._service.enroll(this._name.text.trim());
    }

    // -- the pages ----------------------------------------------------------

    _build() {
        const view = new Adw.ToolbarView();
        const header = new Adw.HeaderBar({show_start_title_buttons: false, show_end_title_buttons: false});
        this._cancelButton = new Gtk.Button({label: _('Cancel')});
        this._cancelButton.connect('clicked', () => this.close());
        header.pack_start(this._cancelButton);
        this._primaryButton = new Gtk.Button({css_classes: ['suggested-action']});
        this._primaryButton.connect('clicked', () => this._primary());
        header.pack_end(this._primaryButton);
        view.add_top_bar(header);

        this._stack = new Gtk.Stack({transition_type: Gtk.StackTransitionType.CROSSFADE, vhomogeneous: false});
        this._stack.add_named(this._buildIntro(), 'intro');
        this._stack.add_named(this._buildScan(), 'scan');
        this._stack.add_named(this._buildFailed(), 'failed');
        this._stack.add_named(this._buildTesting(), 'testing');
        this._stack.add_named(this._buildResult(), 'result');
        view.set_content(this._stack);
        this.set_child(view);
    }

    _page() {
        return new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            spacing: 18,
            margin_top: 12,
            margin_bottom: 24,
            margin_start: 24,
            margin_end: 24,
        });
    }

    _buildIntro() {
        const page = this._page();
        this._introText = new Gtk.Label({wrap: true, xalign: 0});
        page.append(this._introText);
        page.append(new Gtk.Label({label: _('Look at the camera from where you usually sit.'), wrap: true, xalign: 0}));
        const group = new Adw.PreferencesGroup();
        this._name = new Adw.EntryRow({title: _('Name (optional)')});
        this._name.connect('entry-activated', () => this._primary());
        group.add(this._name);
        page.append(group);
        return page;
    }

    _buildScan() {
        const page = this._page();
        this._scanFrame = new FaceFrame({halign: Gtk.Align.CENTER, width_request: 280});
        page.append(this._scanFrame);
        this._scanHint = new Gtk.Label({css_classes: ['title-3'], wrap: true, justify: Gtk.Justification.CENTER});
        page.append(this._scanHint);
        this._dots = [];
        const dots = new Gtk.Box({spacing: 6, halign: Gtk.Align.CENTER});
        for (let index = 0; index < SAMPLES; index++) {
            const dot = new Gtk.Image({icon_name: 'radio-symbolic', pixel_size: 16});
            this._dots.push(dot);
            dots.append(dot);
        }
        page.append(dots);
        return page;
    }

    _buildFailed() {
        const page = this._page();
        this._failedStatus = new Adw.StatusPage({
            icon_name: 'dialog-warning-symbolic',
            title: _('Your face could not be added'),
            css_classes: ['compact'],
        });
        page.append(this._failedStatus);
        return page;
    }

    _buildTesting() {
        const page = this._page();
        this._testFrame = new FaceFrame({halign: Gtk.Align.CENTER, width_request: 280});
        page.append(this._testFrame);
        page.append(new Gtk.Label({label: _('Look at the camera'), css_classes: ['title-3']}));
        page.append(new Gtk.Label({label: _('This works exactly like unlocking.'), css_classes: ['dim-label'], wrap: true}));
        return page;
    }

    _buildResult() {
        const page = this._page();
        this._resultStatus = new Adw.StatusPage({css_classes: ['compact']});
        page.append(this._resultStatus);
        this._resultDetail = new Gtk.Label({css_classes: ['dim-label', 'caption'], wrap: true, justify: Gtk.Justification.CENTER});
        page.append(this._resultDetail);
        const buttons = new Gtk.Box({spacing: 12, halign: Gtk.Align.CENTER});
        this._improveButton = new Gtk.Button({label: _('Improve Recognition…'), css_classes: ['pill']});
        this._improveButton.connect('clicked', () => this.emit('improve-requested'));
        buttons.append(this._improveButton);
        const again = new Gtk.Button({label: _('Test Again'), css_classes: ['pill']});
        again.connect('clicked', () => this._test());
        buttons.append(again);
        page.append(buttons);
        return page;
    }

    // -- state --------------------------------------------------------------

    _setStage(stage) {
        this._stage = stage;
        this._stack.visible_child_name = stage;
        const hasFaces = this._service.hasFaces;
        this.title = stage === 'testing' || stage === 'result' ? _('Test Face Unlock')
            : hasFaces ? _('Improve Recognition') : _('Add Your Face');
        this._introText.label = hasFaces
            ? _('Scan your face again to be recognized in more situations, for example in other light or with glasses.')
            : _('Linux Hello keeps a description of your face, not a picture, and only on this computer. Your password keeps working.');
        this._cancelButton.visible = stage !== 'result';
        const primary = {intro: _('Start'), failed: _('Try Again'), result: _('Done')}[stage];
        this._primaryButton.visible = primary !== undefined;
        this._primaryButton.label = primary ?? '';
        if (stage === 'failed')
            this._failedStatus.description = this._message;
        if (stage === 'result')
            this._updateResult();
        this._updateScan();
        this._updatePreview();
    }

    _primary() {
        switch (this._stage) {
        case 'intro':
        case 'failed':
            this._enroll();
            break;
        case 'result':
            this.close();
            break;
        }
    }

    _updateScan() {
        const service = this._service;
        this._scanHint.label = this._scanText();
        this._scanFrame.face = service.enrollFace;
        this._scanFrame.good = service.enrollState === 'captured';
        this._dots.forEach((dot, index) => {
            dot.icon_name = index < service.enrollSample ? 'radio-checked-symbolic' : 'radio-symbolic';
        });
    }

    _updatePreview() {
        this._scanFrame.preview = this._stage === 'scan' ? this._service.preview : null;
        this._testFrame.preview = this._stage === 'testing' ? this._service.preview : null;
    }

    _updateResult() {
        const recognized = this._recognized;
        const close = recognized && this._certainty > this._threshold * 0.8;
        this._resultStatus.icon_name = recognized ? 'emblem-ok-symbolic' : 'dialog-warning-symbolic';
        this._resultStatus.title = recognized ? _('Recognized') : _('Not recognized');
        this._resultStatus.description = this._message !== '' ? this._message
            : !recognized ? _('Try again facing the camera. If it keeps failing in this light, improve recognition.')
            : close ? _('It worked, but only just. Improving recognition in this light makes it more reliable.')
            : _('A clear match.');
        this._resultDetail.visible = this._certainty >= 0;
        // Lower is closer; a match needs a value below the strictness limit
        this._resultDetail.label = format(_('Difference %s, limit %s'), this._certainty.toFixed(2), this._threshold.toFixed(1));
        this._improveButton.visible = !recognized || close;
    }

    _scanText() {
        switch (this._service.enrollState) {
        case '':
            return _('Getting ready…');
        case 'searching':
            return _('Looking for your face…');
        case 'no-face':
            return _('Look at the camera');
        case 'multiple-faces':
            return _('Make sure only your face is in view');
        case 'too-far':
            return _('Move closer');
        case 'too-dark':
            return _('It is too dark');
        default:
            return _('Hold still…');
        }
    }

    _failureText(result) {
        switch (result) {
        case Result.NO_FACE_MODEL:
            return _('No face has been added yet.');
        case Result.TIMEOUT:
            return _('No face was seen in time. Look straight at the camera and try again.');
        case Result.TOO_DARK:
            return _('The camera picture is too dark. If your camera has an infrared light, see Diagnostics.');
        case Result.CAMERA_UNAVAILABLE:
            return _('The camera could not be used. Another app may be using it.');
        case Result.NOT_AUTHORIZED:
            return _('Adding a face needs your password.');
        default:
            return _('Something went wrong. See Diagnostics for details.');
        }
    }

    _onEnrollFinished(result) {
        if (this._stage !== 'scan')
            return;
        if (result === Result.SUCCESS) {
            // Straight to trying it, like the real thing
            this._test();
        } else if (result === Result.CANCELLED) {
            this.close();
        } else {
            this._message = this._failureText(result);
            this._setStage('failed');
        }
    }

    _onVerifyFinished(result, certainty) {
        if (this._stage !== 'testing')
            return;
        if (result === Result.CANCELLED) {
            this.close();
            return;
        }
        this._recognized = result === Result.SUCCESS;
        this._certainty = certainty;
        this._message = result === Result.SUCCESS || result === Result.TIMEOUT ? '' : this._failureText(result);
        this._setStage('result');
    }
});
