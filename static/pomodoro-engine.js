/* Pure Pomodoro clock and audio playback, independent of the current SPA page. */
(function (root, factory) {
    "use strict";
    const api = factory(root);
    if (typeof module === "object" && module.exports) module.exports = api;
    if (root) root.PomodoroEngine = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";

    function defaults() {
        return {
            workMinutes: 25, restMinutes: 5, totalMinutes: 120, infinite: false,
            display: "ring", showDigits: true,
            workAudio: null, restAudio: null, workBackground: null, restBackground: null,
            volume: 0.5, fadeInSeconds: 3, fadeOutSeconds: 3,
            cueStartSeconds: 5, cueEndSeconds: 5, cueVolume: 0.6,
        };
    }

    function boundedNumber(value, name, minimum, maximum) {
        if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
            throw new RangeError(`${name}: значение должно быть от ${minimum} до ${maximum}`);
        }
        return value;
    }

    function validateSettings(value = {}) {
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Некорректные настройки таймера");
        const settings = { ...defaults() };
        for (const key of Object.keys(settings)) if (Object.prototype.hasOwnProperty.call(value, key)) settings[key] = value[key];
        for (const key of ["workMinutes", "restMinutes"]) boundedNumber(settings[key], key, 1 / 60, 1440);
        boundedNumber(settings.totalMinutes, "totalMinutes", 1 / 60, 10080);
        for (const key of ["volume", "cueVolume"]) boundedNumber(settings[key], key, 0, 1);
        for (const key of ["fadeInSeconds", "fadeOutSeconds", "cueStartSeconds", "cueEndSeconds"]) boundedNumber(settings[key], key, 0, 60);
        for (const key of ["infinite", "showDigits"]) if (typeof settings[key] !== "boolean") throw new TypeError(`${key}: ожидается переключатель`);
        if (!["ring", "bar"].includes(settings.display)) throw new TypeError("Выберите кольцо или полосу таймера");
        for (const key of ["workAudio", "restAudio", "workBackground", "restBackground"]) {
            if (settings[key] !== null && (typeof settings[key] !== "string" || !settings[key] || settings[key].length > 160)) {
                throw new TypeError(`${key}: некорректный файл`);
            }
        }
        return settings;
    }

    function validNow(now) {
        return boundedNumber(now, "Время", 0, Number.MAX_SAFE_INTEGER);
    }

    function clockState(timer) {
        const state = timer || {};
        if (!["idle", "running", "paused", "finished"].includes(state.status)) throw new TypeError("Некорректное состояние таймера");
        boundedNumber(state.elapsed, "Прошедшее время", 0, Number.MAX_SAFE_INTEGER / 1000);
        if (state.status === "running") validNow(state.anchor);
        return { ...state, config: validateSettings(state.config || {}) };
    }

    function snapshot(timer, nowMs = Date.now()) {
        validNow(nowMs);
        const state = clockState(timer);
        const config = state.config;
        const total = config.infinite ? null : config.totalMinutes * 60;
        let elapsed = state.elapsed;
        if (state.status === "running") elapsed += Math.max(0, nowMs - state.anchor) / 1000;
        elapsed = Math.max(0, total === null ? elapsed : Math.min(total, elapsed));
        const finished = total !== null && elapsed >= total;
        const work = config.workMinutes * 60;
        const rest = config.restMinutes * 60;
        const cycleDuration = work + rest;
        // At the finite endpoint, show the phase that has just ended, not a fresh one.
        const position = finished ? Math.max(0, elapsed - 1e-7) : elapsed;
        const cycleIndex = Math.floor(position / cycleDuration);
        const inCycle = position - cycleIndex * cycleDuration;
        const isWork = inCycle < work;
        const phaseStart = cycleIndex * cycleDuration + (isWork ? 0 : work);
        const nominalDuration = isWork ? work : rest;
        const phaseDuration = total === null ? nominalDuration : Math.min(nominalDuration, total - phaseStart);
        const phaseElapsed = Math.min(phaseDuration, Math.max(0, elapsed - phaseStart));
        return {
            elapsed, total, remainingTotal: total === null ? null : Math.max(0, total - elapsed),
            phase: isWork ? "work" : "rest", phaseDuration, phaseElapsed,
            phaseRemaining: Math.max(0, phaseDuration - phaseElapsed),
            cycle: cycleIndex + 1, phaseIndex: cycleIndex * 2 + (isWork ? 0 : 1), phaseStart,
            progress: phaseDuration ? Math.min(1, phaseElapsed / phaseDuration) : 1,
            finished, running: state.status === "running" && !finished,
            status: finished ? "finished" : state.status, anchor: state.anchor, nowMs,
        };
    }

    function transition(timer, action, options = {}) {
        const now = validNow(options.now === undefined ? Date.now() : options.now);
        const current = timer ? clockState(timer) : { status: "idle", elapsed: 0, anchor: null, config: defaults() };
        const config = action === "start" ? validateSettings(options.settings || current.config) : { ...current.config };
        if (action === "start") return { status: "running", elapsed: 0, anchor: now, config };
        if (action === "stop") return { status: "idle", elapsed: 0, anchor: null, config };
        const reading = snapshot(current, now);
        if (action === "seek") {
            boundedNumber(options.seconds, "Перемотка", -Number.MAX_SAFE_INTEGER / 1000, Number.MAX_SAFE_INTEGER / 1000);
            const elapsed = Math.max(0, reading.total === null ? options.seconds : Math.min(options.seconds, reading.total));
            const finished = reading.total !== null && elapsed >= reading.total;
            const status = finished ? "finished" : reading.running ? "running" : "paused";
            return { status, elapsed, anchor: status === "running" ? now : null, config };
        }
        if (action === "pause") {
            if (current.status === "idle") return { ...current, config };
            return { status: reading.finished ? "finished" : "paused", elapsed: reading.elapsed, anchor: null, config };
        }
        if (action === "resume") {
            if (reading.finished) return { status: "finished", elapsed: reading.elapsed, anchor: null, config };
            return { status: "running", elapsed: reading.elapsed, anchor: now, config };
        }
        throw new TypeError(`Неизвестное действие таймера: ${action}`);
    }

    function musicVolume(reading, config) {
        const fadeIn = config.fadeInSeconds > 0 ? reading.phaseElapsed / config.fadeInSeconds : 1;
        const fadeOut = config.fadeOutSeconds > 0 ? reading.phaseRemaining / config.fadeOutSeconds : 1;
        return Math.max(0, Math.min(1, config.volume * Math.min(1, fadeIn, fadeOut)));
    }

    class AudioController {
        constructor({ onError = () => {}, audioFactory, contextFactory } = {}) {
            this.onError = onError;
            this.audioFactory = audioFactory || (() => new root.Audio());
            this.contextFactory = contextFactory || (() => {
                const Context = root.AudioContext || root.webkitAudioContext;
                return Context ? new Context() : null;
            });
            this.audio = null;
            this.context = null;
            this.sourceKey = null;
            this.current = null;
            this.previous = null;
            this.lastCueKey = null;
            this.playPending = false;
            this.blocked = false;
            this.disposed = false;
            this.errors = new Set();
            this.voices = new Set();
        }

        _report(message, key) {
            if (this.errors.has(key) || this.disposed) return;
            this.errors.add(key);
            this.onError(message);
        }

        _ensureAudio() {
            if (this.audio || this.disposed) return;
            try {
                this.audio = this.audioFactory();
                this.audio.loop = true;
                this.audio.preload = "auto";
                this.audio.addEventListener("loadedmetadata", () => {
                    if (this.current && !this.disposed) this._seek(this.current.reading.phaseElapsed, true);
                });
                this.audio.addEventListener("error", () => {
                    this.blocked = true;
                    this._report("Не удалось воспроизвести выбранный звук. Проверьте формат файла.", `media:${this.sourceKey}`);
                });
            } catch (_) {
                this._report("Этот браузер не поддерживает воспроизведение аудио.", "audio-unavailable");
            }
        }

        async unlock() {
            if (this.disposed) return;
            this.blocked = false;
            this.errors.delete("autoplay");
            this._ensureAudio();
            try {
                if (!this.context) this.context = this.contextFactory();
                if (this.context?.state === "suspended") await this.context.resume();
            } catch (_) {
                this._report("Нажмите запуск ещё раз, чтобы разрешить звуковые сигналы.", "context");
            }
            if (this.current?.reading.running) this._play();
        }

        _play() {
            if (!this.audio || this.blocked || this.playPending || !this.audio.paused || !this.audio.src || this.disposed) return;
            this.playPending = true;
            const generation = this.sourceKey;
            Promise.resolve().then(() => {
                if (!this.disposed && this.current?.reading.running) return this.audio?.play();
            }).catch(error => {
                if (this.disposed || generation !== this.sourceKey || error?.name === "AbortError") return;
                this.blocked = true;
                this._report(
                    error?.name === "NotAllowedError" ? "Браузер заблокировал звук. Нажмите запуск или продолжение таймера." : "Не удалось включить музыку. Выберите другой файл или повторите запуск.",
                    error?.name === "NotAllowedError" ? "autoplay" : `play:${generation}`,
                );
            }).finally(() => {
                this.playPending = false;
                if (!this.current?.reading.running) this.audio?.pause();
            });
        }

        _seek(elapsed, force = false) {
            if (!this.audio || !Number.isFinite(this.audio.duration) || this.audio.duration <= 0) return;
            const position = Math.max(0, elapsed) % this.audio.duration;
            const delta = Math.abs(this.audio.currentTime - position);
            const wrappedDelta = Math.min(delta, Math.abs(this.audio.duration - delta));
            if (!force && wrappedDelta < 1.5) return;
            try { this.audio.currentTime = position; } catch (_) { /* The media is still loading. */ }
        }

        _bell(strong, config) {
            if (!this.context || this.context.state !== "running" || config.volume <= 0 || config.cueVolume <= 0 || this.disposed) return;
            const start = this.context.currentTime;
            // A short pair of sine partials gives a bell-like sound without external files.
            for (const [frequency, weight] of [[880, 1], [1760, 0.3]]) {
                const oscillator = this.context.createOscillator();
                const gain = this.context.createGain();
                const voice = { oscillator, gain };
                this.voices.add(voice);
                oscillator.type = "sine";
                oscillator.frequency.setValueAtTime(frequency, start);
                const loudness = config.volume * config.cueVolume * (strong ? 0.32 : 0.16) * weight;
                gain.gain.setValueAtTime(0.0001, start);
                gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, loudness), start + 0.012);
                gain.gain.exponentialRampToValueAtTime(0.0001, start + (strong ? 0.6 : 0.25));
                oscillator.connect(gain);
                gain.connect(this.context.destination);
                oscillator.onended = () => {
                    oscillator.disconnect();
                    gain.disconnect();
                    this.voices.delete(voice);
                };
                oscillator.start(start);
                oscillator.stop(start + (strong ? 0.65 : 0.3));
            }
        }

        _silenceBells() {
            for (const voice of this.voices) {
                try { voice.oscillator.stop(); } catch (_) { /* Already ended. */ }
                try { voice.gain.disconnect(); } catch (_) { /* Already disconnected. */ }
            }
            this.voices.clear();
        }

        update(reading, config, mediaList = []) {
            if (this.disposed) return;
            config = validateSettings(config);
            this._ensureAudio();
            const previous = this.previous;
            const now = reading.nowMs === undefined ? Date.now() : reading.nowMs;
            const expected = previous ? previous.reading.elapsed + (previous.reading.running ? Math.max(0, now - previous.now) / 1000 : 0) : reading.elapsed;
            const cappedExpected = reading.total === null ? expected : Math.min(expected, reading.total);
            const changedAnchor = previous && previous.reading.running && reading.running && previous.reading.anchor !== reading.anchor;
            const jumped = Boolean(previous && (changedAnchor || Math.abs(cappedExpected - reading.elapsed) > 1.2));
            const segment = reading.phaseIndex === undefined ? `${reading.cycle}:${reading.phase}` : reading.phaseIndex;
            const priorSegment = previous ? (previous.reading.phaseIndex === undefined ? `${previous.reading.cycle}:${previous.reading.phase}` : previous.reading.phaseIndex) : null;
            const id = config[reading.phase === "work" ? "workAudio" : "restAudio"];
            const media = mediaList.find(item => item.id === id && item.kind === "audio");
            // A media list can arrive after timer state. Include the resolved URL so
            // that a formerly missing upload starts as soon as it becomes available.
            const key = `${segment}:${id || "silent"}:${media?.url || "missing"}`;
            this.current = { reading, config };
            if (this.sourceKey !== key) {
                this.sourceKey = key;
                this.blocked = false;
                this.audio?.pause();
                if (this.audio) {
                    if (media) {
                        this.audio.src = media.url;
                        this.audio.load();
                    } else {
                        this.audio.removeAttribute("src");
                        this.audio.load();
                    }
                }
                if (id && !media) this._report("Выбранный музыкальный файл недоступен. Выберите другой в настройках.", `missing:${id}`);
            }
            if (this.audio) {
                this.audio.volume = reading.running ? musicVolume(reading, config) : 0;
                this._seek(reading.phaseElapsed, jumped || priorSegment !== segment);
                if (reading.running && media) this._play();
                else this.audio.pause();
            }
            if (!reading.running && !reading.finished) this._silenceBells();
            if (reading.finished && previous?.reading.running && !jumped) {
                this._bell(true, config);
                this.lastCueKey = `finished:${reading.total}`;
            } else if (reading.running) {
                const second = Math.floor(reading.phaseElapsed + 1e-8);
                const cueKey = `${segment}:${second}`;
                const phaseTransition = priorSegment !== segment && reading.phaseElapsed < 1.25;
                const initialStart = !previous && reading.phaseElapsed < 0.25;
                const windowCue = (config.cueStartSeconds > 0 && reading.phaseElapsed < config.cueStartSeconds) ||
                    (config.cueEndSeconds > 0 && reading.phaseRemaining <= config.cueEndSeconds && reading.phaseRemaining > 0);
                if (!jumped && cueKey !== this.lastCueKey && (phaseTransition || initialStart || windowCue)) {
                    this._bell(phaseTransition || initialStart, config);
                    this.lastCueKey = cueKey;
                } else if (jumped) {
                    // A seek updates playback immediately, but does not replay skipped indicator ticks.
                    this._silenceBells();
                    this.lastCueKey = cueKey;
                }
            }
            this.previous = { reading: { ...reading }, now };
        }

        suspend() {
            this.audio?.pause();
            if (this.audio) this.audio.volume = 0;
            this._silenceBells();
            this.previous = null;
            this.current = null;
        }

        dispose() {
            this.suspend();
            this.disposed = true;
            if (this.audio) { this.audio.removeAttribute("src"); this.audio.load(); }
            if (this.context) Promise.resolve(this.context.close()).catch(() => {});
            this.audio = null;
            this.context = null;
        }
    }

    return { defaults, validateSettings, snapshot, transition, musicVolume, AudioController };
});
