// System Settings page "Face Unlock", a client of the Linux Hello service
// (io.github.LinuxHello1, see linux-hello/src/daemon/io.github.LinuxHello1.xml).
//
// Every change is applied right away by the service, which asks polkit, so
// the page has no Apply button.

#pragma once

#include <KQuickConfigModule>

#include <QDBusError>
#include <QDBusMessage>
#include <QRectF>

#include <functional>

class FaceModule : public KQuickConfigModule
{
    Q_OBJECT

    // The service answered GetStatus
    Q_PROPERTY(bool serviceAvailable READ serviceAvailable NOTIFY statusChanged)
    Q_PROPERTY(QString serviceError READ serviceError NOTIFY statusChanged)
    Q_PROPERTY(QVariantMap status READ status NOTIFY statusChanged)
    Q_PROPERTY(QVariantList models READ models NOTIFY modelsChanged)
    Q_PROPERTY(QVariantList targets READ targets NOTIFY targetsChanged)
    Q_PROPERTY(QVariantMap settings READ settings NOTIFY settingsChanged)
    Q_PROPERTY(QVariantList checks READ checks NOTIFY checksChanged)
    Q_PROPERTY(QVariantMap security READ security NOTIFY securityChanged)
    Q_PROPERTY(QVariantList cameras READ cameras NOTIFY camerasChanged)
    Q_PROPERTY(bool findingCameras READ findingCameras NOTIFY camerasChanged)

    // Enrolment and verification in progress, one at a time
    Q_PROPERTY(bool scanning READ scanning NOTIFY scanningChanged)
    // searching, no-face, multiple-faces, too-far, too-dark, captured;
    // empty until the camera is running
    Q_PROPERTY(QString enrollState READ enrollState NOTIFY enrollProgress)
    Q_PROPERTY(int enrollSample READ enrollSample NOTIFY enrollProgress)
    Q_PROPERTY(int enrollSamples READ enrollSamples NOTIFY enrollProgress)
    // The face in the camera picture, as fractions of it; empty if none
    Q_PROPERTY(QRectF enrollFace READ enrollFace NOTIFY enrollProgress)
    // What the camera sees while scanning, as a data: URL; empty if nothing yet
    Q_PROPERTY(QString preview READ preview NOTIFY previewChanged)

public:
    // Result codes of Verify and Enroll
    enum Result {
        Success = 0,
        NoFaceModel = 10,
        Timeout = 11,
        Aborted = 12,
        TooDark = 13,
        CameraUnavailable = 14,
        RubberstampFailed = 15,
        Cancelled = 250,
        // Not from the service: it could not be reached or refused
        Failed = 251,
        NotAuthorized = 252,
    };
    Q_ENUM(Result)

    FaceModule(QObject *parent, const KPluginMetaData &data);

    bool serviceAvailable() const;
    QString serviceError() const;
    QVariantMap status() const;
    QVariantList models() const;
    QVariantList targets() const;
    QVariantMap settings() const;
    QVariantList checks() const;
    QVariantMap security() const;
    QVariantList cameras() const;
    bool findingCameras() const;
    bool scanning() const;
    QString enrollState() const;
    int enrollSample() const;
    int enrollSamples() const;
    QRectF enrollFace() const;
    QString preview() const;

    Q_INVOKABLE void refresh();
    Q_INVOKABLE void diagnose();
    Q_INVOKABLE void checkSecurity();
    Q_INVOKABLE void findCameras();

    Q_INVOKABLE void enroll(const QString &label);
    Q_INVOKABLE void verify();
    Q_INVOKABLE void cancel();

    Q_INVOKABLE void renameModel(uint id, const QString &label);
    Q_INVOKABLE void deleteModel(uint id);
    Q_INVOKABLE void setTarget(const QString &id, bool enabled);
    Q_INVOKABLE void setSetting(const QString &section, const QString &key, const QString &value);

    Q_INVOKABLE void copyReport() const;

Q_SIGNALS:
    void statusChanged();
    void modelsChanged();
    void targetsChanged();
    void settingsChanged();
    void checksChanged();
    void securityChanged();
    void camerasChanged();
    void scanningChanged();
    void enrollProgress();
    void previewChanged();
    void enrollFinished(int result, uint modelId);
    // certainty: the best distance seen times 10, lower is closer; -1 if no face
    void verifyFinished(int result, double certainty);
    // A change was refused or failed; the page shows its old state again
    void actionFailed(const QString &message);

private Q_SLOTS:
    void onEnrollProgress(const QString &user, const QVariantMap &update);
    void onPreview(const QString &user, const QByteArray &jpeg);
    void onModelsChanged(const QString &user);
    void loadTargets();
    void loadSettings();

private:
    using Done = std::function<void(const QDBusMessage &)>;
    using Error = std::function<void(const QDBusError &)>;

    void call(const QString &method, const QVariantList &args, int timeout, Done done, Error failed);
    void loadStatus();
    void loadModels();
    void startScan();
    void finishScan();
    QString user() const;
    // Shows the error, then reloads what the action may have left stale
    Error reportAndReload(std::function<void()> reload);
    static QString describe(const QDBusError &error);

    bool m_serviceAvailable = false;
    QString m_serviceError;
    QVariantMap m_status;
    QVariantList m_models;
    QVariantList m_targets;
    QVariantMap m_settings;
    QVariantList m_checks;
    QVariantMap m_security;
    QVariantList m_cameras;
    bool m_findingCameras = false;

    bool m_scanning = false;
    QString m_enrollState;
    int m_enrollSample = 0;
    int m_enrollSamples = 0;
    QRectF m_enrollFace;
    QString m_preview;
};
