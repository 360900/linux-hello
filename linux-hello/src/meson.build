if meson.is_subproject()
project('linux-hello', 'cpp', license: 'GPL-3.0', version: 'beta', meson_version: '>= 0.64.0')
endif


datadir = get_option('prefix') / get_option('datadir') / 'linux-hello'
py_conf = configuration_data(paths_dict)
py_conf.set('data_dir', datadir)

if use_bundled_python
    # The bundled interpreter lives at a fixed install path; no need to
    # discover it at configure time (it may not exist yet).
    py_full_path = paths_dict.get('python_path')
    py_install_dir = ''
else
    py = import('python').find_installation(paths_dict.get('python_path'))
    py.dependency()
    py_full_path = py.full_path()
    py_install_dir = py.get_install_dir()
endif

py_paths = configure_file(
    input: 'paths.py.in',
    output: 'paths.py',
    configuration: py_conf,
)

py_sources = [
    'cli/__init__.py',
    'cli/config.py',
    'cli/emitter.py',
    'cli/snap.py',
    'cli/test.py',
    'cli/webauthn.py',
    'cli.py',
    'client.py',
    'daemon/__init__.py',
    'daemon/access.py',
    'daemon/dispatch.py',
    'daemon/io.github.LinuxHello1.xml',
    'daemon/manager.py',
    'daemon/presence.py',
    'emitter.py',
    'engine.py',
    'openvino_face.py',
    'i18n.py',
    'integration/__init__.py',
    'integration/kscreenlocker.py',
    'integration/pam.py',
    'models.py',
    'settings.py',
    'paths_factory.py',
    'verification.py',
    'webauthn/__init__.py',
    'webauthn/authenticator.py',
    'webauthn/ctaphid.py',
    'webauthn/keystore.py',
    'webauthn/keystore_tpm.py',
    'webauthn/service.py',
    'webauthn/store.py',
    'webauthn/uhid.py',
    'webauthn_daemon.py',
    'linux_hello_daemon.py',
    'recorders/__init__.py',
    'recorders/device_discovery.py',
    'recorders/errors.py',
    'recorders/ffmpeg_reader.py',
    'recorders/pyv4l2_reader.py',
    'recorders/uvc_xu.py',
    'recorders/v4l2.py',
    'recorders/video_capture.py',
    'recog/__init__.py',
    'recog/backend.py',
    'recog/dlib_backend.py',
    'recog/openvino_backend.py',
    'rubberstamps/__init__.py',
    'rubberstamps/hotkey.py',
    'rubberstamps/nod.py',
    'snapshot.py',
    py_paths,
]

# Include PAM module
if get_option('install_in_site_packages')
    pysourcesinstalldir = join_paths(py_install_dir, 'linux-hello')
else  
    pysourcesinstalldir = get_option('py_sources_dir') != '' ? get_option('py_sources_dir') / 'linux-hello' : join_paths(get_option('prefix'), get_option('libdir'), 'linux-hello')
endif

pam_module_conf_data = configuration_data(paths_dict)
pam_module_conf_data.set('config_file_path', config_path)
pam_module_conf_data.set('vendor_config_path', vendorconfigpath)
subdir('pam')
if get_option('install_pam_config')
    # pamdir is inherited from the pam subproject
    pam_config = configure_file(
        input: 'pam-config/linux-hello.in',
        output: 'pam-config',
        configuration: {'pamdir': pamdir}
    )
    install_data(
        pam_config,
        install_dir: get_option('prefix') / get_option('datadir') / 'pam-configs',
        install_mode: 'rwxr-xr-x',
        install_tag: 'pam',
        rename: 'linux-hello',
    )
endif

if get_option('install_in_site_packages')
    py.install_sources(
        py_sources,
        subdir: 'linux-hello',
        preserve_path: true,
        install_tag: 'py_sources',
    )
else
    install_data(
        py_sources,
        preserve_path: true,
        install_dir: pysourcesinstalldir,
        install_mode: 'r--r--r--',
        install_tag: 'py_sources',
    )
endif

install_data('logo.png', install_tag: 'meta')
install_data(
    'autocomplete/linux-hello-cli',
    install_dir: join_paths(get_option('prefix'), get_option('datadir'), 'bash-completion', 'completions'),
    install_mode: 'rw-r--r--',
    install_tag: 'bash_completion',
    rename: 'linux-hello-cli',
)

# Defaults ship read-only with the package; local changes go to
# /etc/linux-hello/config.ini, which the package never owns
install_data('config.ini', install_dir: datadir, install_mode: 'rw-r--r--', install_tag: 'config')
install_data('emitter-devices.json', install_dir: datadir, install_mode: 'rw-r--r--', install_tag: 'config')

install_data('dlib-data/install.sh', 'dlib-data/Readme.md', install_dir: dlibdatadir, install_mode: 'rwxr--r--')

# Ship the face recognition data with the package instead of downloading
# it after install
if get_option('dlib_data_files') != ''
    dlib_files = []
    foreach name : ['dlib_face_recognition_resnet_model_v1.dat', 'mmod_human_face_detector.dat', 'shape_predictor_5_face_landmarks.dat']
        dlib_files += get_option('dlib_data_files') / name
    endforeach
    install_data(dlib_files, install_dir: dlibdatadir, install_mode: 'rw-r--r--', install_tag: 'dlib_data')
endif

install_data(
    'openvino-models/install.sh',
    install_dir: datadir / 'openvino-models',
    install_mode: 'rwxr-xr-x',
    install_tag: 'openvino_models',
)

install_man('../linux-hello-cli.1')

# if get_option('fetch_dlib_data')
#     downloader = find_program('wget')
#     bunzip2 = find_program('bunzip2')

#     links = [ 
#         'https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2',
#         'https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2',
#         'https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2'
#     ]

#     archived_model_files = [
#         'dlib_face_recognition_resnet_model_v1.dat.bz2',
#         'shape_predictor_5_face_landmarks.dat.bz2',
#         'mmod_human_face_detector.dat.bz2'
#     ]

#     download = run_command(
#         'download',
#         links,
#         output: archived_model_files,
#         command: [downloader, '-O', '@OUTPUT@', '@INPUT@']
#     )

#     model_files = [
#         'dlib_face_recognition_resnet_model_v1.dat',
#         'shape_predictor_5_face_landmarks.dat',
#         'mmod_human_face_detector.dat'
#     ]

#     models = custom_target(
#         'models',
#         input: archived_model_files,
#         output: model_files,
#         command: [bunzip2, '-k', '@INPUT@'],
#     )

#     install_data(
#         model_files,
#         install_dir: join_paths(get_option('prefix'), get_option('libdir'), 'dlib_models'),
#     )

# endif

cli_path = join_paths(pysourcesinstalldir, 'cli.py')
conf_data = configuration_data({ 'script_path': cli_path, 'python_path': py_full_path })

bin_name = 'linux-hello-cli'
bin = configure_file(
    input: 'bin/linux-hello-cli.in',
    output: bin_name,
    configuration: conf_data
)
install_data(
  bin,
  install_mode: 'rwxr-xr-x',
  install_dir: get_option('bindir'),
  install_tag: 'bin',
)

# System service (io.github.LinuxHello1), started on demand over D-Bus
systemd_unit_dir = get_option('prefix') / 'lib' / 'systemd' / 'system'
daemon_path = get_option('prefix') / get_option('libexecdir') / 'linux-hello-daemon'
daemon_bin = configure_file(
    input: 'bin/linux-hello-cli.in',
    output: 'linux-hello-daemon',
    configuration: configuration_data({
        'script_path': join_paths(pysourcesinstalldir, 'linux_hello_daemon.py'),
        'python_path': py_full_path,
    }),
)
install_data(
    daemon_bin,
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('libexecdir'),
    install_tag: 'bin',
)

# Directories under /var/lib, /etc and /var/log are created by systemd
# itself; custom locations elsewhere must exist already
writable_dirs = []
foreach dir : [
    [paths_dict.get('state_dir'), '/var/lib/', 'StateDirectory', '0700'],
    [paths_dict.get('config_dir'), '/etc/', 'ConfigurationDirectory', '0755'],
    [paths_dict.get('log_path'), '/var/log/', 'LogsDirectory', '0755'],
]
    if dir[0].startswith(dir[1])
        writable_dirs += '@0@=@1@\n@0@Mode=@2@'.format(dir[2], dir[0].replace(dir[1], ''), dir[3])
    else
        writable_dirs += 'ReadWritePaths=-' + dir[0]
    endif
endforeach

daemon_conf = configuration_data({
    'daemon_path': daemon_path,
    'writable_dirs': '\n'.join(writable_dirs),
})
install_data(
    configure_file(
        input: 'daemon/io.github.LinuxHello1.service.in',
        output: 'io.github.LinuxHello1.service',
        configuration: daemon_conf,
    ),
    install_dir: get_option('prefix') / get_option('datadir') / 'dbus-1' / 'system-services',
    install_tag: 'daemon',
)
install_data(
    configure_file(
        input: 'daemon/linux-hello-daemon.service.in',
        output: 'linux-hello-daemon.service',
        configuration: daemon_conf,
    ),
    install_dir: systemd_unit_dir,
    install_tag: 'daemon',
)
install_data(
    'daemon/io.github.LinuxHello1.conf',
    install_dir: get_option('prefix') / get_option('datadir') / 'dbus-1' / 'system.d',
    install_tag: 'daemon',
)
install_data(
    'daemon/io.github.LinuxHello1.xml',
    install_dir: get_option('prefix') / get_option('datadir') / 'dbus-1' / 'interfaces',
    install_tag: 'daemon',
)
install_data(
    'daemon/io.github.linuxhello.policy',
    install_dir: get_option('prefix') / get_option('datadir') / 'polkit-1' / 'actions',
    install_mode: 'rw-r--r--',
    install_tag: 'daemon',
)

# Optional WebAuthn virtual FIDO2 authenticator (based on PR #1125 by qilsklo)
if get_option('with_webauthn')
    webauthn_conf_data = configuration_data({
        'script_path': join_paths(pysourcesinstalldir, 'webauthn_daemon.py'),
        'python_path': py_full_path,
    })
    webauthn_bin = configure_file(
        input: 'bin/linux-hello-cli.in',
        output: 'linux-hello-webauthn',
        configuration: webauthn_conf_data,
    )
    install_data(
        webauthn_bin,
        install_mode: 'rwxr-xr-x',
        install_dir: get_option('bindir'),
        install_tag: 'bin',
    )

    webauthn_service = configure_file(
        input: 'webauthn/linux-hello-webauthn.service.in',
        output: 'linux-hello-webauthn.service',
        configuration: {
            'bindir': get_option('prefix') / get_option('bindir'),
            'confdir': confdir,
            'datadir': get_option('prefix') / get_option('datadir'),
        },
    )
    install_data(
        webauthn_service,
        install_dir: systemd_unit_dir,
        install_tag: 'webauthn',
    )

    install_data(
        'webauthn/70-linux-hello-webauthn.rules',
        install_dir: get_option('prefix') / 'lib' / 'udev' / 'rules.d',
        install_tag: 'webauthn',
    )

    install_data(
        'webauthn/docs/webauthn.md',
        'webauthn/docs/webauthn-design.md',
        install_dir: get_option('prefix') / get_option('datadir') / 'doc' / 'linux-hello',
        install_mode: 'r--r--r--',
        install_tag: 'webauthn',
    )
endif
