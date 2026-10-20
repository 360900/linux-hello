#include "lockscreen.h"

#include <KConfig>
#include <KConfigGroup>
#include <QStandardPaths>

namespace
{
const auto FILE_NAME = QStringLiteral("kscreenlockerrc");
const auto FACE = QStringLiteral("Face");

KConfigGroup greeter(KConfig &state)
{
    return KConfigGroup(&state, QStringLiteral("Greeter"));
}
}

namespace LockScreen
{
bool faceEnabled()
{
    // A fresh read: the service has just written the system-wide value
    KConfig config(FILE_NAME, KConfig::CascadeConfig);
    return KConfigGroup(&config, QStringLiteral("Authenticators")).readEntry(FACE, false);
}

void preferFace()
{
    KConfig state(FILE_NAME, KConfig::SimpleConfig, QStandardPaths::GenericStateLocation);
    greeter(state).writeEntry("Authenticator", FACE);
    state.sync();
}

void stopPreferringFace()
{
    KConfig state(FILE_NAME, KConfig::SimpleConfig, QStandardPaths::GenericStateLocation);
    KConfigGroup group = greeter(state);
    if (group.readEntry("Authenticator", QString()) == FACE) {
        group.writeEntry("Authenticator", QStringLiteral("Regular"));
        state.sync();
    }
}
}
