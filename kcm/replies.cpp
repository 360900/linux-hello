#include "replies.h"

#include <QDateTime>
#include <QDBusVariant>

namespace
{
const QDBusArgument argument(const QVariant &value)
{
    return value.value<QDBusArgument>();
}

}

namespace Replies
{
QVariant unpack(const QVariant &value)
{
    if (value.userType() == qMetaTypeId<QDBusVariant>()) {
        return unpack(value.value<QDBusVariant>().variant());
    }
    if (value.userType() != qMetaTypeId<QDBusArgument>()) {
        return value;
    }
    // The only array the service sends inside a{sv} is "face", an ad
    const QDBusArgument arg = argument(value);
    QVariantList list;
    arg.beginArray();
    while (!arg.atEnd()) {
        double number = 0;
        arg >> number;
        list.append(number);
    }
    arg.endArray();
    return list;
}

QVariantMap variantMap(const QVariant &value)
{
    QVariantMap map;
    const QDBusArgument arg = argument(value);
    arg.beginMap();
    while (!arg.atEnd()) {
        QString key;
        QDBusVariant item;
        arg.beginMapEntry();
        arg >> key >> item;
        arg.endMapEntry();
        map.insert(key, unpack(item.variant()));
    }
    arg.endMap();
    return map;
}

QVariantMap stringMap(const QVariant &value)
{
    QVariantMap map;
    const QDBusArgument arg = argument(value);
    arg.beginMap();
    while (!arg.atEnd()) {
        QString key;
        QString item;
        arg.beginMapEntry();
        arg >> key >> item;
        arg.endMapEntry();
        map.insert(key, item);
    }
    arg.endMap();
    return map;
}

QVariantList models(const QVariant &value)
{
    QVariantList list;
    const QDBusArgument arg = argument(value);
    arg.beginArray();
    while (!arg.atEnd()) {
        uint id = 0;
        qlonglong time = 0;
        QString label;
        arg.beginStructure();
        arg >> id >> time >> label;
        arg.endStructure();
        list.append(QVariantMap{
            {QStringLiteral("id"), id},
            {QStringLiteral("time"), time > 0 ? QVariant(QDateTime::fromSecsSinceEpoch(time)) : QVariant()},
            {QStringLiteral("label"), label},
        });
    }
    arg.endArray();
    return list;
}

QVariantList targets(const QVariant &value)
{
    QVariantList list;
    const QDBusArgument arg = argument(value);
    arg.beginArray();
    while (!arg.atEnd()) {
        QString id;
        QString label;
        QString description;
        bool available = false;
        bool enabled = false;
        QStringList services;
        arg.beginStructure();
        arg >> id >> label >> description >> available >> enabled >> services;
        arg.endStructure();
        list.append(QVariantMap{
            {QStringLiteral("id"), id},
            {QStringLiteral("label"), label},
            {QStringLiteral("description"), description},
            {QStringLiteral("available"), available},
            {QStringLiteral("enabled"), enabled},
            {QStringLiteral("services"), services},
        });
    }
    arg.endArray();
    return list;
}

QVariantList checks(const QVariant &value)
{
    QVariantList list;
    const QDBusArgument arg = argument(value);
    arg.beginArray();
    while (!arg.atEnd()) {
        QString check;
        QString state;
        QString message;
        arg.beginStructure();
        arg >> check >> state >> message;
        arg.endStructure();
        list.append(QVariantMap{
            {QStringLiteral("check"), check},
            {QStringLiteral("state"), state},
            {QStringLiteral("message"), message},
        });
    }
    arg.endArray();
    return list;
}

QVariantList cameras(const QVariant &value)
{
    QVariantList list;
    const QDBusArgument arg = argument(value);
    arg.beginArray();
    while (!arg.atEnd()) {
        QString path;
        QString node;
        QString name;
        bool infrared = false;
        double brightness = 0;
        arg.beginStructure();
        arg >> path >> node >> name >> infrared >> brightness;
        arg.endStructure();
        list.append(QVariantMap{
            {QStringLiteral("path"), path},
            {QStringLiteral("node"), node},
            {QStringLiteral("name"), name},
            {QStringLiteral("infrared"), infrared},
            {QStringLiteral("brightness"), brightness},
        });
    }
    arg.endArray();
    return list;
}
}
