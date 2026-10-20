// Turning the service's replies into values QML understands

#pragma once

#include <QDBusArgument>
#include <QVariant>

namespace Replies
{
// A variant's value, with D-Bus variants and arrays of numbers unpacked
QVariant unpack(const QVariant &value);
// a{sv}, with nested D-Bus arrays unpacked
QVariantMap variantMap(const QVariant &value);
// a{ss}
QVariantMap stringMap(const QVariant &value);
// a(uxs): id, time, label
QVariantList models(const QVariant &value);
// a(sssbbas): id, label, description, available, enabled, services
QVariantList targets(const QVariant &value);
// a(sss): check, state, message
QVariantList checks(const QVariant &value);
// a(sssbd): path, node, name, infrared, brightness
QVariantList cameras(const QVariant &value);
}
