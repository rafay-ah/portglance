// PortGlance Helper: keeps the PortGlance desktop widget above other windows.
//
// Wayland clients cannot change their own stacking order, so the PortGlance
// app asks this extension over D-Bus. Only windows that belong to PortGlance
// (matched by GTK application id or WM_CLASS) are ever touched.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const OBJECT_PATH = '/io/github/rafay_ah/PortGlance/ShellHelper';
const APP_ID = 'io.github.rafay_ah.PortGlance';
const INTERFACE = `
<node>
  <interface name="io.github.rafay_ah.PortGlance.ShellHelper">
    <method name="SetKeepAbove">
      <arg type="s" name="title" direction="in"/>
      <arg type="b" name="above" direction="in"/>
      <arg type="b" name="found" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>`;

export default class PortGlanceHelper extends Extension {
    enable() {
        this._pinned = new Set();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        // Re-apply when the widget window is shown again after being hidden.
        this._windowCreatedId = global.display.connect('window-created', (_display, window) => {
            GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._applyTo(window);
                return GLib.SOURCE_REMOVE;
            });
        });
    }

    disable() {
        global.display.disconnect(this._windowCreatedId);
        this._windowCreatedId = 0;
        for (const window of this._ourWindows())
            this._setAbove(window, false);
        this._dbus.unexport();
        this._dbus = null;
        this._pinned = null;
    }

    get Version() {
        return 1;
    }

    SetKeepAbove(title, above) {
        if (above)
            this._pinned.add(title);
        else
            this._pinned.delete(title);

        let found = false;
        for (const window of this._ourWindows()) {
            if (window.get_title() !== title)
                continue;
            found = true;
            this._setAbove(window, above);
        }
        return found;
    }

    _ourWindows() {
        return global.get_window_actors()
            .map(actor => actor.get_meta_window())
            .filter(window => window && this._isOurs(window));
    }

    _isOurs(window) {
        const appId = window.get_gtk_application_id?.() ?? '';
        const wmClass = window.get_wm_class() ?? '';
        return appId.startsWith(APP_ID) || wmClass.startsWith(APP_ID);
    }

    _applyTo(window) {
        if (this._pinned?.has(window.get_title()) && this._isOurs(window))
            this._setAbove(window, true);
    }

    _setAbove(window, above) {
        if (above) {
            window.make_above();
            window.stick();
        } else {
            window.unmake_above();
            window.unstick();
        }
    }
}
