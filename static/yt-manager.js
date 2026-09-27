"use strict";

window.YtManager = (() => {
    const CODECS = {
        none: null,
        snappy: null,
        lz4: null,
        lz4_high_compression: null,
        zstd: [1, 21, 5],
        zlib: [1, 9, 6],
        brotli: [1, 11, 5],
        lzma: [0, 9, 6],
        bzip2: [1, 9, 6],
    };
    const ACTION_LABELS = {
        link: "Создать ссылку",
        copy: "Скопировать",
        move: "Переместить",
    };

    let root;
    let services;
    let state;

    function initialState() {
        return {
            cluster: localStorage.getItem("dev-toolbox:yt-cluster") || "miranda",
            path: "",
            loading: false,
            controller: null,
            info: null,
            error: "",
            attributes: {},
            destinations: { link: "", copy: "", move: "" },
            destinationChecks: { link: null, copy: null, move: null },
            checkingDestination: null,
            confirm: null,
            acting: false,
        };
    }

    function h(value) {
        return services.escapeHtml(value);
    }

    function ytHeaders() {
        return { "X-YT-Token": localStorage.getItem(services.tokenKey) || "" };
    }

    function jsonEqual(left, right) {
        return JSON.stringify(left) === JSON.stringify(right);
    }

    function displayValue(value) {
        return value === null || value === undefined || value === "" ? "—" : String(value);
    }

    function codecParts(codecValue) {
        const codec = String(codecValue || "zstd_5");
        if (codec in CODECS && CODECS[codec] === null) return { family: codec, level: "" };
        const match = codec.match(/^(.+)_([0-9]+)$/);
        if (match && CODECS[match[1]]) return { family: match[1], level: Number(match[2]) };
        return { family: "zstd", level: 5 };
    }

    function setCodecFamily(family) {
        const levels = CODECS[family];
        state.attributes.compressionCodec = levels ? `${family}_${levels[2]}` : family;
    }

    function setCodecLevel(level) {
        const { family } = codecParts(state.attributes.compressionCodec);
        state.attributes.compressionCodec = `${family}_${Number(level)}`;
    }

    function changedAttributes() {
        const original = state.info?.initialAttributes || {};
        return Object.fromEntries(
            Object.entries(state.attributes).filter(([key, value]) => !jsonEqual(value, original[key])),
        );
    }

    function pageHeader() {
        return `
            <header class="page-header manager-page-header">
                <div class="page-header__copy">
                    <p class="kicker">YT TOOL · MANAGE</p>
                    <h1 class="page-title">YT Manager</h1>
                    <p class="page-subtitle">Атрибуты, ссылки, перемещение, состояние dynamic-таблиц и управление репликами.</p>
                </div>
                <div class="page-header__mark"><span class="live-dot"></span><span>REVALIDATE BEFORE ACTION</span></div>
            </header>`;
    }

    function searchCard() {
        return `
            <section class="manager-card manager-search-card">
                <form id="manager-inspect-form">
                    <div class="manager-search-label"><span>Кластер и путь</span><code>cluster.yt.vk.team</code></div>
                    <div class="manager-search-controls">
                        <select id="manager-cluster" aria-label="Кластер">
                            ${["jupiter", "saturn", "miranda"].map((cluster) => `<option value="${cluster}" ${state.cluster === cluster ? "selected" : ""}>${cluster}</option>`).join("")}
                        </select>
                        <input id="manager-path" value="${h(state.path)}" placeholder="//home/project/table" spellcheck="false" autocomplete="off">
                        ${state.loading
                            ? `<button class="button button--secondary manager-stop" id="manager-stop" type="button">Остановить</button>`
                            : `<button class="button" type="submit">Проверить</button>`}
                    </div>
                    <p class="manager-search-error">${h(state.error)}</p>
                </form>
            </section>`;
    }

    function identityCard(info) {
        const stateText = info.tabletState ? `<span class="manager-pill manager-pill--blue">${h(info.tabletState)}</span>` : "";
        return `
            <section class="manager-identity">
                <div class="manager-kind-icon">${info.kind === "directory" ? "D" : info.kind === "link" ? "↗" : "T"}</div>
                <div class="manager-identity-copy">
                    <span>${h(info.kindLabel)}</span>
                    <strong>${h(info.path)}</strong>
                    <small>${h(info.cluster)}.yt.vk.team</small>
                </div>
                <div class="manager-identity-state">${stateText}<span class="manager-pill">${h(info.nodeType)}</span></div>
            </section>`;
    }

    function emptyState() {
        return `
            <section class="manager-empty">
                <span>M</span>
                <h2>Выберите YT-ноду</h2>
                <p>После проверки здесь появятся только операции, применимые к найденному объекту.</p>
            </section>`;
    }

    function field(label, key, options = {}) {
        const value = state.attributes[key] ?? "";
        const original = state.info.initialAttributes[key];
        const changed = !jsonEqual(value, original);
        if (options.select) {
            return `
                <label class="manager-field ${changed ? "is-changed" : ""}">
                    <span>${h(label)}${changed ? " <i>изменено</i>" : ""}</span>
                    <select data-manager-attribute="${key}">
                        ${options.select.map(([optionValue, optionLabel]) => `<option value="${optionValue}" ${String(value ?? "") === optionValue ? "selected" : ""}>${h(optionLabel)}</option>`).join("")}
                    </select>
                    <small>Исходно: ${h(displayValue(original))}</small>
                </label>`;
        }
        return `
            <label class="manager-field ${changed ? "is-changed" : ""}">
                <span>${h(label)}${changed ? " <i>изменено</i>" : ""}</span>
                <input data-manager-attribute="${key}" type="${options.type || "text"}" value="${h(value)}" ${options.min ? `min="${options.min}"` : ""} ${options.max ? `max="${options.max}"` : ""} placeholder="${h(options.placeholder || "Не задано")}">
                <small>Исходно: ${h(displayValue(original))}</small>
            </label>`;
    }

    function attributesCard() {
        const codec = codecParts(state.attributes.compressionCodec);
        const levels = CODECS[codec.family];
        const changes = changedAttributes();
        const directoryNote = state.info.kind === "directory"
            ? `<p class="manager-inline-note">Указанные storage-настройки станут наследуемыми значениями для новых дочерних нод.</p>`
            : "";
        return `
            <section class="manager-card manager-section">
                <div class="manager-section-heading">
                    <div><span class="manager-section-index">01</span><div><h2>Атрибуты</h2><p>Текущие значения загружены из YT</p></div></div>
                    <button class="button button--ghost button--small" id="manager-reset-attributes" type="button" ${Object.keys(changes).length ? "" : "disabled"}>Сбросить изменения</button>
                </div>
                ${directoryNote}
                <div class="manager-fields-grid">
                    ${field("Medium", "primaryMedium", { placeholder: "default" })}
                    ${field("Tablet cell bundle", "tabletCellBundle", { placeholder: "vkvideo" })}
                    ${field("Optimize for", "optimizeFor", { select: [["", "Не задан"], ["scan", "scan"], ["lookup", "lookup"]] })}
                    <label class="manager-field ${state.attributes.compressionCodec !== state.info.initialAttributes.compressionCodec ? "is-changed" : ""}">
                        <span>Compression codec${state.attributes.compressionCodec !== state.info.initialAttributes.compressionCodec ? " <i>изменено</i>" : ""}</span>
                        <select id="manager-codec-family">
                            ${Object.keys(CODECS).map((family) => `<option value="${family}" ${family === codec.family ? "selected" : ""}>${family}</option>`).join("")}
                        </select>
                        <small>Исходно: ${h(displayValue(state.info.initialAttributes.compressionCodec))}</small>
                    </label>
                    <label class="manager-field">
                        <span>Compression level</span>
                        <input id="manager-codec-level" type="number" value="${levels ? codec.level : ""}" ${levels ? `min="${levels[0]}" max="${levels[1]}"` : "disabled"}>
                        <small>${levels ? `Диапазон ${levels[0]}–${levels[1]}` : "У кодека нет уровня"}</small>
                    </label>
                    ${field("Number of replicas", "replicationFactor", { type: "number", min: 1, max: 10 })}
                    <label class="manager-field manager-field--wide ${state.attributes.annotation !== state.info.initialAttributes.annotation ? "is-changed" : ""}">
                        <span>Описание${state.attributes.annotation !== state.info.initialAttributes.annotation ? " <i>изменено</i>" : ""}</span>
                        <textarea data-manager-attribute="annotation" maxlength="1000" placeholder="Описание ноды">${h(state.attributes.annotation || "")}</textarea>
                        <small>Исходно: ${h(displayValue(state.info.initialAttributes.annotation))}</small>
                    </label>
                </div>
                <div class="manager-section-actions">
                    <span>${Object.keys(changes).length ? `Изменено полей: ${Object.keys(changes).length}` : "Изменений нет"}</span>
                    <button class="button" id="manager-save-attributes" type="button" ${Object.keys(changes).length ? "" : "disabled"}>Применить атрибуты</button>
                </div>
            </section>`;
    }

    function destinationStatus(operation) {
        const check = state.destinationChecks[operation];
        if (!check) return "";
        if (check.valid) return `<p class="manager-validation is-ok">✓ Путь свободен, родительские директории существуют</p>`;
        return `<p class="manager-validation is-error">${h(check.reasons?.join(". ") || "Путь использовать нельзя")}</p>`;
    }

    function destinationCard(operation, index) {
        const enabled = state.info.capabilities[operation];
        const restriction = !enabled ? state.info.restrictions?.copyMove : null;
        const value = state.destinations[operation];
        const check = state.destinationChecks[operation];
        const checkMatches = check?.destinationPath === value.trim();
        return `
            <section class="manager-card manager-section ${enabled ? "" : "is-disabled"}">
                <div class="manager-section-heading">
                    <div><span class="manager-section-index">${String(index).padStart(2, "0")}</span><div><h2>${h(ACTION_LABELS[operation])}</h2><p>${operation === "link" ? "Символическая ссылка в том же кластере" : operation === "copy" ? "Новая независимая копия" : "Смена пути исходной ноды"}</p></div></div>
                </div>
                ${restriction ? `<p class="manager-inline-note manager-inline-note--warning">${h(restriction)}</p>` : `
                    <div class="manager-path-row">
                        <input data-manager-destination="${operation}" value="${h(value)}" placeholder="//home/project/new-path" spellcheck="false">
                        <button class="button button--secondary" data-manager-check="${operation}" type="button" ${state.checkingDestination ? "disabled" : ""}>${state.checkingDestination === operation ? "Проверка…" : "Проверить"}</button>
                        <button class="button" data-manager-run-destination="${operation}" type="button" ${checkMatches && check.valid ? "" : "disabled"}>${h(ACTION_LABELS[operation])}</button>
                    </div>
                    ${destinationStatus(operation)}
                `}
            </section>`;
    }

    function dynamicCard(index) {
        if (!state.info.capabilities.mount) return "";
        const isUnmounted = state.info.tabletState === "unmounted";
        return `
            <section class="manager-card manager-section">
                <div class="manager-section-heading">
                    <div><span class="manager-section-index">${String(index).padStart(2, "0")}</span><div><h2>Состояние таблицы</h2><p>Текущее состояние перепроверяется перед командой</p></div></div>
                    <span class="manager-pill manager-pill--blue">${h(state.info.tabletState)}</span>
                </div>
                <div class="manager-button-row">
                    <button class="button" data-manager-simple-action="mount" type="button" ${isUnmounted ? "" : "disabled"}>Смонтировать</button>
                    <button class="button button--secondary" data-manager-simple-action="unmount" type="button" ${isUnmounted ? "disabled" : ""}>Отмонтировать</button>
                </div>
            </section>`;
    }

    function replicaRow(replica) {
        return `
            <article class="manager-replica" data-replica-id="${h(replica.id)}">
                <div class="manager-replica-id"><strong>${h(replica.cluster || "unknown")}</strong><code>${h(replica.path || "—")}</code><small>${h(replica.id)}</small></div>
                <label><span>State</span><select data-replica-field="state"><option value="enabled" ${replica.state === "enabled" ? "selected" : ""}>enabled</option><option value="disabled" ${replica.state !== "enabled" ? "selected" : ""}>disabled</option></select></label>
                <label><span>Mode</span><select data-replica-field="mode"><option value="sync" ${replica.mode === "sync" ? "selected" : ""}>sync</option><option value="async" ${replica.mode !== "sync" ? "selected" : ""}>async</option></select></label>
                <label class="manager-toggle"><input data-replica-field="tracker" type="checkbox" ${replica.trackerEnabled ? "checked" : ""}><span>Tracker</span></label>
                <button class="button button--secondary" data-save-replica="${h(replica.id)}" type="button">Применить</button>
            </article>`;
    }

    function replicationCard(index) {
        if (!state.info.capabilities.replication) return "";
        const replication = state.info.replication || {};
        const showGlobal = state.info.kind === "replicated_table" && state.info.cluster === "miranda";
        return `
            <section class="manager-card manager-section">
                <div class="manager-section-heading">
                    <div><span class="manager-section-index">${String(index).padStart(2, "0")}</span><div><h2>Репликация</h2><p>${h(replication.logicalPath || state.info.path)}</p></div></div>
                    ${showGlobal ? `<button class="button button--secondary" id="manager-global-tracker" data-enabled="${replication.globalTrackerEnabled ? "false" : "true"}" type="button">Automatic mode switch: ${replication.globalTrackerEnabled ? "ON" : "OFF"}</button>` : ""}
                </div>
                <div class="manager-replica-head"><span>Replica</span><span>State</span><span>Mode</span><span>Auto</span><span></span></div>
                <div class="manager-replica-list">
                    ${(replication.replicas || []).map(replicaRow).join("") || `<p class="manager-inline-note manager-inline-note--warning">В метаданных нет реплик.</p>`}
                </div>
            </section>`;
    }

    function deletePlanHtml(plan) {
        const items = plan?.items || [];
        const contents = plan?.contents;
        return `
            <ul class="manager-plan-list">${items.map((item) => `<li><span>${h(item.cluster)}</span><code>${h(item.path)}</code><small>${h(item.action)}</small></li>`).join("")}</ul>
            ${contents ? `
                <div class="manager-directory-preview">
                    <strong>Содержимое директории (${contents.countShown}${contents.truncated ? "+" : ""})</strong>
                    <div>${contents.items.map((item) => `<code>${h(item.path)} <i>${h(item.nodeType)}</i></code>`).join("") || `<span>Директория пуста</span>`}</div>
                    ${contents.truncated ? `<small>Показаны первые ${contents.countShown} нод.</small>` : ""}
                </div>` : ""}`;
    }

    function dangerCard(index) {
        return `
            <section class="manager-card manager-section manager-danger-zone">
                <div class="manager-section-heading">
                    <div><span class="manager-section-index">${String(index).padStart(2, "0")}</span><div><h2>Удаление</h2><p>Двойное подтверждение и повторная проверка структуры</p></div></div>
                    <button class="button button--danger" id="manager-delete" type="button">Удалить</button>
                </div>
                ${deletePlanHtml(state.info.deletePlan)}
            </section>`;
    }

    function linkOnlyView() {
        const info = state.info;
        return `
            ${identityCard(info)}
            <section class="manager-card manager-link-warning">
                <span>↗</span><div><h2>Это символическая ссылка</h2><p>${info.link.broken ? "Ссылка сломана и указывает на" : "Ссылка указывает на"} <code>${h(info.link.targetPath)}</code></p><small>Изменение атрибутов, копирование и перемещение отключены. Можно удалить только саму ссылку — целевая нода останется.</small></div>
            </section>
            ${dangerCard(1)}`;
    }

    function resultView() {
        if (!state.info) return emptyState();
        if (state.info.kind === "link") return linkOnlyView();
        let index = 1;
        const attributes = attributesCard(); index += 1;
        const link = destinationCard("link", index); index += 1;
        const copy = destinationCard("copy", index); index += 1;
        const move = destinationCard("move", index); index += 1;
        const dynamic = dynamicCard(index); if (dynamic) index += 1;
        const replication = replicationCard(index); if (replication) index += 1;
        return `${identityCard(state.info)}${attributes}${link}${copy}${move}${dynamic}${replication}${dangerCard(index)}`;
    }

    function confirmModal() {
        if (!state.confirm) return "";
        const confirm = state.confirm;
        const isDeleteSecond = confirm.kind === "delete-second";
        return `
            <div class="manager-modal-backdrop">
                <section class="manager-modal ${confirm.danger ? "manager-modal--danger" : ""}" role="alertdialog" aria-modal="true">
                    <div class="manager-modal-icon">${confirm.danger ? "!" : "✓"}</div>
                    <h2>${h(confirm.title)}</h2>
                    <p>${h(confirm.description)}</p>
                    ${confirm.details || ""}
                    ${isDeleteSecond ? `<label class="manager-confirm-path"><span>Введите точный путь</span><input id="manager-delete-confirmation" autocomplete="off" placeholder="${h(state.info.path)}"></label>` : ""}
                    <div class="manager-modal-actions">
                        <button class="button button--secondary" id="manager-confirm-cancel" type="button">Отменить</button>
                        <button class="button ${confirm.danger ? "button--danger" : ""}" id="manager-confirm-accept" type="button" ${state.acting ? "disabled" : ""}>${state.acting ? "Выполняется…" : h(confirm.label)}</button>
                    </div>
                </section>
            </div>`;
    }

    function render() {
        root.innerHTML = `${pageHeader()}${searchCard()}<div class="manager-results">${resultView()}</div>${confirmModal()}`;
        bindEvents();
    }

    function syncAttributeDirtyState(input) {
        const changes = changedAttributes();
        const saveButton = document.getElementById("manager-save-attributes");
        const summary = saveButton?.previousElementSibling;
        if (saveButton) saveButton.disabled = Object.keys(changes).length === 0;
        if (summary) summary.textContent = Object.keys(changes).length
            ? `Изменено полей: ${Object.keys(changes).length}`
            : "Изменений нет";
        if (input?.dataset.managerAttribute) {
            const key = input.dataset.managerAttribute;
            input.closest(".manager-field")?.classList.toggle(
                "is-changed",
                !jsonEqual(state.attributes[key], state.info.initialAttributes[key]),
            );
        }
    }

    async function inspectCurrent() {
        const token = localStorage.getItem(services.tokenKey);
        if (!token) {
            services.showTokenModal("yt");
            return;
        }
        state.controller?.abort();
        state.controller = new AbortController();
        state.loading = true;
        state.error = "";
        state.info = null;
        render();
        try {
            const result = await services.apiFetch("/api/yt/manager/inspect", {
                method: "POST",
                headers: ytHeaders(),
                body: JSON.stringify({ cluster: state.cluster, path: state.path.trim() }),
                signal: state.controller.signal,
            });
            state.info = result;
            state.attributes = structuredClone(result.initialAttributes || {});
            state.destinationChecks = { link: null, copy: null, move: null };
        } catch (error) {
            if (error.name !== "AbortError") state.error = error.message;
        } finally {
            state.loading = false;
            state.controller = null;
            render();
        }
    }

    async function validateDestination(operation) {
        const destinationPath = state.destinations[operation].trim();
        state.checkingDestination = operation;
        state.destinationChecks[operation] = null;
        render();
        try {
            const result = await services.apiFetch("/api/yt/manager/validate-destination", {
                method: "POST",
                headers: ytHeaders(),
                body: JSON.stringify({
                    cluster: state.info.cluster,
                    sourcePath: state.info.path,
                    destinationPath,
                    operation,
                }),
            });
            state.destinationChecks[operation] = result;
        } catch (error) {
            services.showToast(error.message, "error");
        } finally {
            state.checkingDestination = null;
            render();
        }
    }

    function openActionConfirm(payload, title, description, label, danger = false, details = "") {
        state.confirm = { kind: "action", payload, title, description, label, danger, details };
        render();
    }

    async function runAction(payload) {
        state.acting = true;
        render();
        try {
            await services.apiFetch("/api/yt/manager/action", {
                method: "POST",
                headers: ytHeaders(),
                body: JSON.stringify({
                    confirmed: true,
                    cluster: state.info.cluster,
                    path: state.info.path,
                    ...payload,
                }),
            });
            const action = payload.action;
            state.confirm = null;
            if (action === "delete") {
                state.info = null;
                state.error = "";
                state.acting = false;
                services.showToast("YT-нода удалена");
                render();
                return;
            }
            if (action === "move") state.path = payload.destinationPath;
            state.acting = false;
            services.showToast("Операция выполнена");
            await inspectCurrent();
        } catch (error) {
            services.showToast(error.message, "error");
            state.confirm = null;
            state.acting = false;
            render();
        }
    }

    function beginDelete() {
        state.confirm = {
            kind: "delete-first",
            title: `Удалить: ${state.info.kindLabel}?`,
            description: "Проверьте полный план. После следующего подтверждения операция станет необратимой.",
            label: "Продолжить",
            danger: true,
            details: deletePlanHtml(state.info.deletePlan),
        };
        render();
    }

    function acceptConfirm() {
        const confirm = state.confirm;
        if (!confirm) return;
        if (confirm.kind === "delete-first") {
            state.confirm = {
                kind: "delete-second",
                title: "Последнее подтверждение",
                description: "Введите путь без изменений. Сервер ещё раз проверит тип, состояние и реплики.",
                label: "Удалить навсегда",
                danger: true,
            };
            render();
            return;
        }
        if (confirm.kind === "delete-second") {
            const input = document.getElementById("manager-delete-confirmation");
            if (input.value !== state.info.path) {
                input.classList.add("is-error");
                services.showToast("Путь не совпадает", "error");
                return;
            }
            runAction({ action: "delete", doubleConfirmed: true, confirmationText: input.value });
            return;
        }
        runAction(confirm.payload);
    }

    function bindEvents() {
        document.getElementById("manager-inspect-form")?.addEventListener("submit", (event) => {
            event.preventDefault();
            state.path = document.getElementById("manager-path").value;
            inspectCurrent();
        });
        document.getElementById("manager-cluster")?.addEventListener("change", (event) => {
            state.cluster = event.target.value;
            localStorage.setItem("dev-toolbox:yt-cluster", state.cluster);
            state.info = null;
            state.error = "";
            render();
        });
        document.getElementById("manager-path")?.addEventListener("input", (event) => { state.path = event.target.value; });
        document.getElementById("manager-stop")?.addEventListener("click", () => {
            state.controller?.abort();
            state.loading = false;
            state.controller = null;
            state.error = "Поиск остановлен";
            render();
        });

        root.querySelectorAll("[data-manager-attribute]").forEach((input) => {
            input.addEventListener("input", (event) => {
                const key = event.target.dataset.managerAttribute;
                state.attributes[key] = event.target.type === "number"
                    ? (event.target.value === "" ? "" : Number(event.target.value))
                    : event.target.value;
                syncAttributeDirtyState(event.target);
            });
        });
        document.getElementById("manager-codec-family")?.addEventListener("change", (event) => {
            setCodecFamily(event.target.value);
            render();
        });
        document.getElementById("manager-codec-level")?.addEventListener("input", (event) => {
            setCodecLevel(event.target.value);
            syncAttributeDirtyState(event.target);
        });
        document.getElementById("manager-reset-attributes")?.addEventListener("click", () => {
            state.attributes = structuredClone(state.info.initialAttributes || {});
            render();
        });
        document.getElementById("manager-save-attributes")?.addEventListener("click", () => {
            openActionConfirm(
                { action: "update_attributes", changes: changedAttributes() },
                "Применить новые атрибуты?",
                `Изменения будут записаны в ${state.info.cluster}:${state.info.path}`,
                "Применить",
            );
        });

        root.querySelectorAll("[data-manager-destination]").forEach((input) => {
            input.addEventListener("input", (event) => {
                const operation = event.target.dataset.managerDestination;
                state.destinations[operation] = event.target.value;
                state.destinationChecks[operation] = null;
            });
        });
        root.querySelectorAll("[data-manager-check]").forEach((button) => {
            button.addEventListener("click", () => validateDestination(button.dataset.managerCheck));
        });
        root.querySelectorAll("[data-manager-run-destination]").forEach((button) => {
            button.addEventListener("click", () => {
                const operation = button.dataset.managerRunDestination;
                const destinationPath = state.destinations[operation].trim();
                openActionConfirm(
                    { action: operation, destinationPath },
                    `${ACTION_LABELS[operation]}?`,
                    `${state.info.cluster}: ${state.info.path} → ${destinationPath}`,
                    ACTION_LABELS[operation],
                    operation === "move",
                );
            });
        });
        root.querySelectorAll("[data-manager-simple-action]").forEach((button) => {
            button.addEventListener("click", () => {
                const action = button.dataset.managerSimpleAction;
                openActionConfirm(
                    { action },
                    action === "mount" ? "Смонтировать таблицу?" : "Отмонтировать таблицу?",
                    `${state.info.cluster}: ${state.info.path}`,
                    action === "mount" ? "Смонтировать" : "Отмонтировать",
                    action === "unmount",
                );
            });
        });
        document.getElementById("manager-global-tracker")?.addEventListener("click", (event) => {
            const enabled = event.currentTarget.dataset.enabled === "true";
            openActionConfirm(
                { action: "update_global_tracker", enabled },
                `${enabled ? "Включить" : "Выключить"} automatic mode switch?`,
                "Настройка применяется к logical replicated_table в Miranda.",
                enabled ? "Включить" : "Выключить",
            );
        });
        root.querySelectorAll("[data-save-replica]").forEach((button) => {
            button.addEventListener("click", () => {
                const row = button.closest("[data-replica-id]");
                const replicaId = row.dataset.replicaId;
                const replicaState = row.querySelector('[data-replica-field="state"]').value;
                const mode = row.querySelector('[data-replica-field="mode"]').value;
                const trackerEnabled = row.querySelector('[data-replica-field="tracker"]').checked;
                openActionConfirm(
                    { action: "update_replica", replicaId, state: replicaState, mode, trackerEnabled },
                    "Изменить реплику?",
                    `${replicaId}: state=${replicaState}, mode=${mode}, tracker=${trackerEnabled ? "on" : "off"}`,
                    "Применить",
                );
            });
        });
        document.getElementById("manager-delete")?.addEventListener("click", beginDelete);
        document.getElementById("manager-confirm-cancel")?.addEventListener("click", () => {
            if (!state.acting) { state.confirm = null; render(); }
        });
        document.getElementById("manager-confirm-accept")?.addEventListener("click", acceptConfirm);
    }

    function renderManager(target, providedServices) {
        root = target;
        services = providedServices;
        state = initialState();
        render();
    }

    function destroy() {
        state?.controller?.abort();
    }

    return { render: renderManager, destroy };
})();
