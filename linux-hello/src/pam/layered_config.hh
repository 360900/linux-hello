#ifndef LAYERED_CONFIG_H_
#define LAYERED_CONFIG_H_

#include <INIReader.h>
#include <string>

/**
 * Read-only view of the config: defaults shipped with the package, with
 * the values from the local override file taking precedence. The override
 * file is optional, the defaults file is only optional when an override
 * file exists (older installs kept a full config in /etc).
 */
class LayeredConfig {
public:
  LayeredConfig(const std::string &vendor_path, const std::string &local_path)
      : vendor(vendor_path), local(local_path) {}

  /**
   * 0 when usable, otherwise the INIReader error of the file at fault:
   * -1 when no file could be opened, or the line of a syntax error
   */
  auto ParseError() const -> int {
    if (local.ParseError() > 0) {
      return local.ParseError();
    }
    if (vendor.ParseError() > 0) {
      return vendor.ParseError();
    }
    if (vendor.ParseError() < 0 && local.ParseError() < 0) {
      return -1;
    }
    return 0;
  }

  auto GetString(const std::string &section, const std::string &name,
                 const std::string &default_value) const -> std::string {
    return local.GetString(section, name,
                           vendor.GetString(section, name, default_value));
  }

  auto GetBoolean(const std::string &section, const std::string &name,
                  bool default_value) const -> bool {
    return local.GetBoolean(section, name,
                            vendor.GetBoolean(section, name, default_value));
  }

private:
  INIReader vendor;
  INIReader local;
};

#endif // LAYERED_CONFIG_H_
