project('linux-hello', 'cpp', license: 'GPL-3.0', version: 'beta', meson_version: '>= 0.64.0')

dlibdatadir = get_option('dlib_data_dir') != '' ? get_option('dlib_data_dir') : join_paths(get_option('prefix'), get_option('datadir'), 'dlib-data')
confdir = get_option('config_dir') != '' ? get_option('config_dir') : join_paths(get_option('prefix'), get_option('sysconfdir'), 'linux-hello')
usermodelsdir = get_option('user_models_dir') != '' ? get_option('user_models_dir') : join_paths(confdir, 'models')
logpath = get_option('log_path')
pythonpath = get_option('python_path')

# A bundled runtime (built with tools/build_runtime.sh) replaces the system
# interpreter entirely: PAM, wrappers and the GUI all use it, so the target
# machine needs no python packages from its distro.
bundled_python_dir = get_option('bundled_python_dir')
use_bundled_python = bundled_python_dir != ''
if use_bundled_python
    pythonpath = join_paths(get_option('prefix'), get_option('libdir'), 'linux-hello-runtime', 'python', 'bin', 'python3')
    meson.add_install_script('tools/install_runtime.py', bundled_python_dir, get_option('libdir'))
endif

config_path = join_paths(confdir, 'config.ini')

paths_dict = {
    'config_dir': confdir,
    'dlib_data_dir': dlibdatadir,
    'user_models_dir': usermodelsdir,
    'log_path': logpath,
    'python_path': pythonpath
}

# The gui subdir sets up the Qt settings app; the core uses shared paths from paths_dict
subdir('gui')
subdir('linux-hello')