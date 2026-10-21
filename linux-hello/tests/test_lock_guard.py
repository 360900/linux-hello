import os
import subprocess

import pytest

GUARD = os.path.join(os.path.dirname(__file__), "..", "src", "session", "linux-hello-lock-guard")

# Stand-ins for the commands the guard runs, recording what they were asked.
# $STATE/locked makes the screen count as locked, $STATE/lock_works makes
# Lock lock it.
FAKES = {
	"busctl": """
case "$*" in
	*GetActive*) [ -e "$STATE/locked" ] && echo "b true" || echo "b false" ;;
	*Lock*) echo lock >> "$STATE/log"; [ -e "$STATE/lock_works" ] && touch "$STATE/locked" ;;
esac
""",
	"loginctl": """
case "$1" in
	list-sessions) echo "7 $(id -u) me seat0 100 user tty2 no -" ;;
	show-session)
		case "$*" in
			*Service*) cat "$STATE/service" ;;
			*State*) echo active ;;
		esac ;;
	terminate-session) echo "terminate $2" >> "$STATE/log" ;;
esac
""",
	"kreadconfig6": """
key=$(echo "$*" | sed 's/.*--key //')
grep -x "$key=.*" "$STATE/kscreenlockerrc" 2>/dev/null | cut -d= -f2
""",
}


@pytest.fixture
def guard(tmp_path):
	bin_dir = tmp_path / "bin"
	state = tmp_path / "state"
	bin_dir.mkdir()
	state.mkdir()
	for name, body in FAKES.items():
		path = bin_dir / name
		path.write_text("#!/bin/sh\n" + body)
		path.chmod(0o755)
	(bin_dir / "kwriteconfig6").write_text("""#!/bin/sh
key=""; value=""; delete=""
while [ $# -gt 0 ]; do
	case "$1" in
		--key) key=$2; shift ;;
		--delete) delete=1 ;;
		--file|--group) shift ;;
		*) value=$1 ;;
	esac
	shift
done
touch "$STATE/kscreenlockerrc"
grep -vx "$key=.*" "$STATE/kscreenlockerrc" > "$STATE/rc.new"
[ -z "$delete" ] && echo "$key=$value" >> "$STATE/rc.new"
mv "$STATE/rc.new" "$STATE/kscreenlockerrc"
""")
	(bin_dir / "kwriteconfig6").chmod(0o755)
	# Fast, and pointed at a drop-in under tmp_path
	dropin = tmp_path / "linux-hello.conf"
	script = tmp_path / "guard"
	text = open(GUARD).read().replace("GRACE=15", "GRACE=1").replace(
		"/etc/plasmalogin.conf.d/linux-hello.conf", str(dropin))
	script.write_text(text)
	script.chmod(0o755)

	def run(service, locked=False, lock_works=False, rc=""):
		(state / "service").write_text(service + "\n")
		(state / "kscreenlockerrc").write_text(rc)
		for name, wanted in (("locked", locked), ("lock_works", lock_works)):
			if wanted:
				(state / name).touch()
		env = dict(os.environ, STATE=str(state), PATH=str(bin_dir) + ":" + os.environ["PATH"])
		result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=30)
		log = (state / "log").read_text().split("\n") if (state / "log").exists() else []
		return result.returncode, [line for line in log if line], (state / "kscreenlockerrc").read_text()

	run.dropin = dropin
	return run


def test_password_logins_are_left_alone(guard):
	code, log, _rc = guard("plasmalogin")
	assert code == 0 and log == []


def test_auto_login_is_locked_at_once(guard):
	code, log, rc = guard("plasmalogin-autologin", lock_works=True)
	assert code == 0
	assert log == ["lock"]
	# So the next boot starts locked before anything shows
	assert "LockOnStart=true" in rc and "LinuxHelloLockOnStart=true" in rc


def test_already_locked_by_lock_on_start(guard):
	code, log, _rc = guard("plasmalogin-autologin", locked=True, rc="LockOnStart=true\n")
	assert code == 0 and log == []


def test_session_ends_when_it_never_locks(guard):
	code, log, _rc = guard("plasmalogin-autologin")
	assert code == 1
	assert "terminate 7" in log


def test_own_lock_on_start_is_kept(guard):
	_code, _log, rc = guard("plasmalogin-autologin", locked=True, rc="LockOnStart=true\n")
	assert "LinuxHelloLockOnStart" not in rc


def test_lock_on_start_goes_once_auto_login_is_off(guard):
	_code, _log, rc = guard("plasmalogin", rc="LockOnStart=true\nLinuxHelloLockOnStart=true\n")
	assert "LockOnStart" not in rc


def test_lock_on_start_stays_while_auto_login_is_on(guard):
	# A password login after logging out, with auto-login still on
	guard.dropin.write_text("[Autologin]\n")
	_code, _log, rc = guard("plasmalogin", rc="LockOnStart=true\nLinuxHelloLockOnStart=true\n")
	assert "LockOnStart=true" in rc
