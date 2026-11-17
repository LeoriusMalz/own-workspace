"use strict";

window.Efficiency = (() => {
    const Engine = window.PomodoroEngine;
    const AUDIO_LEASE = "workspace:efficiency-audio-owner";
    const tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let services, initialized = false, ready, state, media = [], audio, ownsAudio = false;
    let loadedTimer = false, loadedMedia = false, fetchingMedia = false;
    let audioLockRelease = null, audioLockPending = false, settingsTimer, settingsDirty = false;
    let completing = false;
    let saving = Promise.resolve(), polling = false, view = null, viewCleanup = () => {}, audioNotice = "";
    let full = null, fullTrackerCleanup = () => {}, pipWindow = null;
    const minis = new Set();
    const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel("workspace-efficiency") : null;
    const copy = value => JSON.parse(JSON.stringify(value));
    const fmt = (seconds, roundUp = true) => {
        const n = Math.max(0, (roundUp ? Math.ceil : Math.floor)(seconds || 0));
        return n >= 3600 ? `${Math.floor(n / 3600)}:${String(Math.floor(n / 60) % 60).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}` : `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
    };
    function timerSnapshot() { return Engine.snapshot(state.pomodoro.status === "idle" ? { ...state.pomodoro, config: state.settings } : state.pomodoro, Date.now()); }
    function settings() { return state?.settings || Engine.defaults(); }
    function active() { return state && ["running", "paused"].includes(state.pomodoro.status); }
    function notify(message, type = "error") { services?.showToast(message, type); }
    async function api(path, options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), path === "/media" && options.method === "POST" ? 180000 : 15000);
        try { return await services.apiFetch(`/api/efficiency${path}`, { ...options, signal: controller.signal }); }
        catch (error) { if (error.name === "AbortError") throw new Error("Сервер не ответил вовремя. Проверьте соединение."); throw error; }
        finally { clearTimeout(timeout); }
    }
    async function refresh() {
        if (polling || settingsDirty) return;
        polling = true;
        try {
            const result = await api("/timer");
            if (!settingsDirty && (!state || result.revision >= state.revision)) {
                loadedTimer = true;
                const changed = !state || JSON.stringify(result.settings) !== JSON.stringify(state.settings);
                state = result;
                if (changed) fillSettings();
                const referenced = [result.settings, result.pomodoro.config].flatMap(config => [config.workAudio, config.restAudio, config.workBackground, config.restBackground]).filter(Boolean);
                if (loadedMedia && referenced.some(id => !media.some(item => item.id === id))) refreshMedia().catch(() => {});
            }
        } catch (error) { if (!loadedTimer) throw error; }
        finally { polling = false; }
    }
    async function refreshMedia() {
        if (fetchingMedia) return;
        fetchingMedia = true;
        try { media = await api("/media"); loadedMedia = true; mediaOptions(); }
        finally { fetchingMedia = false; }
    }
    function commit(mutator) {
        const operation = saving.catch(() => {}).then(async () => {
            await ready;
            const draft = mutator(copy(state));
            try {
                const result = await api("/timer", { method: "PUT", body: JSON.stringify(draft) });
                const pendingPreferences = settingsDirty ? state.settings : null;
                state = result;
                if (pendingPreferences) state.settings = pendingPreferences;
                channel?.postMessage({ type: "timer" });
                tick();
                return state;
            } catch (error) {
                if (error.status === 409) {
                    settingsDirty = false;
                    await refresh();
                    throw new Error("Таймер изменён в другом окне. Обновил состояние — повторите действие.");
                }
                throw error;
            }
        });
        saving = operation;
        return operation;
    }
    function persistSettings() {
        clearTimeout(settingsTimer);
        if (!settingsDirty) return saving;
        const chosen = copy(state.settings);
        settingsDirty = false;
        return commit(current => {
            current.settings = chosen;
            // Timing edits take effect on the next start; presentation and sound are live.
            if (current.pomodoro.config) for (const key of Object.keys(chosen)) {
                if (!["workMinutes", "restMinutes", "totalMinutes", "infinite"].includes(key)) current.pomodoro.config[key] = chosen[key];
            }
            return current;
        }).catch(error => { notify(error.message); throw error; });
    }
    async function control(action, seconds) {
        audioNotice = "";
        claimAudio();
        audio?.unlock();
        try {
            await persistSettings();
            await commit(current => {
                if (current.pomodoro.status === "idle") current.pomodoro.config = copy(current.settings);
                const targetSeconds = action === "seekBy" ? Engine.snapshot(current.pomodoro, Date.now()).elapsed + seconds : seconds;
                current.pomodoro = Engine.transition(current.pomodoro, action === "seekBy" ? "seek" : action, { settings: current.settings, seconds: targetSeconds, now: Date.now() });
                return current;
            });
        } catch (error) { notify(error.message); }
    }
    function readLease() {
        try { return JSON.parse(localStorage.getItem(AUDIO_LEASE) || "null"); } catch (_) { return null; }
    }
    function setLease() {
        try { localStorage.setItem(AUDIO_LEASE, JSON.stringify({ id: tabId, until: Date.now() + 4500 })); } catch (_) { /* Web Locks remains available in private mode. */ }
    }
    function releaseAudio() {
        ownsAudio = false;
        audio?.suspend();
        audioLockRelease?.();
        audioLockRelease = null;
    }
    function claimAudio() { setLease(); maintainAudioOwner(); }
    function maintainAudioOwner() {
        const lease = readLease();
        if (lease && lease.id !== tabId && lease.until > Date.now()) { releaseAudio(); return; }
        if (lease?.id === tabId || !lease || lease.until <= Date.now()) {
            setLease();
            if (!navigator.locks?.request) { ownsAudio = true; return; }
            if (ownsAudio || audioLockPending) return;
            audioLockPending = true;
            navigator.locks.request("workspace-efficiency-audio", { ifAvailable: true }, async lock => {
                if (!lock || (readLease()?.id && readLease().id !== tabId)) return;
                ownsAudio = true;
                await new Promise(resolve => { audioLockRelease = resolve; });
            }).catch(() => {}).finally(() => { audioLockPending = false; });
        }
    }
    function ringMarkup(prefix = "") {
        return `<div class="ef-clock" data-clock><svg viewBox="0 0 240 240" aria-hidden="true"><circle class="ef-ring-track" cx="120" cy="120" r="108"/><circle class="ef-ring-fill" data-ring cx="120" cy="120" r="108"/></svg><div class="ef-clock-copy"><span class="ef-phase" data-phase>Готовы к фокусу?</span><strong data-time>25:00</strong><span class="ef-cycle" data-cycle>Цикл 1</span></div></div><div class="ef-bar" data-bar hidden><span data-fill></span></div><p class="ef-total" data-total></p>`;
    }
    function controlsMarkup() {
        return `<div class="ef-controls"><button type="button" class="button button--secondary" data-control="back" title="На минуту назад">−1 мин</button><button type="button" class="button" data-control="toggle">Начать</button><button type="button" class="button button--secondary" data-control="forward" title="На минуту вперёд">+1 мин</button><button type="button" class="button button--ghost" data-control="stop">Сбросить</button></div>`;
    }
    function bindControls(node) {
        node.addEventListener("click", event => {
            const action = event.target.closest("[data-control]")?.dataset.control;
            if (!action || !state) return;
            if (action === "back" || action === "forward") control("seekBy", action === "back" ? -60 : 60);
            else if (action === "toggle") control(state.pomodoro.status === "running" ? "pause" : state.pomodoro.status === "paused" ? "resume" : "start");
            else control(action);
        });
    }
    function updateClock(node, snapshot) {
        const conf = state.pomodoro.status === "idle" ? state.settings : state.pomodoro.config;
        const s = state.pomodoro.status === "idle" ? { ...snapshot, phase: "work", phaseRemaining: conf.workMinutes * 60, progress: 0, cycle: 1 } : snapshot;
        node.dataset.phase = s.phase;
        node.querySelectorAll("[data-phase]").forEach(el => el.textContent = snapshot.finished ? "Сессия завершена" : s.phase === "work" ? "Время фокуса" : "Время отдыха");
        node.querySelectorAll("[data-time]").forEach(el => { el.textContent = snapshot.finished ? "00:00" : fmt(s.phaseRemaining); el.hidden = !conf.showDigits; });
        node.querySelectorAll("[data-cycle]").forEach(el => el.textContent = `${state.pomodoro.status === "paused" ? "Пауза · " : ""}Цикл ${s.cycle}`);
        node.querySelectorAll("[data-ring]").forEach(el => el.style.strokeDashoffset = String(678.584 * Math.min(1, Math.max(0, s.progress))));
        node.querySelectorAll("[data-clock]").forEach(el => el.classList.toggle("ef-clock--line", conf.display === "bar"));
        node.querySelectorAll("[data-bar]").forEach(el => el.hidden = conf.display !== "bar");
        node.querySelectorAll("[data-fill]").forEach(el => el.style.width = `${Math.max(0, 1 - s.progress) * 100}%`);
        node.querySelectorAll("[data-total]").forEach(el => el.textContent = snapshot.total === null ? `Без конца · прошло ${fmt(snapshot.elapsed, false)}` : `Прошло ${fmt(snapshot.elapsed, false)} / ${fmt(snapshot.total)}`);
        node.querySelectorAll('[data-control="toggle"]').forEach(el => el.textContent = state.pomodoro.status === "running" ? "Пауза" : state.pomodoro.status === "paused" ? "Продолжить" : snapshot.finished ? "Ещё раз" : "Начать");
    }
    function setBackground(node, snapshot) {
        const conf = state.pomodoro.config || state.settings;
        const id = snapshot.phase === "rest" ? conf.restBackground : conf.workBackground;
        const target = node.querySelector("[data-background]");
        const file = media.find(item => item.id === id);
        const mediaKey = `${id}:${file?.url || "pending"}`;
        if (!target || target.dataset.media === mediaKey) return;
        target.dataset.media = mediaKey;
        target.replaceChildren();
        if (!file) return;
        const element = target.ownerDocument.createElement(file.kind === "video" ? "video" : "img");
        element.src = file.url;
        if (file.kind === "video") { element.muted = true; element.loop = true; element.autoplay = true; element.playsInline = true; element.play().catch(() => {}); }
        else element.alt = "";
        target.append(element);
    }
    function tick() {
        if (!state) return;
        const snapshot = timerSnapshot();
        if (ownsAudio && loadedMedia) audio?.update(snapshot, state.pomodoro.config || state.settings, media);
        else audio?.suspend();
        if (view?.isConnected) {
            updateClock(view, snapshot);
            const seek = view.querySelector("#ef-seek");
            if (seek && document.activeElement !== seek) {
                seek.max = String(snapshot.total ?? Math.max(snapshot.elapsed + 600, (state.settings.workMinutes + state.settings.restMinutes) * 60));
                seek.value = String(snapshot.elapsed);
            }
            const banner = view.querySelector("#ef-audio-notice");
            if (banner) { banner.hidden = !audioNotice; banner.textContent = audioNotice; }
        }
        if (full && !full.hidden) { updateClock(full, snapshot); setBackground(full, snapshot); }
        for (const mini of minis) {
            const showPomo = active() || snapshot.finished;
            mini.pomo.hidden = !showPomo;
            mini.time.textContent = fmt(snapshot.phaseRemaining);
            mini.phase.textContent = snapshot.finished ? "Помодоро завершён" : `${snapshot.phase === "work" ? "Фокус" : "Отдых"}${state.pomodoro.status === "paused" ? " · пауза" : ""}`;
            mini.toggle.textContent = state.pomodoro.status === "running" ? "Ⅱ" : "▶";
            mini.toggle.setAttribute("aria-label", state.pomodoro.status === "running" ? "Приостановить помодоро" : "Продолжить помодоро");
            mini.node.hidden = !(showPomo || window.TimeTracker.getState()?.activeSession);
            if (mini.dock) mini.node.hidden ||= Boolean(view?.isConnected) || Boolean(full && !full.hidden) || location.pathname === "/efficiency/mini";
        }
        if (snapshot.finished && state.pomodoro.status === "running" && ownsAudio && !settingsDirty && !completing) {
            completing = true;
            commit(current => {
                if (Engine.snapshot(current.pomodoro, Date.now()).finished) current.pomodoro = Engine.transition(current.pomodoro, "pause", { now: Date.now() });
                return current;
            }).catch(error => notify(error.message)).finally(() => { completing = false; });
        }
    }
    function createMini(container, dock = false) {
        const doc = container.ownerDocument;
        const node = doc.createElement("aside");
        node.className = dock ? "ef-dock" : "ef-mini-panel";
        node.innerHTML = `<div class="ef-mini-pomo"><a href="/efficiency/pomodoro" data-return class="ef-mini-open"><span class="ef-mini-icon">◷</span><span><small data-mini-phase>Фокус</small><strong data-mini-time>25:00</strong></span></a><button type="button" class="ef-mini-pause" aria-label="Приостановить помодоро">Ⅱ</button></div><div class="ef-mini-tracker"></div><div class="ef-mini-footer"><a href="/efficiency/pomodoro" data-return>Открыть страницу ↗</a>${dock ? '<button type="button" data-popout aria-label="Открыть отдельное мини-окно">▣</button>' : ""}</div>`;
        container.append(node);
        const record = { node, dock, pomo: node.querySelector(".ef-mini-pomo"), phase: node.querySelector("[data-mini-phase]"), time: node.querySelector("[data-mini-time]"), toggle: node.querySelector(".ef-mini-pause") };
        minis.add(record);
        record.toggle.addEventListener("click", () => control(state.pomodoro.status === "running" ? "pause" : state.pomodoro.status === "paused" ? "resume" : "start"));
        node.querySelectorAll("[data-return]").forEach(link => link.addEventListener("click", event => {
            event.preventDefault();
            if (location.pathname === "/efficiency/mini" && window.opener && !window.opener.closed) { window.opener.focus(); window.opener.postMessage({ type: "efficiency-open" }, location.origin); }
            else { window.focus(); services.navigate("/efficiency/pomodoro"); }
        }));
        node.querySelector("[data-popout]")?.addEventListener("click", openMiniWindow);
        const trackerCleanup = window.TimeTracker.renderMini(node.querySelector(".ef-mini-tracker"));
        node.addEventListener("click", event => {
            if (!event.target.closest("a[data-link]")) return;
            event.preventDefault(); event.stopPropagation();
            node.querySelector("[data-return]").click();
        });
        return () => { minis.delete(record); trackerCleanup?.(); node.remove(); };
    }
    async function openMiniWindow() {
        try {
            if (window.documentPictureInPicture?.requestWindow) {
                if (pipWindow && !pipWindow.closed) { pipWindow.focus(); return; }
                pipWindow = await window.documentPictureInPicture.requestWindow({ width: 380, height: 320 });
                const doc = pipWindow.document;
                doc.title = "Фокус и время";
                document.querySelectorAll('link[rel="stylesheet"]').forEach(link => { const style = doc.createElement("link"); style.rel = "stylesheet"; style.href = link.href; doc.head.append(style); });
                doc.body.className = "ef-pip-body";
                const remove = createMini(doc.body);
                pipWindow.addEventListener("pagehide", remove, { once: true });
                tick();
            } else {
                const popup = window.open("/efficiency/mini", "workspace-efficiency-mini", "popup,width=390,height=380,resizable=yes");
                if (!popup) throw new Error("Браузер заблокировал мини-окно. Разрешите всплывающие окна для этого сайта.");
            }
        } catch (error) { notify(error.message); }
    }
    function ensureFull() {
        if (full) return;
        full = document.createElement("section");
        full.className = "ef-full"; full.hidden = true;
        full.innerHTML = `<div class="ef-background" data-background></div><div class="ef-full-shade"></div><div class="ef-full-top"><span>WORKSPACE / ФОКУС</span><button type="button" class="button button--secondary" data-close-full>Вернуться ↙</button></div><div class="ef-full-clock">${ringMarkup()}${controlsMarkup()}</div><div class="ef-full-tracker"></div>`;
        document.body.append(full); bindControls(full);
        full.querySelector("[data-close-full]").addEventListener("click", closeFull);
        full.addEventListener("click", event => { if (event.target.closest("a[data-link]")) closeFull(); });
        fullTrackerCleanup = window.TimeTracker.renderMini(full.querySelector(".ef-full-tracker"));
    }
    function openFull() {
        ensureFull(); full.hidden = false; tick();
        full.requestFullscreen?.().catch(() => { /* The full-window overlay also works without browser Fullscreen. */ });
    }
    function closeFull() {
        if (document.fullscreenElement === full) document.exitFullscreen().catch(() => {});
        if (full) { full.hidden = true; full.querySelectorAll("video").forEach(v => v.pause()); const bg = full.querySelector("[data-background]"); bg.dataset.media = ""; }
        tick();
    }
    function formMarkup() {
        return `<div class="ef-pomo-layout"><section class="tool-card ef-settings"><div class="ef-section-heading"><span class="ef-section-num">01</span><h2>Ритм работы</h2><span class="ef-tag">Помодоро</span></div>
          <div class="ef-grid-two"><label>Работа, минут<input type="number" name="workMinutes" min="0.0166666667" max="1440" step="any"></label><label>Отдых, минут<input type="number" name="restMinutes" min="0.0166666667" max="1440" step="any"></label></div>
          <div class="ef-grid-two"><label>Вся сессия, минут<input type="number" name="totalMinutes" min="0.0166666667" max="10080" step="any"></label><label class="ef-check"><input type="checkbox" name="infinite">Без ограничения времени</label></div>
          <p class="ef-hint">Длительности применяются при новом запуске. Сеанс может закончиться посреди работы или отдыха.</p>
          <div class="ef-divider"></div><div class="ef-grid-two"><label>Оформление<select name="display"><option value="ring">Истекающее кольцо</option><option value="bar">Убывающая полоса</option></select></label><label class="ef-check"><input type="checkbox" name="showDigits">Показывать цифры</label></div>
          <details class="ef-details" open><summary>Музыка и фон</summary><div class="ef-grid-two"><label>Музыка для работы<select name="workAudio" data-media-kind="audio"></select></label><label>Музыка для отдыха<select name="restAudio" data-media-kind="audio"></select></label><label>Фон работы<select name="workBackground" data-media-kind="background"></select></label><label>Фон отдыха<select name="restBackground" data-media-kind="background"></select></label></div>
            <label class="ef-volume">Громкость <output id="ef-volume-label">50%</output><input type="range" name="volume" min="0" max="1" step="0.01"></label>
            <div class="ef-grid-two"><label class="ef-check"><input type="checkbox" id="ef-fade-in-enabled">Плавное начало</label><label class="ef-check"><input type="checkbox" id="ef-fade-out-enabled">Плавный конец</label><label>Нарастание, секунд<input type="number" name="fadeInSeconds" min="0" max="60" step="1"></label><label>Затухание, секунд<input type="number" name="fadeOutSeconds" min="0" max="60" step="1"></label></div>
            <div class="ef-upload-row"><label class="button button--secondary ef-upload">Загрузить музыку / фон<input id="ef-upload" type="file" multiple accept="audio/*,video/mp4,video/webm,image/jpeg,image/png,image/webp,image/gif" hidden></label><span id="ef-upload-status" class="ef-hint" role="status">Файлы хранятся на сервере</span></div><div id="ef-library" class="ef-library"></div>
          </details>
          <details class="ef-details"><summary>Звуковые сигналы</summary><p class="ef-hint">Короткий звон каждую секунду в начале и конце участка. При переключении — более сильный сигнал. 0 отключает предупреждения.</p><div class="ef-grid-two"><label>Первые N секунд<input type="number" name="cueStartSeconds" min="0" max="60" step="1"></label><label>Последние N секунд<input type="number" name="cueEndSeconds" min="0" max="60" step="1"></label></div><label class="ef-volume">Громкость сигналов<input type="range" name="cueVolume" min="0" max="1" step="0.01"></label></details>
          <p class="ef-hint" id="ef-save-status" role="status">Настройки сохраняются автоматически</p>
        </section><section class="tool-card ef-preview"><div class="ef-preview-eyebrow"><span class="ef-live-dot"></span> ОДНА ЗАДАЧА ЗА РАЗ</div>${ringMarkup()}${controlsMarkup()}
          <label class="ef-seek-label">Перемотка от начала<input id="ef-seek" type="range" min="0" max="7200" step="1" value="0"></label><div class="ef-seek-exact"><label>Перейти к минуте<input id="ef-seek-minutes" type="number" min="0" step="any" value="0"></label><button id="ef-seek-go" type="button" class="button button--secondary">Перейти</button></div>
          <div class="ef-launch"><button id="ef-start-background" class="button" type="button">Запустить в фоне</button><button id="ef-start-full" class="button button--secondary" type="button">Старт в полный экран</button></div><div class="ef-view-actions"><button id="ef-full" type="button" class="button button--ghost">Полный экран ↗</button><button id="ef-popout" type="button" class="button button--ghost">Мини-окно ▣</button><button id="ef-sound" type="button" class="button button--ghost">Включить звук здесь ♪</button></div><p id="ef-audio-notice" class="ef-warning" hidden></p>
          <p class="ef-hint">Таймер продолжает идти на других страницах. Мини-окно поверх приложений доступно в браузерах с Picture-in-Picture; иначе откроется отдельное окно. Сон компьютера или ограничения браузера могут приостановить звук, но время не потеряется.</p>
        </section></div><section id="efficiency-tracker"></section>`;
    }
    function mediaOptions() {
        if (!view) return;
        view.querySelectorAll("[data-media-kind]").forEach(select => {
            const kind = select.dataset.mediaKind;
            select.innerHTML = '<option value="">Без файла</option>' + media.filter(item => kind === "audio" ? item.kind === "audio" : ["image", "video"].includes(item.kind)).map(item => `<option value="${services.escapeHtml(item.id)}">${services.escapeHtml(item.name)}</option>`).join("");
            select.value = settings()[select.name] || "";
        });
        const library = view.querySelector("#ef-library");
        library.innerHTML = media.map(item => `<div><span>${item.kind === "audio" ? "♪" : item.kind === "video" ? "▶" : "▧"} ${services.escapeHtml(item.name)}</span><small>${(item.size / 1048576).toFixed(1)} МБ</small><button type="button" data-delete-media="${services.escapeHtml(item.id)}" aria-label="Удалить ${services.escapeHtml(item.name)}">×</button></div>`).join("");
    }
    function fillSettings() {
        if (!view?.isConnected || !state) return;
        view.querySelectorAll(".ef-settings [name]").forEach(input => { if (document.activeElement === input) return; if (input.type === "checkbox") input.checked = Boolean(settings()[input.name]); else input.value = settings()[input.name] ?? ""; });
        view.querySelector('[name="totalMinutes"]').disabled = settings().infinite;
        view.querySelector("#ef-volume-label").textContent = `${Math.round(settings().volume * 100)}%`;
        view.querySelector("#ef-fade-in-enabled").checked = settings().fadeInSeconds > 0;
        view.querySelector("#ef-fade-out-enabled").checked = settings().fadeOutSeconds > 0;
    }
    function editSetting(event) {
        const input = event.target;
        if (!input.name || !input.closest(".ef-settings") || !state) return;
        const value = input.type === "checkbox" ? input.checked : ["number", "range"].includes(input.type) ? Number(input.value) : input.value || null;
        if (input.type === "number" && (!input.value || !input.checkValidity())) return;
        state.settings[input.name] = value;
        settingsDirty = true;
        fillSettings();
        clearTimeout(settingsTimer);
        settingsTimer = setTimeout(() => persistSettings().catch(() => {}), 400);
    }
    function render(container) {
        leavePage();
        container.innerHTML = `<section class="ef-page"><header class="page-header"><div><p class="kicker">ЭФФЕКТИВНОСТЬ / ПОМОДОРО</p><h1 class="page-title">В своём ритме</h1><p class="page-subtitle">Чередуй фокус и отдых. Отмечай, на что уходит день.</p></div><span class="ef-independent">Два независимых инструмента</span></header>${formMarkup()}</section>`;
        view = container.querySelector(".ef-page");
        const current = view;
        bindControls(current);
        current.addEventListener("input", editSetting);
        current.querySelector("#ef-start-background").addEventListener("click", () => control("start"));
        current.querySelector("#ef-start-full").addEventListener("click", () => { openFull(); control("start"); });
        current.querySelector("#ef-full").addEventListener("click", openFull);
        current.querySelector("#ef-popout").addEventListener("click", openMiniWindow);
        current.querySelector("#ef-sound").addEventListener("click", () => { audioNotice = ""; claimAudio(); audio.unlock(); tick(); });
        current.querySelector("#ef-seek").addEventListener("change", event => control("seek", Number(event.target.value)));
        current.querySelector("#ef-seek-go").addEventListener("click", () => {
            const field = current.querySelector("#ef-seek-minutes");
            if (field.reportValidity()) control("seek", Number(field.value) * 60);
        });
        for (const [id, name] of [["ef-fade-in-enabled", "fadeInSeconds"], ["ef-fade-out-enabled", "fadeOutSeconds"]]) current.querySelector(`#${id}`).addEventListener("change", event => {
            state.settings[name] = event.target.checked ? 3 : 0; settingsDirty = true; fillSettings(); persistSettings().catch(() => {});
        });
        current.querySelector("#ef-upload").addEventListener("change", async event => {
            const files = Array.from(event.target.files || []);
            event.target.value = "";
            for (const file of files) {
                current.querySelector("#ef-upload-status").textContent = `Загружаем ${file.name}…`;
                try {
                    const body = new FormData(); body.append("file", file);
                    await api("/media", { method: "POST", body });
                    await refreshMedia(); channel?.postMessage({ type: "media" });
                    current.querySelector("#ef-upload-status").textContent = `Загружено: ${file.name}`;
                } catch (error) { current.querySelector("#ef-upload-status").textContent = error.message; }
            }
        });
        current.addEventListener("click", async event => {
            const button = event.target.closest("[data-delete-media]");
            if (!button) return;
            const id = button.dataset.deleteMedia;
            if (["workAudio", "restAudio", "workBackground", "restBackground"].some(key => state.settings[key] === id || state.pomodoro.config?.[key] === id)) { notify("Сначала уберите файл из настроек и завершите использующий его таймер."); return; }
            try { await api(`/media/${encodeURIComponent(id)}`, { method: "DELETE" }); await refreshMedia(); channel?.postMessage({ type: "media" }); }
            catch (error) { notify(error.message); }
        });
        const cleanupTracker = window.TimeTracker.render(current.querySelector("#efficiency-tracker"));
        viewCleanup = () => cleanupTracker?.();
        ready.then(() => { if (view === current) { mediaOptions(); fillSettings(); tick(); } }).catch(error => {
            if (view === current) { current.querySelector("#ef-save-status").textContent = error.message; current.querySelectorAll("button, input, select").forEach(el => el.disabled = true); }
        });
    }
    function leavePage() { viewCleanup(); viewCleanup = () => {}; view = null; if (settingsDirty) persistSettings().catch(() => {}); document.body.classList.remove("ef-compact-body"); }
    function renderCompact(container) { leavePage(); document.body.classList.add("ef-compact-body"); container.innerHTML = ""; viewCleanup = createMini(container); tick(); }
    function init(svc) {
        if (initialized) return ready;
        initialized = true; services = svc;
        state = { revision: 0, settings: Engine.defaults(), pomodoro: { status: "idle", elapsed: 0, anchor: null, config: Engine.defaults() } };
        audio = new Engine.AudioController({ audioFactory: () => { const element = document.createElement("audio"); element.id = "efficiency-audio"; element.hidden = true; document.body.append(element); return element; }, onError: message => { audioNotice = String(message || "Нажмите «Включить звук здесь», чтобы разрешить воспроизведение."); } });
        const trackerReady = window.TimeTracker.init(services);
        ready = Promise.all([refresh(), refreshMedia(), trackerReady]).then(() => { fillSettings(); return state; });
        ready.catch(error => notify(`Эффективность: ${error.message}`));
        createMini(document.body, true);
        setInterval(tick, 200);
        setInterval(() => { maintainAudioOwner(); }, 1000);
        setInterval(() => refresh().catch(() => {}), 3000);
        channel?.addEventListener("message", event => { if (event.data?.type === "timer") refresh().catch(() => {}); if (event.data?.type === "media") refreshMedia().catch(() => {}); });
        window.addEventListener("storage", event => { if (event.key === AUDIO_LEASE) maintainAudioOwner(); });
        document.addEventListener("pointerdown", () => { audio.unlock(); }, { once: true });
        window.addEventListener("pagehide", () => { releaseAudio(); if (readLease()?.id === tabId) localStorage.removeItem(AUDIO_LEASE); });
        document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh().catch(() => {}); tick(); } });
        window.addEventListener("message", event => { if (event.origin === location.origin && event.data?.type === "efficiency-open") services.navigate("/efficiency/pomodoro"); });
        document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && full && !full.hidden) closeFull(); });
        document.addEventListener("keydown", event => { if (event.key === "Escape" && full && !full.hidden) closeFull(); });
        maintainAudioOwner();
        return ready;
    }
    return { init, render, renderCompact, leavePage };
})();
