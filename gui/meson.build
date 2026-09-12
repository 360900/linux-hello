# Qt settings app "Linux Hello"
# The app runs elevated through polkit so it can manage models and the
# system-wide config without any terminal usage.

if use_bundled_python
    py_full_path = paths_dict.get('python_path')
    py_install_dir = ''
else
    py = import('python').find_installation(paths_dict.get('python_path'))
    py_full_path = py.full_path()
    py_install_dir = py.get_install_dir()
endif

gui_datadir = get_option('prefix') / get_option('datadir') / 'linux-hello'

# Where the core python sources (recorders, cli helpers) live
if get_option('py_sources_dir') != ''
    core_lib_dir = get_option('py_sources_dir') / 'linux-hello'
elif get_option('install_in_site_packages')
    core_lib_dir = py_install_dir / 'linux-hello'
else
    core_lib_dir = get_option('prefix') / get_option('libdir') / 'linux-hello'
endif

gui_conf = configuration_data(paths_dict)
gui_conf.set('data_dir', gui_datadir)
gui_conf.set('core_lib_dir', core_lib_dir)
gui_conf.set('logo_path', gui_datadir / 'logo.png')
gui_conf.set('cli_path', get_option('prefix') / get_option('bindir') / 'linux-hello-cli')

gui_paths = configure_file(
    input: 'src/paths.py.in',
    output: 'paths.py',
    configuration: gui_conf,
)

sources = files(
    'src/authui.py',
    'src/cli_bridge.py',
    'src/main.py',
    'src/mainwindow.py',
    'src/pagemodels.py',
    'src/pagewelcome.py',
    'src/pagewizard.py',
    'src/pagesettings.py',
    'src/pagetest.py',
    'src/pam_services.py',
)

if get_option('install_in_site_packages')
    py.install_sources(
        sources,
        gui_paths,
        subdir: 'linux-hello-gui',
        install_tag: 'py_sources',
    )
    pysourcesinstalldir = join_paths(py_install_dir, 'linux-hello-gui')
else
    pysourcesinstalldir = get_option('py_sources_dir') != '' ? get_option('py_sources_dir') / 'linux-hello-gui' : join_paths(get_option('prefix'), get_option('libdir'), 'linux-hello-gui')
    install_data(
        sources,
        gui_paths,
        install_dir: pysourcesinstalldir,
        install_mode: 'r--r--r--',
        install_tag: 'py_sources',
    )
endif

# The bin wrapper elevates through polkit when not running as root
wrapper_conf = configuration_data({
    'python_path': py_full_path,
    'script_path': join_paths(pysourcesinstalldir, 'main.py'),
})

linux_hello_bin = configure_file(
    input: 'bin/linux-hello.in',
    output: 'linux-hello',
    configuration: wrapper_conf,
)
install_data(
    linux_hello_bin,
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('prefix') / get_option('bindir'),
    install_tag: 'bin',
)

# Desktop entry
desktop = configure_file(
    input: 'linux-hello.desktop.in',
    output: 'linux-hello.desktop',
    configuration: configuration_data({}),
)
install_data(
    desktop,
    install_dir: get_option('prefix') / get_option('datadir') / 'applications',
    install_tag: 'desktop',
)

# polkit policy (required: the GUI needs it to elevate)
if get_option('with_polkit')
    polkit_config = configure_file(
        input: 'src/polkit/io.github.linuxhello.app.policy.in',
        output: 'io.github.linuxhello.app.policy',
        configuration: wrapper_conf,
    )
    install_data(
        polkit_config,
        install_dir: get_option('prefix') / get_option('datadir') / 'polkit-1' / 'actions',
        install_mode: 'rw-r--r--',
        install_tag: 'polkit',
    )
endif

# systemd drop-in so polkit-agent-helper can open the camera
# (PrivateDevices=yes in polkit >= 127 blocks face auth; see boltgolt/howdy#1077)
install_data(
    files('src/systemd/polkit-agent-helper@.service.d/10-linux-hello.conf'),
    rename: '10-linux-hello.conf',
    install_dir: get_option('prefix') / get_option('libdir') / 'systemd' / 'system' / 'polkit-agent-helper@.service.d',
    install_mode: 'rw-r--r--',
    install_tag: 'config',
)

# App icon for the icon theme
install_data(
    files('src/logo.png'),
    rename: 'linux-hello.png',
    install_dir: get_option('prefix') / get_option('datadir') / 'icons' / 'hicolor' / '256x256' / 'apps',
    install_tag: 'meta',
)
