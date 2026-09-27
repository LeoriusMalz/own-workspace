"use strict";

window.YtMutator = (() => {
    const TYPES = [
        "int64", "int32", "int16", "int8", "uint64", "uint32", "uint16", "uint8",
        "double", "float", "bool", "string", "utf8", "json", "uuid",
        "date", "datetime", "timestamp", "interval",
        "date32", "datetime64", "timestamp64", "interval64", "yson",
    ];
    const INTEGER_RANGES = {
        int64: [-(2n ** 63n), 2n ** 63n - 1n], int32: [-(2n ** 31n), 2n ** 31n - 1n],
        int16: [-(2n ** 15n), 2n ** 15n - 1n], int8: [-(2n ** 7n), 2n ** 7n - 1n],
        uint64: [0n, 2n ** 64n - 1n], uint32: [0n, 2n ** 32n - 1n],
        uint16: [0n, 2n ** 16n - 1n], uint8: [0n, 2n ** 8n - 1n],
        date: [0n, 49672n], datetime: [0n, BigInt(49673 * 86400 - 1)],
        timestamp: [0n, BigInt(49673) * 86400n * 1000000n - 1n],
        date32: [-53375809n, 53375807n],
        datetime64: [-53375809n * 86400n, 53375808n * 86400n - 1n],
        timestamp64: [-53375809n * 86400n * 1000000n, 53375808n * 86400n * 1000000n - 1n],
        interval: [-(BigInt(49673) * 86400n * 1000000n) + 1n, BigInt(49673) * 86400n * 1000000n - 1n],
        interval64: [-9223339708800000000n, 9223339708800000000n],
    };

    let root;
    let services;
    let state;

    function initialState() {
        return {
            mode: "alter",
            dataMode: "edit",
            cluster: localStorage.getItem("dev-toolbox:yt-cluster") || "miranda",
            path: "",
            loading: false,
            controller: null,
            info: null,
            error: "",
            schemaDialog: null,
            pendingSchema: null,
            confirm: null,
            acting: false,
            searchMode: "keys",
            keyFilters: {},
            filterErrors: {},
            where: "",
            limit: 10,
            applyLimit: true,
            searching: false,
            searchResult: null,
            editingIndex: null,
            stagedUpdates: {},
            stagedDeletes: new Set(),
            insertRows: [],
            insertErrors: {},
            command: null,
            strictPlanning: false,
        };
    }

    function h(value) { return services.escapeHtml(value); }
    function clone(value) { return structuredClone(value); }
    function headers() { return { "X-YT-Token": localStorage.getItem(services.tokenKey) || "" }; }
    function cell(value = "", isNull = false) { return { value: String(value ?? ""), isNull }; }

    function pageHeader() {
        return `
            <header class="page-header mutator-page-header">
                <div class="page-header__copy">
                    <p class="kicker">YT TOOL · MUTATE</p>
                    <h1 class="page-title">YT Mutator</h1>
                    <p class="page-subtitle">Безопасное изменение схемы и пакетная работа со строками таблицы.</p>
                </div>
                <div class="page-header__mark"><span class="live-dot"></span><span>SCHEMA-AWARE</span></div>
            </header>`;
    }

    function modeSwitch() {
        return `
            <div class="mutator-mode-switch" role="tablist">
                <button type="button" data-mutator-mode="alter" class="${state.mode === "alter" ? "is-active" : ""}"><span>Альтеринг таблицы</span><small>Колонки и типы</small></button>
                <button type="button" data-mutator-mode="data" class="${state.mode === "data" ? "is-active" : ""}"><span>Манипуляция данными</span><small>Поиск и изменение строк</small></button>
            </div>`;
    }

    function searchCard() {
        return `
            <section class="mutator-card mutator-search-card">
                <form id="mutator-table-form">
                    <div class="mutator-label-row"><span>Таблица</span><code>${h(state.cluster)}.yt.vk.team</code></div>
                    <div class="mutator-search-controls">
                        <select id="mutator-cluster">
                            ${["jupiter", "saturn", "miranda"].map((cluster) => `<option value="${cluster}" ${state.cluster === cluster ? "selected" : ""}>${cluster}</option>`).join("")}
                        </select>
                        <input id="mutator-path" value="${h(state.path)}" placeholder="//home/project/table" autocomplete="off" spellcheck="false">
                        ${state.loading
                            ? `<button class="button button--secondary" id="mutator-stop" type="button">Остановить</button>`
                            : `<button class="button" type="submit">Загрузить</button>`}
                    </div>
                    <p class="mutator-error">${h(state.error)}</p>
                </form>
            </section>`;
    }

    function identity() {
        const info = state.info;
        return `
            <section class="mutator-identity">
                <span class="mutator-identity-icon">${info.replicated ? "R" : info.dynamic ? "D" : "S"}</span>
                <div><small>${h(info.kindLabel)}</small><strong>${h(info.path)}</strong><code>${h(info.cluster)} · ${h(info.schemaMode || "unknown schema")} · ${info.schemaAttributes.strict ? "strict" : "non-strict"}</code></div>
                <div class="mutator-identity-meta"><span>${h(info.optimizeFor || "—")}</span><span>${info.rowCount.toLocaleString("ru-RU")} строк</span>${info.tabletState ? `<span>${h(info.tabletState)}</span>` : ""}</div>
            </section>`;
    }

    function commandPanel() {
        if (!state.command) return "";
        return `
            <section class="mutator-command ${state.command.operationUrl ? "has-link" : ""}">
                <span>✓</span><div><strong>Последняя YT-команда</strong><small>${h(state.command.message || "Команда выполнена")}</small></div>
                ${state.command.operationUrl ? `<a href="${h(state.command.operationUrl)}" target="_blank" rel="noreferrer">Открыть operation ↗</a>` : `<em>Scheduler operation не создавалась</em>`}
            </section>`;
    }

    function emptyState() {
        return `<section class="mutator-empty"><span>μ</span><h2>Загрузите таблицу</h2><p>Схема и доступные операции появятся после проверки типа таблицы.</p></section>`;
    }

    function columnType(column) {
        return `${column.displayType}${column.optional ? "?" : ""}`;
    }

    function schemaRow(column) {
        const policy = column.policy || {};
        const canEdit = Boolean(policy.canChange);
        const canDelete = Boolean(policy.canDelete);
        return `
            <article class="mutator-schema-row">
                <div class="mutator-column-order">${column.index + 1}</div>
                <div class="mutator-column-name"><strong>${h(column.name)}</strong>${column.expression ? `<small>${h(column.expression)}</small>` : ""}</div>
                <code>${h(column.displayType)}</code>
                <div class="mutator-column-flags">
                    ${column.key ? `<span class="is-key">KEY · ${h(column.sortOrder)}</span>` : ""}
                    <span class="${column.optional ? "is-optional" : "is-required"}">${column.optional ? "OPTIONAL" : "REQUIRED"}</span>
                    ${column.computed ? `<span>COMPUTED</span>` : ""}
                </div>
                <div class="mutator-row-actions">
                    <button type="button" data-schema-change="${h(column.name)}" title="${h(canEdit ? "Поменять тип или включить optional" : policy.changeReason || "Изменение недоступно")}" ${canEdit ? "" : "disabled"}>✎</button>
                    <button type="button" data-schema-delete="${h(column.name)}" title="${h(canDelete ? "Удалить поле" : policy.deleteReason || "Удаление недоступно")}" ${canDelete ? "" : "disabled"}>⌫</button>
                </div>
            </article>`;
    }

    function strictControl() {
        const policy = state.info.schemaPolicy?.strict || {
            value: Boolean(state.info.schemaAttributes.strict), canToggle: false, reason: "",
        };
        const disabled = !policy.canToggle || state.strictPlanning;
        return `
            <div class="mutator-strict-control ${policy.value ? "is-strict" : "is-nonstrict"}">
                <div><span>Режим схемы</span><strong>${policy.value ? "STRICT" : "NON-STRICT"}</strong><small>${h(policy.reason || "")}</small></div>
                <label class="mutator-switch" title="${h(policy.reason || "Переключить strict")}">
                    <input id="mutator-strict-toggle" type="checkbox" ${policy.value ? "checked" : ""} ${disabled ? "disabled" : ""}>
                    <span></span>
                </label>
            </div>`;
    }

    function alterView() {
        const info = state.info;
        if (!info.capabilities.alterSchema) {
            return `<section class="mutator-card mutator-restriction"><strong>Альтеринг недоступен</strong><p>${h(info.restrictions.alterSchema)}</p></section>`;
        }
        return `
            ${strictControl()}
            <section class="mutator-card mutator-section">
                <div class="mutator-section-heading">
                    <div><span class="mutator-step">01</span><div><h2>Схема таблицы</h2><p>${info.columns.length} колонок · ${info.keyColumns.length} ключевых</p></div></div>
                    <button class="button" id="mutator-add-column" type="button">+ Добавить поле</button>
                </div>
                <div class="mutator-schema-head"><span>#</span><span>Поле</span><span>Тип</span><span>Свойства</span><span></span></div>
                <div class="mutator-schema-list">${info.columns.map(schemaRow).join("")}</div>
                <div class="mutator-rules">
                    <span>${info.schemaAttributes.strict ? "STRICT" : "NON-STRICT"}</span>
                    <p>${info.rowCount
                        ? "Для непустой таблицы разрешаются только изменения, совместимые без чтения всех данных."
                        : "Таблица пуста: типы и optional можно менять без перезаписи данных."}</p>
                </div>
            </section>`;
    }

    function dataModeSwitch() {
        return `
            <div class="mutator-sub-switch" role="tablist" aria-label="Режим работы с данными">
                <button type="button" data-data-mode="edit" class="${state.dataMode === "edit" ? "is-active" : ""}"><span>Изменить / удалить</span><small>Найти существующие строки</small></button>
                <button type="button" data-data-mode="insert" class="${state.dataMode === "insert" ? "is-active" : ""}"><span>Создать записи</span><small>Подготовить и опубликовать пакет</small></button>
            </div>`;
    }

    function allowedOperators(column) {
        if (!column.primitive || ["bool", "yson", "json"].includes(column.baseType)) return ["=", "IN"];
        return ["=", ">", ">=", "<", "<=", "BETWEEN", "IN"];
    }

    function filterValueControl(column, current) {
        const listMode = current.operator === "IN";
        const betweenMode = current.operator === "BETWEEN";
        const placeholder = betweenMode
            ? "от, до"
            : listMode ? "значение 1, значение 2" : "значение";
        if (column.baseType === "bool") {
            const values = listMode ? ["", "true", "false", "true, false"] : ["", "true", "false"];
            return `<select data-filter-value="${h(column.name)}">${values.map((value) => `<option value="${value}" ${current.value === value ? "selected" : ""}>${value || "Выберите значение"}</option>`).join("")}</select>`;
        }
        const numeric = Boolean(INTEGER_RANGES[column.baseType]) || ["double", "float"].includes(column.baseType);
        return `<input data-filter-value="${h(column.name)}" value="${h(current.value)}" placeholder="${h(placeholder)}" ${numeric ? 'inputmode="decimal"' : ""} autocomplete="off" spellcheck="false">`;
    }

    function filterRow(column) {
        const current = state.keyFilters[column.name] || { enabled: false, negated: false, operator: "=", value: "" };
        const listMode = current.operator === "IN";
        const betweenMode = current.operator === "BETWEEN";
        const error = state.filterErrors[column.name] || "";
        return `
            <article class="mutator-filter-row ${current.enabled ? "is-enabled" : ""} ${error ? "has-error" : ""}">
                <label class="mutator-filter-enable"><input type="checkbox" data-filter-enabled="${h(column.name)}" ${current.enabled ? "checked" : ""}><span></span></label>
                <div class="mutator-filter-name"><strong>${h(column.name)}</strong><code>${h(columnType(column))}</code></div>
                <label class="mutator-not-toggle"><input type="checkbox" data-filter-negated="${h(column.name)}" ${current.negated ? "checked" : ""}><span>NOT</span></label>
                <select data-filter-operator="${h(column.name)}">${allowedOperators(column).map((operator) => `<option value="${operator}" ${current.operator === operator ? "selected" : ""}>${operator}</option>`).join("")}</select>
                <div class="mutator-filter-value ${listMode ? "is-list" : ""}">
                    <div class="mutator-filter-input-line">${listMode ? `<b>(</b>` : ""}${filterValueControl(column, current)}${listMode ? `<b>)</b>` : ""}</div>
                    ${(listMode || betweenMode) ? `<small>Значения разделяются запятой</small>` : ""}
                    ${error ? `<em>${h(error)}</em>` : ""}
                </div>
            </article>`;
    }

    function columnsChips() {
        return `<div class="mutator-column-chips">${state.info.columns.map((column) => `<button type="button" data-copy-column="${h(column.name)}" title="Скопировать имя"><span>${column.key ? "K" : "V"}</span>${h(column.name)}<code>${h(columnType(column))}</code></button>`).join("")}</div>`;
    }

    function searchBuilder() {
        const hasKeys = state.info.keyColumns.length > 0;
        if (!hasKeys && state.searchMode === "keys") state.searchMode = "where";
        return `
            <section class="mutator-card mutator-section">
                <div class="mutator-section-heading"><div><span class="mutator-step">01</span><div><h2>Поиск строк</h2><p>Storage mode: ${h(state.info.optimizeFor || "—")}</p></div></div></div>
                <div class="mutator-search-mode" role="tablist" aria-label="Режим фильтрации">
                    <button type="button" data-search-mode="keys" class="${state.searchMode === "keys" ? "is-active" : ""}" ${hasKeys ? "" : "disabled"}><span>По ключам</span><small>Конструктор условий</small></button>
                    <button type="button" data-search-mode="where" class="${state.searchMode === "where" ? "is-active" : ""}"><span>Свой WHERE</span><small>Выражение вручную</small></button>
                </div>
                ${state.searchMode === "keys"
                    ? `<div class="mutator-filter-list">${state.info.keyColumns.map(filterRow).join("")}</div>`
                    : `<div class="mutator-where-block">${columnsChips()}<label><strong>WHERE</strong><input id="mutator-where" value="${h(state.where)}" placeholder="[itemId] > 0 AND [actual] = true" spellcheck="false"></label></div>`}
                <div class="mutator-query-footer">
                    <label><span>Лимит вывода</span><input id="mutator-limit" type="number" min="1" max="500" value="${state.limit}"></label>
                    <label class="mutator-checkbox"><input id="mutator-apply-limit" type="checkbox" ${state.applyLimit ? "checked" : ""}><span>Применить LIMIT прямо в запросе</span></label>
                    <button class="button" id="mutator-run-search" type="button" ${state.searching ? "disabled" : ""}>${state.searching ? "Ищу…" : "Найти"}</button>
                </div>
            </section>`;
    }

    function resultCell(rowIndex, column, valueCell, editing) {
        const staged = state.stagedUpdates[rowIndex]?.[column.name];
        const shown = staged || valueCell || cell();
        if (!editing || column.key) {
            return `<td><div class="mutator-cell-view ${shown.isNull ? "is-null" : ""}">${shown.isNull ? "null" : h(shown.value)}</div></td>`;
        }
        return `<td>${cellEditor(column, shown, `data-edit-row="${rowIndex}" data-edit-column="${h(column.name)}"`)}</td>`;
    }

    function cellEditor(column, valueCell, attributes = "") {
        const boolean = column.baseType === "bool";
        const complex = !column.primitive || ["yson", "json"].includes(column.baseType);
        return `
            <div class="mutator-cell-editor ${complex ? "is-complex" : ""}">
                ${boolean
                    ? `<select ${attributes} data-cell-input><option value="" ${valueCell.value === "" ? "selected" : ""}>—</option><option value="true" ${valueCell.value === "true" ? "selected" : ""}>true</option><option value="false" ${valueCell.value === "false" ? "selected" : ""}>false</option></select>`
                    : complex
                        ? `<textarea ${attributes} data-cell-input spellcheck="false">${h(valueCell.value)}</textarea>`
                        : `<input ${attributes} data-cell-input value="${h(valueCell.value)}" spellcheck="false">`}
                ${column.optional ? `<label title="Записать null"><input ${attributes} data-cell-null type="checkbox" ${valueCell.isNull ? "checked" : ""}><span>null</span></label>` : ""}
            </div>`;
    }

    function resultsTable() {
        const result = state.searchResult;
        if (!result) return "";
        const canAddress = state.info.keyColumns.length > 0 && state.info.capabilities.writeRows;
        const changedCount = Object.keys(state.stagedUpdates).length + state.stagedDeletes.size;
        return `
            <section class="mutator-card mutator-section mutator-results-section">
                <div class="mutator-section-heading">
                    <div><span class="mutator-step">02</span><div><h2>Результаты</h2><p>Найдено ${result.total.toLocaleString("ru-RU")} · показано ${result.shown}</p></div></div>
                    ${changedCount ? `<button class="button button--ghost" id="mutator-reset-batch" type="button">Сбросить пакет (${changedCount})</button>` : ""}
                </div>
                ${!canAddress ? `<p class="mutator-warning">${state.info.keyColumns.length ? h(state.info.restrictions.writeRows || "Таблица доступна только для чтения.") : "У таблицы нет ключевых колонок. Результаты доступны только для чтения: адресно изменить или удалить строку невозможно."}</p>` : ""}
                <div class="mutator-table-wrap"><table class="mutator-data-table"><thead><tr><th>#</th>${state.info.columns.map((column) => `<th><span>${h(column.name)}</span><code>${h(columnType(column))}</code></th>`).join("")}<th></th></tr></thead>
                    <tbody>${result.rows.map((row, index) => {
                        const deleted = state.stagedDeletes.has(index);
                        const editing = state.editingIndex === index;
                        const updated = Boolean(state.stagedUpdates[index]);
                        return `<tr class="${deleted ? "is-deleted" : updated ? "is-updated" : ""}"><td class="mutator-row-number">${index + 1}</td>${state.info.columns.map((column) => resultCell(index, column, row.values[column.name], editing)).join("")}<td class="mutator-table-actions">${canAddress ? (editing
                            ? `<button type="button" data-save-edit="${index}" title="Принять изменения">✓</button><button type="button" data-cancel-edit="${index}" title="Отменить">×</button>`
                            : `<button type="button" data-start-edit="${index}" title="Редактировать" ${deleted ? "disabled" : ""}>✎</button><button type="button" data-toggle-delete="${index}" title="${deleted ? "Вернуть" : "Удалить"}">${deleted ? "↶" : "⌫"}</button>${updated ? `<button type="button" data-reset-row="${index}" title="Сбросить изменения">↶</button>` : ""}`) : ""}</td></tr>`;
                    }).join("")}</tbody></table></div>
                <div class="mutator-batch-footer"><span>${changedCount ? `В пакете изменений: ${changedCount}` : "Изменений пока нет"}</span><button class="button" id="mutator-apply-batch" type="button" ${changedCount ? "" : "disabled"}>Применить изменения</button></div>
            </section>`;
    }

    function editDataView() { return `${searchBuilder()}${resultsTable()}`; }

    function emptyInsertCells() {
        return Object.fromEntries(state.info.columns.map((column) => [column.name, cell("", column.optional)]));
    }

    function insertRowCard(row, index) {
        const errors = state.insertErrors[index] || {};
        return `
            <article class="mutator-insert-row ${Object.keys(errors).length ? "has-errors" : ""}">
                <header><span>Запись ${index + 1}</span><div><button type="button" data-copy-insert="${index}" title="Копировать">⧉</button><button type="button" data-delete-insert="${index}" title="Удалить">×</button></div></header>
                ${errors._row ? `<p class="mutator-row-error">${h(errors._row)}</p>` : ""}
                <div class="mutator-insert-grid">${state.info.columns.map((column) => `<label class="${errors[column.name] ? "is-error" : ""}"><span>${h(column.name)} <code>${h(columnType(column))}</code>${column.key ? `<b>KEY</b>` : ""}</span>${cellEditor(column, row[column.name], `data-insert-row="${index}" data-insert-column="${h(column.name)}"`)}${errors[column.name] ? `<small>${h(errors[column.name])}</small>` : ""}</label>`).join("")}</div>
            </article>`;
    }

    function insertDataView() {
        return `
            <section class="mutator-card mutator-section">
                <div class="mutator-section-heading"><div><span class="mutator-step">01</span><div><h2>Новые записи</h2><p>Создайте одну или несколько строк; ключи проверятся повторно перед insert_rows</p></div></div><button class="button" data-add-record type="button">+ Создать запись</button></div>
                <div class="mutator-insert-schema">${state.info.columns.map((column) => `<span>${h(column.name)}<code>${h(columnType(column))}</code>${column.key ? `<b>KEY</b>` : ""}</span>`).join("")}</div>
                <div class="mutator-insert-list">${state.insertRows.map(insertRowCard).join("") || `<div class="mutator-no-rows"><span>＋</span><strong>Пакет пока пуст</strong><p>Каждая запись будет проверена по типам, optional и уникальности ключа.</p><button class="button button--secondary" data-add-record type="button">Создать первую запись</button></div>`}</div>
                <div class="mutator-batch-footer"><span>${state.insertRows.length ? `Подготовлено записей: ${state.insertRows.length}` : ""}</span><button class="button" id="mutator-publish-records" type="button" ${state.insertRows.length ? "" : "disabled"}>Опубликовать записи</button></div>
            </section>`;
    }

    function dataView() {
        const info = state.info;
        if (!info.capabilities.readRows) {
            return `${dataModeSwitch()}<section class="mutator-card mutator-restriction"><strong>Манипуляция данными недоступна</strong><p>${h(info.restrictions.data)}</p></section>`;
        }
        if (state.dataMode === "insert" && !info.capabilities.writeRows) {
            return `${dataModeSwitch()}<section class="mutator-card mutator-restriction"><strong>Добавление записей недоступно</strong><p>${h(info.restrictions.writeRows)}</p></section>`;
        }
        return `${dataModeSwitch()}${state.dataMode === "edit" ? editDataView() : insertDataView()}`;
    }

    function schemaDialog() {
        const dialog = state.schemaDialog;
        if (!dialog) return "";
        const deleting = dialog.action === "delete";
        const changing = dialog.action === "change_type";
        const availableTypes = changing ? dialog.allowedTypes : TYPES;
        return `
            <div class="mutator-modal-backdrop"><section class="mutator-modal ${deleting ? "is-danger" : ""}" role="dialog" aria-modal="true">
                <header><div><small>SCHEMA CHANGE</small><h2>${deleting ? "Удалить поле" : changing ? "Поменять тип" : "Добавить поле"}</h2></div><button id="mutator-close-schema" type="button">×</button></header>
                ${deleting ? `<p>Колонка <code>${h(dialog.name)}</code> будет удалена из схемы, если это совместимо с текущими данными.</p>` : `
                    <label class="mutator-modal-field"><span>Название</span><input id="mutator-column-name" value="${h(dialog.name || "")}" ${changing ? "disabled" : ""}></label>
                    <div class="mutator-modal-grid"><label class="mutator-modal-field"><span>Тип данных</span><select id="mutator-column-type">${availableTypes.map((type) => `<option value="${type}" ${dialog.baseType === type ? "selected" : ""}>${type}</option>`).join("")}</select><small>${changing ? h(dialog.policyReason) : ""}</small></label>
                    <label class="mutator-modal-check"><input id="mutator-column-optional" type="checkbox" ${dialog.optional ? "checked" : ""} ${(dialog.baseType === "yson" || dialog.optionalLocked) ? "disabled" : ""}><span><strong>Optional</strong><small>${dialog.optionalLocked ? "Optional уже включён и не может быть выключен" : "Можно включить; обратный переход требует миграции данных"}</small></span></label></div>
                `}
                <p class="mutator-dialog-error">${h(dialog.error || "")}</p>
                <footer><button class="button button--secondary" id="mutator-cancel-schema" type="button">Отменить</button><button class="button ${deleting ? "button--danger" : ""}" id="mutator-plan-schema" type="button" ${dialog.planning ? "disabled" : ""}>${dialog.planning ? "Проверяю…" : "Проверить изменение"}</button></footer>
            </section></div>`;
    }

    function planDetails(plan) {
        return `<div class="mutator-confirm-plan">${plan.steps.map((step) => `<p><span>✓</span>${h(step)}</p>`).join("")}${plan.requiresUnmount ? `<p><span>↕</span>Отмонтировать таблицу, применить схему и вернуть состояние</p>` : ""}${plan.replicaCount ? `<p><span>R</span>Применить схему к ${plan.replicaCount} физическим репликам</p>` : ""}${plan.warnings.map((warning) => `<p class="is-warning"><span>!</span>${h(warning)}</p>`).join("")}</div>`;
    }

    function confirmDialog() {
        const confirm = state.confirm;
        if (!confirm) return "";
        return `
            <div class="mutator-modal-backdrop"><section class="mutator-confirm ${confirm.danger ? "is-danger" : ""}" role="alertdialog" aria-modal="true">
                <span class="mutator-confirm-icon">${confirm.danger ? "!" : "✓"}</span><h2>${h(confirm.title)}</h2><p>${h(confirm.description)}</p>${confirm.details || ""}
                <footer><button class="button button--secondary" id="mutator-cancel-confirm" type="button">Отменить</button><button class="button ${confirm.danger ? "button--danger" : ""}" id="mutator-accept-confirm" type="button" ${state.acting ? "disabled" : ""}>${state.acting ? "Выполняется…" : h(confirm.label)}</button></footer>
            </section></div>`;
    }

    function bodyView() {
        if (!state.info) return emptyState();
        return `${identity()}${commandPanel()}${state.mode === "alter" ? alterView() : dataView()}`;
    }

    function render() {
        root.innerHTML = `${pageHeader()}${modeSwitch()}${searchCard()}<div class="mutator-results">${bodyView()}</div>${schemaDialog()}${confirmDialog()}`;
        bindEvents();
    }

    function resetTableState() {
        state.info = null;
        state.searchResult = null;
        state.stagedUpdates = {};
        state.stagedDeletes = new Set();
        state.insertRows = [];
        state.filterErrors = {};
        state.command = null;
    }

    async function inspectTable() {
        if (!localStorage.getItem(services.tokenKey)) { services.showTokenModal("yt"); return; }
        state.controller?.abort();
        state.controller = new AbortController();
        state.loading = true;
        state.error = "";
        resetTableState();
        render();
        try {
            const info = await services.apiFetch("/api/yt/mutator/inspect", {
                method: "POST", headers: headers(), signal: state.controller.signal,
                body: JSON.stringify({ cluster: state.cluster, path: state.path.trim() }),
            });
            state.info = info;
            state.searchMode = info.keyColumns.length ? "keys" : "where";
            state.keyFilters = Object.fromEntries(info.keyColumns.map((column) => [column.name, { enabled: false, negated: false, operator: "=", value: "" }]));
        } catch (error) {
            if (error.name !== "AbortError") state.error = error.message;
        } finally {
            state.loading = false;
            state.controller = null;
            render();
        }
    }

    function openSchemaDialog(action, column = null) {
        const policy = column?.policy || {};
        state.schemaDialog = {
            action,
            name: column?.name || "",
            baseType: column?.baseType && TYPES.includes(column.baseType) ? column.baseType : "string",
            optional: column?.baseType === "yson" ? true : (column?.optional ?? true),
            optionalLocked: action === "change_type" && Boolean(column?.optional),
            allowedTypes: action === "change_type"
                ? (policy.allowedTargetTypes || [column?.baseType]).filter((type) => TYPES.includes(type))
                : TYPES,
            policyReason: policy.changeReason || "",
            error: "",
            planning: false,
        };
        render();
    }

    function schemaChangeFromDialog() {
        const dialog = state.schemaDialog;
        if (dialog.action === "delete") return { action: "delete", name: dialog.name };
        const name = document.getElementById("mutator-column-name").value.trim();
        const baseType = document.getElementById("mutator-column-type").value;
        const optional = baseType === "yson" || document.getElementById("mutator-column-optional").checked;
        return { action: dialog.action, name, baseType, optional };
    }

    async function planSchemaChange() {
        const change = schemaChangeFromDialog();
        state.schemaDialog.planning = true;
        state.schemaDialog.error = "";
        render();
        try {
            const plan = await services.apiFetch("/api/yt/mutator/schema/plan", {
                method: "POST", headers: headers(),
                body: JSON.stringify({ cluster: state.info.cluster, path: state.info.path, change }),
            });
            state.pendingSchema = { change, plan };
            state.schemaDialog = null;
            state.confirm = {
                kind: "schema", title: "Применить изменение схемы?",
                description: `${state.info.cluster}: ${state.info.path}`,
                label: "Применить", danger: change.action === "delete", details: planDetails(plan),
            };
        } catch (error) {
            state.schemaDialog.planning = false;
            state.schemaDialog.error = error.message;
        }
        render();
    }

    async function planStrictChange(strict) {
        state.strictPlanning = true;
        render();
        const change = { action: "set_strict", strict };
        try {
            const plan = await services.apiFetch("/api/yt/mutator/schema/plan", {
                method: "POST", headers: headers(),
                body: JSON.stringify({ cluster: state.info.cluster, path: state.info.path, change }),
            });
            state.pendingSchema = { change, plan };
            state.confirm = {
                kind: "schema",
                title: strict ? "Включить strict-схему?" : "Отключить strict-схему?",
                description: `${state.info.cluster}: ${state.info.path}`,
                label: "Применить",
                danger: !strict,
                details: planDetails(plan),
            };
        } catch (error) {
            services.showToast(error.message, "error");
        }
        state.strictPlanning = false;
        render();
    }

    async function applySchema() {
        state.acting = true; render();
        try {
            const result = await services.apiFetch("/api/yt/mutator/schema/apply", {
                method: "POST", headers: headers(),
                body: JSON.stringify({
                    confirmed: true, cluster: state.info.cluster, path: state.info.path,
                    expectedSchemaHash: state.info.schemaHash, change: state.pendingSchema.change,
                }),
            });
            state.command = result.command;
            state.confirm = null; state.pendingSchema = null; state.acting = false;
            services.showToast("Схема таблицы обновлена");
            await refreshInfo();
        } catch (error) {
            state.acting = false; state.confirm = null; services.showToast(error.message, "error"); render();
        }
    }

    async function refreshInfo() {
        try {
            state.info = await services.apiFetch("/api/yt/mutator/inspect", {
                method: "POST", headers: headers(),
                body: JSON.stringify({ cluster: state.cluster, path: state.path.trim() }),
            });
        } catch (error) { state.error = error.message; }
        render();
    }

    function filterPayload() {
        return state.info.keyColumns.map((column) => {
            const filter = state.keyFilters[column.name];
            const values = ["IN", "BETWEEN"].includes(filter.operator)
                ? filter.value.split(",").map((item) => item.trim()).filter((item) => item !== "")
                : [filter.value.trim()];
            return { name: column.name, enabled: filter.enabled, negated: filter.negated, operator: filter.operator, values };
        });
    }

    function sanitizeFilterValue(column, raw) {
        if (INTEGER_RANGES[column.baseType]) {
            const signed = INTEGER_RANGES[column.baseType][0] < 0n;
            return raw.replace(signed ? /[^0-9,\-\s]/g : /[^0-9,\s]/g, "");
        }
        if (["double", "float"].includes(column.baseType)) {
            return raw.replace(/[^0-9eE+.,\-\s]/g, "");
        }
        if (column.baseType === "uuid") {
            return raw.replace(/[^0-9a-fA-F,\-\s]/g, "");
        }
        return raw;
    }

    function filterValues(filter) {
        if (["IN", "BETWEEN"].includes(filter.operator)) {
            return filter.value.split(",").map((item) => item.trim()).filter((item) => item !== "");
        }
        return [filter.value.trim()];
    }

    function validateKeyFilters() {
        const errors = {};
        state.info.keyColumns.forEach((column) => {
            const filter = state.keyFilters[column.name];
            if (!filter?.enabled) return;
            const values = filterValues(filter);
            if (!filter.value.trim()) {
                errors[column.name] = "Заполните значение условия";
                return;
            }
            if (filter.operator === "BETWEEN" && values.length !== 2) {
                errors[column.name] = "BETWEEN требует ровно два значения";
                return;
            }
            if (filter.operator === "IN" && values.length === 0) {
                errors[column.name] = "IN требует хотя бы одно значение";
                return;
            }
            for (const value of values) {
                const error = validateCell(column, cell(value, false));
                if (error) {
                    errors[column.name] = error;
                    return;
                }
            }
        });
        state.filterErrors = errors;
        return Object.keys(errors).length === 0;
    }

    async function runSearch({ preserveCommand = false } = {}) {
        if (state.searchMode === "keys" && !validateKeyFilters()) {
            services.showToast("Исправьте значения ключевых фильтров", "error");
            render();
            return;
        }
        state.searching = true; state.searchResult = null; state.stagedUpdates = {}; state.stagedDeletes = new Set(); render();
        try {
            const result = await services.apiFetch("/api/yt/mutator/data/search", {
                method: "POST", headers: headers(),
                body: JSON.stringify({
                    cluster: state.info.cluster, path: state.info.path, mode: state.searchMode,
                    filters: filterPayload(), where: state.where, limit: state.limit, applyLimit: state.applyLimit,
                }),
            });
            state.searchResult = result;
            if (!preserveCommand) state.command = result.command;
        } catch (error) { services.showToast(error.message, "error"); }
        state.searching = false; render();
    }

    function validateCell(column, valueCell) {
        if (valueCell.isNull || valueCell.value === "") return column.optional ? "" : "Обязательное значение";
        const value = valueCell.value.trim();
        if (INTEGER_RANGES[column.baseType]) {
            try {
                const number = BigInt(value); const [minimum, maximum] = INTEGER_RANGES[column.baseType];
                if (number < minimum || number > maximum) return `Диапазон: ${minimum}…${maximum}`;
            } catch { return "Ожидается целое число"; }
        }
        if (["double", "float"].includes(column.baseType) && !Number.isFinite(Number(value))) return "Ожидается конечное число";
        if (column.baseType === "bool" && !["true", "false"].includes(value.toLowerCase())) return "Только true или false";
        if (column.baseType === "json") { try { JSON.parse(value); } catch { return "Некорректный JSON"; } }
        if (column.baseType === "uuid" && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) return "Некорректный UUID";
        return "";
    }

    function captureEditedRow(index) {
        const row = state.searchResult.rows[index];
        const changes = {};
        let firstError = "";
        state.info.valueColumns.forEach((column) => {
            const input = root.querySelector(`[data-cell-input][data-edit-row="${index}"][data-edit-column="${CSS.escape(column.name)}"]`);
            const nullInput = root.querySelector(`[data-cell-null][data-edit-row="${index}"][data-edit-column="${CSS.escape(column.name)}"]`);
            const valueCell = cell(input?.value || "", Boolean(nullInput?.checked));
            const error = validateCell(column, valueCell);
            if (error && !firstError) firstError = `${column.name}: ${error}`;
            if (JSON.stringify(valueCell) !== JSON.stringify(row.values[column.name])) changes[column.name] = valueCell;
        });
        if (firstError) { services.showToast(firstError, "error"); return; }
        if (Object.keys(changes).length) state.stagedUpdates[index] = changes;
        else delete state.stagedUpdates[index];
        state.editingIndex = null; render();
    }

    function rowKeys(index) {
        const row = state.searchResult.rows[index];
        return Object.fromEntries(state.info.keyColumns.map((column) => [column.name, row.values[column.name]]));
    }

    function batchPayload() {
        return {
            deletes: [...state.stagedDeletes].map(rowKeys),
            updates: Object.entries(state.stagedUpdates).map(([index, changes]) => ({ keys: rowKeys(Number(index)), changes })),
        };
    }

    async function applyBatch() {
        state.acting = true; render();
        try {
            const payload = batchPayload();
            const result = await services.apiFetch("/api/yt/mutator/data/apply", {
                method: "POST", headers: headers(), body: JSON.stringify({
                    confirmed: true, cluster: state.info.cluster, path: state.info.path,
                    expectedSchemaHash: state.info.schemaHash, ...payload,
                }),
            });
            state.command = result.command; state.confirm = null; state.acting = false;
            services.showToast(`Обновлено: ${result.updated}, удалено: ${result.deleted}`);
            await runSearch({ preserveCommand: true });
        } catch (error) { state.acting = false; state.confirm = null; services.showToast(error.message, "error"); render(); }
    }

    function captureInsertInputs() {
        state.insertRows.forEach((row, index) => state.info.columns.forEach((column) => {
            const selector = `[data-insert-row="${index}"][data-insert-column="${CSS.escape(column.name)}"]`;
            const input = root.querySelector(`[data-cell-input]${selector}`);
            const nullInput = root.querySelector(`[data-cell-null]${selector}`);
            if (input) row[column.name] = cell(input.value, Boolean(nullInput?.checked));
        }));
    }

    function validateInsertRowsLocal() {
        captureInsertInputs();
        const errors = {};
        state.insertRows.forEach((row, index) => state.info.columns.forEach((column) => {
            const error = validateCell(column, row[column.name]);
            if (error) (errors[index] ||= {})[column.name] = error;
        }));
        state.insertErrors = errors;
        return Object.keys(errors).length === 0;
    }

    async function prepareInsert() {
        if (!validateInsertRowsLocal()) { services.showToast("Исправьте значения в записях", "error"); render(); return; }
        try {
            const validation = await services.apiFetch("/api/yt/mutator/data/validate-inserts", {
                method: "POST", headers: headers(), body: JSON.stringify({ cluster: state.info.cluster, path: state.info.path, rows: state.insertRows }),
            });
            if (!validation.valid) {
                validation.duplicateIndexes.forEach((index) => (state.insertErrors[index] ||= {})._row = "Ключ повторяется в текущем пакете");
                validation.existingIndexes.forEach((index) => (state.insertErrors[index] ||= {})._row = "Такой ключ уже есть в таблице");
                services.showToast("Есть повторяющиеся или уже существующие ключи", "error"); render(); return;
            }
            state.confirm = {
                kind: "insert", title: `Опубликовать ${state.insertRows.length} записей?`,
                description: "Ключи будут ещё раз проверены непосредственно перед insert_rows.",
                label: "Опубликовать", danger: false,
                details: `<div class="mutator-confirm-plan"><p><span>+</span>Новых записей: ${state.insertRows.length}</p></div>`,
            };
            render();
        } catch (error) { services.showToast(error.message, "error"); }
    }

    async function publishInsert() {
        state.acting = true; render();
        try {
            const result = await services.apiFetch("/api/yt/mutator/data/insert", {
                method: "POST", headers: headers(), body: JSON.stringify({
                    confirmed: true, cluster: state.info.cluster, path: state.info.path,
                    expectedSchemaHash: state.info.schemaHash, rows: state.insertRows,
                }),
            });
            state.command = result.command; state.insertRows = []; state.insertErrors = {}; state.confirm = null; state.acting = false;
            services.showToast(`Добавлено записей: ${result.inserted}`); render();
        } catch (error) { state.acting = false; state.confirm = null; services.showToast(error.message, "error"); render(); }
    }

    function bindEvents() {
        document.getElementById("mutator-table-form")?.addEventListener("submit", (event) => { event.preventDefault(); state.path = document.getElementById("mutator-path").value; inspectTable(); });
        document.getElementById("mutator-path")?.addEventListener("input", (event) => { state.path = event.target.value; });
        document.getElementById("mutator-cluster")?.addEventListener("change", (event) => { state.cluster = event.target.value; localStorage.setItem("dev-toolbox:yt-cluster", state.cluster); resetTableState(); render(); });
        document.getElementById("mutator-stop")?.addEventListener("click", () => { state.controller?.abort(); state.loading = false; state.error = "Загрузка остановлена"; render(); });
        root.querySelectorAll("[data-mutator-mode]").forEach((button) => button.addEventListener("click", () => { state.mode = button.dataset.mutatorMode; render(); }));
        root.querySelectorAll("[data-data-mode]").forEach((button) => button.addEventListener("click", () => { state.dataMode = button.dataset.dataMode; render(); }));
        document.getElementById("mutator-add-column")?.addEventListener("click", () => openSchemaDialog("add"));
        root.querySelectorAll("[data-schema-change]").forEach((button) => button.addEventListener("click", () => openSchemaDialog("change_type", state.info.columns.find((column) => column.name === button.dataset.schemaChange))));
        root.querySelectorAll("[data-schema-delete]").forEach((button) => button.addEventListener("click", () => openSchemaDialog("delete", state.info.columns.find((column) => column.name === button.dataset.schemaDelete))));
        document.getElementById("mutator-strict-toggle")?.addEventListener("change", (event) => planStrictChange(event.target.checked));
        document.getElementById("mutator-close-schema")?.addEventListener("click", () => { state.schemaDialog = null; render(); });
        document.getElementById("mutator-cancel-schema")?.addEventListener("click", () => { state.schemaDialog = null; render(); });
        document.getElementById("mutator-column-type")?.addEventListener("change", (event) => {
            const nameInput = document.getElementById("mutator-column-name");
            const optionalInput = document.getElementById("mutator-column-optional");
            state.schemaDialog.name = nameInput?.value.trim() || state.schemaDialog.name;
            state.schemaDialog.optional = Boolean(optionalInput?.checked);
            state.schemaDialog.baseType = event.target.value;
            if (event.target.value === "yson") state.schemaDialog.optional = true;
            render();
        });
        document.getElementById("mutator-plan-schema")?.addEventListener("click", planSchemaChange);
        root.querySelectorAll("[data-search-mode]").forEach((button) => button.addEventListener("click", () => { state.searchMode = button.dataset.searchMode; state.filterErrors = {}; render(); }));
        root.querySelectorAll("[data-filter-enabled]").forEach((input) => input.addEventListener("change", () => { state.keyFilters[input.dataset.filterEnabled].enabled = input.checked; delete state.filterErrors[input.dataset.filterEnabled]; input.closest(".mutator-filter-row").classList.toggle("is-enabled", input.checked); }));
        root.querySelectorAll("[data-filter-negated]").forEach((input) => input.addEventListener("change", () => { state.keyFilters[input.dataset.filterNegated].negated = input.checked; }));
        root.querySelectorAll("[data-filter-operator]").forEach((select) => select.addEventListener("change", () => { state.keyFilters[select.dataset.filterOperator].operator = select.value; delete state.filterErrors[select.dataset.filterOperator]; render(); }));
        root.querySelectorAll("[data-filter-value]").forEach((input) => input.addEventListener("input", () => {
            const name = input.dataset.filterValue;
            const column = state.info.keyColumns.find((item) => item.name === name);
            const value = sanitizeFilterValue(column, input.value);
            input.value = value;
            state.keyFilters[name].value = value;
            delete state.filterErrors[name];
            input.closest(".mutator-filter-row")?.classList.remove("has-error");
        }));
        document.getElementById("mutator-where")?.addEventListener("input", (event) => { state.where = event.target.value; });
        document.getElementById("mutator-limit")?.addEventListener("input", (event) => { state.limit = Number(event.target.value) || 10; });
        document.getElementById("mutator-apply-limit")?.addEventListener("change", (event) => { state.applyLimit = event.target.checked; });
        root.querySelectorAll("[data-copy-column]").forEach((button) => button.addEventListener("click", async () => { try { await navigator.clipboard.writeText(`[${button.dataset.copyColumn}]`); services.showToast("Имя поля скопировано"); } catch { services.showToast("Не удалось скопировать", "error"); } }));
        document.getElementById("mutator-run-search")?.addEventListener("click", runSearch);
        root.querySelectorAll("[data-start-edit]").forEach((button) => button.addEventListener("click", () => { state.editingIndex = Number(button.dataset.startEdit); render(); }));
        root.querySelectorAll("[data-cancel-edit]").forEach((button) => button.addEventListener("click", () => { state.editingIndex = null; render(); }));
        root.querySelectorAll("[data-save-edit]").forEach((button) => button.addEventListener("click", () => captureEditedRow(Number(button.dataset.saveEdit))));
        root.querySelectorAll("[data-toggle-delete]").forEach((button) => button.addEventListener("click", () => { const index = Number(button.dataset.toggleDelete); if (state.stagedDeletes.has(index)) state.stagedDeletes.delete(index); else { state.stagedDeletes.add(index); delete state.stagedUpdates[index]; } render(); }));
        root.querySelectorAll("[data-reset-row]").forEach((button) => button.addEventListener("click", () => { delete state.stagedUpdates[Number(button.dataset.resetRow)]; render(); }));
        document.getElementById("mutator-reset-batch")?.addEventListener("click", () => { state.stagedUpdates = {}; state.stagedDeletes = new Set(); render(); });
        document.getElementById("mutator-apply-batch")?.addEventListener("click", () => { const payload = batchPayload(); state.confirm = { kind: "batch", title: "Применить пакет изменений?", description: "Все изменения будут выполнены в одной tablet-транзакции.", label: "Применить", danger: payload.deletes.length > 0, details: `<div class="mutator-confirm-plan"><p><span>✎</span>Обновить строк: ${payload.updates.length}</p><p class="is-warning"><span>⌫</span>Удалить строк: ${payload.deletes.length}</p></div>` }; render(); });
        root.querySelectorAll("[data-add-record]").forEach((button) => button.addEventListener("click", () => { captureInsertInputs(); state.insertRows.push(emptyInsertCells()); render(); }));
        root.querySelectorAll("[data-copy-insert]").forEach((button) => button.addEventListener("click", () => { captureInsertInputs(); state.insertRows.splice(Number(button.dataset.copyInsert) + 1, 0, clone(state.insertRows[Number(button.dataset.copyInsert)])); render(); }));
        root.querySelectorAll("[data-delete-insert]").forEach((button) => button.addEventListener("click", () => { state.insertRows.splice(Number(button.dataset.deleteInsert), 1); render(); }));
        document.getElementById("mutator-publish-records")?.addEventListener("click", prepareInsert);
        document.getElementById("mutator-cancel-confirm")?.addEventListener("click", () => { if (!state.acting) { state.confirm = null; render(); } });
        document.getElementById("mutator-accept-confirm")?.addEventListener("click", () => { if (state.confirm.kind === "schema") applySchema(); else if (state.confirm.kind === "batch") applyBatch(); else publishInsert(); });
    }

    function renderMutator(target, providedServices) { root = target; services = providedServices; state = initialState(); render(); }
    function destroy() { state?.controller?.abort(); }
    return { render: renderMutator, destroy };
})();
