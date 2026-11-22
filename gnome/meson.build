# GNOME: the shell extension that scans on the lock screen and the Face
# Unlock settings app. Plain JavaScript run by gnome-shell and gjs, nothing
# to compile.

datadir = get_option('prefix') / get_option('datadir')

# Enabled per user, see integration/gnomeshell.py
extension_dir = datadir / 'gnome-shell' / 'extensions' / 'linux-hello@360900.github.io'
install_data(
    'extension/extension.js',
    'extension/metadata.json',
    install_dir: extension_dir,
    install_tag: 'gnome',
)

settings_dir = datadir / 'linux-hello' / 'settings'
install_data(
    'settings/enroll.js',
    'settings/faceframe.js',
    'settings/i18n.js',
    'settings/main.js',
    'settings/pages.js',
    'settings/service.js',
    'settings/shellextension.js',
    'settings/window.js',
    install_dir: settings_dir,
    install_tag: 'gnome',
)
install_data(
    configure_file(
        input: 'linux-hello-settings.in',
        output: 'linux-hello-settings',
        configuration: configuration_data({'settings_dir': settings_dir}),
    ),
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('bindir'),
    install_tag: 'gnome',
)
install_data(
    'io.github.linuxhello.FaceUnlock.desktop',
    install_dir: datadir / 'applications',
    install_tag: 'gnome',
)

# Locks sessions that GDM started without a password, like Plasma's
install_symlink(
    'linux-hello-lock-guard.service',
    pointing_to: '../linux-hello-lock-guard.service',
    install_dir: user_unit_dir / 'gnome-session.target.wants',
    install_tag: 'gnome',
)
