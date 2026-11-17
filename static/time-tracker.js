"use strict";

window.TimeTracker = (() => {
    const listeners = new Set();
    let services;
    let tracker = { revision: 0, modes: [], activeSession: null };
    let initialized = false;
    let initPromise;
    let fetching = false;
    let mutation = false;
    let connectionError = "";
    let timeZone = "Europe/Moscow";
    const views = new Set();
    const miniViews = new Set();
    const clone = value => JSON.parse(JSON.stringify(value));
    function randomId() {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        if (window.crypto?.getRandomValues) return Array.from(window.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
        return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    }
    const h = value => services.escapeHtml(String(value ?? ""));
    const color = value => /^#[\da-f]{6}$/i.test(value || "") ? value : "#70a7ff";
    const visibleModes = () => tracker.modes || [];
    const modeFor = (id, modes = tracker.modes) => modes.find(mode => mode.id === id) || { id, name: "Режим", color: "#69788d" };

    function duration(seconds, compact = false) {
        const value = Math.max(0, Math.floor(Number(seconds) || 0));
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor(value % 3600 / 60);
        const remainder = value % 60;
        if (compact) return hours ? `${hours} ч ${minutes} мин` : `${minutes} мин`;
        return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
    }

    function sessionTotals(session, now = Date.now()) {
        const totals = {};
        if (!session) return totals;
        for (const segment of session.segments || []) {
            const end = segment.end ?? now;
            totals[segment.modeId] = (totals[segment.modeId] || 0) + Math.max(0, end - segment.start) / 1000;
        }
        return totals;
    }

    function dateParts(milliseconds, zone = timeZone) {
        const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(milliseconds));
        return Object.fromEntries(parts.map(part => [part.type, part.value]));
    }

    function localInput(milliseconds, zone = timeZone) {
        const p = dateParts(milliseconds, zone);
        return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
    }

    function parseLocalInput(value, zone = timeZone) {
        if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value)) throw new Error("Укажите дату и время полностью");
        const canonical = value.length === 16 ? `${value}:00` : value;
        const wall = Date.parse(`${canonical}Z`);
        if (!Number.isFinite(wall)) throw new Error("Некорректная дата");
        let result = wall;
        for (let i = 0; i < 4; i++) {
            const offset = Date.parse(`${localInput(result, zone)}Z`) - result;
            result = wall - offset;
        }
        if (localInput(result, zone) !== canonical) throw new Error("Такого местного времени нет в выбранном часовом поясе");
        return result;
    }

    function dayString(milliseconds = Date.now()) { return localInput(milliseconds).slice(0, 10); }
    function clock(milliseconds) { return new Intl.DateTimeFormat("ru-RU", { timeZone, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(milliseconds)); }
    function dateTime(milliseconds) { return new Intl.DateTimeFormat("ru-RU", { timeZone, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(milliseconds)); }
    function shortDate(iso) { return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${iso}T12:00:00Z`)); }
    function currentSegment() { return tracker.activeSession?.segments?.at(-1) || null; }
    function modePalette(session) {
        const merged = new Map((session?.modes || []).map(mode => [mode.id, mode]));
        for (const mode of tracker.modes) if (!merged.has(mode.id)) merged.set(mode.id, mode);
        return [...merged.values()];
    }

    function validateSession(session) {
        if (!Number.isFinite(session.start) || !Number.isFinite(session.end) || session.end <= session.start) throw new Error("Конец сессии должен быть позже начала");
        if (!Array.isArray(session.segments) || !session.segments.length) throw new Error("В сессии должен быть хотя бы один интервал");
        let cursor = session.start;
        const modeIds = new Set(session.modes.map(mode => mode.id));
        for (const segment of session.segments) {
            if (segment.start !== cursor || !Number.isFinite(segment.end) || segment.end < segment.start) throw new Error("Интервалы должны идти подряд, без пересечений и пробелов");
            if (!modeIds.has(segment.modeId)) throw new Error("Выберите режим для каждого интервала");
            cursor = segment.end;
        }
        if (cursor !== session.end) throw new Error("Интервалы должны покрывать всю сессию");
        return true;
    }

    function setBoundary(session, index, position) {
        if (index < 0 || index >= session.segments.length - 1) return;
        const left = session.segments[index];
        const right = session.segments[index + 1];
        const value = Math.round(Math.min(right.end, Math.max(left.start, position)));
        left.end = value;
        right.start = value;
    }

    async function api(path, options = {}) { return services.apiFetch(`/api/efficiency${path}`, options); }
    function emit() {
        for (const callback of [...listeners]) {
            try { callback(tracker); } catch (error) { console.error("Time tracker subscriber failed", error); }
        }
        for (const view of [...views]) {
            if (!view.root.isConnected) { view.cleanup(); continue; }
            updateActive(view);
        }
        for (const view of [...miniViews]) {
            if (!view.root.isConnected) { view.cleanup(); continue; }
            updateMini(view);
        }
    }

    async function refresh() {
        if (fetching || mutation) return;
        fetching = true;
        try {
            const result = await api("/tracker");
            if (!mutation && result.revision >= tracker.revision) tracker = result;
            connectionError = "";
        } catch (error) { connectionError = error.message; }
        finally { fetching = false; emit(); }
    }

    function init(context) {
        if (initialized) return initPromise;
        initialized = true;
        services = context;
        try { timeZone = localStorage.getItem("efficiency:timezone") || "Europe/Moscow"; dateParts(Date.now()); } catch (_) { timeZone = "Europe/Moscow"; }
        initPromise = refresh();
        setInterval(emit, 1000);
        setInterval(refresh, 5000);
        window.addEventListener("focus", refresh);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
        return initPromise;
    }

    async function action(name, modeId) {
        if (mutation) return tracker;
        mutation = true;
        emit();
        try {
            tracker = await api("/tracker/action", { method: "POST", body: JSON.stringify({ revision: tracker.revision, action: name, ...(modeId ? { modeId } : {}) }) });
            connectionError = "";
            if (name === "finish") for (const view of views) { loadHistory(view); loadStats(view); }
            return tracker;
        } catch (error) {
            if (error.status === 409) {
                mutation = false;
                await refresh();
            }
            services.showToast(error.message, "error");
            throw error;
        } finally { mutation = false; emit(); }
    }

    function totalsMarkup(session, now = Date.now()) {
        const totals = sessionTotals(session, now);
        const modes = modePalette(session);
        return modes.filter(mode => totals[mode.id] !== undefined || tracker.modes.some(item => item.id === mode.id)).map(mode => `<div class="tt-total"><span><i style="background:${color(mode.color)}"></i>${h(mode.name)}</span><strong data-tt-total="${h(mode.id)}">${duration(totals[mode.id])}</strong></div>`).join("");
    }

    function timelineMarkup(session, editable = false) {
        const end = session.end ?? Date.now();
        const span = Math.max(1, end - session.start);
        const modes = modePalette(session);
        const bars = session.segments.map((segment, index) => {
            const mode = modeFor(segment.modeId, modes);
            const segmentEnd = segment.end ?? end;
            const width = Math.max(0, segmentEnd - segment.start) / span * 100;
            return `<div class="tt-timeline-segment" data-tt-segment="${index}" style="width:${width}%;background:${color(mode.color)}" title="${h(mode.name)} · ${clock(segment.start)}—${clock(segmentEnd)} · ${duration((segmentEnd - segment.start) / 1000)}"><span>${h(mode.name)}</span></div>`;
        }).join("");
        const handles = editable ? session.segments.slice(0, -1).map((segment, index) => `<input class="tt-boundary" type="range" data-tt-boundary="${index}" aria-label="Граница интервалов ${index + 1} и ${index + 2}" min="${session.start}" max="${session.end}" step="1000" value="${segment.end}" title="${clock(segment.end)}">`).join("") : "";
        return `<div class="tt-timeline-track${editable ? " tt-timeline-track--editable" : ""}">${bars}${handles}</div><div class="tt-timeline-axis"><span>${dateTime(session.start)}</span><span>${dateTime(end)}</span></div>`;
    }

    function activeSignature() {
        return `${tracker.revision}:${mutation}:${tracker.activeSession?.id || ""}:${connectionError}`;
    }

    function updateActive(view) {
        const session = tracker.activeSession;
        const signature = activeSignature();
        if (!view.modesDirty && JSON.stringify(view.modeDraft) !== JSON.stringify(tracker.modes)) {
            view.modeDraft = clone(tracker.modes);
            view.modeBase = clone(tracker.modes);
            view.modeRevision = tracker.revision;
            paintModes(view);
        }
        // Activity changes must update the controls without replacing edited names/colors.
        view.root.querySelectorAll("[data-tt-remove-mode]").forEach(button => {
            const mode = view.modeDraft[Number(button.dataset.ttRemoveMode)];
            const isCurrent = mode?.id === currentSegment()?.modeId;
            button.disabled = mutation || isCurrent;
            if (isCurrent) button.title = "Сначала переключите текущий режим";
            else button.removeAttribute("title");
        });
        if (view.activeSignature !== signature) {
            view.activeSignature = signature;
            const mode = session ? modeFor(currentSegment()?.modeId, modePalette(session)) : null;
            view.active.innerHTML = `<div class="tt-live-heading"><div><p class="tt-eyebrow">${session ? "ДЕНЬ ИДЁТ" : "НОВАЯ СЕССИЯ"}</p><div class="tt-current"><span class="tt-live-dot${session ? " is-active" : ""}" style="--tt-mode:${color(mode?.color)}"></span><h3>${h(mode?.name || "Готов к новому дню")}</h3></div></div><div class="tt-session-duration" data-tt-session-duration>${session ? duration((Date.now() - session.start) / 1000) : "00:00:00"}</div></div>
              <div class="tt-mode-buttons">${visibleModes().map(item => `<button type="button" class="tt-mode-button${mode?.id === item.id ? " is-active" : ""}" data-tt-switch="${h(item.id)}" style="--tt-mode:${color(item.color)}" ${mutation || mode?.id === item.id ? "disabled" : ""}><i></i>${h(item.name)}</button>`).join("")}</div>
              <div class="tt-live-actions">${session ? `<span class="tt-muted">Начало ${dateTime(session.start)}</span><button type="button" class="button button--secondary" data-tt-action="finish" ${mutation ? "disabled" : ""}>Завершить день</button>` : `<button type="button" class="button" data-tt-action="start" ${mutation || !tracker.modes.length ? "disabled" : ""}>Начать день</button><span class="tt-muted">Или нажми на нужный режим</span>`}</div>
              ${connectionError ? `<p class="tt-error" role="status">${h(connectionError)} · Счётчики продолжают считать по последнему состоянию.</p>` : ""}
              <div class="tt-totals">${totalsMarkup(session)}</div><div data-tt-live-timeline>${session ? timelineMarkup(session) : `<p class="tt-empty">Здесь появится таймлайн дня: каждый режим — своим цветом.</p>`}</div>`;
        }
        if (session) {
            const now = Date.now();
            view.active.querySelector("[data-tt-session-duration]").textContent = duration((now - session.start) / 1000);
            const totals = sessionTotals(session, now);
            view.active.querySelectorAll("[data-tt-total]").forEach(node => node.textContent = duration(totals[node.dataset.ttTotal]));
            const timeline = view.active.querySelector("[data-tt-live-timeline]");
            if (timeline) timeline.innerHTML = timelineMarkup(session);
        }
    }

    function render(container) {
        if (!initialized) throw new Error("TimeTracker.init() must be called before render()");
        container.innerHTML = `<section class="tt-root"><header class="tt-section-heading"><div><p class="tt-eyebrow">ВРЕМЯ ПОД КОНТРОЛЕМ</p><h2>Трекер дня</h2><p>Переключай режимы. Счётчик каждого режима и таймлайн сохранят картину дня.</p></div><span class="tt-independent">Независимо от помодоро</span></header>
          <section class="tool-card tt-card" data-tt-active></section>
          <details class="tool-card tt-card tt-mode-settings"><summary>Настроить режимы</summary><p class="tt-muted">Добавляй свои занятия. Удалённые режимы сохранятся в прошлых сессиях.</p><div data-tt-modes-editor></div><div class="tt-inline"><button type="button" class="button button--secondary" data-tt-add-mode>Добавить режим</button><button type="button" class="button" data-tt-save-modes>Сохранить режимы</button></div><p class="tt-error" data-tt-modes-error role="status"></p></details>
          <div class="tt-history-stats"><section class="tool-card tt-card tt-history"><div class="tt-card-heading"><h3>История сессий</h3><button type="button" class="button button--ghost button--small" data-tt-refresh-history>Обновить</button></div><div data-tt-history><p class="tt-muted">Загружаем историю…</p></div></section>
          <section class="tool-card tt-card tt-statistics"><h3>По дням и неделям</h3><form data-tt-stats-form><div class="tt-range"><label>С<input type="date" name="from" required></label><label>По<input type="date" name="to" required></label><button class="button button--secondary" type="submit">Показать</button></div><div class="tt-inline tt-stat-controls"><label>Группировка<select name="group"><option value="daily">Дни</option><option value="weekly">Недели</option></select></label><label>Часовой пояс<select name="timezone"><option value="Europe/Moscow">Москва (UTC+3)</option><option value="UTC">UTC</option><option value="local">Часовой пояс устройства</option></select></label></div></form><div data-tt-stats><p class="tt-muted">Загружаем статистику…</p></div></section></div>
          <section class="tool-card tt-card tt-session-editor" data-tt-editor hidden></section>
        </section>`;
        const root = container.querySelector(".tt-root");
        const view = { root, active: root.querySelector("[data-tt-active]"), activeSignature: "", modeDraft: clone(tracker.modes), modeBase: clone(tracker.modes), modeRevision: tracker.revision, modesDirty: false, editor: null, editorDirty: false, historyRequest: 0, statsRequest: 0, stats: null, history: [], cleanup: () => {} };
        views.add(view);
        const form = root.querySelector("[data-tt-stats-form]");
        form.elements.from.value = dayString(Date.now() - 6 * 86400000);
        form.elements.to.value = dayString();
        form.elements.timezone.value = ["Europe/Moscow", "UTC"].includes(timeZone) ? timeZone : "local";
        paintModes(view);
        updateActive(view);
        const onClick = async event => {
            const button = event.target.closest("button");
            if (!button || !root.contains(button)) return;
            if (button.dataset.ttAction) await action(button.dataset.ttAction).catch(() => {});
            if (button.dataset.ttSwitch) await action(tracker.activeSession ? "switch" : "start", button.dataset.ttSwitch).catch(() => {});
            if (button.hasAttribute("data-tt-add-mode")) {
                readModeDraft(view);
                view.modeDraft.push({ id: `mode-${randomId()}`, name: "Новый режим", color: ["#70a7ff", "#54d49b", "#f2ba62", "#b6a2ff"][view.modeDraft.length % 4] });
                view.modesDirty = true;
                paintModes(view);
            }
            if (button.dataset.ttRemoveMode !== undefined) {
                readModeDraft(view);
                view.modeDraft.splice(Number(button.dataset.ttRemoveMode), 1);
                view.modesDirty = true;
                paintModes(view);
            }
            if (button.hasAttribute("data-tt-save-modes")) await saveModes(view);
            if (button.hasAttribute("data-tt-refresh-history")) loadHistory(view);
            if (button.dataset.ttOpenSession) await openSession(view, button.dataset.ttOpenSession);
            if (button.hasAttribute("data-tt-close-editor") && (!view.editorDirty || window.confirm("Закрыть редактор без сохранения изменений?"))) { view.editor = null; view.editorDirty = false; root.querySelector("[data-tt-editor]").hidden = true; }
            if (button.hasAttribute("data-tt-save-session")) await saveSession(view);
            if (button.dataset.ttSplit !== undefined) splitSegment(view, Number(button.dataset.ttSplit));
            if (button.dataset.ttMerge !== undefined) mergeSegment(view, Number(button.dataset.ttMerge));
        };
        const onInput = event => {
            const input = event.target;
            if (input.closest("[data-tt-modes-editor]")) view.modesDirty = true;
            if (input.dataset.ttBoundary !== undefined && view.editor) {
                view.editorDirty = true;
                setBoundary(view.editor, Number(input.dataset.ttBoundary), Number(input.value));
                input.value = view.editor.segments[Number(input.dataset.ttBoundary)].end;
                updateEditorPreview(view, false);
            }
        };
        const onChange = event => {
            const input = event.target;
            if (input.dataset.ttBoundary !== undefined) paintEditor(view);
            if (input.hasAttribute("data-tt-session-mode") && view.editor) { view.editorDirty = true; view.editor.segments[Number(input.dataset.ttSessionMode)].modeId = input.value; updateEditorPreview(view); }
            if (input.hasAttribute("data-tt-session-label") && view.editor) {
                view.editorDirty = true;
                view.editor.modes[Number(input.dataset.ttSessionLabel)].name = input.value.trim(); updateEditorPreview(view);
            }
            if (input.hasAttribute("data-tt-session-color") && view.editor) { view.editorDirty = true; view.editor.modes[Number(input.dataset.ttSessionColor)].color = input.value; updateEditorPreview(view); }
            if (input.dataset.ttTimestamp && view.editor) changeTimestamp(view, input);
            if (input.name === "group") paintStats(view);
            if (input.name === "timezone") {
                timeZone = input.value === "local" ? Intl.DateTimeFormat().resolvedOptions().timeZone : input.value;
                try { localStorage.setItem("efficiency:timezone", timeZone); } catch (_) { /* Keep the selection for this page. */ }
                view.activeSignature = ""; updateActive(view); if (view.editor) paintEditor(view); paintHistory(view); loadStats(view);
            }
        };
        const onSubmit = event => { if (event.target === form) { event.preventDefault(); loadStats(view); } };
        root.addEventListener("click", onClick); root.addEventListener("input", onInput); root.addEventListener("change", onChange); root.addEventListener("submit", onSubmit);
        view.cleanup = () => { views.delete(view); root.removeEventListener("click", onClick); root.removeEventListener("input", onInput); root.removeEventListener("change", onChange); root.removeEventListener("submit", onSubmit); };
        loadHistory(view); loadStats(view);
        // Initial network fetch may complete after this view has been mounted.
        initPromise?.then(() => { if (!root.isConnected || view.modesDirty) return; view.modeDraft = clone(tracker.modes); view.modeBase = clone(tracker.modes); view.modeRevision = tracker.revision; paintModes(view); });
        return view.cleanup;
    }

    function readModeDraft(view) {
        view.root.querySelectorAll("[data-tt-mode-row]").forEach(row => {
            const mode = view.modeDraft[Number(row.dataset.ttModeRow)];
            mode.name = row.querySelector("[data-tt-mode-name]").value.trim();
            mode.color = row.querySelector("[data-tt-mode-color]").value;
        });
    }
    function paintModes(view) {
        view.root.querySelector("[data-tt-modes-editor]").innerHTML = view.modeDraft.map((mode, i) => `<div class="tt-mode-row" data-tt-mode-row="${i}"><input type="color" data-tt-mode-color aria-label="Цвет режима ${h(mode.name)}" value="${color(mode.color)}"><input type="text" maxlength="60" data-tt-mode-name aria-label="Название режима ${i + 1}" value="${h(mode.name)}"><button type="button" class="button button--ghost" data-tt-remove-mode="${i}" ${currentSegment()?.modeId === mode.id ? 'disabled title="Сначала переключите текущий режим"' : ""}>Удалить</button></div>`).join("");
    }
    async function saveModes(view) {
        if (mutation) return;
        readModeDraft(view);
        const errorNode = view.root.querySelector("[data-tt-modes-error]");
        errorNode.textContent = "";
        if (!view.modeDraft.length || view.modeDraft.some(mode => !mode.name)) { errorNode.textContent = "Добавьте хотя бы один режим и заполните названия."; return; }
        if (JSON.stringify(view.modeBase) !== JSON.stringify(tracker.modes)) {
            errorNode.textContent = "Режимы изменились в другом окне. Скопируйте свои правки и откройте страницу заново, чтобы не затереть чужие изменения.";
            return;
        }
        mutation = true; emit();
        try {
            // Tracker activity may advance its revision while the mode catalogue remains unchanged.
            tracker = await api("/modes", { method: "PUT", body: JSON.stringify({ revision: tracker.revision, modes: view.modeDraft }) });
            view.modeRevision = tracker.revision; view.modeBase = clone(tracker.modes); view.modesDirty = false;
            services.showToast("Режимы сохранены");
        } catch (error) { errorNode.textContent = error.message; if (error.status === 409) { mutation = false; await refresh(); } }
        finally { mutation = false; emit(); }
    }

    async function loadHistory(view) {
        const request = ++view.historyRequest;
        try {
            const result = await api("/sessions");
            if (!view.root.isConnected || request !== view.historyRequest) return;
            view.history = result;
            paintHistory(view);
        } catch (error) { if (view.root.isConnected && request === view.historyRequest) view.root.querySelector("[data-tt-history]").innerHTML = `<p class="tt-error">${h(error.message)}</p>`; }
    }
    function paintHistory(view) {
        view.root.querySelector("[data-tt-history]").innerHTML = view.history.length ? `<div class="tt-session-list">${view.history.map(session => `<button type="button" class="tt-session-link" data-tt-open-session="${h(session.id)}"><span><strong>${dateTime(session.start)}</strong><small>до ${dateTime(session.end)}</small></span><span class="tt-session-length">${duration(session.durationSeconds, true)}<small>Открыть и изменить →</small></span></button>`).join("")}</div>` : `<p class="tt-empty">Пока нет завершённых сессий. Заверши день — он появится здесь.</p>`;
    }
    async function openSession(view, id) {
        if (view.editorDirty && !window.confirm("Открыть другую сессию? Несохранённые изменения редактора будут потеряны.")) return;
        try {
            const result = await api(`/sessions/${encodeURIComponent(id)}`);
            if (!view.root.isConnected) return;
            view.editor = clone(result);
            view.editorDirty = false;
            for (const mode of tracker.modes) if (!view.editor.modes.some(item => item.id === mode.id)) view.editor.modes.push(clone(mode));
            paintEditor(view);
            view.root.querySelector("[data-tt-editor]").scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) { services.showToast(error.message, "error"); }
    }
    function paintEditor(view) {
        if (!view.editor) return;
        const session = view.editor;
        const editor = view.root.querySelector("[data-tt-editor]");
        editor.hidden = false;
        editor.innerHTML = `<div class="tt-card-heading"><div><p class="tt-eyebrow">РЕДАКТОР СЕССИИ</p><h3>${dateTime(session.start)}</h3></div><button type="button" class="button button--ghost" data-tt-close-editor>Закрыть</button></div><p class="tt-muted">Перетаскивай границы на таймлайне или укажи точное время. Часовой пояс: ${h(timeZone)}. Изменения сохранятся после нажатия кнопки.</p><div data-tt-editor-timeline>${timelineMarkup(session, true)}</div><div class="tt-totals" data-tt-editor-totals>${totalsMarkup(session)}</div><div class="tt-session-bounds"><label>Начало сессии<input type="datetime-local" step="1" data-tt-timestamp="session-start" value="${localInput(session.start)}"></label><label>Конец сессии<input type="datetime-local" step="1" data-tt-timestamp="session-end" value="${localInput(session.end)}"></label></div><div class="tt-intervals">${session.segments.map((segment, i) => `<div class="tt-interval-row"><label>Режим<select data-tt-session-mode="${i}">${session.modes.map(mode => `<option value="${h(mode.id)}" ${mode.id === segment.modeId ? "selected" : ""}>${h(mode.name)}</option>`).join("")}</select></label><label>С<input type="datetime-local" step="1" data-tt-timestamp="start" data-tt-index="${i}" value="${localInput(segment.start)}"></label><label>До<input type="datetime-local" step="1" data-tt-timestamp="end" data-tt-index="${i}" value="${localInput(segment.end)}"></label><span class="tt-interval-duration">${duration((segment.end - segment.start) / 1000)}</span><div class="tt-interval-actions"><button type="button" class="button button--ghost button--small" data-tt-split="${i}" ${segment.end - segment.start < 2000 ? "disabled" : ""}>Разделить</button>${session.segments.length > 1 ? `<button type="button" class="button button--ghost button--small" data-tt-merge="${i}">Объединить ${i ? "с предыдущим" : "со следующим"}</button>` : ""}</div></div>`).join("")}</div><details class="tt-session-modes"><summary>Названия и цвета режимов в этой сессии</summary>${session.modes.map((mode, i) => `<div class="tt-mode-row"><input type="color" data-tt-session-color="${i}" aria-label="Цвет ${h(mode.name)}" value="${color(mode.color)}"><input type="text" maxlength="60" data-tt-session-label="${i}" aria-label="Название режима в сессии" value="${h(mode.name)}"></div>`).join("")}</details><p class="tt-error" data-tt-editor-error role="status"></p><div class="tt-inline"><button type="button" class="button" data-tt-save-session>Сохранить изменения</button><span class="tt-muted">Итоги и статистика пересчитаются автоматически</span></div>`;
    }
    function updateEditorPreview(view, replaceTimeline = true) {
        if (!view.editor) return;
        const editor = view.root.querySelector("[data-tt-editor]");
        editor.querySelector("[data-tt-editor-totals]").innerHTML = totalsMarkup(view.editor);
        if (replaceTimeline) editor.querySelector("[data-tt-editor-timeline]").innerHTML = timelineMarkup(view.editor, true);
        else {
            const span = view.editor.end - view.editor.start;
            editor.querySelectorAll("[data-tt-segment]").forEach(node => {
                const segment = view.editor.segments[Number(node.dataset.ttSegment)];
                node.style.width = `${(segment.end - segment.start) / span * 100}%`;
                node.title = `${clock(segment.start)}—${clock(segment.end)} · ${duration((segment.end - segment.start) / 1000)}`;
            });
        }
    }
    function changeTimestamp(view, input) {
        const before = clone(view.editor);
        try {
            const value = parseLocalInput(input.value);
            const session = view.editor;
            const index = Number(input.dataset.ttIndex);
            switch (input.dataset.ttTimestamp) {
                case "session-start": session.start = value; session.segments[0].start = value; break;
                case "session-end": session.end = value; session.segments.at(-1).end = value; break;
                case "start":
                    session.segments[index].start = value;
                    if (index) session.segments[index - 1].end = value; else session.start = value;
                    break;
                case "end":
                    session.segments[index].end = value;
                    if (index < session.segments.length - 1) session.segments[index + 1].start = value; else session.end = value;
                    break;
            }
            validateSession(session);
            view.editorDirty = true;
            paintEditor(view);
        } catch (error) {
            view.editor = before;
            const index = Number(input.dataset.ttIndex);
            const original = input.dataset.ttTimestamp === "session-start" ? before.start : input.dataset.ttTimestamp === "session-end" ? before.end : before.segments[index][input.dataset.ttTimestamp];
            input.value = localInput(original);
            view.root.querySelector("[data-tt-editor-error]").textContent = error.message;
        }
    }
    function splitSegment(view, index) {
        const segment = view.editor?.segments[index];
        if (!segment || segment.end - segment.start < 2000) return;
        const middle = Math.floor((segment.start + segment.end) / 2 / 1000) * 1000;
        const tail = { ...segment, id: randomId(), start: middle };
        segment.end = middle;
        view.editorDirty = true;
        view.editor.segments.splice(index + 1, 0, tail);
        paintEditor(view);
    }
    function mergeSegment(view, index) {
        const session = view.editor;
        if (!session || session.segments.length < 2) return;
        const segment = session.segments[index];
        if (index) session.segments[index - 1].end = segment.end;
        else session.segments[1].start = segment.start;
        session.segments.splice(index, 1);
        view.editorDirty = true;
        paintEditor(view);
    }
    async function saveSession(view) {
        const editor = view.root.querySelector("[data-tt-editor]");
        const button = editor.querySelector("[data-tt-save-session]");
        const errors = editor.querySelector("[data-tt-editor-error]");
        errors.textContent = "";
        try {
            validateSession(view.editor);
            if (view.editor.modes.some(mode => !mode.name.trim())) throw new Error("Названия режимов не должны быть пустыми");
            button.disabled = true;
            const session = view.editor;
            const result = await api(`/sessions/${encodeURIComponent(session.id)}`, { method: "PUT", body: JSON.stringify({ revision: session.revision, start: session.start, end: session.end, segments: session.segments, modes: session.modes }) });
            view.editor = clone(result);
            view.editorDirty = false;
            paintEditor(view);
            services.showToast("Сессия сохранена, статистика пересчитана");
            loadHistory(view); loadStats(view);
        } catch (error) { errors.textContent = error.status === 409 ? `${error.message} Закройте редактор и откройте сессию заново, чтобы получить актуальные данные.` : error.message; }
        finally { button.disabled = false; }
    }

    async function loadStats(view) {
        const form = view.root.querySelector("[data-tt-stats-form]");
        const from = form.elements.from.value;
        const to = form.elements.to.value;
        if (!from || !to || from > to) { view.root.querySelector("[data-tt-stats]").innerHTML = `<p class="tt-error">Выберите корректный диапазон дат.</p>`; return; }
        const request = ++view.statsRequest;
        view.root.querySelector("[data-tt-stats]").innerHTML = `<p class="tt-muted">Считаем статистику…</p>`;
        try {
            const result = await api(`/stats?${new URLSearchParams({ from, to, timezone: timeZone })}`);
            if (!view.root.isConnected || request !== view.statsRequest) return;
            view.stats = result; paintStats(view);
        } catch (error) { if (view.root.isConnected && request === view.statsRequest) view.root.querySelector("[data-tt-stats]").innerHTML = `<p class="tt-error">${h(error.message)}</p>`; }
    }
    function paintStats(view) {
        if (!view.stats) return;
        const stats = view.stats;
        const group = view.root.querySelector("[data-tt-stats-form]").elements.group.value;
        const rows = stats[group] || [];
        const modes = stats.modes || [];
        const maximum = Math.max(1, ...rows.map(row => row.totalSeconds));
        const total = stats.totalSeconds || 0;
        view.root.querySelector("[data-tt-stats]").innerHTML = `<div class="tt-stat-total"><span>Всего за период</span><strong>${duration(total, true)}</strong></div><div class="tt-stat-legend">${modes.filter(mode => stats.totals[mode.id]).map(mode => `<span><i style="background:${color(mode.color)}"></i>${h(mode.name)} <strong>${duration(stats.totals[mode.id], true)}</strong></span>`).join("")}</div><div class="tt-chart">${rows.map(row => `<div class="tt-chart-row"><span class="tt-chart-date">${group === "daily" ? shortDate(row.date) : `${shortDate(row.startDate)} — ${shortDate(row.endDate)}`}</span><div class="tt-chart-track">${modes.filter(mode => row.totals[mode.id] > 0).map(mode => `<span style="width:${row.totals[mode.id] / maximum * 100}%;background:${color(mode.color)}" title="${h(mode.name)}: ${duration(row.totals[mode.id])}"></span>`).join("")}</div><strong>${duration(row.totalSeconds, true)}</strong><div class="tt-chart-detail">${modes.filter(mode => row.totals[mode.id] > 0).map(mode => `${h(mode.name)}: ${duration(row.totals[mode.id], true)}`).join(" · ") || "Нет записей"}</div></div>`).join("")}</div><p class="tt-muted">Границы суток: ${h(stats.timezone)}. Неделя начинается в понедельник. Сессии через полночь распределены по соответствующим дням.</p>`;
    }

    function renderMini(container) {
        const view = { root: container, signature: "", cleanup: () => {} };
        container.classList.add("tt-mini");
        const click = event => {
            const button = event.target.closest("[data-tt-mini-mode]");
            if (button && container.contains(button)) action(tracker.activeSession ? "switch" : "start", button.dataset.ttMiniMode).catch(() => {});
        };
        container.addEventListener("click", click);
        view.cleanup = () => { miniViews.delete(view); container.removeEventListener("click", click); };
        miniViews.add(view); updateMini(view);
        return view.cleanup;
    }
    function updateMini(view) {
        const session = tracker.activeSession;
        const segment = currentSegment();
        const mode = session ? modeFor(segment?.modeId, modePalette(session)) : null;
        const signature = `${tracker.revision}:${mutation}`;
        if (view.signature !== signature) {
            view.signature = signature;
            // Patch existing controls when possible so keyboard focus remains stable while ticking.
            const ids = tracker.modes.map(item => item.id).join("|");
            if (view.root.dataset.ttModes !== ids || !view.root.querySelector("[data-tt-mini-time]")) {
                view.root.dataset.ttModes = ids;
                view.root.innerHTML = `<div class="tt-mini-heading"><span><i data-tt-mini-dot></i><strong data-tt-mini-label>Трекер дня</strong></span><time data-tt-mini-time>00:00:00</time></div><div class="tt-mini-buttons">${tracker.modes.map(item => `<button type="button" data-tt-mini-mode="${h(item.id)}" style="--tt-mode:${color(item.color)}">${h(item.name)}</button>`).join("")}</div><a href="/efficiency/pomodoro" data-link class="tt-mini-return">Открыть трекер ↗</a>`;
            }
            view.root.querySelector("[data-tt-mini-label]").textContent = mode?.name || "Трекер дня";
            view.root.querySelector("[data-tt-mini-dot]").style.background = color(mode?.color);
            view.root.querySelectorAll("[data-tt-mini-mode]").forEach(button => {
                const item = modeFor(button.dataset.ttMiniMode);
                button.textContent = item.name;
                button.style.setProperty("--tt-mode", color(item.color));
                button.classList.toggle("is-active", mode?.id === item.id);
                button.disabled = mutation || mode?.id === item.id;
            });
        }
        const node = view.root.querySelector("[data-tt-mini-time]");
        if (node) node.textContent = duration(segment ? (Date.now() - segment.start) / 1000 : 0);
    }

    return {
        init, render, renderMini, action, refresh,
        getState: () => tracker,
        subscribe(callback) { listeners.add(callback); callback(tracker); return () => listeners.delete(callback); },
        utils: { duration, sessionTotals, localInput, parseLocalInput, validateSession, setBoundary },
    };
})();
