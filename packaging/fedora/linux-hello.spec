# Builds a GitHub snapshot with the bundled Python runtime. The pinned
# downloads below come from tools/runtime-manifest.json; refresh them with
# tools/update_packaging.py after changing the manifest.
%{!?commit: %global commit master}

# The bundled CPython and wheels are prebuilt and must stay as they are
%global debug_package %{nil}
%global __strip /bin/true
%global __brp_check_rpaths %{nil}
%global __brp_mangle_shebangs_exclude_from ^%{_libdir}/linux-hello-runtime/
%global __requires_exclude_from ^%{_libdir}/linux-hello-runtime/
%global __provides_exclude_from ^%{_libdir}/linux-hello-runtime/

Name:           linux-hello
Version:        0.1.0
Release:        0.1%{?dist}
Summary:        Face unlock for KDE Plasma, sudo and polkit with IR cameras
License:        GPL-3.0-only AND MIT
URL:            https://github.com/360900/linux-hello
Source0:        %{url}/archive/%{commit}/%{name}-%{commit}.tar.gz
# BEGIN pinned sources (tools/update_packaging.py)
%ifarch x86_64
Source100: https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz#/cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
Source101: https://files.pythonhosted.org/packages/65/af/aa78d1a88805456e212b65461354cd943197fb9acecc4c90fd12295123a3/numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl#/numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
Source102: https://files.pythonhosted.org/packages/2b/97/8170e9819764c47e436c130d3ff6cfb73b58f923eae9d3a03d8982b04aec/opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl#/opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
Source103: https://files.pythonhosted.org/packages/b1/db/dceb9dd5b231e1da801793f8acc9f3c52a7e1afe40bb1aae37e02b0faad5/cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl#/cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
Source104: https://files.pythonhosted.org/packages/0c/c3/44f3fbbfa403ea2a7c779186dc20772604442dde72947e7d01069cbe98e3/pycparser-3.0-py3-none-any.whl#/pycparser-3.0-py3-none-any.whl
Source105: https://files.pythonhosted.org/packages/57/26/e6d4fc8512a51a5f9ee7bfdbfb853bce1197087df40c9ad993ad370b846f/cryptography-50.0.1-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl#/cryptography-50.0.1-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
Source106: https://files.pythonhosted.org/packages/71/84/198d99c3312557ef6121cf78c38281efe9b3bc88cba0e2c05446f38a024d/fido2-2.2.1-py3-none-any.whl#/fido2-2.2.1-py3-none-any.whl
Source107: https://files.pythonhosted.org/packages/25/1e/17570a07f9db19014f5df9cc5de2b4acfb47834e9921e019372b51d7cc03/dlib-20.0.1.tar.gz#/dlib-20.0.1.tar.gz
Source108: https://files.pythonhosted.org/packages/ac/d8/528ce993791bc06fb5bc8b8abef87f49240945302e858a1d6894073e7c54/dbus_fast-5.2.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl#/dbus_fast-5.2.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
Source109: https://files.pythonhosted.org/packages/95/9c/c510029fc6ef33a6275cd2c5d3cecd6613dfd6aa401d57c54f1c18852ccf/setuptools-84.0.0-py3-none-any.whl#/setuptools-84.0.0-py3-none-any.whl
Source110: https://files.pythonhosted.org/packages/2e/29/69cfbb602cd91690c55d38ba9fe53e6a7e76a6fa647bf38f19c138d25449/wheel-0.48.0-py3-none-any.whl#/wheel-0.48.0-py3-none-any.whl
Source111: https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2#/dlib_face_recognition_resnet_model_v1.dat.bz2
Source112: https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2#/mmod_human_face_detector.dat.bz2
Source113: https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2#/shape_predictor_5_face_landmarks.dat.bz2
%endif
%ifarch aarch64
Source200: https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%%2B20260901-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz#/cpython-3.12.14+20260901-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz
Source201: https://files.pythonhosted.org/packages/f1/32/84146fc020ad3c25f805f70ab60da46fe3c540a21369754a7e4369754b6f/numpy-2.5.3-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl#/numpy-2.5.3-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl
Source202: https://files.pythonhosted.org/packages/ec/78/afca939f40ffe2b2380bfa86f812b2f7d4acc5a27b27dc41b49cad7ce7b4/opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl#/opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
Source203: https://files.pythonhosted.org/packages/44/de/f98430906df1545ffde0d543dd124a7a439bc2cd32b36b9c53f805df7333/cffi-2.1.1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl#/cffi-2.1.1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
Source204: https://files.pythonhosted.org/packages/0c/c3/44f3fbbfa403ea2a7c779186dc20772604442dde72947e7d01069cbe98e3/pycparser-3.0-py3-none-any.whl#/pycparser-3.0-py3-none-any.whl
Source205: https://files.pythonhosted.org/packages/90/34/9ce9a62ed9dc82ca9fd6a34445b6904af56e5f38b3eae2ed32e49c36053d/cryptography-50.0.1-cp311-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl#/cryptography-50.0.1-cp311-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
Source206: https://files.pythonhosted.org/packages/71/84/198d99c3312557ef6121cf78c38281efe9b3bc88cba0e2c05446f38a024d/fido2-2.2.1-py3-none-any.whl#/fido2-2.2.1-py3-none-any.whl
Source207: https://files.pythonhosted.org/packages/25/1e/17570a07f9db19014f5df9cc5de2b4acfb47834e9921e019372b51d7cc03/dlib-20.0.1.tar.gz#/dlib-20.0.1.tar.gz
Source208: https://files.pythonhosted.org/packages/d2/81/3c3ab1c728e2493e6c01c2e652ed42a60821cc190c4a596d2405d3374812/dbus_fast-5.2.0-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl#/dbus_fast-5.2.0-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl
Source209: https://files.pythonhosted.org/packages/95/9c/c510029fc6ef33a6275cd2c5d3cecd6613dfd6aa401d57c54f1c18852ccf/setuptools-84.0.0-py3-none-any.whl#/setuptools-84.0.0-py3-none-any.whl
Source210: https://files.pythonhosted.org/packages/2e/29/69cfbb602cd91690c55d38ba9fe53e6a7e76a6fa647bf38f19c138d25449/wheel-0.48.0-py3-none-any.whl#/wheel-0.48.0-py3-none-any.whl
Source211: https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2#/dlib_face_recognition_resnet_model_v1.dat.bz2
Source212: https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2#/mmod_human_face_detector.dat.bz2
Source213: https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2#/shape_predictor_5_face_landmarks.dat.bz2
%endif
# END pinned sources

ExclusiveArch:  x86_64 aarch64

BuildRequires:  bzip2
BuildRequires:  cmake
BuildRequires:  extra-cmake-modules
BuildRequires:  file
BuildRequires:  gcc-c++
BuildRequires:  gettext
BuildRequires:  inih-devel
BuildRequires:  kf6-kcmutils-devel
BuildRequires:  kf6-kconfig-devel
BuildRequires:  kf6-kcoreaddons-devel
BuildRequires:  kf6-ki18n-devel
BuildRequires:  libevdev-devel
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  pam-devel
BuildRequires:  python3
BuildRequires:  qt6-qtbase-devel
BuildRequires:  qt6-qtdeclarative-devel
BuildRequires:  systemd-devel
BuildRequires:  systemd-rpm-macros

Requires:       dbus
Requires:       kf6-kirigami
Requires:       libxcrypt-compat
Requires:       polkit
%{?systemd_requires}

%description
Linux Hello unlocks the Plasma lock screen and authenticates sudo and polkit
prompts with your face, using an infrared camera when there is one. Set it up
in System Settings > Security & Privacy > Face Unlock.

%prep
%autosetup -n %{name}-%{commit}
mkdir dlib-data
for archive in %{_sourcedir}/*.dat.bz2; do
    bzip2 -dc "$archive" > "dlib-data/$(basename "$archive" .bz2)"
done

%build
tools/build_runtime.sh --outdir "$PWD/runtime" --download-dir %{_sourcedir} --offline --arch %{_target_cpu}
%meson -Dbundled_python_dir="$PWD/runtime/linux-hello-runtime" -Ddlib_data_files="$PWD/dlib-data"
%meson_build
%define _vpath_srcdir kcm
%define __cmake_builddir redhat-linux-build-kcm
%cmake -DBUILD_TESTING=OFF
%cmake_build

%install
%meson_install
%cmake_install
# Bytecode for the paths it runs from, so nothing is written there later
%{buildroot}%{_libdir}/linux-hello-runtime/python/bin/python3 -B -m compileall -f -q -j0 \
    -s %{buildroot} -p / %{buildroot}%{_libdir}/linux-hello-runtime %{buildroot}%{_libdir}/linux-hello

%post
%systemd_post linux-hello-daemon.service linux-hello-webauthn.service

%preun
if [ $1 -eq 0 ]; then
    # Undo every PAM change before the module disappears
    linux-hello-cli reset-system >/dev/null || :
fi
%systemd_preun linux-hello-daemon.service linux-hello-webauthn.service

%postun
%systemd_postun_with_restart linux-hello-daemon.service

%files
%license LICENSE NOTICE
%doc README.md
%{_bindir}/linux-hello-cli
%{_bindir}/linux-hello-webauthn
%{_libexecdir}/linux-hello-daemon
%{_libdir}/linux-hello/
%{_libdir}/linux-hello-runtime/
%{_libdir}/security/pam_linux_hello.so
%{_libdir}/qt6/plugins/plasma/kcms/systemsettings/kcm_linuxhello.so
%{_unitdir}/linux-hello-daemon.service
%{_unitdir}/linux-hello-webauthn.service
%{_udevrulesdir}/70-linux-hello-webauthn.rules
%{_datadir}/applications/linux-hello.desktop
%{_datadir}/bash-completion/completions/linux-hello-cli
%{_datadir}/dbus-1/interfaces/io.github.LinuxHello1.xml
%{_datadir}/dbus-1/system-services/io.github.LinuxHello1.service
%{_datadir}/dbus-1/system.d/io.github.LinuxHello1.conf
%{_datadir}/dlib-data/
%{_datadir}/icons/hicolor/scalable/apps/linux-hello.svg
%{_datadir}/icons/hicolor/symbolic/apps/linux-hello-symbolic.svg
%{_datadir}/linux-hello/
%{_datadir}/polkit-1/actions/io.github.linuxhello.policy
%{_docdir}/linux-hello/
%{_mandir}/man1/linux-hello-cli.1*
