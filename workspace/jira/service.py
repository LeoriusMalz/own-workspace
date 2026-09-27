"""Project metadata, epic search, and validated Jira issue creation."""

import re

from workspace.jira.client import JiraError
from workspace.jira.composer import ASSIGNEE, ASSIGNEE_LABEL, compose, text


FIELD_NAMES = {"plannedStart": "Planned Start", "plannedEnd": "Planned End", "epic": "Epic Link"}
STANDARD_FIELDS = {"project", "issuetype", "summary", "description", "assignee"}


def project_key(value):
    value = text(value, "Проект", 50, required=True).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        raise ValueError("Некорректный ключ проекта")
    return value


def field_mapping(fields):
    result = {}
    for key, name in FIELD_NAMES.items():
        matches = [field_id for field_id, field in fields.items() if field.get("name", "").casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError(f"В проекте несколько полей {name}; требуется уточнить настройку Jira")
        result[key] = matches[0] if matches else None
    return result


def metadata(client, project, issue_type=""):
    project = project_key(project)
    types = [item for item in client.issue_types(project) if not item.get("subtask")]
    if not types:
        raise JiraError("Нет доступных типов задач. Проверьте проект и право создания задач.", 403)
    if not issue_type:
        preferred = next((item for item in types if item["name"].casefold() in ("task", "задача")), types[0])
        issue_type = str(preferred["id"])
    if not any(str(item["id"]) == issue_type for item in types):
        raise ValueError("Выбранный тип задачи недоступен в проекте")
    fields = client.fields(project, issue_type)
    mapping = field_mapping(fields)
    own = STANDARD_FIELDS | set(mapping.values())
    required = [dict(field, id=field_id) for field_id, field in fields.items()
                if field.get("required") and not field.get("hasDefaultValue") and field_id not in own]
    return {
        "project": project,
        "issueTypes": [{"id": str(t["id"]), "name": t["name"]} for t in types],
        "issueType": issue_type,
        "mapping": mapping,
        "requiredFields": required,
        "assignee": {"name": ASSIGNEE, "label": ASSIGNEE_LABEL},
        "fields": fields,
    }


def search_epics(client, project, query):
    project = project_key(project)
    query = text(query, "Поиск эпика", 100)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", query):
        # A selected epic may belong to another project.
        jql = f'issuetype = Epic AND key = "{query.upper()}"'
    else:
        jql = f'project = "{project}" AND issuetype = Epic'
        if query:
            # Quote a Lucene phrase, then quote the complete JQL string.
            escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r'\\\1', query)
            literal = f'"{escaped}"'.replace('\\', '\\\\').replace('"', '\\"')
            jql += f' AND summary ~ "{literal}"'
    result = client.get("/search", jql=jql + " ORDER BY updated DESC", maxResults=30, fields="summary")
    return [{"key": issue["key"], "summary": issue["fields"]["summary"], "url": client.issue_url(issue["key"])}
            for issue in result.get("issues", [])]


def extra_value(field, value):
    schema = field.get("schema", {})
    choices = field.get("allowedValues") or []
    if choices:
        def select(item):
            match = next((option for option in choices if str(option.get("id", option.get("value", option.get("name")))) == str(item)), None)
            if match is None:
                raise ValueError(f"{field['name']}: выберите значение из списка")
            for key in ("id", "value", "name"):
                if key in match:
                    return {key: match[key]}
        if schema.get("type") == "array":
            if not isinstance(value, list) or not value:
                raise ValueError(f"{field['name']}: выберите хотя бы одно значение")
            return [select(item) for item in value]
        return select(value)
    if schema.get("type") == "number":
        import math
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError(f"{field['name']}: введите число")
        return value
    if schema.get("type") in ("string", "date", "datetime"):
        return text(value, field["name"], 4000, required=True)
    raise ValueError(f"Обязательное поле «{field['name']}» пока не поддерживается конструктором")


def create_issue(client, body):
    composed = compose(body)
    project = project_key(body.get("project"))
    issue_type = text(body.get("issueType"), "Тип задачи Jira", 30, required=True)
    meta = metadata(client, project, issue_type)
    fields = {"project": {"key": project}, "issuetype": {"id": issue_type},
              "summary": composed["summary"], "assignee": {"name": ASSIGNEE}}
    if composed["description"]:
        fields["description"] = composed["description"]
    for key in ("plannedStart", "plannedEnd"):
        if composed.get(key):
            field_id = meta["mapping"][key]
            if not field_id:
                raise ValueError(f"{FIELD_NAMES[key]} отсутствует на экране создания этого типа задач")
            fields[field_id] = composed[key]
    epic = text(body.get("epic"), "Epic Link", 80)
    if epic:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", epic):
            raise ValueError("Выберите Epic Link из результатов поиска")
        field_id = meta["mapping"]["epic"]
        if not field_id:
            raise ValueError("Epic Link недоступен для этого типа задачи")
        matches = search_epics(client, project, epic)
        if not any(item["key"] == epic for item in matches):
            raise ValueError("Выбранный эпик не найден или недоступен")
        fields[field_id] = epic
    extras = body.get("extraFields", {})
    if not isinstance(extras, dict):
        raise ValueError("Некорректные дополнительные поля")
    for field in meta["requiredFields"]:
        fields[field["id"]] = extra_value(field, extras.get(field["id"]))
    for field_id, field in meta["fields"].items():
        if field.get("required") and not field.get("hasDefaultValue") and fields.get(field_id) in (None, "", []):
            raise ValueError(f"Заполните обязательное поле «{field['name']}»")
    # All fields, including the epic, are sent in one request. No partial post-create update.
    result = client.request("POST", "/issue", body={"fields": fields})
    if not isinstance(result, dict):
        raise JiraError("Jira вернула неожиданный ответ. Проверьте, создалась ли задача.", uncertain=True)
    key = result.get("key")
    return {"key": key, "url": client.issue_url(key), **composed}
