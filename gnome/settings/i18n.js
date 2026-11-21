// Translations, when there are any: strings stay English until then
import Gettext from 'gettext';

Gettext.bindtextdomain('linux-hello', '/usr/share/locale');
Gettext.textdomain('linux-hello');

export const _ = Gettext.gettext;
export const ngettext = Gettext.ngettext;

// Fill the %s placeholders of a translated string in order
export function format(template, ...values) {
    let index = 0;
    return template.replace(/%s/g, () => String(values[index++]));
}
