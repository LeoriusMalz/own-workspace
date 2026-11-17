"use strict";

(function (root, factory) {
    const Controller = factory();
    if (typeof module === "object" && module.exports) module.exports = Controller;
    if (root) root.EfficiencyWindow = Controller;
})(typeof window === "undefined" ? null : window, () => {
    const KEY = "workspace:efficiency-mini-mode";
    const MODES = ["pinned", "auto", "manual"];

    return class EfficiencyWindow {
        constructor({ host = window, render = () => {}, hasActivity = () => false, canAutoOpen = () => false, onChange = () => {}, onError = () => {} } = {}) {
            this.host = host;
            this.render = render;
            this.hasActivity = hasActivity;
            this.canAutoOpen = canAutoOpen;
            this.onChange = onChange;
            this.onError = onError;
            this.mode = "pinned";
            try { const saved = host.localStorage.getItem(KEY); if (MODES.includes(saved)) this.mode = saved; } catch (_) { /* Private browsing may deny storage. */ }
            this.handle = null;
            this.kind = null;
            this.pending = null;
            this.cleanup = null;
            this.automatic = false;
            this.disposed = false;
            this.notice = "";
            this.autoSupported = false;
            this.onVisible = () => { if (!host.document.hidden && this.automatic) this.close(); };
            this.onUnload = () => this.dispose();
            host.document.addEventListener("visibilitychange", this.onVisible);
            host.addEventListener("pagehide", this.onUnload);
            if (this.supported && !this.isMini()) {
                try {
                    host.navigator.mediaSession.setActionHandler("enterpictureinpicture", () => {
                        if (this.mode === "auto" && this.hasActivity() && this.canAutoOpen()) return this.open({ automatic: true, focus: false });
                    });
                    this.autoSupported = true;
                } catch (_) { /* Older browsers do not expose this Media Session action. */ }
            }
        }

        get supported() { return Boolean(this.host.isSecureContext && this.host.documentPictureInPicture?.requestWindow); }
        isMini() { return this.host.location?.pathname === "/efficiency/mini" || this.host.documentPictureInPicture?.window === this.host; }
        getState() {
            return { mode: this.mode, supported: this.supported, secure: Boolean(this.host.isSecureContext), autoSupported: this.autoSupported,
                open: Boolean(this.handle && !this.handle.closed), kind: this.handle && !this.handle.closed ? this.kind : null, notice: this.notice };
        }
        changed() { this.onChange(this.getState()); }
        setMode(mode) {
            if (!MODES.includes(mode)) throw new TypeError("Неизвестный режим мини-окна");
            this.mode = mode;
            try { this.host.localStorage.setItem(KEY, mode); } catch (_) { /* The preference still applies to this page. */ }
            if (this.automatic && mode !== "auto") this.close();
            this.changed();
        }
        onStart() {
            if (this.mode !== "pinned" || !this.supported || this.isMini() || this.disposed) return Promise.resolve(null);
            return this.open({ focus: false });
        }
        open({ automatic = false, focus = true } = {}) {
            if (this.disposed || this.isMini()) return Promise.resolve(null);
            if (automatic && (!this.supported || this.mode !== "auto" || !this.hasActivity() || !this.canAutoOpen())) return Promise.resolve(null);
            if (this.handle && !this.handle.closed) {
                if (!automatic) this.automatic = false;
                if (focus && !automatic) this.handle.focus();
                return Promise.resolve(this.handle);
            }
            if (this.handle) this.detach(this.handle);
            if (this.pending) return this.pending;
            this.notice = "";
            if (!this.supported) {
                try {
                    const popup = this.host.open("/efficiency/mini", "workspace-efficiency-mini", "popup,width=400,height=340,resizable=yes");
                    if (!popup) throw new Error("Браузер заблокировал мини-окно. Разрешите всплывающие окна для этого сайта.");
                    this.handle = popup;
                    this.kind = "popup";
                    this.notice = this.host.isSecureContext
                        ? "Открыто отдельное окно. Для меню поверх приложений используйте Chrome или Edge с поддержкой Picture-in-Picture."
                        : "Открыто отдельное окно. Для меню поверх приложений откройте сервис через HTTPS или localhost в Chrome/Edge.";
                    this.changed();
                    return Promise.resolve(popup);
                } catch (error) { this.failed(error); return Promise.resolve(null); }
            }
            // Invoke immediately inside the click: awaiting a server write would lose user activation.
            let requested;
            try { requested = this.host.documentPictureInPicture.requestWindow({ width: 400, height: 340 }); }
            catch (error) { this.failed(error); return Promise.resolve(null); }
            this.pending = Promise.resolve(requested).then(handle => {
                if (this.disposed || (automatic && (this.mode !== "auto" || !this.host.document.hidden || !this.hasActivity() || !this.canAutoOpen()))) { handle.close(); return null; }
                this.handle = handle;
                this.kind = "pip";
                this.automatic = automatic;
                this.onWindowClose = () => this.detach(handle);
                handle.addEventListener("pagehide", this.onWindowClose, { once: true });
                const doc = handle.document;
                doc.title = "Фокус и время";
                doc.body.className = "ef-pip-body";
                this.host.document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
                    const url = new URL(link.href, this.host.location.href);
                    if (url.origin !== this.host.location.origin) return;
                    const stylesheet = doc.createElement("link"); stylesheet.rel = "stylesheet"; stylesheet.href = url.href; doc.head.append(stylesheet);
                });
                this.cleanup = this.render(doc);
                this.changed();
                return handle;
            }).catch(error => { this.close(); if (!this.disposed) this.failed(error); return null; }).finally(() => { this.pending = null; });
            return this.pending;
        }
        failed(error) {
            this.notice = error?.name === "NotAllowedError"
                ? "Браузер требует нажатия: откройте мини-окно кнопкой «Поверх окон». Для автоматического появления разрешите Picture-in-Picture в настройках сайта."
                : error?.message || "Не удалось открыть мини-окно. Попробуйте кнопку «Поверх окон» в Chrome или Edge.";
            this.onError(this.notice);
            this.changed();
        }
        detach(handle) {
            if (this.handle !== handle) return;
            if (this.onWindowClose) handle.removeEventListener("pagehide", this.onWindowClose);
            const cleanup = this.cleanup;
            this.cleanup = null;
            this.handle = null;
            this.kind = null;
            this.automatic = false;
            this.onWindowClose = null;
            cleanup?.();
            this.changed();
        }
        close() {
            const handle = this.handle;
            if (!handle) return;
            this.detach(handle);
            if (!handle.closed) handle.close();
        }
        dispose() {
            if (this.disposed) return;
            this.disposed = true;
            this.host.document.removeEventListener("visibilitychange", this.onVisible);
            this.host.removeEventListener("pagehide", this.onUnload);
            if (this.autoSupported) { try { this.host.navigator.mediaSession.setActionHandler("enterpictureinpicture", null); } catch (_) {} }
            this.close();
        }
    };
});
