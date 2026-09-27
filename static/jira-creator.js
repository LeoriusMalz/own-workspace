"use strict";

window.JiraCreator = (() => {
    const DRAFT_KEY = "dev-toolbox:jira-draft";
    const CREATION_KEY = "dev-toolbox:jira-creation";
    let cleanup = () => {};
    let notifyCreation = () => {};
    let creation = loadCreation();

    function loadCreation() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(CREATION_KEY));
            if (saved && typeof saved.signature === "string") {
                if (saved.pending) return { ...saved, pending: false, error: { message: "Страница была закрыта во время создания. Проверьте Jira: задача могла создаться.", uncertain: true } };
                if (!saved.result || /^https:\/\//.test(saved.result.url)) return saved;
            }
        } catch (_) { /* A new tab starts with no previous creation. */ }
        return { pending: false, result: null, error: null, signature: "" };
    }

    function saveCreation() {
        try { sessionStorage.setItem(CREATION_KEY, JSON.stringify(creation)); } catch (_) { /* In-memory protection still applies. */ }
    }

    function today() {
        const date = new Date();
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    }

    function emptyDraft() {
        return { project: "UCP", issueType: "", subproject: "", kind: "", title: "", description: "", plannedStart: today(), plannedEnd: "", epic: "", epicLabel: "", extraFields: {}, experiment: { enabled: false, audience: "20", count: 4, layer: "", groups: ["контроль", "", "", ""] } };
    }

    function loadDraft() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY));
            if (saved && typeof saved === "object" && Array.isArray(saved.experiment?.groups)) return saved;
        } catch (_) { /* A missing or old draft should not prevent opening the form. */ }
        return emptyDraft();
    }

    function render(container, services) {
        cleanup();
        const h = services.escapeHtml;
        const draft = loadDraft();
        let options = { subprojects: [], kinds: [] };
        let meta = null;
        let alive = true;
        let previewTimer;
        let epicTimer;
        let previewRevision = 0;
        let epicRevision = 0;
        let metadataRevision = 0;
        const controllers = new Set();
        container.innerHTML = `
          <section class="jira-creator">
            <header class="page-header"><div><p class="kicker">WORKSPACE / JIRA</p><h1 class="page-title">Jira Creator</h1><p class="page-subtitle">Собери задачу из привычных деталей. Название, эксперимент и описание — в одной форме.</p></div><button type="button" class="button button--secondary" id="jira-token">Jira-токен</button></header>
            <div class="jira-layout"><form id="jira-form" class="jira-form">
              <fieldset id="jira-fields">
                <section class="tool-card jira-card"><div class="jira-section-head"><span class="jira-step">01</span><h2>Задача</h2></div>
                  <div class="jira-two"><label class="jira-field">Проект Jira<span class="jira-inline"><input id="jira-project" required maxlength="50" value="${h(draft.project)}" aria-label="Проект Jira"><button type="button" id="jira-connect" class="button button--secondary">Подключить</button></span></label><label class="jira-field">Тип задачи в Jira<select id="jira-type" disabled><option>Подключите Jira</option></select></label></div>
                  <p id="jira-connection" class="jira-hint" role="status">Загружаем параметры проекта…</p>
                  <div class="jira-two">
                    <div><label class="jira-field">Подпроект <span class="jira-optional">необязательно</span><select id="jira-subproject"><option value="">Без подпроекта</option></select></label>${optionEditor("subprojects", "Подпроект")}</div>
                    <div><label class="jira-field">Вид задачи <span class="jira-optional">необязательно</span><select id="jira-kind"><option value="">Без вида задачи</option></select></label>${optionEditor("kinds", "Вид задачи")}</div>
                  </div>
                  <label class="jira-field">Название задачи <span class="jira-required">*</span><input id="jira-title" required maxlength="255" placeholder="Greenline на мягких подписках" value="${h(draft.title)}"></label>
                  <div class="jira-assignee"><span class="jira-avatar">ЛМ</span><div><strong>Мальцев Лев</strong><span>Исполнитель · lev.maltsev@vkteam.ru</span></div><span class="jira-fixed">Всегда ты</span></div>
                  <div class="jira-two"><label class="jira-field">Planned Start <span class="jira-optional">необязательно</span><input type="date" id="jira-start" value="${h(draft.plannedStart)}"></label><label class="jira-field">Planned End <span class="jira-optional">необязательно</span><input type="date" id="jira-end" value="${h(draft.plannedEnd)}"></label></div>
                  <label class="jira-field">Epic Link <span class="jira-optional">необязательно</span><input id="jira-epic-search" autocomplete="off" placeholder="Найти по названию или ключу UCP-…" aria-controls="jira-epics"></label>
                  <div id="jira-epic-selected" class="jira-epic-selected" hidden></div><div id="jira-epics" class="jira-epics" aria-live="polite"></div>
                  <div id="jira-extra"></div>
                </section>
                <section class="tool-card jira-card"><div class="jira-section-head"><span class="jira-step">02</span><h2>Описание</h2><label class="jira-toggle"><input type="checkbox" id="jira-experiment" ${draft.experiment.enabled ? "checked" : ""}> Это эксперимент</label></div>
                  <div id="jira-experiment-fields" ${draft.experiment.enabled ? "" : "hidden"}>
                    <div class="jira-two"><label class="jira-field">Аудитория, %<input id="jira-audience" type="number" min="0.01" max="100" step="0.01" value="${h(draft.experiment.audience)}"></label><label class="jira-field">Количество групп<input id="jira-count" type="number" min="1" max="26" step="1" value="${h(draft.experiment.count)}"></label></div>
                    <label class="jira-field">Слой эксперимента<input id="jira-layer" maxlength="200" placeholder="Название слоя" value="${h(draft.experiment.layer)}"></label><div id="jira-groups" class="jira-groups"></div>
                  </div>
                  <label class="jira-field"><span id="jira-description-label">Описание задачи</span><textarea id="jira-description" rows="7" maxlength="30000" placeholder="Контекст, что нужно сделать и ожидаемый результат…">${h(draft.description)}</textarea></label>
                  <p class="jira-hint">В режиме эксперимента этот текст будет добавлен после блока групп и разделителя.</p>
                </section>
              </fieldset>
              <div class="jira-submit"><button class="button" id="jira-create" type="submit" disabled>Создать задачу</button><span class="jira-hint">Черновик сохраняется в этой вкладке</span></div>
              <div id="jira-status" class="jira-status" role="status" aria-live="polite"></div>
            </form>
            <aside class="tool-card jira-preview"><p class="kicker">ПРЕДПРОСМОТР</p><h2>Так будет в Jira</h2><div id="jira-preview-summary" class="jira-preview-summary">Название задачи</div><div class="jira-preview-meta" id="jira-preview-meta"></div><pre id="jira-preview-description">Описание необязательно</pre><p id="jira-preview-error" class="jira-error" role="status"></p></aside></div>
          </section>`;
        const root = container.querySelector(".jira-creator");
        const $ = (id) => root.querySelector(`#jira-${id}`);

        function optionEditor(category, label) {
            return `<details class="jira-option-editor"><summary>Настроить варианты</summary><div class="jira-inline"><input data-new="${category}" maxlength="80" aria-label="Новый вариант: ${label}" placeholder="Новый вариант"><button type="button" class="button button--secondary" data-add="${category}">Добавить</button></div><div data-options="${category}" class="jira-option-list"></div></details>`;
        }
        function save() { try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch (_) { /* Form remains usable without storage. */ } }
        function payload() { return { ...draft, experiment: { ...draft.experiment, groups: draft.experiment.groups.slice(0, draft.experiment.count) } }; }
        function signature() { return JSON.stringify(payload()); }
        async function api(path, init = {}) {
            const controller = new AbortController();
            controllers.add(controller);
            try {
                return await services.apiFetch(`/api/jira${path}`, { ...init, headers: { "X-Jira-Token": localStorage.getItem(services.tokenKey) || "", ...init.headers }, signal: controller.signal });
            } finally { controllers.delete(controller); }
        }
        function errorText(element, message) { element.textContent = message; element.classList.add("jira-error"); }
        function changed() {
            save();
            previewRevision++;
            clearTimeout(previewTimer);
            $("preview-summary").textContent = [draft.subproject ? `[${draft.subproject}]` : "", draft.kind, draft.title].filter(Boolean).join(" | ") || "Название задачи";
            $("preview-meta").textContent = `${draft.project} · Мальцев Лев${draft.plannedStart ? ` · с ${draft.plannedStart}` : ""}${draft.plannedEnd ? ` → ${draft.plannedEnd}` : ""}`;
            previewTimer = setTimeout(preview, 200);
            updateCreation();
        }
        async function preview() {
            const revision = previewRevision;
            try {
                const result = await api("/preview", { method: "POST", body: JSON.stringify({ ...payload(), title: draft.title || "Название задачи" }) });
                if (!alive || revision !== previewRevision) return;
                $("preview-summary").textContent = result.summary;
                $("preview-description").textContent = result.description || "Описание необязательно";
                $("preview-error").textContent = "";
            } catch (error) {
                if (!alive || revision !== previewRevision || error.name === "AbortError") return;
                $("preview-error").textContent = error.message;
                $("preview-description").textContent = "Заполните параметры, чтобы увидеть итоговое описание.";
            }
        }
        function paintOptions() {
            for (const [category, field, id] of [["subprojects", "subproject", "subproject"], ["kinds", "kind", "kind"]]) {
                const values = [...options[category]];
                if (draft[field] && !values.includes(draft[field])) values.push(draft[field]);
                $(id).innerHTML = `<option value="">${field === "kind" ? "Без вида задачи" : "Без подпроекта"}</option>` + values.map(value => `<option value="${h(value)}">${h(value)}</option>`).join("");
                $(id).value = draft[field];
                root.querySelector(`[data-options="${category}"]`).innerHTML = options[category].map(value => `<span class="jira-option-chip">${h(value)}<button type="button" data-remove="${category}" data-value="${h(value)}" aria-label="Удалить вариант ${h(value)}">×</button></span>`).join("");
            }
        }
        function paintGroups() {
            $("groups").innerHTML = Array.from({ length: draft.experiment.count }, (_, i) => `<label class="jira-group"><span>${String.fromCharCode(65 + i)}</span><input data-group="${i}" maxlength="2000" aria-label="Описание группы ${String.fromCharCode(65 + i)}" placeholder="Описание группы" value="${h(draft.experiment.groups[i] || "")}"></label>`).join("");
        }
        function paintExperiment() {
            $("experiment-fields").hidden = !draft.experiment.enabled;
            for (const id of ["audience", "count", "layer"]) {
                $(id).required = draft.experiment.enabled;
                $(id).disabled = !draft.experiment.enabled;
            }
            $("description-label").textContent = draft.experiment.enabled ? "Описание после блока эксперимента" : "Описание задачи";
        }
        function paintEpic() {
            $("epic-selected").hidden = !draft.epic;
            $("epic-selected").innerHTML = draft.epic ? `<span>${h(draft.epicLabel || draft.epic)}</span><button type="button" id="jira-clear-epic" class="button button--ghost">Убрать</button>` : "";
        }
        async function findEpics() {
            const revision = ++epicRevision;
            $("epics").textContent = "Ищем эпики…";
            $("epics").classList.remove("jira-error");
            try {
                const result = await api(`/epics?project=${encodeURIComponent(draft.project)}&q=${encodeURIComponent($("epic-search").value)}`);
                if (!alive || revision !== epicRevision) return;
                $("epics").innerHTML = result.length ? result.map(epic => `<button type="button" class="jira-epic-result" data-epic="${h(epic.key)}" data-summary="${h(epic.summary)}"><strong>${h(epic.key)}</strong><span>${h(epic.summary)}</span></button>`).join("") : `<p class="jira-hint">Эпики не найдены</p>`;
            } catch (error) {
                if (alive && revision === epicRevision && error.name !== "AbortError") errorText($("epics"), error.message);
            }
        }
        function paintExtra() {
            $("extra").innerHTML = meta.requiredFields.length ? `<h3>Обязательные поля проекта</h3>` + meta.requiredFields.map(field => {
                const choices = field.allowedValues || [];
                const type = field.schema?.type;
                let control;
                if (choices.length) control = `<select data-extra="${h(field.id)}" ${type === "array" ? "multiple" : ""} required><option value="">Выберите…</option>${choices.map(option => `<option value="${h(String(option.id ?? option.value ?? option.name))}">${h(option.value ?? option.name ?? option.id)}</option>`).join("")}</select>`;
                else if (["number", "string", "date", "datetime"].includes(type)) control = `<input data-extra="${h(field.id)}" type="${type === "number" ? "number" : type === "date" ? "date" : "text"}" ${type === "number" ? 'step="any"' : ""} required>`;
                else control = `<span class="jira-error">Поле пока не поддерживается конструктором.</span>`;
                return `<label class="jira-field">${h(field.name)} *${control}</label>`;
            }).join("") : "";
            root.querySelectorAll("[data-extra]").forEach(input => {
                const value = draft.extraFields[input.dataset.extra];
                if (input.multiple) Array.from(input.options).forEach(o => o.selected = (value || []).includes(o.value));
                else input.value = value ?? "";
            });
        }
        async function connect() {
            const revision = ++metadataRevision;
            meta = null;
            updateCreation();
            $("type").disabled = true;
            $("connection").classList.remove("jira-error");
            $("connection").textContent = "Проверяем проект и доступные поля…";
            try {
                const result = await api(`/metadata?project=${encodeURIComponent(draft.project)}&issueType=${encodeURIComponent(draft.issueType)}`);
                if (!alive || revision !== metadataRevision) return;
                meta = result;
                draft.issueType = meta.issueType;
                $("type").innerHTML = meta.issueTypes.map(type => `<option value="${h(type.id)}">${h(type.name)}</option>`).join("");
                $("type").value = meta.issueType;
                $("type").disabled = false;
                $("connection").textContent = `Подключено к ${meta.project}. Исполнитель — Мальцев Лев.`;
                paintExtra();
                const unsupported = [["plannedStart", "start"], ["plannedEnd", "end"], ["epic", "epic-search"]].filter(([key]) => !meta.mapping[key]);
                if (unsupported.length) $("connection").textContent += ` Недоступные поля: ${unsupported.map(([key]) => ({ plannedStart: "Planned Start", plannedEnd: "Planned End", epic: "Epic Link" })[key]).join(", ")}. Очистите их перед созданием.`;
                changed();
            } catch (error) {
                if (alive && revision === metadataRevision && error.name !== "AbortError") errorText($("connection"), error.message);
            }
        }
        function updateCreation() {
            saveCreation();
            if (!alive) return;
            $("fields").disabled = creation.pending;
            const same = creation.signature === signature();
            $("create").disabled = creation.pending || !meta || (same && Boolean(creation.result || creation.error?.uncertain));
            $("create").textContent = creation.pending ? "Создаём задачу…" : "Создать задачу";
            $("status").classList.toggle("jira-error", Boolean(creation.error));
            if (creation.pending) $("status").textContent = "Отправляем задачу в Jira…";
            else if (creation.result) $("status").innerHTML = `<div class="jira-success">Задача создана: <a href="${h(creation.result.url)}" target="_blank" rel="noopener noreferrer">${h(creation.result.key)} ↗</a></div><button class="button button--secondary" type="button" id="jira-new">Новая задача</button>`;
            else if (creation.error) {
                $("status").textContent = creation.error.message;
                if (creation.error.uncertain) $("status").insertAdjacentHTML("beforeend", `<p><button type="button" id="jira-allow-retry" class="button button--secondary">Проверил Jira — задача не создалась</button></p>`);
            }
            else $("status").textContent = "";
        }
        notifyCreation = updateCreation;
        async function submit(event) {
            event.preventDefault();
            if (creation.pending || !meta || $("create").disabled) return;
            const body = payload();
            creation = { pending: true, result: null, error: null, signature: signature() };
            updateCreation();
            try {
                // Creation is not aborted on route changes; never retry automatically.
                creation.result = await services.apiFetch("/api/jira/issues", { method: "POST", headers: { "X-Jira-Token": localStorage.getItem(services.tokenKey) || "" }, body: JSON.stringify(body) });
            } catch (error) {
                creation.error = { message: error.message, uncertain: error.payload?.uncertain ?? !error.status };
                if (creation.error.uncertain && !error.payload?.uncertain) creation.error.message = "Ответ не получен. Задача могла создаться — проверьте Jira перед повторной отправкой.";
            } finally {
                creation.pending = false;
                saveCreation();
                notifyCreation();
            }
        }
        const fields = { title: "title", description: "description", subproject: "subproject", kind: "kind", start: "plannedStart", end: "plannedEnd" };
        for (const [id, key] of Object.entries(fields)) $(id).addEventListener("input", () => { draft[key] = $(id).value; changed(); });
        $("project").addEventListener("input", () => {
            draft.project = $("project").value.trim().toUpperCase();
            draft.issueType = "";
            draft.extraFields = {};
            draft.epic = "";
            meta = null;
            metadataRevision++;
            epicRevision++;
            $("epics").textContent = "";
            $("type").disabled = true;
            $("connection").textContent = "Нажмите «Подключить», чтобы загрузить поля проекта.";
            paintEpic(); changed();
        });
        $("type").addEventListener("change", () => { draft.issueType = $("type").value; draft.extraFields = {}; connect(); });
        $("connect").addEventListener("click", connect);
        $("token").addEventListener("click", () => services.showTokenModal("jira"));
        $("experiment").addEventListener("change", () => { draft.experiment.enabled = $("experiment").checked; paintExperiment(); changed(); });
        for (const key of ["audience", "layer"]) $(key).addEventListener("input", () => { draft.experiment[key] = $(key).value; changed(); });
        $("count").addEventListener("input", () => {
            const count = Number($("count").value);
            if (!Number.isInteger(count) || count < 1 || count > 26) return;
            draft.experiment.count = count;
            while (draft.experiment.groups.length < count) draft.experiment.groups.push("");
            paintGroups(); changed();
        });
        $("groups").addEventListener("input", event => { if (event.target.dataset.group !== undefined) { draft.experiment.groups[Number(event.target.dataset.group)] = event.target.value; changed(); } });
        $("extra").addEventListener("input", event => {
            const input = event.target;
            if (!input.dataset.extra) return;
            draft.extraFields[input.dataset.extra] = input.multiple ? Array.from(input.selectedOptions).map(o => o.value).filter(Boolean) : input.type === "number" ? Number(input.value) : input.value;
            changed();
        });
        $("epic-search").addEventListener("input", () => { epicRevision++; clearTimeout(epicTimer); epicTimer = setTimeout(findEpics, 300); });
        $("epic-search").addEventListener("focus", () => { if (!$("epic-search").value && !draft.epic) findEpics(); });
        root.addEventListener("click", async event => {
            const button = event.target.closest("button");
            if (!button) return;
            if (button.dataset.epic) {
                draft.epic = button.dataset.epic;
                draft.epicLabel = `${button.dataset.epic} · ${button.dataset.summary}`;
                $("epics").textContent = "";
                $("epic-search").value = "";
                epicRevision++;
                paintEpic(); changed();
            }
            if (button.id === "jira-clear-epic") { draft.epic = ""; draft.epicLabel = ""; paintEpic(); changed(); }
            if (button.id === "jira-allow-retry") { creation.error = null; updateCreation(); }
            if (button.id === "jira-new") {
                creation = { pending: false, result: null, error: null, signature: "" };
                draft.title = ""; draft.description = ""; draft.plannedStart = today(); draft.plannedEnd = "";
                $("title").value = ""; $("description").value = ""; $("start").value = draft.plannedStart; $("end").value = "";
                changed(); $("title").focus();
            }
            if (button.dataset.add || button.dataset.remove) {
                const category = button.dataset.add || button.dataset.remove;
                const input = root.querySelector(`[data-new="${category}"]`);
                const value = button.dataset.remove ? button.dataset.value : input.value.trim();
                if (!value) return;
                button.disabled = true;
                try {
                    options = await api("/options", { method: button.dataset.remove ? "DELETE" : "POST", body: JSON.stringify({ category, value }) });
                    if (!alive) return;
                    const field = category === "subprojects" ? "subproject" : "kind";
                    if (button.dataset.add) { draft[field] = options[category].find(item => item.toLocaleLowerCase() === value.toLocaleLowerCase()) || value; input.value = ""; }
                    else if (draft[field] === value) draft[field] = "";
                    paintOptions(); changed();
                } catch (error) { if (alive && error.name !== "AbortError") services.showToast(error.message, "error"); }
                finally { button.disabled = false; }
            }
        });
        $("form").addEventListener("submit", submit);
        root.querySelectorAll("[data-new]").forEach(input => input.addEventListener("keydown", event => {
            if (event.key === "Enter") { event.preventDefault(); root.querySelector(`[data-add="${input.dataset.new}"]`).click(); }
        }));
        $("project").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); connect(); } });
        $("epic-search").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); clearTimeout(epicTimer); findEpics(); } });
        const tokenChanged = event => { if (event.detail === "jira") connect(); };
        window.addEventListener("workspace-token-changed", tokenChanged);
        cleanup = () => {
            alive = false;
            clearTimeout(previewTimer); clearTimeout(epicTimer);
            controllers.forEach(controller => controller.abort());
            window.removeEventListener("workspace-token-changed", tokenChanged);
            notifyCreation = () => {};
        };
        paintGroups(); paintExperiment(); paintEpic(); changed();
        api("/options").then(result => { if (alive) { options = result; paintOptions(); } }).catch(error => { if (alive && error.name !== "AbortError") services.showToast(error.message, "error"); });
        connect();
    }
    return { render, destroy: () => cleanup() };
})();
