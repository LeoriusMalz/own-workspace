"use strict";

window.YtCreator = (() => {
    const TYPE_OPTIONS = [
        ["int64", "Int64"], ["int32", "Int32"], ["int16", "Int16"], ["int8", "Int8"],
        ["uint64", "Uint64"], ["uint32", "Uint32"], ["uint16", "Uint16"], ["uint8", "Uint8"],
        ["double", "Double"], ["float", "Float"], ["bool", "Boolean"],
        ["string", "String / bytes"], ["utf8", "UTF-8"], ["json", "JSON"], ["yson", "YSON"], ["uuid", "UUID"],
        ["date", "Date"], ["datetime", "Datetime"], ["timestamp", "Timestamp"], ["interval", "Interval"],
        ["date32", "Date32"], ["datetime64", "Datetime64"], ["timestamp64", "Timestamp64"], ["interval64", "Interval64"],
        ["null", "Null"], ["void", "Void"],
    ];

    const CODECS = {
        none: { label: "none", description: "Без сжатия: минимальная нагрузка на CPU, максимальный объём данных." },
        snappy: { label: "snappy", description: "Очень быстрое сжатие и распаковка с умеренной экономией места." },
        lz4: { label: "lz4", description: "Быстрый стандартный кодек YTsaurus, хороший выбор для часто используемых данных." },
        lz4_high_compression: { label: "lz4 high compression", description: "Усиленное LZ4: сжимает лучше обычного LZ4, но работает медленнее." },
        zstd: { label: "zstd", min: 1, max: 21, description: "Современный баланс скорости и степени сжатия. Чем выше уровень, тем медленнее запись и меньше объём." },
        zlib: { label: "zlib", min: 1, max: 9, description: "Надёжное сильное сжатие, но заметно медленнее LZ4." },
        brotli: { label: "brotli", min: 1, max: 11, description: "Высокая степень сжатия для редко меняющихся данных. Уровни 3, 5 и 8 обычно дают хороший баланс." },
        lzma: { label: "lzma", min: 0, max: 9, description: "Очень сильное, но медленное и ресурсоёмкое сжатие." },
        bzip2: { label: "bzip2", min: 1, max: 9, description: "Сильное классическое сжатие, обычно медленнее современных кодеков." },
    };

    let root;
    let services;
    let state;

    function initialState() {
        return {
            resourceMode: "table",
            tableKind: "static",
            replicated: false,
            cluster: localStorage.getItem("dev-toolbox:yt-cluster") || "miranda",
            replicaTargets: ["jupiter", "saturn"],
            path: "",
            pathCheck: null,
            pathChecking: false,
            enableTracker: true,
            minSync: 1,
            maxSync: 1,
            preferredClusters: ["jupiter"],
            bundle: "vkvideo",
            advancedEnabled: false,
            optimizeFor: "scan",
            codecFamily: "zstd",
            codecLevel: 5,
            replicationFactor: 3,
            columns: [],
            schema: [],
            schemaMode: "friendly",
            schemaText: { yson: "[]", json: "[]" },
            textValid: true,
            textError: "",
            friendlyConvertible: true,
            friendlyReason: "",
            parsedFriendlyColumns: [],
            textRevision: 0,
            textTimer: null,
            editingColumnIndex: null,
            confirmOpen: false,
            creating: false,
            created: [],
            creationPartial: false,
            directoryPath: "",
            directoryCheck: null,
            directoryChecking: false,
            directoryConfirmOpen: false,
            directoryCreating: false,
            directoryCreated: [],
            directoryCreationPartial: false,
            directoryInheritAcl: true,
            directoryAnnotation: "",
        };
    }

    function h(value) {
        return services.escapeHtml(value);
    }

    function codecValue() {
        const codec = CODECS[state.codecFamily];
        return codec?.min === undefined ? state.codecFamily : `${state.codecFamily}_${state.codecLevel}`;
    }

    function toYson(value, indent = 0) {
        const padding = "  ".repeat(indent);
        const nextPadding = "  ".repeat(indent + 1);
        if (value === null) return "#";
        if (value === true) return "%true";
        if (value === false) return "%false";
        if (typeof value === "number") return String(value);
        if (typeof value === "string") return JSON.stringify(value);
        if (Array.isArray(value)) {
            if (!value.length) return "[]";
            return `[\n${value.map((item) => `${nextPadding}${toYson(item, indent + 1)};`).join("\n")}\n${padding}]`;
        }
        const entries = Object.entries(value);
        if (!entries.length) return "{}";
        return `{\n${entries.map(([key, item]) => `${nextPadding}${key} = ${toYson(item, indent + 1)};`).join("\n")}\n${padding}}`;
    }

    function uiColumnsToSchema() {
        return state.columns.map((column) => {
            const typeV3 = column.optional
                ? { type_name: "optional", item: column.type }
                : column.type;
            let type = column.type === "yson" && column.optional ? "any" : column.type;
            type = column.type === "bool" ? "boolean" : type;
            return {
                name: column.name,
                required: !column.optional,
                type,
                type_v3: typeV3,
                ...(column.sortOrder === "asc" ? { sort_order: "ascending" } : {}),
                ...(column.sortOrder === "desc" ? { sort_order: "descending" } : {}),
            };
        });
    }

    function normalizeColumnOrder() {
        state.columns = [
            ...state.columns.filter((column) => column.sortOrder !== "none"),
            ...state.columns.filter((column) => column.sortOrder === "none"),
        ];
    }

    function syncSchemaFromColumns() {
        normalizeColumnOrder();
        state.schema = uiColumnsToSchema();
        state.schemaText.json = JSON.stringify(state.schema, null, 2);
        state.schemaText.yson = toYson(state.schema);
        state.textValid = true;
        state.textError = "";
        state.friendlyConvertible = true;
        state.friendlyReason = "";
        state.parsedFriendlyColumns = state.columns.map((column) => ({ ...column }));
    }

    function creatorPayload(extra = {}) {
        return {
            tableKind: state.tableKind,
            replicated: state.replicated,
            cluster: state.replicated ? "miranda" : state.cluster,
            replicaTargets: state.replicated ? state.replicaTargets : [],
            path: state.path.trim(),
            schema: state.schema,
            bundle: state.bundle.trim(),
            advancedEnabled: state.advancedEnabled,
            optimizeFor: state.optimizeFor,
            compressionCodec: codecValue(),
            replicationFactor: state.replicationFactor,
            enableReplicatedTableTracker: state.enableTracker,
            minSyncReplicaCount: state.minSync,
            maxSyncReplicaCount: state.maxSync,
            preferredSyncReplicaClusters: state.preferredClusters,
            leaveUnmounted: true,
            ...extra,
        };
    }

    function currentPathSignature() {
        return JSON.stringify({
            path: state.path.trim(),
            cluster: state.replicated ? "miranda" : state.cluster,
            replicated: state.replicated,
            targets: state.replicaTargets,
        });
    }

    function currentDirectorySignature() {
        return JSON.stringify({
            path: state.directoryPath.trim(),
            cluster: state.cluster,
        });
    }

    function invalidatePathCheck() {
        state.pathCheck = null;
        state.created = [];
        state.creationPartial = false;
    }

    function invalidateDirectoryCheck() {
        state.directoryCheck = null;
        state.directoryCreated = [];
        state.directoryCreationPartial = false;
    }

    function hasSortedColumn() {
        return state.schema.some((column) => Boolean(column.sort_order));
    }

    function creatorErrors() {
        const errors = [];
        if (!state.path.trim()) errors.push("Укажите путь таблицы");
        if (!state.pathCheck?.valid || state.pathCheck.signature !== currentPathSignature()) {
            errors.push("Сначала успешно проверьте путь");
        }
        if (!state.textValid) errors.push("Исправьте текст схемы");
        if (state.tableKind === "dynamic" && !hasSortedColumn()) {
            errors.push("Добавьте хотя бы одну сортированную колонку");
        }
        if (state.tableKind === "dynamic" && !state.bundle.trim()) errors.push("Укажите tablet cell bundle");
        if (state.replicated) {
            if (!state.replicaTargets.length) errors.push("Выберите кластер для реплики");
            if (state.minSync > state.maxSync) errors.push("min sync не может быть больше max sync");
            if (state.preferredClusters.length > state.maxSync) errors.push("Слишком много preferred-кластеров");
        }
        return errors;
    }

    function finalTableLabel() {
        if (state.replicated) return "Создать реплицированную таблицу";
        return state.tableKind === "dynamic" ? "Создать динамическую таблицу" : "Создать статическую таблицу";
    }

    function typeOptions(selected) {
        return TYPE_OPTIONS.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${h(label)} · ${value}</option>`).join("");
    }

    function renderReplicaSettings() {
        if (!state.replicated) return "";
        const targetsMode = state.replicaTargets.length === 2 ? "both" : state.replicaTargets[0];
        return `
            <section class="creator-card creator-card--nested">
                <div class="creator-section-heading"><div><span class="creator-step">02</span><h2>Репликация</h2></div><span class="creator-required">обязательно</span></div>
                <div class="creator-grid creator-grid--2">
                    <label class="creator-field">
                        <span>Куда создать реплики</span>
                        <select id="creator-replica-targets">
                            <option value="both" ${targetsMode === "both" ? "selected" : ""}>Jupiter и Saturn</option>
                            <option value="jupiter" ${targetsMode === "jupiter" ? "selected" : ""}>Только Jupiter</option>
                            <option value="saturn" ${targetsMode === "saturn" ? "selected" : ""}>Только Saturn</option>
                        </select>
                    </label>
                    <label class="creator-switch-field">
                        <span><strong>Replicated table tracker</strong><small>Автоматически переключает sync/async-режимы реплик.</small></span>
                        <input id="creator-tracker" type="checkbox" ${state.enableTracker ? "checked" : ""}>
                    </label>
                </div>
                <div class="creator-grid creator-grid--2 creator-replication-counts">
                    <label class="creator-field" title="Минимальное число синхронных реплик, которое должен поддерживать tracker.">
                        <span>min_sync_replica_count</span>
                        <input id="creator-min-sync" type="number" min="0" max="${state.replicaTargets.length}" value="${state.minSync}">
                    </label>
                    <label class="creator-field" title="Максимальное число синхронных реплик. Оно также ограничивает число preferred-кластеров.">
                        <span>max_sync_replica_count</span>
                        <input id="creator-max-sync" type="number" min="1" max="${state.replicaTargets.length}" value="${state.maxSync}">
                    </label>
                </div>
                <fieldset class="creator-fieldset">
                    <legend>preferred_sync_replica_clusters <span class="creator-help" title="Кластеры, которые tracker будет предпочитать при выборе синхронных реплик. Можно выбрать не больше max_sync_replica_count.">?</span></legend>
                    <div class="creator-check-row">
                        ${state.replicaTargets.map((cluster) => `
                            <label class="creator-check-chip">
                                <input type="checkbox" data-preferred-cluster="${cluster}" ${state.preferredClusters.includes(cluster) ? "checked" : ""}>
                                <span>${cluster}</span>
                            </label>
                        `).join("")}
                    </div>
                    <p class="creator-inline-hint">Выбрано ${state.preferredClusters.length} из ${state.maxSync}</p>
                </fieldset>
            </section>
        `;
    }

    function renderAdvancedSettings() {
        if (!state.advancedEnabled) return "";
        const codec = CODECS[state.codecFamily];
        return `
            <div class="creator-advanced-panel">
                <label class="creator-field" title="lookup оптимизирован для точечных чтений по ключу; scan — для последовательного чтения больших диапазонов.">
                    <span>Optimize for <span class="creator-help">?</span></span>
                    <select id="creator-optimize-for">
                        <option value="scan" ${state.optimizeFor === "scan" ? "selected" : ""}>scan</option>
                        <option value="lookup" ${state.optimizeFor === "lookup" ? "selected" : ""}>lookup</option>
                    </select>
                </label>
                <label class="creator-field" title="${h(codec.description)}">
                    <span>Compression <span class="creator-help">?</span></span>
                    <select id="creator-codec-family">
                        ${Object.entries(CODECS).map(([value, item]) => `<option value="${value}" ${value === state.codecFamily ? "selected" : ""} title="${h(item.description)}">${h(item.label)}</option>`).join("")}
                    </select>
                </label>
                ${codec.min !== undefined ? `
                    <label class="creator-field" title="Чем выше уровень, тем сильнее сжатие и выше затраты CPU при записи.">
                        <span>Уровень</span>
                        <input id="creator-codec-level" type="number" min="${codec.min}" max="${codec.max}" value="${state.codecLevel}">
                    </label>
                ` : ""}
                <label class="creator-field" title="Количество копий каждого обычного чанка на разных узлах и стойках. Значение 3 — стандартный баланс надёжности и объёма.">
                    <span>Number of replicas <span class="creator-help">?</span></span>
                    <input id="creator-replication-factor" type="number" min="1" max="10" value="${state.replicationFactor}">
                </label>
            </div>
        `;
    }

    function renderPathStatus() {
        if (state.pathChecking) return `<div class="creator-validation creator-validation--loading"><span class="button__spinner"></span> Проверяю директории и целевые пути…</div>`;
        if (!state.pathCheck) return "";
        return `
            <div class="creator-validation ${state.pathCheck.valid ? "creator-validation--success" : "creator-validation--error"}">
                <strong>${state.pathCheck.valid ? "Путь готов к созданию" : "Создание по этому пути невозможно"}</strong>
                <div class="creator-check-results">
                    ${state.pathCheck.checks.map((check) => `
                        <div class="creator-check-result">
                            <span>${h(check.cluster)}</span>
                            ${check.targetExists ? `<em>таблица или нода уже существует</em>` : ""}
                            ${check.missingDirectories.length ? `<em>нет директорий: ${h(check.missingDirectories.join(", "))}</em>` : ""}
                            ${check.nonDirectoryNodes.length ? `<em>часть пути не является папкой: ${h(check.nonDirectoryNodes.join(", "))}</em>` : ""}
                            ${check.ok ? `<em class="is-ok">всё в порядке</em>` : ""}
                        </div>
                    `).join("")}
                </div>
            </div>
        `;
    }

    function columnCard(column, index) {
        const sortLabel = column.sortOrder === "asc" ? "ASC" : column.sortOrder === "desc" ? "DESC" : "без сортировки";
        return `
            <article class="creator-column-card" draggable="true" data-column-index="${index}" tabindex="0">
                <span class="creator-drag-handle" title="Перетащить">⠿</span>
                <span class="creator-column-order">${index + 1}</span>
                <span class="creator-column-copy">
                    <strong>${h(column.name)}</strong>
                    <span><code>${h(column.type)}</code> · ${column.optional ? "optional" : "required"} · ${sortLabel}</span>
                </span>
                ${column.sortOrder !== "none" ? `<span class="creator-key-badge">KEY</span>` : ""}
                <span class="creator-column-actions">
                    <button type="button" data-copy-column="${index}" title="Копировать">⧉</button>
                    <button type="button" data-delete-column="${index}" title="Удалить">×</button>
                </span>
            </article>
        `;
    }

    function renderSchemaEditor() {
        const dynamicKeyError = state.tableKind === "dynamic" && !hasSortedColumn();
        const friendlyTitle = state.friendlyConvertible ? "Визуальный редактор" : state.friendlyReason;
        return `
            <section class="creator-card">
                <div class="creator-section-heading creator-section-heading--schema">
                    <div><span class="creator-step">04</span><h2>Схема таблицы</h2></div>
                    <div class="creator-schema-tabs" role="tablist">
                        <button type="button" data-schema-mode="friendly" class="${state.schemaMode === "friendly" ? "is-active" : ""} ${state.friendlyConvertible ? "" : "is-disabled"}" aria-disabled="${!state.friendlyConvertible}" title="${h(friendlyTitle)}">Понятно</button>
                        <button type="button" data-schema-mode="yson" class="${state.schemaMode === "yson" ? "is-active" : ""}">YSON</button>
                        <button type="button" data-schema-mode="json" class="${state.schemaMode === "json" ? "is-active" : ""}">JSON</button>
                    </div>
                </div>
                ${state.schemaMode === "friendly" ? `
                    <div class="creator-columns-toolbar">
                        <span>${state.columns.length ? `${state.columns.length} колонок` : "Колонки ещё не добавлены"}</span>
                        <button class="button button--secondary" id="creator-add-column" type="button">+ Добавить поле</button>
                    </div>
                    <div class="creator-columns-list">
                        ${state.columns.map(columnCard).join("") || `<div class="creator-columns-empty"><span>＋</span><p>Добавьте первое поле таблицы</p></div>`}
                    </div>
                ` : `
                    <label class="creator-text-editor">
                        <span>Схема в формате ${state.schemaMode.toUpperCase()}</span>
                        <textarea id="creator-schema-text" spellcheck="false">${h(state.schemaText[state.schemaMode])}</textarea>
                    </label>
                    <div id="creator-text-status" class="creator-text-status ${state.textValid ? "is-valid" : "is-invalid"}">
                        ${state.textValid ? "Схема корректна" : h(state.textError)}
                    </div>
                `}
                ${dynamicKeyError ? `<p class="creator-schema-error">Для динамической таблицы добавьте хотя бы одну колонку с сортировкой ASC.</p>` : ""}
            </section>
        `;
    }

    function confirmationPlan() {
        if (!state.replicated) {
            return [{ cluster: state.cluster, path: state.path.trim(), type: state.tableKind === "dynamic" ? "Дин. таблица" : "Стат. таблица" }];
        }
        return [
            { cluster: "miranda", path: state.path.trim(), type: "Replicated table" },
            ...state.replicaTargets.map((cluster) => ({ cluster, path: state.path.trim(), type: "Дин. реплика" })),
        ];
    }

    function renderConfirmation() {
        if (!state.confirmOpen) return "";
        return `
            <div class="modal-backdrop" id="creator-confirm-modal">
                <section class="modal creator-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="creator-confirm-title">
                    <header class="modal__header">
                        <div><p class="kicker">ПОДТВЕРЖДЕНИЕ</p><h2 id="creator-confirm-title">Создать таблицу?</h2></div>
                        <button class="icon-button" id="creator-close-confirm" type="button" aria-label="Закрыть">×</button>
                    </header>
                    <div class="creator-plan-list">
                        ${confirmationPlan().map((item) => `
                            <div class="creator-plan-item"><span>${h(item.cluster)}</span><strong>${h(item.type)}</strong><code>${h(item.path)}</code></div>
                        `).join("")}
                    </div>
                    ${state.tableKind === "dynamic" ? `
                        <label class="creator-confirm-check">
                            <input id="creator-leave-unmounted" type="checkbox" checked>
                            <span><strong>Оставить таблицы отмонтированными</strong><small>Если снять флажок, после создания таблицы будут смонтированы.</small></span>
                        </label>
                    ` : ""}
                    <footer class="modal__footer modal__footer--actions">
                        <span class="footer-spacer"></span>
                        <button class="button button--secondary" id="creator-cancel-confirm" type="button">Отменить</button>
                        <button class="button" id="creator-confirm-create" type="button">Да, создать</button>
                    </footer>
                </section>
            </div>
        `;
    }

    function renderColumnModal() {
        return `
            <div class="modal-backdrop" id="creator-column-modal" hidden>
                <section class="modal modal--compact" role="dialog" aria-modal="true" aria-labelledby="creator-column-title">
                    <header class="modal__header">
                        <div><p class="kicker">СХЕМА</p><h2 id="creator-column-title">Добавить поле</h2></div>
                        <button class="icon-button" id="creator-close-column" type="button" aria-label="Закрыть">×</button>
                    </header>
                    <div class="creator-column-form">
                        <label class="creator-field"><span>Название</span><input id="creator-column-name" maxlength="256" autocomplete="off" placeholder="itemId"></label>
                        <label class="creator-field"><span>Тип данных</span><select id="creator-column-type">${typeOptions("string")}</select></label>
                        <label class="creator-switch-field creator-switch-field--modal">
                            <span><strong>Optional</strong><small>Колонка может содержать Null.</small></span>
                            <input id="creator-column-optional" type="checkbox" checked>
                        </label>
                        <label class="creator-field"><span>Сортировка</span>
                            <select id="creator-column-sort">
                                <option value="none">Без сортировки</option>
                                <option value="asc">ASC</option>
                                ${state.tableKind === "static" ? `<option value="desc">DESC</option>` : ""}
                            </select>
                        </label>
                        <p id="creator-column-error" class="creator-form-error"></p>
                    </div>
                    <footer class="modal__footer modal__footer--actions">
                        <span class="footer-spacer"></span>
                        <button class="button button--secondary" id="creator-cancel-column" type="button">Отменить</button>
                        <button class="button" id="creator-save-column" type="button">Сохранить</button>
                    </footer>
                </section>
            </div>
        `;
    }

    function renderSuccess() {
        if (!state.created.length) return "";
        return `
            <section class="creator-card creator-success-card ${state.creationPartial ? "creator-success-card--partial" : ""}">
                <div class="creator-success-icon">${state.creationPartial ? "!" : "✓"}</div>
                <div>
                    <h2>${state.creationPartial ? "Создание завершилось частично" : "Таблица создана"}</h2>
                    ${state.creationPartial ? "<p>Проверьте уже созданные объекты перед повторным запуском.</p>" : ""}
                    <div class="creator-created-list">${state.created.filter((item) => item.path).map((item) => `<code>${h(item.cluster)} · ${h(item.path)}</code>`).join("")}</div>
                </div>
            </section>
        `;
    }

    function renderResourceModeSwitch() {
        return `
            <div class="creator-resource-switch" role="tablist" aria-label="Что создать">
                <button type="button" data-resource-mode="table" class="${state.resourceMode === "table" ? "is-active" : ""}" role="tab" aria-selected="${state.resourceMode === "table"}">
                    <span>Таблица</span><small>Статическая, динамическая или реплицированная</small>
                </button>
                <button type="button" data-resource-mode="directory" class="${state.resourceMode === "directory" ? "is-active" : ""}" role="tab" aria-selected="${state.resourceMode === "directory"}">
                    <span>Директория</span><small>Одна папка или целая цепочка</small>
                </button>
            </div>
        `;
    }

    function directoryPayload(extra = {}) {
        return {
            cluster: state.cluster,
            path: state.directoryPath.trim(),
            inheritAcl: state.directoryInheritAcl,
            annotation: state.directoryAnnotation.trim(),
            ...extra,
        };
    }

    function directoryErrors() {
        const errors = [];
        if (!state.directoryPath.trim()) errors.push("Укажите путь директории");
        if (!state.directoryCheck?.valid || state.directoryCheck.signature !== currentDirectorySignature()) {
            errors.push("Сначала успешно проверьте путь");
        }
        return errors;
    }

    function directoryStepCopy(step) {
        if (step.status === "create") return "будет создана";
        if (step.status === "exists") return "уже существует — используем как родительскую";
        if (step.nodeType === "map_node") return "целевая директория уже существует";
        return `путь занят нодой типа ${step.nodeType || "unknown"}`;
    }

    function renderDirectoryStatus() {
        if (state.directoryChecking) {
            return `<div class="creator-validation creator-validation--loading"><span class="button__spinner"></span> Проверяю всю цепочку пути…</div>`;
        }
        if (!state.directoryCheck) return "";
        const check = state.directoryCheck;
        return `
            <div class="creator-validation ${check.valid ? "creator-validation--success" : "creator-validation--error"}">
                <strong>${check.valid ? `Будет создано директорий: ${check.toCreate.length}` : h(check.reason || "Создание невозможно")}</strong>
                <div class="creator-directory-chain">
                    ${check.steps.map((step) => `
                        <div class="creator-directory-step creator-directory-step--${h(step.status)}">
                            <span class="creator-directory-step__icon">${step.status === "create" ? "+" : step.status === "exists" ? "✓" : "×"}</span>
                            <code>${h(step.path)}</code>
                            <em>${h(directoryStepCopy(step))}</em>
                        </div>
                    `).join("")}
                </div>
            </div>
        `;
    }

    function renderDirectorySuccess() {
        if (!state.directoryCreated.length) return "";
        return `
            <section class="creator-card creator-success-card ${state.directoryCreationPartial ? "creator-success-card--partial" : ""}">
                <div class="creator-success-icon">${state.directoryCreationPartial ? "!" : "✓"}</div>
                <div>
                    <h2>${state.directoryCreationPartial ? "Цепочка создана частично" : "Директории созданы"}</h2>
                    ${state.directoryCreationPartial ? "<p>Проверьте созданные пути перед повторным запуском.</p>" : ""}
                    <div class="creator-created-list">${state.directoryCreated.map((item) => `<code>${h(item.cluster)} · ${h(item.path)}</code>`).join("")}</div>
                </div>
            </section>
        `;
    }

    function renderDirectoryConfirmation() {
        if (!state.directoryConfirmOpen || !state.directoryCheck) return "";
        return `
            <div class="modal-backdrop" id="creator-directory-confirm-modal">
                <section class="modal creator-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="creator-directory-confirm-title">
                    <header class="modal__header">
                        <div><p class="kicker">ПОДТВЕРЖДЕНИЕ</p><h2 id="creator-directory-confirm-title">Создать директории?</h2></div>
                        <button class="icon-button" id="creator-directory-close-confirm" type="button" aria-label="Закрыть">×</button>
                    </header>
                    <div class="creator-plan-list">
                        ${state.directoryCheck.toCreate.map((path) => `
                            <div class="creator-plan-item"><span>${h(state.cluster)}</span><strong>Директория</strong><code>${h(path)}</code></div>
                        `).join("")}
                    </div>
                    <footer class="modal__footer modal__footer--actions">
                        <span class="footer-spacer"></span>
                        <button class="button button--secondary" id="creator-directory-cancel-confirm" type="button">Отменить</button>
                        <button class="button" id="creator-directory-confirm-create" type="button">Да, создать</button>
                    </footer>
                </section>
            </div>
        `;
    }

    function renderDirectoryCreator() {
        const errors = directoryErrors();
        const createCount = state.directoryCheck?.toCreate?.length || 0;
        return `
            ${renderDirectorySuccess()}
            <form id="creator-directory-form" class="creator-form">
                <section class="creator-card">
                    <div class="creator-section-heading"><div><span class="creator-step">01</span><h2>Расположение</h2></div><span class="creator-required">обязательно</span></div>
                    <div class="creator-grid creator-grid--2">
                        <label class="creator-field"><span>Кластер</span>
                            <select id="creator-directory-cluster">
                                ${["jupiter", "saturn", "miranda"].map((cluster) => `<option value="${cluster}" ${state.cluster === cluster ? "selected" : ""}>${cluster}</option>`).join("")}
                            </select>
                        </label>
                        <div></div>
                    </div>
                    <div class="creator-path-block">
                        <label class="creator-field"><span>Путь директории</span>
                            <div class="creator-path-control">
                                <input id="creator-directory-path" class="${state.directoryCheck && !state.directoryCheck.valid ? "is-error" : state.directoryCheck?.valid ? "is-success" : ""}" value="${h(state.directoryPath)}" autocomplete="off" spellcheck="false" placeholder="//home/video/project/data">
                                <button class="button button--secondary" id="creator-directory-check" type="button" ${state.directoryChecking ? "disabled" : ""}>${state.directoryChecking ? "Проверяю…" : "Проверить"}</button>
                            </div>
                        </label>
                        ${renderDirectoryStatus()}
                    </div>
                </section>

                <section class="creator-card">
                    <div class="creator-section-heading"><div><span class="creator-step">02</span><h2>Параметры директории</h2></div><span class="creator-optional">необязательно</span></div>
                    <div class="creator-grid creator-grid--2">
                        <label class="creator-switch-field">
                            <span><strong>Наследовать ACL</strong><small>Конечная директория наследует права родительской.</small></span>
                            <input id="creator-directory-inherit-acl" type="checkbox" ${state.directoryInheritAcl ? "checked" : ""}>
                        </label>
                        <label class="creator-field">
                            <span>Описание</span>
                            <input id="creator-directory-annotation" maxlength="1000" value="${h(state.directoryAnnotation)}" placeholder="Для чего нужна директория">
                        </label>
                    </div>
                    <p class="creator-inline-hint">Параметры применяются к конечной директории. Промежуточные создаются с настройками по умолчанию.</p>
                </section>

                <section class="creator-final-card">
                    <div>
                        <strong>${createCount === 1 ? "Создать директорию" : `Создать директории: ${createCount}`}</strong>
                        <span>${errors[0] ? h(errors[0]) : "Цепочка пути проверена и готова к созданию"}</span>
                    </div>
                    <button class="button creator-submit" id="creator-directory-submit" type="submit" ${errors.length || state.directoryCreating ? "disabled" : ""}>${state.directoryCreating ? "Создаю…" : createCount === 1 ? "Создать директорию" : `Создать ${createCount}`}</button>
                </section>
            </form>
            ${renderDirectoryConfirmation()}
        `;
    }

    async function checkDirectoryPath() {
        if (!state.directoryPath.trim()) {
            services.showToast("Укажите путь директории", "error");
            return;
        }
        const token = localStorage.getItem(services.tokenKey) || "";
        if (!token) {
            services.showTokenModal("yt");
            return;
        }
        state.directoryChecking = true;
        state.directoryCheck = null;
        renderPage();
        try {
            const result = await services.apiFetch("/api/yt/creator/directory/validate", {
                method: "POST",
                headers: { "X-YT-Token": token },
                body: JSON.stringify(directoryPayload()),
            });
            state.directoryCheck = { ...result, signature: currentDirectorySignature() };
        } catch (error) {
            const message = error.code === "access_denied"
                ? "Доступ ограничен для данного токена"
                : error.message;
            state.directoryCheck = {
                valid: false,
                signature: currentDirectorySignature(),
                steps: [],
                toCreate: [],
                reason: message,
            };
            services.showToast(message, "error");
        } finally {
            state.directoryChecking = false;
            renderPage();
        }
    }

    async function createDirectories() {
        const token = localStorage.getItem(services.tokenKey) || "";
        if (!token) {
            state.directoryConfirmOpen = false;
            renderPage();
            services.showTokenModal("yt");
            return;
        }
        const button = document.getElementById("creator-directory-confirm-create");
        button.disabled = true;
        button.textContent = "Создаю…";
        try {
            const result = await services.apiFetch("/api/yt/creator/directory/create", {
                method: "POST",
                headers: { "X-YT-Token": token },
                body: JSON.stringify(directoryPayload({ confirmed: true })),
            });
            state.directoryCreated = result.created || [];
            state.directoryCreationPartial = false;
            state.directoryConfirmOpen = false;
            state.directoryCheck = null;
            services.showToast("Директории успешно созданы");
            renderPage();
            window.scrollTo({ top: 0, behavior: "smooth" });
        } catch (error) {
            if (error.code === "partial_creation" && Array.isArray(error.payload?.created)) {
                state.directoryCreated = error.payload.created;
                state.directoryCreationPartial = true;
                state.directoryConfirmOpen = false;
                state.directoryCheck = null;
                renderPage();
                window.scrollTo({ top: 0, behavior: "smooth" });
                services.showToast("Цепочка создана частично — проверьте список", "error");
                return;
            }
            button.disabled = false;
            button.textContent = "Да, создать";
            services.showToast(error.message, "error");
        }
    }

    function renderPage() {
        const errors = creatorErrors();
        root.innerHTML = `
            <section class="page-header">
                <div class="page-header__copy"><p class="kicker">ИНСТРУМЕНТ / YT CREATOR</p><h1 class="page-title">YT Creator</h1><p class="page-subtitle">Создание таблиц и директорий в YTsaurus.</p></div>
            </section>

            ${renderResourceModeSwitch()}

            ${state.resourceMode === "table" ? `
            ${renderSuccess()}

            <form id="creator-form" class="creator-form">
                <section class="creator-card">
                    <div class="creator-section-heading"><div><span class="creator-step">01</span><h2>Тип и расположение</h2></div><span class="creator-required">обязательно</span></div>
                    <div class="creator-choice-grid">
                        <label class="creator-choice ${state.tableKind === "static" ? "is-selected" : ""}">
                            <input type="radio" name="creator-kind" value="static" ${state.tableKind === "static" ? "checked" : ""}>
                            <span><strong>Статическая</strong><small>Файловая таблица для batch-обработки.</small></span>
                        </label>
                        <label class="creator-choice ${state.tableKind === "dynamic" ? "is-selected" : ""}">
                            <input type="radio" name="creator-kind" value="dynamic" ${state.tableKind === "dynamic" ? "checked" : ""}>
                            <span><strong>Динамическая</strong><small>Таблица для lookup и транзакционных изменений.</small></span>
                        </label>
                    </div>
                    <div class="creator-choice-grid creator-choice-grid--replication">
                        <label class="creator-choice ${!state.replicated ? "is-selected" : ""}">
                            <input type="radio" name="creator-replicated" value="regular" ${!state.replicated ? "checked" : ""}>
                            <span><strong>Обычная</strong><small>Одна таблица на выбранном кластере.</small></span>
                        </label>
                        <label class="creator-choice ${state.replicated ? "is-selected" : ""} ${state.tableKind !== "dynamic" ? "is-disabled" : ""}" title="${state.tableKind !== "dynamic" ? "Репликация доступна только для динамических таблиц" : ""}">
                            <input type="radio" name="creator-replicated" value="replicated" ${state.replicated ? "checked" : ""} ${state.tableKind !== "dynamic" ? "disabled" : ""}>
                            <span><strong>Реплицированная</strong><small>Логическая таблица на Miranda и реплики на data-кластерах.</small></span>
                        </label>
                    </div>
                    <div class="creator-grid creator-grid--2">
                        <label class="creator-field"><span>Кластер</span>
                            <select id="creator-cluster" ${state.replicated ? "disabled" : ""}>
                                ${["jupiter", "saturn", "miranda"].map((cluster) => `<option value="${cluster}" ${(state.replicated ? "miranda" : state.cluster) === cluster ? "selected" : ""}>${cluster}</option>`).join("")}
                            </select>
                        </label>
                        ${state.tableKind === "dynamic" ? `<label class="creator-field"><span>Tablet cell bundle</span><input id="creator-bundle" value="${h(state.bundle)}" placeholder="vkvideo"></label>` : `<div></div>`}
                    </div>
                    <div class="creator-path-block">
                        <label class="creator-field"><span>Путь таблицы</span>
                            <div class="creator-path-control">
                                <input id="creator-path" class="${state.pathCheck && !state.pathCheck.valid ? "is-error" : state.pathCheck?.valid ? "is-success" : ""}" value="${h(state.path)}" autocomplete="off" spellcheck="false" placeholder="//home/video/example/table">
                                <button class="button button--secondary" id="creator-check-path" type="button" ${state.pathChecking ? "disabled" : ""}>${state.pathChecking ? "Проверяю…" : "Проверить"}</button>
                            </div>
                        </label>
                        ${renderPathStatus()}
                    </div>
                </section>

                ${renderReplicaSettings()}

                <section class="creator-card">
                    <div class="creator-section-heading"><div><span class="creator-step">03</span><h2>Параметры хранения</h2></div></div>
                    <label class="creator-switch-field creator-switch-field--advanced">
                        <span><strong>Дополнительные параметры</strong><small>Если выключено, YTsaurus применит собственные значения по умолчанию.</small></span>
                        <input id="creator-advanced" type="checkbox" ${state.advancedEnabled ? "checked" : ""}>
                    </label>
                    ${renderAdvancedSettings()}
                </section>

                ${renderSchemaEditor()}

                <section class="creator-final-card">
                    <div>
                        <strong>${h(finalTableLabel())}</strong>
                        <span>${errors.length ? h(errors[0]) : "Все обязательные настройки заполнены"}</span>
                    </div>
                    <button class="button creator-submit" id="creator-submit" type="submit" ${errors.length || state.creating ? "disabled" : ""}>${state.creating ? "Создаю…" : h(finalTableLabel())}</button>
                </section>
            </form>
            ${renderColumnModal()}
            ${renderConfirmation()}
            ` : renderDirectoryCreator()}
        `;
        attachEvents();
    }

    function setReplicaTargets(mode) {
        state.replicaTargets = mode === "both" ? ["jupiter", "saturn"] : [mode];
        state.maxSync = Math.min(Math.max(1, state.maxSync), state.replicaTargets.length);
        state.minSync = Math.min(state.minSync, state.maxSync);
        state.preferredClusters = state.preferredClusters.filter((cluster) => state.replicaTargets.includes(cluster)).slice(0, state.maxSync);
        if (!state.preferredClusters.length) state.preferredClusters = [state.replicaTargets.includes("jupiter") ? "jupiter" : "saturn"];
        invalidatePathCheck();
    }

    async function checkPath() {
        if (!state.path.trim()) {
            services.showToast("Укажите путь таблицы", "error");
            return;
        }
        const token = localStorage.getItem(services.tokenKey) || "";
        if (!token) {
            services.showTokenModal("yt");
            return;
        }
        state.pathChecking = true;
        state.pathCheck = null;
        renderPage();
        try {
            const result = await services.apiFetch("/api/yt/creator/validate", {
                method: "POST",
                headers: { "X-YT-Token": token },
                body: JSON.stringify(creatorPayload()),
            });
            state.pathCheck = { ...result, signature: currentPathSignature() };
        } catch (error) {
            state.pathCheck = {
                valid: false,
                signature: currentPathSignature(),
                checks: [],
            };
            services.showToast(error.code === "access_denied" ? "Доступ ограничен для данного токена" : error.message, "error");
        } finally {
            state.pathChecking = false;
            renderPage();
        }
    }

    function openColumnModal(index = null) {
        state.editingColumnIndex = index;
        const column = index === null
            ? { name: "", type: "string", optional: true, sortOrder: "none" }
            : state.columns[index];
        const modal = document.getElementById("creator-column-modal");
        modal.hidden = false;
        document.getElementById("creator-column-title").textContent = index === null ? "Добавить поле" : "Настроить поле";
        document.getElementById("creator-column-name").value = column.name;
        document.getElementById("creator-column-type").innerHTML = typeOptions(column.type);
        document.getElementById("creator-column-optional").checked = column.optional;
        document.getElementById("creator-column-sort").value = column.sortOrder;
        document.getElementById("creator-column-name").focus();
    }

    function closeColumnModal() {
        document.getElementById("creator-column-modal").hidden = true;
        state.editingColumnIndex = null;
    }

    function saveColumn() {
        const index = state.editingColumnIndex;
        const column = {
            name: document.getElementById("creator-column-name").value.trim(),
            type: document.getElementById("creator-column-type").value,
            optional: document.getElementById("creator-column-optional").checked,
            sortOrder: document.getElementById("creator-column-sort").value,
        };
        const errorElement = document.getElementById("creator-column-error");
        if (!column.name) {
            errorElement.textContent = "Введите название поля";
            return;
        }
        if (state.columns.some((item, itemIndex) => item.name === column.name && itemIndex !== index)) {
            errorElement.textContent = "Поле с таким названием уже существует";
            return;
        }
        if (state.tableKind === "dynamic" && column.sortOrder === "desc") {
            errorElement.textContent = "Для динамической таблицы доступна только ASC-сортировка";
            return;
        }
        if (index === null) state.columns.push(column);
        else state.columns[index] = column;
        syncSchemaFromColumns();
        renderPage();
    }

    function copyColumn(index) {
        const source = state.columns[index];
        let name = `${source.name}_copy`;
        let suffix = 2;
        while (state.columns.some((column) => column.name === name)) {
            name = `${source.name}_copy_${suffix}`;
            suffix += 1;
        }
        state.columns.splice(index + 1, 0, { ...source, name });
        syncSchemaFromColumns();
        renderPage();
    }

    function deleteColumn(index) {
        state.columns.splice(index, 1);
        syncSchemaFromColumns();
        renderPage();
    }

    function moveColumn(from, to) {
        if (from === to || from < 0 || to < 0) return;
        const [column] = state.columns.splice(from, 1);
        state.columns.splice(to, 0, column);
        syncSchemaFromColumns();
        renderPage();
    }

    function selectSchemaMode(mode) {
        if (mode === "friendly" && !state.friendlyConvertible) return;
        if (mode === "friendly" && state.parsedFriendlyColumns) {
            state.columns = state.parsedFriendlyColumns.map((column) => ({ ...column }));
            syncSchemaFromColumns();
        }
        state.schemaMode = mode;
        renderPage();
    }

    async function validateSchemaText(format, text, revision) {
        try {
            const result = await services.apiFetch("/api/yt/creator/schema/convert", {
                method: "POST",
                body: JSON.stringify({ format, text }),
            });
            if (revision !== state.textRevision) return;
            state.schema = result.schema;
            state.schemaText[format] = text;
            state.schemaText[format === "json" ? "yson" : "json"] = format === "json" ? result.schemaYson : result.schemaJson;
            state.textValid = true;
            state.textError = "";
            state.friendlyConvertible = result.friendlyConvertible;
            state.friendlyReason = result.friendlyReason || "";
            state.parsedFriendlyColumns = result.friendlyColumns;
        } catch (error) {
            if (revision !== state.textRevision) return;
            state.textValid = false;
            state.textError = error.message;
            state.friendlyConvertible = false;
            state.friendlyReason = error.message;
        }
        updateTextUi();
    }

    function scheduleTextValidation(event) {
        const format = state.schemaMode;
        const text = event.target.value;
        state.schemaText[format] = text;
        state.textValid = false;
        state.textError = "Проверяю…";
        state.friendlyConvertible = false;
        state.friendlyReason = "Сначала исправьте или дождитесь проверки текста";
        state.textRevision += 1;
        const revision = state.textRevision;
        clearTimeout(state.textTimer);
        state.textTimer = setTimeout(() => validateSchemaText(format, text, revision), 420);
        updateTextUi();
    }

    function updateTextUi() {
        const status = document.getElementById("creator-text-status");
        if (status) {
            status.className = `creator-text-status ${state.textValid ? "is-valid" : "is-invalid"}`;
            status.textContent = state.textValid ? "Схема корректна" : state.textError;
        }
        const friendlyButton = document.querySelector('[data-schema-mode="friendly"]');
        if (friendlyButton) {
            friendlyButton.classList.toggle("is-disabled", !state.friendlyConvertible);
            friendlyButton.setAttribute("aria-disabled", String(!state.friendlyConvertible));
            friendlyButton.title = state.friendlyConvertible ? "Визуальный редактор" : state.friendlyReason;
        }
        updateSubmitUi();
        const schemaError = document.querySelector(".creator-schema-error");
        const needsKey = state.tableKind === "dynamic" && !hasSortedColumn();
        if (schemaError) schemaError.hidden = !needsKey;
    }

    function updateSubmitUi() {
        const errors = creatorErrors();
        const submit = document.getElementById("creator-submit");
        if (submit) submit.disabled = errors.length > 0 || state.creating;
        const finalCopy = document.querySelector(".creator-final-card > div span");
        if (finalCopy) finalCopy.textContent = errors[0] || "Все обязательные настройки заполнены";
    }

    function openConfirmation() {
        state.confirmOpen = true;
        renderPage();
    }

    function closeConfirmation() {
        state.confirmOpen = false;
        renderPage();
    }

    async function createTable() {
        const token = localStorage.getItem(services.tokenKey) || "";
        if (!token) {
            state.confirmOpen = false;
            renderPage();
            services.showTokenModal("yt");
            return;
        }
        const leaveUnmounted = document.getElementById("creator-leave-unmounted")?.checked ?? true;
        const button = document.getElementById("creator-confirm-create");
        button.disabled = true;
        button.textContent = "Создаю…";
        try {
            const result = await services.apiFetch("/api/yt/creator/create", {
                method: "POST",
                headers: { "X-YT-Token": token },
                body: JSON.stringify(creatorPayload({ confirmed: true, leaveUnmounted })),
            });
            state.created = result.created || [];
            state.creationPartial = false;
            state.confirmOpen = false;
            state.pathCheck = null;
            services.showToast("Таблица успешно создана");
            renderPage();
            window.scrollTo({ top: 0, behavior: "smooth" });
        } catch (error) {
            if (error.code === "partial_creation" && Array.isArray(error.payload?.created)) {
                state.created = error.payload.created;
                state.creationPartial = true;
                state.confirmOpen = false;
                state.pathCheck = null;
                renderPage();
                window.scrollTo({ top: 0, behavior: "smooth" });
                services.showToast("Создание завершилось частично — проверьте список объектов", "error");
                return;
            }
            button.disabled = false;
            button.textContent = "Да, создать";
            services.showToast(error.message, "error");
        }
    }

    function attachEvents() {
        document.querySelectorAll("[data-resource-mode]").forEach((button) => button.addEventListener("click", () => {
            state.resourceMode = button.dataset.resourceMode;
            state.confirmOpen = false;
            state.directoryConfirmOpen = false;
            renderPage();
        }));
        document.querySelectorAll('input[name="creator-kind"]').forEach((input) => input.addEventListener("change", (event) => {
            state.tableKind = event.target.value;
            if (state.tableKind === "static") state.replicated = false;
            if (state.tableKind === "dynamic") {
                state.columns = state.columns.map((column) => ({ ...column, sortOrder: column.sortOrder === "desc" ? "asc" : column.sortOrder }));
                syncSchemaFromColumns();
            }
            invalidatePathCheck();
            renderPage();
        }));
        document.querySelectorAll('input[name="creator-replicated"]').forEach((input) => input.addEventListener("change", (event) => {
            state.replicated = event.target.value === "replicated";
            if (state.replicated) state.cluster = "miranda";
            invalidatePathCheck();
            invalidateDirectoryCheck();
            renderPage();
        }));
        document.getElementById("creator-cluster")?.addEventListener("change", (event) => {
            state.cluster = event.target.value;
            localStorage.setItem("dev-toolbox:yt-cluster", state.cluster);
            invalidatePathCheck();
            invalidateDirectoryCheck();
            renderPage();
        });
        document.getElementById("creator-bundle")?.addEventListener("input", (event) => {
            state.bundle = event.target.value;
            updateSubmitUi();
        });
        document.getElementById("creator-path")?.addEventListener("input", (event) => {
            state.path = event.target.value;
            invalidatePathCheck();
            event.target.classList.remove("is-error", "is-success");
            document.querySelector(".creator-validation")?.remove();
            updateSubmitUi();
        });
        document.getElementById("creator-check-path")?.addEventListener("click", checkPath);
        document.getElementById("creator-replica-targets")?.addEventListener("change", (event) => {
            setReplicaTargets(event.target.value);
            renderPage();
        });
        document.getElementById("creator-tracker")?.addEventListener("change", (event) => { state.enableTracker = event.target.checked; });
        document.getElementById("creator-min-sync")?.addEventListener("change", (event) => {
            state.minSync = Math.max(0, Math.min(Number(event.target.value), state.replicaTargets.length));
            if (state.minSync > state.maxSync) state.maxSync = state.minSync;
            renderPage();
        });
        document.getElementById("creator-max-sync")?.addEventListener("change", (event) => {
            state.maxSync = Math.max(1, Math.min(Number(event.target.value), state.replicaTargets.length));
            if (state.minSync > state.maxSync) state.minSync = state.maxSync;
            state.preferredClusters = state.preferredClusters.slice(0, state.maxSync);
            renderPage();
        });
        document.querySelectorAll("[data-preferred-cluster]").forEach((input) => input.addEventListener("change", (event) => {
            const cluster = event.target.dataset.preferredCluster;
            if (event.target.checked) {
                if (state.preferredClusters.length >= state.maxSync) {
                    services.showToast(`Можно выбрать не больше ${state.maxSync} preferred-кластеров`, "error");
                    event.target.checked = false;
                    return;
                }
                state.preferredClusters.push(cluster);
            } else {
                state.preferredClusters = state.preferredClusters.filter((item) => item !== cluster);
            }
            renderPage();
        }));
        document.getElementById("creator-advanced")?.addEventListener("change", (event) => {
            state.advancedEnabled = event.target.checked;
            renderPage();
        });
        document.getElementById("creator-optimize-for")?.addEventListener("change", (event) => { state.optimizeFor = event.target.value; });
        document.getElementById("creator-codec-family")?.addEventListener("change", (event) => {
            state.codecFamily = event.target.value;
            const codec = CODECS[state.codecFamily];
            if (codec.min !== undefined) state.codecLevel = Math.max(codec.min, Math.min(state.codecLevel, codec.max));
            renderPage();
        });
        document.getElementById("creator-codec-level")?.addEventListener("change", (event) => {
            const codec = CODECS[state.codecFamily];
            state.codecLevel = Math.max(codec.min, Math.min(Number(event.target.value), codec.max));
            renderPage();
        });
        document.getElementById("creator-replication-factor")?.addEventListener("change", (event) => {
            state.replicationFactor = Math.max(1, Math.min(Number(event.target.value), 10));
            renderPage();
        });
        document.querySelectorAll("[data-schema-mode]").forEach((button) => button.addEventListener("click", () => selectSchemaMode(button.dataset.schemaMode)));
        document.getElementById("creator-add-column")?.addEventListener("click", () => openColumnModal());
        document.getElementById("creator-schema-text")?.addEventListener("input", scheduleTextValidation);
        document.querySelectorAll(".creator-column-card").forEach((card) => {
            card.addEventListener("click", (event) => {
                if (!event.target.closest("button")) openColumnModal(Number(card.dataset.columnIndex));
            });
            card.addEventListener("keydown", (event) => {
                if (event.key === "Enter") openColumnModal(Number(card.dataset.columnIndex));
            });
            card.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", card.dataset.columnIndex));
            card.addEventListener("dragover", (event) => event.preventDefault());
            card.addEventListener("drop", (event) => {
                event.preventDefault();
                moveColumn(Number(event.dataTransfer.getData("text/plain")), Number(card.dataset.columnIndex));
            });
        });
        document.querySelectorAll("[data-copy-column]").forEach((button) => button.addEventListener("click", () => copyColumn(Number(button.dataset.copyColumn))));
        document.querySelectorAll("[data-delete-column]").forEach((button) => button.addEventListener("click", () => deleteColumn(Number(button.dataset.deleteColumn))));
        document.getElementById("creator-close-column")?.addEventListener("click", closeColumnModal);
        document.getElementById("creator-cancel-column")?.addEventListener("click", closeColumnModal);
        document.getElementById("creator-save-column")?.addEventListener("click", saveColumn);
        document.getElementById("creator-form")?.addEventListener("submit", (event) => {
            event.preventDefault();
            if (!creatorErrors().length) openConfirmation();
        });
        document.getElementById("creator-close-confirm")?.addEventListener("click", closeConfirmation);
        document.getElementById("creator-cancel-confirm")?.addEventListener("click", closeConfirmation);
        document.getElementById("creator-confirm-create")?.addEventListener("click", createTable);

        document.getElementById("creator-directory-cluster")?.addEventListener("change", (event) => {
            state.cluster = event.target.value;
            localStorage.setItem("dev-toolbox:yt-cluster", state.cluster);
            invalidateDirectoryCheck();
            invalidatePathCheck();
            renderPage();
        });
        document.getElementById("creator-directory-path")?.addEventListener("input", (event) => {
            state.directoryPath = event.target.value;
            invalidateDirectoryCheck();
            event.target.classList.remove("is-error", "is-success");
            document.querySelector(".creator-validation")?.remove();
            const submit = document.getElementById("creator-directory-submit");
            if (submit) submit.disabled = true;
            const finalCopy = document.querySelector(".creator-final-card > div span");
            if (finalCopy) finalCopy.textContent = "Сначала успешно проверьте путь";
        });
        document.getElementById("creator-directory-check")?.addEventListener("click", checkDirectoryPath);
        document.getElementById("creator-directory-inherit-acl")?.addEventListener("change", (event) => {
            state.directoryInheritAcl = event.target.checked;
        });
        document.getElementById("creator-directory-annotation")?.addEventListener("input", (event) => {
            state.directoryAnnotation = event.target.value;
        });
        document.getElementById("creator-directory-form")?.addEventListener("submit", (event) => {
            event.preventDefault();
            if (!directoryErrors().length) {
                state.directoryConfirmOpen = true;
                renderPage();
            }
        });
        document.getElementById("creator-directory-close-confirm")?.addEventListener("click", () => {
            state.directoryConfirmOpen = false;
            renderPage();
        });
        document.getElementById("creator-directory-cancel-confirm")?.addEventListener("click", () => {
            state.directoryConfirmOpen = false;
            renderPage();
        });
        document.getElementById("creator-directory-confirm-create")?.addEventListener("click", createDirectories);
    }

    function render(target, injectedServices) {
        root = target;
        services = injectedServices;
        state = initialState();
        syncSchemaFromColumns();
        renderPage();
    }

    return { render };
})();
