"""Build Jira summaries and wiki descriptions from the task constructor."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ASSIGNEE = "lev.maltsev@mail.msk"
ASSIGNEE_LABEL = "Мальцев Лев · lev.maltsev@vkteam.ru"


def text(value, label, limit, *, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label}: ожидается текст")
    value = value.strip()
    if required and not value:
        raise ValueError(f"Заполните поле «{label}»")
    if len(value) > limit:
        raise ValueError(f"{label}: максимум {limit} символов")
    return value


def summary(body):
    title = text(body.get("title"), "Название задачи", 255, required=True)
    subproject = text(body.get("subproject"), "Подпроект", 80)
    kind = text(body.get("kind"), "Вид задачи", 80)
    result = " | ".join(part for part in (f"[{subproject}]" if subproject else "", kind, title) if part)
    if len(result) > 255 or "\n" in result or "\r" in result:
        raise ValueError("Итоговое название должно быть одной строкой длиной до 255 символов")
    return result


def description(body):
    prose = text(body.get("description"), "Описание", 30000)
    experiment = body.get("experiment")
    if experiment is None:
        return prose
    if not isinstance(experiment, dict) or type(experiment.get("enabled")) is not bool:
        raise ValueError("Некорректные параметры эксперимента")
    if not experiment["enabled"]:
        return prose
    try:
        audience = Decimal(str(experiment.get("audience", "")))
    except InvalidOperation:
        raise ValueError("Введите процент аудитории от 0 до 100") from None
    if not audience.is_finite() or not 0 < audience <= 100:
        raise ValueError("Процент аудитории должен быть больше 0 и не больше 100")
    count = experiment.get("count")
    if type(count) is not int or not 1 <= count <= 26:
        raise ValueError("Количество групп должно быть целым числом от 1 до 26")
    groups = experiment.get("groups")
    if not isinstance(groups, list) or len(groups) != count:
        raise ValueError("Число описаний должно совпадать с количеством групп")
    layer = text(experiment.get("layer"), "Слой эксперимента", 200, required=True)
    group_texts = [text(value, f"Группа {chr(65 + i)}", 2000) for i, value in enumerate(groups)]
    if any("{noformat}" in value.lower() for value in [layer, *group_texts]):
        raise ValueError("Не добавляйте {noformat} внутри параметров эксперимента")
    share = format((audience / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")
    rows = "\n".join(f"{chr(65 + i)} - {value}" for i, value in enumerate(group_texts))
    block = "{noformat}\n" + "x".join([share] * count) + f"\n{layer}\n\n{rows}\n" + "{noformat}\n\n----"
    result = block + (f"\n\n{prose}" if prose else "")
    if len(result) > 32767:
        raise ValueError("Итоговое описание превышает 32767 символов")
    return result


def planned_dates(body):
    result = {}
    for key, label in (("plannedStart", "Planned Start"), ("plannedEnd", "Planned End")):
        value = text(body.get(key), label, 10)
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{label}: используйте дату YYYY-MM-DD") from None
            if parsed.isoformat() != value:
                raise ValueError(f"{label}: используйте дату YYYY-MM-DD")
            result[key] = value
    if result.get("plannedStart") and result.get("plannedEnd") and result["plannedEnd"] < result["plannedStart"]:
        raise ValueError("Planned End не может быть раньше Planned Start")
    return result


def compose(body):
    return {"summary": summary(body), "description": description(body), **planned_dates(body)}
