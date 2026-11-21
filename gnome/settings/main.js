// Face Unlock: the Linux Hello settings for GNOME, a GTK client of the
// Linux Hello service. Started by its desktop entry or linux-hello-settings.

import Adw from 'gi://Adw?version=1';
import Gio from 'gi://Gio';
import System from 'system';

import {Service} from './service.js';
import {FaceWindow} from './window.js';

const application = new Adw.Application({
    application_id: 'io.github.linuxhello.FaceUnlock',
    flags: Gio.ApplicationFlags.DEFAULT_FLAGS,
});

application.connect('activate', () => {
    let window = application.active_window;
    if (!window)
        window = new FaceWindow(application, new Service());
    window.present();
});

application.run([System.programInvocationName, ...ARGV]);
