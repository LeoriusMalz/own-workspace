"use strict";

const TOKEN_KEYS = {
    yt: "dev-toolbox:yt-token",
    git: "dev-toolbox:git-token",
};
const CLUSTER_KEY = "dev-toolbox:yt-cluster";
const AVATAR_KEY = "dev-toolbox:avatar";
const DEFAULT_AVATAR = "/assets/avatar-placeholder.svg";
const MAX_AVATAR_BYTES = 2 * 1024 * 1024;

const state = {
    notes: { drafts: [], articles: [] },
    currentNote: null,
    currentTokenKind: null,
    confirmAction: null,
    saveTimer: null,
    editorRange: null,
    isSaving: false,
    searchController: null,
};

const app = document.getElementById("app");
const toastRegion = document.getElementById("toast-region");

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatDate(value, withTime = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    }).format(date);
}

function formatRelativeTime(value) {
    if (!value) return "";
    const date = new Date(value);
    const timestamp = date.getTime();
    if (Number.isNaN(timestamp)) return "";

    const deltaSeconds = Math.round((timestamp - Date.now()) / 1000);
    const absoluteSeconds = Math.abs(deltaSeconds);
    const ranges = [
        [60, "second", 1],
        [3600, "minute", 60],
        [86400, "hour", 3600],
        [604800, "day", 86400],
        [2629800, "week", 604800],
        [31557600, "month", 2629800],
        [Infinity, "year", 31557600],
    ];
    const [, unit, divider] = ranges.find(([limit]) => absoluteSeconds < limit);
    const amount = Math.round(deltaSeconds / divider);
    return new Intl.RelativeTimeFormat("ru-RU", { numeric: "auto" }).format(amount, unit);
}

function formatDateWithRelative(value) {
    const absolute = formatDate(value, true);
    const relative = formatRelativeTime(value);
    return relative ? `${absolute} · ${relative}` : absolute;
}

function formatInteger(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return formatValue(value);
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(number);
}

function formatBytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return formatValue(value);
    const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let current = number;
    let index = 0;
    while (Math.abs(current) >= 1024 && index < units.length - 1) {
        current /= 1024;
        index += 1;
    }
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(current)} ${units[index]}`;
}

function formatValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " toast--error" : ""}`;
    toast.innerHTML = `
        <span class="toast__icon">${type === "error" ? "!" : "✓"}</span>
        <span class="toast__copy">${escapeHtml(message)}</span>
    `;
    toastRegion.append(toast);
    setTimeout(() => toast.remove(), 4200);
}

async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }
    const response = await fetch(url, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload.error || `Ошибка HTTP ${response.status}`);
        error.status = response.status;
        error.code = payload.code;
        error.payload = payload;
        throw error;
    }
    return payload.result ?? payload;
}

function navigate(path, { replace = false } = {}) {
    if (replace) history.replaceState({}, "", path);
    else history.pushState({}, "", path);
    closeDropdowns();
    renderRoute();
}

function openModal(id) {
    const element = document.getElementById(id);
    if (!element) return;
    element.hidden = false;
    const focusable = element.querySelector("input, button:not([disabled])");
    setTimeout(() => focusable?.focus(), 0);
}

function closeModal(id) {
    const element = document.getElementById(id);
    if (element) element.hidden = true;
}

function closeDropdowns() {
    document.querySelectorAll("[data-dropdown-menu]").forEach((menu) => { menu.hidden = true; });
    document.querySelectorAll("[data-dropdown-trigger]").forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function toggleDropdown(name) {
    const menu = document.querySelector(`[data-dropdown-menu="${name}"]`);
    const trigger = document.querySelector(`[data-dropdown-trigger="${name}"]`);
    const shouldOpen = menu.hidden;
    closeDropdowns();
    menu.hidden = !shouldOpen;
    trigger.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen && name === "notes") loadNotesIndex();
}

function tokenIsSet(kind) {
    return Boolean(localStorage.getItem(TOKEN_KEYS[kind]));
}

function updateTokenStatus() {
    for (const kind of ["yt", "git"]) {
        const isSet = tokenIsSet(kind);
        const badge = document.getElementById(`${kind}-token-badge`);
        const status = document.getElementById(`${kind}-setting-status`);
        badge.classList.toggle("is-set", isSet);
        badge.querySelector("strong").textContent = isSet ? "установлен" : "не установлен";
        status.textContent = isSet ? "Токен уже установлен" : "Токен не установлен";
        status.classList.toggle("is-set", isSet);
    }
}

function updateAvatar() {
    const savedAvatar = localStorage.getItem(AVATAR_KEY);
    const source = savedAvatar || DEFAULT_AVATAR;
    document.getElementById("brand-avatar").src = source;
    document.getElementById("settings-avatar-preview").src = source;
    document.getElementById("avatar-setting-status").textContent = savedAvatar ? "Фотография установлена" : "Стандартная фотография";
    document.getElementById("avatar-setting-status").classList.toggle("is-set", Boolean(savedAvatar));
    document.getElementById("remove-avatar-button").hidden = !savedAvatar;
}

function selectAvatar(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
        showToast("Выберите изображение", "error");
        return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
        showToast("Фотография должна быть меньше 2 МБ", "error");
        return;
    }

    const reader = new FileReader();
    reader.addEventListener("load", () => {
        try {
            localStorage.setItem(AVATAR_KEY, String(reader.result));
            updateAvatar();
            showToast("Фотография обновлена");
        } catch (error) {
            showToast("Не удалось сохранить фотографию в браузере", "error");
        }
    });
    reader.addEventListener("error", () => showToast("Не удалось прочитать фотографию", "error"));
    reader.readAsDataURL(file);
}

function removeAvatar() {
    localStorage.removeItem(AVATAR_KEY);
    document.getElementById("avatar-input").value = "";
    updateAvatar();
    showToast("Установлена стандартная фотография");
}

function showTokenModal(kind) {
    state.currentTokenKind = kind;
    const label = kind === "yt" ? "YT" : "Git";
    document.getElementById("token-modal-title").textContent = `Задать ${label}-токен`;
    const input = document.getElementById("token-input");
    input.value = "";
    document.getElementById("accept-token-button").disabled = true;
    document.getElementById("remove-token-button").hidden = !tokenIsSet(kind);
    openModal("token-modal");
}

function saveToken() {
    const input = document.getElementById("token-input");
    const value = input.value.trim();
    if (!value || !state.currentTokenKind) return;
    localStorage.setItem(TOKEN_KEYS[state.currentTokenKind], value);
    const label = state.currentTokenKind === "yt" ? "YT" : "Git";
    input.value = "";
    closeModal("token-modal");
    updateTokenStatus();
    showToast(`${label}-токен установлен`);
}

function removeToken() {
    if (!state.currentTokenKind) return;
    const label = state.currentTokenKind === "yt" ? "YT" : "Git";
    localStorage.removeItem(TOKEN_KEYS[state.currentTokenKind]);
    document.getElementById("token-input").value = "";
    closeModal("token-modal");
    updateTokenStatus();
    showToast(`${label}-токен удалён`);
}

function sanitizeHtml(html) {
    const template = document.createElement("template");
    template.innerHTML = String(html || "");
    const allowedTags = new Set([
        "P", "BR", "H1", "H2", "H3", "H4", "STRONG", "B", "EM", "I", "U", "S",
        "UL", "OL", "LI", "A", "IMG", "BLOCKQUOTE", "CODE", "PRE", "DETAILS", "SUMMARY",
        "HR", "SPAN", "DIV",
    ]);
    const forbidden = template.content.querySelectorAll("script,style,iframe,object,embed,form,input,button,textarea,select,link,meta");
    forbidden.forEach((element) => element.remove());

    const elements = [...template.content.querySelectorAll("*")];
    for (const element of elements) {
        if (!allowedTags.has(element.tagName)) {
            element.replaceWith(...element.childNodes);
            continue;
        }
        for (const attribute of [...element.attributes]) {
            const name = attribute.name.toLowerCase();
            if (name.startsWith("on") || !["href", "src", "alt", "title", "target", "rel"].includes(name)) {
                element.removeAttribute(attribute.name);
            }
        }
        if (element.tagName === "A") {
            const href = element.getAttribute("href") || "";
            if (!/^(https?:\/\/|mailto:|\/)/i.test(href)) element.removeAttribute("href");
            if (element.getAttribute("target") === "_blank") element.setAttribute("rel", "noopener noreferrer");
        }
        if (element.tagName === "IMG") {
            const src = element.getAttribute("src") || "";
            if (!/^(\/notes-media\/|https?:\/\/|data:image\/)/i.test(src)) element.remove();
        }
    }
    return template.innerHTML;
}

function toolPageTemplate() {
    const cluster = localStorage.getItem(CLUSTER_KEY) || "miranda";
    return `
        <section class="page-header">
            <div class="page-header__copy">
                <p class="kicker">ИНСТРУМЕНТ / YT OBSERVER</p>
                <h1 class="page-title">YT Observer</h1>
                <p class="page-subtitle">Информация о таблицах и содержимом папок YT.</p>
            </div>
        </section>

        <section class="tool-card search-panel">
            <div class="search-panel__label-row">
                <label class="field-label" for="table-path">Путь к таблице или папке</label>
                <span class="field-hint">proxy: &lt;cluster&gt;.yt.vk.team</span>
            </div>
            <form id="yt-search-form" class="search-controls">
                <select id="yt-cluster" class="cluster-select" aria-label="YT-кластер">
                    ${["jupiter", "saturn", "miranda"].map((name) => `<option value="${name}" ${cluster === name ? "selected" : ""}>${name}</option>`).join("")}
                </select>
                <input id="table-path" class="path-input" type="text" autocomplete="off" spellcheck="false" placeholder="//home/video/promo/configs" aria-describedby="table-error">
                <button id="search-button" class="button button--search" type="submit"><span>Найти</span></button>
            </form>
            <p id="table-error" class="field-error" role="alert"></p>
        </section>
        <div id="yt-results"></div>
    `;
}

function friendlyTypeValue(value) {
    if (typeof value === "string") {
        const optionalMatch = value.match(/^optional\s*<(.+)>$/i);
        return optionalMatch ? friendlyTypeValue(optionalMatch[1]) : value;
    }
    if (!value || typeof value !== "object") return String(value ?? "—");

    const typeName = value.type_name ?? value.typeName;
    if (typeName === "optional") {
        return friendlyTypeValue(value.item ?? value.element ?? value.type ?? "—");
    }
    return JSON.stringify(value);
}

function schemaType(column) {
    const value = column.type_v3 ?? column.type ?? "—";
    return friendlyTypeValue(value);
}

function formatAttribute(value, kind) {
    if (kind === "bytes") return formatBytes(value);
    if (kind === "integer") return formatInteger(value);
    if (kind === "date") return formatDateWithRelative(value);
    return formatValue(value);
}

function attributesTemplate(attributes, entries) {
    const visible = entries.filter(([, value]) => value !== null && value !== undefined && value !== "");
    if (!visible.length) return "";
    return `
        <section class="content-card info-section">
            <div class="section-heading"><h2>Атрибуты</h2></div>
            <div class="attribute-grid">
                ${visible.map(([name, value, kind]) => {
                    const display = formatAttribute(value, kind);
                    return `<div class="attribute"><span class="attribute__name">${escapeHtml(name)}</span><span class="attribute__value" title="${escapeHtml(display)}">${escapeHtml(display)}</span></div>`;
                }).join("")}
            </div>
        </section>
    `;
}

function renderDirectoryInfo(info) {
    const results = document.getElementById("yt-results");
    const attributes = info.attributes || {};
    const tables = info.tables || [];
    results.innerHTML = `
        <section class="results">
            <div class="summary-grid">
                <article class="summary-card">
                    <div class="summary-card__label"><span>Папка</span><span class="summary-card__icon">D</span></div>
                    <div class="summary-card__value" title="${escapeHtml(info.path)}">${escapeHtml(info.path.split("/").filter(Boolean).at(-1) || info.path)}</div>
                    <div class="summary-card__meta">${escapeHtml(info.path)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Тип объекта</span><span class="summary-card__icon">N</span></div>
                    <div class="summary-card__value">Папка</div>
                    <div class="summary-card__meta">node: ${escapeHtml(info.nodeType)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Таблицы</span><span class="summary-card__icon">T</span></div>
                    <div class="summary-card__value">${formatInteger(info.tableCount)}</div>
                    <div class="summary-card__meta">непосредственно в папке</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Кластер</span><span class="summary-card__icon">C</span></div>
                    <div class="summary-card__value">${escapeHtml(info.cluster)}</div>
                    <div class="summary-card__meta">${escapeHtml(info.proxy)}</div>
                </article>
            </div>
            ${attributesTemplate(attributes, [
                ["Аккаунт", attributes.account],
                ["Владелец", attributes.owner],
                ["Создана", attributes.creationTime, "date"],
                ["Изменена", attributes.modificationTime, "date"],
            ])}
            <section class="content-card info-section">
                <div class="section-heading">
                    <div><h2>Таблицы в папке</h2><p>${tables.length} найдено</p></div>
                </div>
                <div class="directory-table-list">
                    ${tables.length ? tables.map((table) => `
                        <button class="directory-table" type="button" data-table-path="${escapeHtml(table.path)}">
                            <span class="directory-table__icon">T</span>
                            <span class="directory-table__copy">
                                <strong>${escapeHtml(table.name)}</strong>
                                <code>${escapeHtml(table.path)}</code>
                            </span>
                            <span class="status-pill ${table.dynamic ? "" : "status-pill--neutral"}">${table.dynamic ? "Динамическая" : "Статическая"}</span>
                        </button>
                    `).join("") : `<div class="inline-empty">В этой папке нет таблиц.</div>`}
                </div>
            </section>
        </section>
    `;
    results.querySelectorAll("[data-table-path]").forEach((button) => {
        button.addEventListener("click", () => {
            document.getElementById("table-path").value = button.dataset.tablePath;
            document.getElementById("yt-search-form").requestSubmit();
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    });
}

function renderGenericNodeInfo(info) {
    const attributes = info.attributes || {};
    document.getElementById("yt-results").innerHTML = `
        <section class="results">
            <div class="summary-grid">
                <article class="summary-card">
                    <div class="summary-card__label"><span>YT-нода</span><span class="summary-card__icon">N</span></div>
                    <div class="summary-card__value">${escapeHtml(info.path.split("/").filter(Boolean).at(-1) || info.path)}</div>
                    <div class="summary-card__meta">${escapeHtml(info.path)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Тип объекта</span><span class="summary-card__icon">i</span></div>
                    <div class="summary-card__value">${escapeHtml(info.nodeType)}</div>
                    <div class="summary-card__meta">Это не таблица</div>
                </article>
            </div>
            ${attributesTemplate(attributes, [
                ["Аккаунт", attributes.account],
                ["Владелец", attributes.owner],
                ["Создана", attributes.creationTime, "date"],
                ["Изменена", attributes.modificationTime, "date"],
            ])}
        </section>
    `;
}

function renderTableInfo(info) {
    if (info.kind === "directory") {
        renderDirectoryInfo(info);
        return;
    }
    if (info.kind !== "table") {
        renderGenericNodeInfo(info);
        return;
    }

    const results = document.getElementById("yt-results");
    const attributes = info.attributes || {};
    const keys = info.keyColumns || [];
    const schema = info.schema || [];
    const rowCount = attributes.rowCount;
    results.innerHTML = `
        <section class="results">
            <div class="summary-grid">
                <article class="summary-card">
                    <div class="summary-card__label"><span>Таблица</span><span class="summary-card__icon">T</span></div>
                    <div class="summary-card__value" title="${escapeHtml(info.path)}">${escapeHtml(info.path.split("/").filter(Boolean).at(-1) || info.path)}</div>
                    <div class="summary-card__meta">${escapeHtml(info.path)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Тип</span><span class="summary-card__icon">↯</span></div>
                    <div class="summary-card__value">${info.dynamic ? "Динамическая" : "Статическая"}</div>
                    <div class="summary-card__meta">node: ${escapeHtml(info.nodeType)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Строки</span><span class="summary-card__icon">#</span></div>
                    <div class="summary-card__value">${rowCount === null || rowCount === undefined ? "—" : `≈ ${formatInteger(rowCount)}`}</div>
                    <div class="summary-card__meta">оценка YT</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Репликация</span><span class="summary-card__icon">R</span></div>
                    <div class="summary-card__value"><span class="status-pill ${info.replicated ? "" : "status-pill--neutral"}">${info.replicated ? "● Реплицированная" : "○ Не реплицированная"}</span></div>
                    <div class="summary-card__meta">${escapeHtml(info.proxy)}</div>
                </article>
                <article class="summary-card">
                    <div class="summary-card__label"><span>Схема</span><span class="summary-card__icon">{ }</span></div>
                    <div class="summary-card__value">${schema.length} колонок</div>
                    <div class="summary-card__meta">ключевых: ${keys.length}</div>
                </article>
            </div>

            ${attributesTemplate(attributes, [
                ["Аккаунт", attributes.account],
                ["Владелец", attributes.owner],
                ["Создана", attributes.creationTime, "date"],
                ["Изменена", attributes.modificationTime, "date"],
                ["Строк", attributes.rowCount, "integer"],
                ["Вес данных", attributes.dataWeight, "bytes"],
                ["Tablet state", attributes.tabletState],
                ["Compression", attributes.compressionCodec],
                ["Erasure", attributes.erasureCodec],
                ["Optimize for", attributes.optimizeFor],
                ["Chunk format", attributes.chunkFormat],
                ["Upstream replica", attributes.upstreamReplicaId],
            ])}

            ${info.dynamic ? `
                <section class="content-card info-section">
                    <div class="section-heading"><div><h2>Ключевые колонки</h2><p>В порядке сортировки схемы</p></div></div>
                    <div class="key-columns">
                        ${keys.length ? keys.map((key, index) => `<span class="key-column"><span class="key-index">${index + 1}</span>${escapeHtml(key.name)} · ${escapeHtml(friendlyTypeValue(key.type))} · ${escapeHtml(key.sortOrder)}</span>`).join("") : `<span class="no-mark">Ключевые колонки не найдены</span>`}
                    </div>
                </section>
            ` : ""}

            <section class="content-card info-section">
                <div class="section-heading">
                    <div><h2>Схема таблицы</h2><p>${schema.length} колонок</p></div>
                    <div class="schema-tabs" role="tablist">
                        <button class="schema-tab is-active" type="button" data-schema-tab="friendly">Понятно</button>
                        <button class="schema-tab" type="button" data-schema-tab="yson">YSON</button>
                        <button class="schema-tab" type="button" data-schema-tab="json">JSON</button>
                    </div>
                </div>
                <div data-schema-panel="friendly">
                    <div class="schema-table-wrap">
                        <table class="schema-table">
                            <thead><tr><th>#</th><th>Имя</th><th>Тип</th><th>Ключ</th><th>Обязательная</th><th>Вычислимая</th><th>Формула</th></tr></thead>
                            <tbody>
                                ${schema.map((column, index) => {
                                    const expression = column.expression ?? column.computed_expression;
                                    const keyIndex = keys.findIndex((key) => key.name === column.name);
                                    return `<tr>
                                        <td>${index + 1}</td>
                                        <td class="schema-name">${escapeHtml(column.name ?? "—")}</td>
                                        <td><code class="type-code">${escapeHtml(schemaType(column))}</code></td>
                                        <td>${keyIndex >= 0 ? `<span class="yes-mark">${keyIndex + 1} · ${escapeHtml(column.sort_order)}</span>` : `<span class="no-mark">Нет</span>`}</td>
                                        <td>${column.required ? `<span class="yes-mark">Да</span>` : `<span class="no-mark">Нет</span>`}</td>
                                        <td>${expression ? `<span class="yes-mark">Да</span>` : `<span class="no-mark">Нет</span>`}</td>
                                        <td><code class="formula-code">${expression ? escapeHtml(expression) : "—"}</code></td>
                                    </tr>`;
                                }).join("") || `<tr><td colspan="7" class="no-mark">Схема пуста</td></tr>`}
                            </tbody>
                        </table>
                    </div>
                </div>
                <div data-schema-panel="yson" hidden><pre class="code-view"></pre></div>
                <div data-schema-panel="json" hidden><pre class="code-view"></pre></div>
            </section>
        </section>
    `;
    results.querySelector('[data-schema-panel="yson"] pre').textContent = info.schemaYson || "[]";
    results.querySelector('[data-schema-panel="json"] pre').textContent = JSON.stringify(schema, null, 2);
    results.querySelectorAll("[data-schema-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            const tab = button.dataset.schemaTab;
            results.querySelectorAll("[data-schema-tab]").forEach((item) => item.classList.toggle("is-active", item === button));
            results.querySelectorAll("[data-schema-panel]").forEach((panel) => { panel.hidden = panel.dataset.schemaPanel !== tab; });
        });
    });
}

async function searchYtTable(event) {
    event.preventDefault();
    if (state.searchController) {
        state.searchController.abort();
        return;
    }

    const input = document.getElementById("table-path");
    const errorElement = document.getElementById("table-error");
    const button = document.getElementById("search-button");
    const cluster = document.getElementById("yt-cluster").value;
    const path = input.value.trim();
    input.classList.remove("is-error");
    errorElement.textContent = "";
    document.getElementById("yt-results").innerHTML = "";

    if (!path) {
        input.classList.add("is-error");
        errorElement.textContent = "Введите путь к таблице";
        input.focus();
        return;
    }
    const token = localStorage.getItem(TOKEN_KEYS.yt) || "";
    if (!token) {
        input.classList.add("is-error");
        errorElement.textContent = "Сначала установите YT-токен в настройках";
        showTokenModal("yt");
        return;
    }

    localStorage.setItem(CLUSTER_KEY, cluster);
    const controller = new AbortController();
    state.searchController = controller;
    button.classList.add("button--stop");
    button.innerHTML = `<span class="button__stop-icon"></span><span>Остановить</span>`;
    try {
        const info = await apiFetch("/api/yt/table-info", {
            method: "POST",
            headers: { "X-YT-Token": token },
            body: JSON.stringify({ path, cluster }),
            signal: controller.signal,
        });
        renderTableInfo(info);
    } catch (error) {
        if (error.name === "AbortError") {
            showToast("Поиск остановлен");
            return;
        }
        input.classList.add("is-error");
        if (error.code === "node_not_found") {
            errorElement.textContent = "Таблица или папка не найдена";
        } else if (error.code === "access_denied") {
            errorElement.textContent = "Доступ ограничен для данного токена";
        } else {
            errorElement.textContent = error.message;
        }
    } finally {
        if (state.searchController === controller) {
            state.searchController = null;
            button.classList.remove("button--stop");
            button.innerHTML = `<span>Найти</span>`;
        }
    }
}

async function loadNotesIndex() {
    try {
        state.notes = await apiFetch("/api/notes");
        renderNotesMenus();
    } catch (error) {
        showToast(error.message, "error");
    }
}

function renderNotesMenus() {
    const draftsMenu = document.getElementById("drafts-menu");
    const articlesMenu = document.getElementById("articles-menu");
    document.getElementById("drafts-count").textContent = state.notes.drafts.length;
    document.getElementById("articles-count").textContent = state.notes.articles.length;
    draftsMenu.innerHTML = state.notes.drafts.map((note) => noteMenuItem(note, true)).join("");
    articlesMenu.innerHTML = state.notes.articles.map((note) => noteMenuItem(note, false)).join("");
}

function noteMenuItem(note, draft) {
    const href = draft ? `/notes/drafts/${note.slug}` : `/notes/${note.slug}`;
    return `
        <a class="note-menu-item" href="${escapeHtml(href)}" data-link>
            <span class="note-menu-item__icon">${draft ? "◐" : "▤"}</span>
            <span class="note-menu-item__copy"><span class="note-menu-item__title">${escapeHtml(note.title)}</span><span class="note-menu-item__date">${escapeHtml(formatRelativeTime(note.updatedAt))}</span></span>
        </a>
    `;
}

async function createDraft() {
    try {
        const note = await apiFetch("/api/notes/drafts", { method: "POST" });
        await loadNotesIndex();
        navigate(`/notes/drafts/${note.slug}`);
    } catch (error) {
        showToast(error.message, "error");
    }
}

function notesHomeTemplate() {
    const drafts = state.notes.drafts || [];
    const articles = state.notes.articles || [];
    const hasNotes = drafts.length > 0 || articles.length > 0;

    const listItem = (note, draft) => {
        const href = draft ? `/notes/drafts/${note.slug}` : `/notes/${note.slug}`;
        return `
            <a class="notes-index-item" href="${escapeHtml(href)}" data-link>
                <span class="notes-index-item__icon">${draft ? "✎" : "▤"}</span>
                <span class="notes-index-item__copy">
                    <strong>${escapeHtml(note.title)}</strong>
                    <span>${draft ? "Черновик" : "Статья"} · ${escapeHtml(formatDateWithRelative(note.updatedAt))}</span>
                </span>
                <span class="notes-index-item__arrow">→</span>
            </a>
        `;
    };

    return `
        <section class="page-header">
            <div class="page-header__copy"><p class="kicker">ЗАМЕТКИ</p><h1 class="page-title">Заметки</h1></div>
        </section>
        ${hasNotes ? `
            <section class="content-card notes-create-panel">
                <div><h2>Создать новую заметку</h2><p>Начните с черновика и опубликуйте, когда всё будет готово.</p></div>
                <button class="button" id="empty-create-note" type="button">+ Создать</button>
            </section>
            <div class="notes-overview">
                ${drafts.length ? `
                    <section class="notes-group">
                        <div class="notes-group__heading"><h2>Черновики</h2><span>${drafts.length}</span></div>
                        <div class="notes-index-list">${drafts.map((note) => listItem(note, true)).join("")}</div>
                    </section>
                ` : ""}
                ${articles.length ? `
                    <section class="notes-group">
                        <div class="notes-group__heading"><h2>Статьи</h2><span>${articles.length}</span></div>
                        <div class="notes-index-list">${articles.map((note) => listItem(note, false)).join("")}</div>
                    </section>
                ` : ""}
            </div>
        ` : `
            <section class="content-card empty-state">
                <div><div class="empty-state__icon">✎</div><h2>Создайте первую заметку</h2><button class="button" id="empty-create-note" type="button">+ Создать</button></div>
            </section>
        `}
    `;
}

function loadingTemplate(label = "Загружаю…") {
    return `<section class="content-card empty-state"><div><div class="button__spinner" style="margin:0 auto 18px;border-color:#cbd8ee;border-top-color:#2869ed;width:24px;height:24px"></div><p>${escapeHtml(label)}</p></div></section>`;
}

function editorTemplate(note) {
    const isEdit = Boolean(note.sourceArticleSlug);
    return `
        <section class="page-header">
            <div class="page-header__copy"><p class="kicker">ЗАМЕТКИ / ${isEdit ? "РЕДАКТИРОВАНИЕ" : "ЧЕРНОВИК"}</p><h1 class="page-title">${isEdit ? "Редактирование статьи" : "Черновик"}</h1></div>
        </section>
        <section class="content-card editor-shell">
            <div class="editor-topbar">
                <input id="editor-title" class="editor-title-input" maxlength="200" value="${escapeHtml(note.title)}" aria-label="Название черновика">
                <span id="save-indicator" class="save-indicator">Сохранено</span>
                ${isEdit ? `<button class="button button--ghost" id="cancel-edit-button" type="button">Отменить изменения</button>` : ""}
                <button class="button button--secondary" id="delete-note-button" type="button">Удалить</button>
                <button class="button" id="publish-note-button" type="button">Опубликовать</button>
            </div>
            <div class="editor-toolbar" role="toolbar" aria-label="Форматирование текста">
                <div class="toolbar-group">
                    <button class="toolbar-button" type="button" data-block="p">Текст</button>
                    <button class="toolbar-button" type="button" data-block="h1">H1</button>
                    <button class="toolbar-button" type="button" data-block="h2">H2</button>
                    <button class="toolbar-button" type="button" data-block="h3">H3</button>
                </div>
                <div class="toolbar-group">
                    <button class="toolbar-button" type="button" data-command="bold" title="Жирный"><strong>B</strong></button>
                    <button class="toolbar-button" type="button" data-command="italic" title="Курсив"><em>I</em></button>
                    <button class="toolbar-button" type="button" data-command="underline" title="Подчёркнутый"><u>U</u></button>
                    <button class="toolbar-button" type="button" data-command="strikeThrough" title="Зачёркнутый"><s>S</s></button>
                </div>
                <div class="toolbar-group">
                    <button class="toolbar-button" type="button" data-command="insertUnorderedList" title="Маркированный список">• List</button>
                    <button class="toolbar-button" type="button" data-command="insertOrderedList" title="Нумерованный список">1. List</button>
                    <button class="toolbar-button" type="button" data-block="blockquote" title="Цитата">❝</button>
                </div>
                <div class="toolbar-group">
                    <button class="toolbar-button" type="button" data-editor-action="link">🔗 Ссылка</button>
                    <button class="toolbar-button" type="button" data-editor-action="image">▧ Картинка</button>
                    <button class="toolbar-button" type="button" data-editor-action="details">⌄ Закладка</button>
                    <button class="toolbar-button" type="button" data-command="removeFormat">Очистить стиль</button>
                </div>
            </div>
            <article id="editor-canvas" class="editor-canvas" contenteditable="true" spellcheck="true" aria-label="Текст заметки">${sanitizeHtml(note.content)}</article>
        </section>
    `;
}

function articleTemplate(note) {
    return `
        <section class="page-header">
            <div class="page-header__copy"><p class="kicker">ЗАМЕТКИ / СТАТЬЯ</p><h1 class="page-title">База знаний</h1></div>
            <div><button class="button button--secondary" id="delete-article-button" type="button">Удалить</button> <button class="button" id="edit-article-button" type="button">Редактировать</button></div>
        </section>
        <article class="content-card article-shell">
            <div class="article-meta"><span class="status-pill">● Опубликовано</span><span>${formatDateWithRelative(note.publishedAt || note.createdAt)}</span><span>·</span><span>обновлено ${formatDateWithRelative(note.updatedAt)}</span></div>
            <h1 class="article-title">${escapeHtml(note.title)}</h1>
            <div class="article-content">${sanitizeHtml(note.content)}</div>
        </article>
    `;
}

async function loadNote(kind, slug) {
    app.innerHTML = loadingTemplate("Открываю заметку…");
    try {
        const note = await apiFetch(`/api/notes/${kind}/${encodeURIComponent(slug)}`);
        state.currentNote = note;
        if (kind === "drafts") {
            app.innerHTML = editorTemplate(note);
            attachEditorEvents();
        } else {
            app.innerHTML = articleTemplate(note);
            attachArticleEvents();
        }
    } catch (error) {
        app.innerHTML = `<section class="content-card empty-state"><div><div class="empty-state__icon">!</div><h2>Заметка не найдена</h2><p>${escapeHtml(error.message)}</p><button class="button" id="back-to-notes" type="button">К заметкам</button></div></section>`;
        document.getElementById("back-to-notes").addEventListener("click", () => navigate("/notes"));
    }
}

function rememberEditorSelection() {
    const selection = window.getSelection();
    const editor = document.getElementById("editor-canvas");
    if (!selection || !selection.rangeCount || !editor?.contains(selection.anchorNode)) return;
    state.editorRange = selection.getRangeAt(0).cloneRange();
}

function restoreEditorSelection() {
    if (!state.editorRange) return;
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(state.editorRange);
}

function scheduleDraftSave() {
    const indicator = document.getElementById("save-indicator");
    if (indicator) {
        indicator.textContent = "Сохраняю…";
        indicator.classList.add("is-saving");
    }
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => saveDraft(), 650);
}

async function saveDraft({ keepalive = false, silent = false } = {}) {
    if (!state.currentNote || state.currentNote.kind !== "draft") return;
    const titleInput = document.getElementById("editor-title");
    const editor = document.getElementById("editor-canvas");
    if (!titleInput || !editor) return;
    if (state.isSaving) {
        await new Promise((resolve) => setTimeout(resolve, 80));
        return saveDraft({ keepalive, silent });
    }
    clearTimeout(state.saveTimer);
    const title = titleInput.value.trim() || "Новый черновик";
    const content = sanitizeHtml(editor.innerHTML);
    state.currentNote.title = title;
    state.currentNote.content = content;
    state.isSaving = true;
    try {
        await apiFetch(`/api/notes/drafts/${encodeURIComponent(state.currentNote.slug)}`, {
            method: "PUT",
            body: JSON.stringify({ title, content }),
            keepalive,
        });
        const indicator = document.getElementById("save-indicator");
        if (indicator) {
            indicator.textContent = "Сохранено";
            indicator.classList.remove("is-saving");
        }
    } catch (error) {
        if (!silent) showToast(error.message, "error");
        const indicator = document.getElementById("save-indicator");
        if (indicator) indicator.textContent = "Ошибка сохранения";
    } finally {
        state.isSaving = false;
    }
}

function runEditorCommand(command, value = null) {
    restoreEditorSelection();
    document.getElementById("editor-canvas")?.focus();
    document.execCommand(command, false, value);
    rememberEditorSelection();
    scheduleDraftSave();
}

async function uploadEditorImage(file) {
    if (!file) return;
    const form = new FormData();
    form.append("image", file);
    try {
        const result = await apiFetch("/api/notes/uploads", { method: "POST", body: form });
        restoreEditorSelection();
        document.getElementById("editor-canvas")?.focus();
        document.execCommand("insertImage", false, result.url);
        scheduleDraftSave();
        showToast("Изображение добавлено");
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        document.getElementById("editor-image-input").value = "";
    }
}

function attachEditorEvents() {
    const editor = document.getElementById("editor-canvas");
    const title = document.getElementById("editor-title");
    editor.addEventListener("input", scheduleDraftSave);
    title.addEventListener("input", scheduleDraftSave);
    editor.addEventListener("keyup", rememberEditorSelection);
    editor.addEventListener("mouseup", rememberEditorSelection);
    document.querySelectorAll(".toolbar-button").forEach((button) => {
        button.addEventListener("mousedown", (event) => {
            event.preventDefault();
            rememberEditorSelection();
        });
    });
    document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => runEditorCommand(button.dataset.command)));
    document.querySelectorAll("[data-block]").forEach((button) => button.addEventListener("click", () => runEditorCommand("formatBlock", button.dataset.block)));
    document.querySelector('[data-editor-action="link"]').addEventListener("click", () => {
        const href = window.prompt("Адрес ссылки (https://…)", "https://");
        if (href) runEditorCommand("createLink", href);
    });
    document.querySelector('[data-editor-action="details"]').addEventListener("click", () => {
        runEditorCommand("insertHTML", `<details open><summary>Название закладки</summary><p>Содержимое…</p></details><p><br></p>`);
    });
    document.querySelector('[data-editor-action="image"]').addEventListener("click", () => document.getElementById("editor-image-input").click());
    document.getElementById("delete-note-button").addEventListener("click", () => confirmDeleteNote("drafts", state.currentNote.slug, "Удалить черновик?"));
    document.getElementById("publish-note-button").addEventListener("click", openPublishDialog);
    document.getElementById("cancel-edit-button")?.addEventListener("click", cancelArticleEdit);
}

function attachArticleEvents() {
    document.getElementById("edit-article-button").addEventListener("click", editCurrentArticle);
    document.getElementById("delete-article-button").addEventListener("click", () => confirmDeleteNote("articles", state.currentNote.slug, "Удалить статью?"));
}

async function editCurrentArticle() {
    try {
        const draft = await apiFetch(`/api/notes/articles/${encodeURIComponent(state.currentNote.slug)}/edit`, { method: "POST" });
        await loadNotesIndex();
        navigate(`/notes/drafts/${draft.slug}`);
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function cancelArticleEdit() {
    const sourceSlug = state.currentNote?.sourceArticleSlug;
    if (!sourceSlug) return;
    setConfirmAction(
        "Отменить изменения?",
        "Черновик редактирования будет удалён, опубликованная статья останется без изменений.",
        "Отменить изменения",
        async () => {
            try {
                await apiFetch(`/api/notes/drafts/${encodeURIComponent(state.currentNote.slug)}/cancel`, { method: "POST" });
                await loadNotesIndex();
                navigate(`/notes/${sourceSlug}`);
            } catch (error) {
                showToast(error.message, "error");
            }
        },
    );
}

function openPublishDialog() {
    saveDraft();
    const input = document.getElementById("publish-title-input");
    input.value = document.getElementById("editor-title").value.trim();
    document.getElementById("confirm-publish-button").disabled = !input.value;
    openModal("publish-modal");
}

async function publishCurrentDraft() {
    const input = document.getElementById("publish-title-input");
    const title = input.value.trim();
    if (!title || !state.currentNote) return;
    const button = document.getElementById("confirm-publish-button");
    button.disabled = true;
    try {
        await saveDraft();
        const article = await apiFetch(`/api/notes/drafts/${encodeURIComponent(state.currentNote.slug)}/publish`, {
            method: "POST",
            body: JSON.stringify({ title }),
        });
        closeModal("publish-modal");
        await loadNotesIndex();
        showToast("Статья опубликована");
        navigate(`/notes/${article.slug}`);
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        button.disabled = false;
    }
}

function setConfirmAction(title, description, actionLabel, callback) {
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-description").textContent = description;
    document.getElementById("confirm-action-button").textContent = actionLabel;
    state.confirmAction = callback;
    openModal("confirm-modal");
}

function confirmDeleteNote(kind, slug, title) {
    setConfirmAction(title, "Файл заметки будет удалён без возможности восстановления.", "Удалить", async () => {
        try {
            await apiFetch(`/api/notes/${kind}/${encodeURIComponent(slug)}`, { method: "DELETE" });
            await loadNotesIndex();
            closeModal("confirm-modal");
            showToast("Заметка удалена");
            navigate("/notes");
        } catch (error) {
            showToast(error.message, "error");
        }
    });
}

async function renderRoute() {
    window.YtManager?.destroy?.();
    window.YtMutator?.destroy?.();
    if (state.searchController) {
        state.searchController.abort();
        state.searchController = null;
    }
    clearTimeout(state.saveTimer);
    state.currentNote = null;
    const path = decodeURIComponent(location.pathname);
    if (path === "/" || path === "/tools" || path === "/tools/yt-god" || path === "/tools/yt-observer") {
        if (path !== "/tools/yt-observer") history.replaceState({}, "", "/tools/yt-observer");
        app.innerHTML = toolPageTemplate();
        document.getElementById("yt-search-form").addEventListener("submit", searchYtTable);
        document.getElementById("yt-cluster").addEventListener("change", (event) => localStorage.setItem(CLUSTER_KEY, event.target.value));
    } else if (path === "/tools/yt-creator") {
        window.YtCreator.render(app, {
            apiFetch,
            escapeHtml,
            showToast,
            showTokenModal,
            tokenKey: TOKEN_KEYS.yt,
        });
    } else if (path === "/tools/yt-manager") {
        window.YtManager.render(app, {
            apiFetch,
            escapeHtml,
            showToast,
            showTokenModal,
            tokenKey: TOKEN_KEYS.yt,
        });
    } else if (path === "/tools/yt-mutator") {
        window.YtMutator.render(app, {
            apiFetch,
            escapeHtml,
            showToast,
            showTokenModal,
            tokenKey: TOKEN_KEYS.yt,
        });
    } else if (path === "/notes" || path === "/notes/") {
        app.innerHTML = notesHomeTemplate();
        document.getElementById("empty-create-note").addEventListener("click", createDraft);
    } else {
        const draftMatch = path.match(/^\/notes\/drafts\/([a-z0-9-]+)$/);
        const articleMatch = path.match(/^\/notes\/([a-z0-9-]+)$/);
        if (draftMatch) await loadNote("drafts", draftMatch[1]);
        else if (articleMatch) await loadNote("articles", articleMatch[1]);
        else {
            app.innerHTML = `<section class="content-card empty-state"><div><div class="empty-state__icon">404</div><h2>Страница не найдена</h2><p>Проверьте адрес или вернитесь в инструменты.</p><button class="button" id="back-home" type="button">Открыть YT Observer</button></div></section>`;
            document.getElementById("back-home").addEventListener("click", () => navigate("/tools/yt-observer"));
        }
    }
    app.focus({ preventScroll: true });
}

document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-link]");
    if (link && link.origin === location.origin) {
        event.preventDefault();
        navigate(link.pathname);
        return;
    }
    const trigger = event.target.closest("[data-dropdown-trigger]");
    if (trigger) {
        toggleDropdown(trigger.dataset.dropdownTrigger);
        return;
    }
    if (!event.target.closest(".nav-dropdown")) closeDropdowns();
});

document.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => closeModal(button.dataset.closeModal)));
document.querySelectorAll(".modal-backdrop").forEach((backdrop) => backdrop.addEventListener("mousedown", (event) => {
    if (event.target === backdrop) closeModal(backdrop.id);
}));
document.getElementById("settings-button").addEventListener("click", () => openModal("settings-modal"));
document.querySelectorAll("[data-token-kind]").forEach((button) => button.addEventListener("click", () => showTokenModal(button.dataset.tokenKind)));
document.getElementById("token-input").addEventListener("input", (event) => { document.getElementById("accept-token-button").disabled = !event.target.value.trim(); });
document.getElementById("accept-token-button").addEventListener("click", saveToken);
document.getElementById("remove-token-button").addEventListener("click", removeToken);
document.getElementById("choose-avatar-button").addEventListener("click", () => document.getElementById("avatar-input").click());
document.getElementById("remove-avatar-button").addEventListener("click", removeAvatar);
document.getElementById("avatar-input").addEventListener("change", (event) => {
    selectAvatar(event.target.files?.[0]);
    event.target.value = "";
});
document.getElementById("create-note-button").addEventListener("click", createDraft);
document.getElementById("publish-title-input").addEventListener("input", (event) => { document.getElementById("confirm-publish-button").disabled = !event.target.value.trim(); });
document.getElementById("confirm-publish-button").addEventListener("click", publishCurrentDraft);
document.getElementById("confirm-action-button").addEventListener("click", async () => {
    const callback = state.confirmAction;
    state.confirmAction = null;
    if (callback) await callback();
});
document.getElementById("editor-image-input").addEventListener("change", (event) => uploadEditorImage(event.target.files?.[0]));
window.addEventListener("popstate", renderRoute);
window.addEventListener("pagehide", () => {
    if (state.currentNote?.kind === "draft") saveDraft({ keepalive: true, silent: true });
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeDropdowns();
        document.querySelectorAll(".modal-backdrop:not([hidden])").forEach((modal) => closeModal(modal.id));
    }
});

ensureInitialState();

async function ensureInitialState() {
    updateTokenStatus();
    updateAvatar();
    await loadNotesIndex();
    renderRoute();
}
