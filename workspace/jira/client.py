"""Small Jira Server/Data Center REST client using personal access tokens."""

import json
import os
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JiraError(Exception):
    def __init__(self, message, status=502, *, fields=None, uncertain=False):
        super().__init__(message)
        self.status = status
        self.fields = fields or {}
        self.uncertain = uncertain


class JiraClient:
    def __init__(self, token):
        self.base_url = os.environ.get("JIRA_URL", "https://jira.vk.team").rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise JiraError("JIRA_URL должен быть HTTPS-адресом Jira", 503)
        self.token = (token or os.environ.get("JIRA_TOKEN", "")).strip()
        if not self.token:
            raise JiraError("Добавьте Jira-токен в настройках", 401)
        if "\n" in self.token or "\r" in self.token:
            raise JiraError("Некорректный Jira-токен", 401)

    def request(self, method, path, *, params=None, body=None):
        url = self.base_url + "/rest/api/2" + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = build_opener(NoRedirect()).open(request, timeout=25)
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                raw = response.read()
        except (URLError, OSError, HTTPException):
            uncertain = method == "POST"
            message = "Не удалось связаться с Jira. Проверьте сеть/VPN."
            if uncertain:
                message = "Ответ Jira не получен. Задача могла создаться: проверьте Jira перед повторной отправкой."
            raise JiraError(message, 502, uncertain=uncertain) from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            payload = None
        if not 200 <= status < 300:
            if status == 401:
                raise JiraError("Jira-токен не принят или истёк. Обновите его в настройках.", 401)
            if status == 403:
                raise JiraError("Jira запретила действие. Проверьте права доступа к проекту.", 403)
            fields = payload.get("errors", {}) if isinstance(payload, dict) else {}
            messages = payload.get("errorMessages", []) if isinstance(payload, dict) else []
            message = "; ".join([str(m) for m in messages] + [f"{k}: {v}" for k, v in fields.items()])
            uncertain = method == "POST" and (status >= 500 or 300 <= status < 400)
            if uncertain:
                message = "Jira вернула ошибку сервера. Проверьте, создалась ли задача, перед повторной отправкой."
            raise JiraError(message[:2000] or f"Jira вернула HTTP {status}",
                            status if status in (400, 404, 429) else 502,
                            fields=fields, uncertain=uncertain)
        if not isinstance(payload, (dict, list)):
            raise JiraError("Jira вернула неожиданный ответ. Проверьте адрес и авторизацию.", uncertain=method == "POST")
        return payload

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def pages(self, path):
        values = []
        for _ in range(100):
            page = self.get(path, startAt=len(values), maxResults=50)
            batch = page.get("values", [])
            values.extend(batch)
            if not batch or page.get("isLast") is True or len(values) >= page.get("total", float("inf")):
                return values
        raise JiraError("Jira вернула слишком много страниц метаданных")

    def issue_types(self, project):
        return self.pages(f"/issue/createmeta/{quote(project, safe='')}/issuetypes")

    def fields(self, project, issue_type):
        values = self.pages(f"/issue/createmeta/{quote(project, safe='')}/issuetypes/{quote(issue_type, safe='')}")
        return {field["fieldId"]: field for field in values}

    def issue_url(self, key):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", str(key)):
            raise JiraError("Jira вернула некорректный номер задачи. Проверьте список задач.", uncertain=True)
        return f"{self.base_url}/browse/{key}"
