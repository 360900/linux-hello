// The Plasma lock screen's starting sign-in option, mirrors
// linux-hello/src/integration/kscreenlocker.py.
//
// Plasma 6.8+ starts the lock screen with the option that last unlocked it,
// saved per user in $XDG_STATE_HOME/kscreenlockerrc [Greeter]
// Authenticator. It crashes on start when that option is Face while the
// Face option is not enabled, so Face is only chosen once it is in effect.

#pragma once

namespace LockScreen
{
// [Authenticators] Face in kscreenlockerrc, the user's value winning
bool faceEnabled();
void preferFace();
// Back to the password, but only if face was the choice
void stopPreferringFace();
}
