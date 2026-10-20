#include "facemodule.h"

#include "lockscreen.h"
#include "replies.h"

#include <KLocalizedString>
#include <KPluginFactory>
#include <KUser>

#include <QClipboard>
#include <QDBusConnection>
#include <QDBusPendingCallWatcher>
#include <QDBusPendingReply>
#include <QGuiApplication>

K_PLUGIN_CLASS_WITH_JSON(FaceModule, "kcm_linuxhello.json")

namespace
{
const auto SERVICE = QStringLiteral("io.github.LinuxHello1");
const auto PATH = QStringLiteral("/io/github/LinuxHello1");
const auto INTERFACE = QStringLiteral("io.github.LinuxHello1.Manager");
const auto ERROR_PREFIX = QStringLiteral("io.github.LinuxHello1.Error.");

// Quick reads; the service may have to start first
constexpr int QUICK = 30 * 1000;
// Calls that can wait for a polkit prompt, or scan with the camera after one
constexpr int INTERACTIVE = 10 * 60 * 1000;

bool isServiceError(const QDBusError &error, const char *name)
{
    return error.name() == ERROR_PREFIX + QLatin1String(name);
}

QRectF faceRect(const QVariant &value)
{
    const QVariantList face = value.toList();
    if (face.size() != 4) {
        return {};
    }
    return QRectF(QPointF(face[0].toDouble(), face[1].toDouble()), QPointF(face[2].toDouble(), face[3].toDouble()));
}
}

FaceModule::FaceModule(QObject *parent, const KPluginMetaData &data)
    : KQuickConfigModule(parent, data)
{
    setButtons(NoAdditionalButton);

    auto bus = QDBusConnection::systemBus();
    bus.connect(SERVICE, PATH, INTERFACE, QStringLiteral("EnrollProgress"), this, SLOT(onEnrollProgress(QString, QVariantMap)));
    bus.connect(SERVICE, PATH, INTERFACE, QStringLiteral("ModelsChanged"), this, SLOT(onModelsChanged(QString)));
    bus.connect(SERVICE, PATH, INTERFACE, QStringLiteral("TargetsChanged"), this, SLOT(loadTargets()));
    bus.connect(SERVICE, PATH, INTERFACE, QStringLiteral("SettingsChanged"), this, SLOT(loadSettings()));

    refresh();
}

bool FaceModule::serviceAvailable() const
{
    return m_serviceAvailable;
}

QString FaceModule::serviceError() const
{
    return m_serviceError;
}

QVariantMap FaceModule::status() const
{
    return m_status;
}

QVariantList FaceModule::models() const
{
    return m_models;
}

QVariantList FaceModule::targets() const
{
    return m_targets;
}

QVariantMap FaceModule::settings() const
{
    return m_settings;
}

QVariantList FaceModule::checks() const
{
    return m_checks;
}

QVariantList FaceModule::cameras() const
{
    return m_cameras;
}

bool FaceModule::findingCameras() const
{
    return m_findingCameras;
}

bool FaceModule::scanning() const
{
    return m_scanning;
}

QString FaceModule::enrollState() const
{
    return m_enrollState;
}

int FaceModule::enrollSample() const
{
    return m_enrollSample;
}

int FaceModule::enrollSamples() const
{
    return m_enrollSamples;
}

QRectF FaceModule::enrollFace() const
{
    return m_enrollFace;
}

// -- calls ------------------------------------------------------------------

void FaceModule::call(const QString &method, const QVariantList &args, int timeout, Done done, Error failed)
{
    QDBusMessage message = QDBusMessage::createMethodCall(SERVICE, PATH, INTERFACE, method);
    message.setArguments(args);
    message.setInteractiveAuthorizationAllowed(true);
    auto watcher = new QDBusPendingCallWatcher(QDBusConnection::systemBus().asyncCall(message, timeout), this);
    connect(watcher, &QDBusPendingCallWatcher::finished, this, [done, failed](QDBusPendingCallWatcher *watcher) {
        watcher->deleteLater();
        const QDBusMessage reply = watcher->reply();
        if (reply.type() == QDBusMessage::ErrorMessage) {
            if (failed) {
                failed(QDBusError(reply));
            }
            return;
        }
        if (done) {
            done(reply);
        }
    });
}

FaceModule::Error FaceModule::reportAndReload(std::function<void()> reload)
{
    return [this, reload](const QDBusError &error) {
        // A dismissed password prompt needs no message
        if (!isServiceError(error, "PermissionDenied")) {
            Q_EMIT actionFailed(describe(error));
        }
        reload();
    };
}

QString FaceModule::describe(const QDBusError &error)
{
    if (error.type() == QDBusError::ServiceUnknown || error.type() == QDBusError::NoReply || error.type() == QDBusError::Disconnected) {
        return i18n("The Linux Hello service is not responding. Details: journalctl -u linux-hello-daemon");
    }
    if (isServiceError(error, "PermissionDenied")) {
        return i18n("You are not allowed to make this change.");
    }
    return error.message();
}

QString FaceModule::user() const
{
    const QString name = m_status.value(QStringLiteral("user")).toString();
    return name.isEmpty() ? KUser().loginName() : name;
}

void FaceModule::refresh()
{
    loadStatus();
    loadModels();
    loadTargets();
    loadSettings();
    diagnose();
}

void FaceModule::loadStatus()
{
    call(QStringLiteral("GetStatus"), {}, QUICK, [this](const QDBusMessage &reply) {
        m_status = Replies::variantMap(reply.arguments().value(0));
        m_serviceAvailable = true;
        m_serviceError.clear();
        Q_EMIT statusChanged();
    }, [this](const QDBusError &error) {
        m_serviceAvailable = false;
        m_serviceError = describe(error);
        Q_EMIT statusChanged();
    });
}

void FaceModule::loadModels()
{
    call(QStringLiteral("ListModels"), {user()}, QUICK, [this](const QDBusMessage &reply) {
        m_models = Replies::models(reply.arguments().value(0));
        Q_EMIT modelsChanged();
    }, {});
}

void FaceModule::loadTargets()
{
    call(QStringLiteral("ListTargets"), {}, QUICK, [this](const QDBusMessage &reply) {
        m_targets = Replies::targets(reply.arguments().value(0));
        Q_EMIT targetsChanged();
    }, {});
}

void FaceModule::loadSettings()
{
    call(QStringLiteral("GetSettings"), {}, QUICK, [this](const QDBusMessage &reply) {
        m_settings = Replies::stringMap(reply.arguments().value(0));
        Q_EMIT settingsChanged();
    }, {});
    // The global switch is part of the status
    loadStatus();
}

void FaceModule::diagnose()
{
    call(QStringLiteral("Diagnose"), {}, QUICK, [this](const QDBusMessage &reply) {
        m_checks = Replies::checks(reply.arguments().value(0));
        Q_EMIT checksChanged();
    }, {});
}

void FaceModule::findCameras()
{
    if (m_findingCameras) {
        return;
    }
    m_findingCameras = true;
    Q_EMIT camerasChanged();
    call(QStringLiteral("ListCameras"), {}, INTERACTIVE, [this](const QDBusMessage &reply) {
        m_cameras = Replies::cameras(reply.arguments().value(0));
        m_findingCameras = false;
        Q_EMIT camerasChanged();
    }, reportAndReload([this] {
        m_findingCameras = false;
        Q_EMIT camerasChanged();
    }));
}

// -- enrolment and verification ---------------------------------------------

void FaceModule::enroll(const QString &label)
{
    if (m_scanning) {
        return;
    }
    m_scanning = true;
    m_enrollState.clear();
    m_enrollSample = 0;
    m_enrollSamples = 0;
    m_enrollFace = QRectF();
    Q_EMIT scanningChanged();
    Q_EMIT enrollProgress();

    call(QStringLiteral("Enroll"), {user(), label}, INTERACTIVE, [this](const QDBusMessage &reply) {
        finishScan();
        Q_EMIT enrollFinished(reply.arguments().value(0).toInt(), reply.arguments().value(1).toUInt());
    }, [this](const QDBusError &error) {
        finishScan();
        if (isServiceError(error, "PermissionDenied")) {
            Q_EMIT enrollFinished(NotAuthorized, 0);
        } else if (isServiceError(error, "Cancelled")) {
            Q_EMIT enrollFinished(Cancelled, 0);
        } else {
            Q_EMIT actionFailed(describe(error));
            Q_EMIT enrollFinished(Failed, 0);
        }
    });
}

void FaceModule::verify()
{
    if (m_scanning) {
        return;
    }
    m_scanning = true;
    Q_EMIT scanningChanged();
    call(QStringLiteral("Verify"), {user()}, INTERACTIVE, [this](const QDBusMessage &reply) {
        finishScan();
        Q_EMIT verifyFinished(reply.arguments().value(0).toInt(), reply.arguments().value(1).toDouble());
    }, [this](const QDBusError &error) {
        finishScan();
        Q_EMIT actionFailed(describe(error));
        Q_EMIT verifyFinished(isServiceError(error, "Cancelled") ? Cancelled : Failed, -1);
    });
}

void FaceModule::cancel()
{
    if (m_scanning) {
        call(QStringLiteral("Cancel"), {}, QUICK, {}, {});
    }
}

void FaceModule::finishScan()
{
    m_scanning = false;
    Q_EMIT scanningChanged();
}

void FaceModule::onEnrollProgress(const QString &user, const QVariantMap &update)
{
    if (!m_scanning || user != this->user()) {
        return;
    }
    QVariantMap values;
    for (auto it = update.cbegin(); it != update.cend(); ++it) {
        values.insert(it.key(), Replies::unpack(it.value()));
    }
    m_enrollState = values.value(QStringLiteral("state")).toString();
    m_enrollSample = values.value(QStringLiteral("sample")).toInt();
    m_enrollSamples = values.value(QStringLiteral("samples")).toInt();
    m_enrollFace = faceRect(values.value(QStringLiteral("face")));
    Q_EMIT enrollProgress();
}

// -- changes ----------------------------------------------------------------

void FaceModule::onModelsChanged(const QString &user)
{
    if (user == this->user()) {
        loadModels();
        loadStatus();
        diagnose();
    }
}

void FaceModule::renameModel(uint id, const QString &label)
{
    call(QStringLiteral("RenameModel"), {user(), id, label}, INTERACTIVE, {}, reportAndReload([this] {
        loadModels();
    }));
}

void FaceModule::deleteModel(uint id)
{
    call(QStringLiteral("DeleteModel"), {user(), id}, INTERACTIVE, {}, reportAndReload([this] {
        loadModels();
    }));
}

void FaceModule::setTarget(const QString &id, bool enabled)
{
    // The lock screen's own face stack (Plasma 6.8+) comes with a starting
    // choice to keep in step, see lockscreen.h
    bool ownLockStack = false;
    for (const QVariant &target : std::as_const(m_targets)) {
        const QVariantMap map = target.toMap();
        if (map.value(QStringLiteral("id")) == id && id == QLatin1String("lock-screen")) {
            ownLockStack = map.value(QStringLiteral("services")).toStringList().contains(QLatin1String("kde-face"));
        }
    }
    if (ownLockStack && !enabled) {
        LockScreen::stopPreferringFace();
    }
    call(QStringLiteral("SetTarget"), {id, enabled}, INTERACTIVE, [this, ownLockStack, enabled](const QDBusMessage &) {
        if (ownLockStack && enabled && LockScreen::faceEnabled()) {
            LockScreen::preferFace();
        }
        loadTargets();
        diagnose();
    }, reportAndReload([this] {
        loadTargets();
    }));
}

void FaceModule::setSetting(const QString &section, const QString &key, const QString &value)
{
    call(QStringLiteral("SetSetting"), {section, key, value}, INTERACTIVE, [this](const QDBusMessage &) {
        loadSettings();
        diagnose();
    }, reportAndReload([this] {
        loadSettings();
    }));
}

// -- diagnostics ------------------------------------------------------------

void FaceModule::copyReport() const
{
    QStringList lines;
    lines << QStringLiteral("Linux Hello %1").arg(m_status.value(QStringLiteral("version")).toString());
    if (!m_serviceAvailable) {
        lines << QStringLiteral("service: %1").arg(m_serviceError);
    }
    for (const QVariant &check : m_checks) {
        const QVariantMap map = check.toMap();
        lines << QStringLiteral("%1 [%2] %3")
                     .arg(map.value(QStringLiteral("check")).toString(),
                          map.value(QStringLiteral("state")).toString(),
                          map.value(QStringLiteral("message")).toString());
    }
    for (const QVariant &target : m_targets) {
        const QVariantMap map = target.toMap();
        lines << QStringLiteral("target %1: %2 (%3)")
                     .arg(map.value(QStringLiteral("id")).toString(),
                          map.value(QStringLiteral("enabled")).toBool() ? QStringLiteral("on") : QStringLiteral("off"),
                          map.value(QStringLiteral("services")).toStringList().join(QLatin1Char(',')));
    }
    lines << QStringLiteral("face models: %1").arg(m_models.size());
    for (auto it = m_settings.cbegin(); it != m_settings.cend(); ++it) {
        if (it.key().startsWith(QLatin1String("video.")) || it.key().startsWith(QLatin1String("core."))) {
            lines << QStringLiteral("%1 = %2").arg(it.key(), it.value().toString());
        }
    }
    QGuiApplication::clipboard()->setText(lines.join(QLatin1Char('\n')));
}

#include "facemodule.moc"
