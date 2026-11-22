// What the camera sees and where it finds the face, drawn like a mirror

import Adw from 'gi://Adw?version=1';
import Gdk from 'gi://Gdk?version=4.0';
import GObject from 'gi://GObject';
import Graphene from 'gi://Graphene';
import Gsk from 'gi://Gsk';
import Gtk from 'gi://Gtk?version=4.0';

const RADIUS = 12;
const MIN_WIDTH = 160;
const NATURAL_WIDTH = 280;

// libadwaita's palette, light and dark
function color(light, dark) {
    const rgba = new Gdk.RGBA();
    rgba.parse(Adw.StyleManager.get_default().dark ? dark : light);
    return rgba;
}

export const FaceFrame = GObject.registerClass(class FaceFrame extends Gtk.Widget {
    _init(params) {
        super._init(params);
        this.overflow = Gtk.Overflow.HIDDEN;
        this.add_css_class('card');
        // [left, top, right, bottom] as fractions of the picture, or null
        this._face = null;
        // The face is being captured
        this._good = false;
        this._texture = null;
        this._icon = Gtk.IconTheme.get_for_display(this.get_display()).lookup_icon(
            'linux-hello-symbolic', null, 48, 1, Gtk.TextDirection.NONE, 0);
    }

    set face(face) {
        this._face = face;
        this.queue_draw();
    }

    set good(good) {
        this._good = good;
        this.queue_draw();
    }

    set preview(texture) {
        const resize = (texture?.get_width() ?? 0) !== (this._texture?.get_width() ?? 0) ||
            (texture?.get_height() ?? 0) !== (this._texture?.get_height() ?? 0);
        this._texture = texture;
        if (resize)
            this.queue_resize();
        else
            this.queue_draw();
    }

    vfunc_get_request_mode() {
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH;
    }

    // The picture's shape, so face boxes land where they belong. The
    // height is measured for the width the widget ends up with: centred,
    // it never grows past its natural width even when the parent offers
    // more.
    vfunc_measure(orientation, forSize) {
        if (orientation === Gtk.Orientation.HORIZONTAL)
            return [MIN_WIDTH, NATURAL_WIDTH, -1, -1];
        const width = forSize > 0 ? Math.min(forSize, NATURAL_WIDTH) : NATURAL_WIDTH;
        const ratio = this._texture ? this._texture.get_height() / this._texture.get_width() : 3 / 4;
        const height = Math.round(width * ratio);
        return [height, height, -1, -1];
    }

    vfunc_snapshot(snapshot) {
        const width = this.get_width();
        const height = this.get_height();
        const bounds = new Graphene.Rect().init(0, 0, width, height);
        const rounded = new Gsk.RoundedRect().init_from_rect(bounds, RADIUS);
        snapshot.push_rounded_clip(rounded);

        if (this._texture) {
            // Mirrored: moving left moves the picture left
            snapshot.save();
            snapshot.translate(new Graphene.Point({x: width, y: 0}));
            snapshot.scale(-1, 1);
            this._texture.snapshot(snapshot, width, height);
            snapshot.restore();
        } else if (!this._face && this._icon) {
            snapshot.save();
            snapshot.translate(new Graphene.Point({x: (width - 48) / 2, y: (height - 48) / 2}));
            snapshot.push_opacity(0.4);
            this._icon.snapshot(snapshot, 48, 48);
            snapshot.pop();
            snapshot.restore();
        }

        const context = snapshot.append_cairo(bounds);
        const outline = this.get_color();

        // Where the face should be
        context.save();
        context.translate(width / 2, height / 2);
        context.scale(width * 0.19, height * 0.35);
        context.arc(0, 0, 1, 0, 2 * Math.PI);
        context.restore();
        context.setSourceRGBA(outline.red, outline.green, outline.blue, 0.4);
        context.setLineWidth(1);
        context.stroke();

        if (this._face) {
            const [left, top, right, bottom] = this._face;
            const box = this._good ? color('#2ec27e', '#57e389') : color('#e5a50a', '#f8e45c');
            const x = (1 - right) * width;
            const w = (right - left) * width;
            const y = top * height;
            const h = (bottom - top) * height;
            context.setSourceRGBA(box.red, box.green, box.blue, 1);
            context.setLineWidth(2);
            this._roundedRectangle(context, x, y, w, h, 8);
            context.stroke();
        }
        context.$dispose();
        snapshot.pop();
    }

    _roundedRectangle(context, x, y, w, h, r) {
        context.newSubPath();
        context.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
        context.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
        context.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
        context.arc(x + r, y + r, r, Math.PI, 3 * Math.PI / 2);
        context.closePath();
    }
});
