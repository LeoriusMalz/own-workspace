from __future__ import annotations

import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from workspace import create_app
from workspace.jira import service, storage
from workspace.jira.client import JiraClient, JiraError
from workspace.jira.composer import ASSIGNEE, compose


def task(**changes):
    result = {"project": "UCP", "issueType": "100", "title": "Greenline на мягких подписках", "subproject": "Greenline", "kind": "Релиз", "description": "Текст", "plannedStart": "2026-09-27", "plannedEnd": "", "epic": "", "extraFields": {}, "experiment": {"enabled": False}}
    result.update(changes)
    return result


class FakeJira:
    def __init__(self):
        self.created = []
        self.queries = []
        self.field_map = {name: {"fieldId": name, "name": name, "required": True} for name in ("project", "issuetype", "summary", "assignee")}
        for field_id, name in (("customfield_1", "Planned Start"), ("customfield_2", "Planned End"), ("customfield_3", "Epic Link")):
            self.field_map[field_id] = {"fieldId": field_id, "name": name, "schema": {"type": "string"}}

    def issue_types(self, project):
        return [{"id": "100", "name": "Task"}, {"id": "101", "name": "Bug"}, {"id": "102", "name": "Sub-task", "subtask": True}]

    def fields(self, project, issue_type):
        return self.field_map

    def get(self, path, **params):
        self.queries.append(params)
        return {"issues": [{"key": "UCP-123", "fields": {"summary": "Greenline"}}]}

    def request(self, method, path, *, body):
        self.created.append(body)
        return {"key": "UCP-999"}

    def issue_url(self, key):
        return f"https://jira.vk.team/browse/{key}"


class ComposerTest(unittest.TestCase):
    def test_title_components_are_optional(self):
        self.assertEqual(compose(task())["summary"], "[Greenline] | Релиз | Greenline на мягких подписках")
        self.assertEqual(compose(task(kind=""))["summary"], "[Greenline] | Greenline на мягких подписках")
        self.assertEqual(compose(task(kind="", subproject=""))["summary"], "Greenline на мягких подписках")
        for title in ("", " ", "a\nb", "x" * 255):
            with self.subTest(title=title), self.assertRaises(ValueError):
                compose(task(title=title))

    def test_exact_experiment_markup(self):
        experiment = {"enabled": True, "audience": "20", "count": 4, "layer": "слой", "groups": ["контроль описание", "описание", "описание", "описание"]}
        expected = "{noformat}\n5x5x5x5\nслой\n\nA - контроль описание\nB - описание\nC - описание\nD - описание\n{noformat}\n\n----\n\nТекст"
        self.assertEqual(compose(task(experiment=experiment))["description"], expected)
        experiment["count"] = 3
        experiment["groups"] = ["новый контроль", "", ""]
        self.assertIn("6.67x6.67x6.67", compose(task(experiment=experiment))["description"])
        self.assertIn("A - новый контроль", compose(task(experiment=experiment))["description"])
        experiment["audience"] = "0.03"
        experiment["count"] = 2
        experiment["groups"] = ["", ""]
        self.assertIn("0.02x0.02", compose(task(experiment=experiment))["description"])

    def test_invalid_experiments(self):
        base = {"enabled": True, "audience": 20, "count": 2, "layer": "слой", "groups": ["контроль", ""]}
        for changes in ({"audience": "NaN"}, {"audience": "Infinity"}, {"audience": 0}, {"audience": 101}, {"count": True}, {"count": 2.5}, {"count": 27}, {"groups": []}, {"layer": ""}, {"layer": "{noformat}"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compose(task(experiment={**base, **changes}))

    def test_optional_text_and_dates(self):
        self.assertEqual(compose(task(description="", plannedStart="")), {"summary": "[Greenline] | Релиз | Greenline на мягких подписках", "description": ""})
        self.assertEqual(compose(task(experiment={"enabled": False, "count": "wrong"}))["description"], "Текст")
        for changes in ({"plannedEnd": "2026-09-26"}, {"plannedStart": "2026-02-30"}, {"plannedStart": "20260927"}):
            with self.assertRaises(ValueError):
                compose(task(**changes))


class JiraServiceTest(unittest.TestCase):
    def setUp(self):
        self.jira = FakeJira()

    def test_create_sends_all_fields_once_and_forces_assignee(self):
        result = service.create_issue(self.jira, task(epic="UCP-123", plannedEnd="2026-09-28", assignee="someone-else", extraFields={"assignee": "attacker"}))
        self.assertEqual(result["url"], "https://jira.vk.team/browse/UCP-999")
        self.assertEqual(len(self.jira.created), 1)
        self.assertEqual(self.jira.created[0]["fields"], {
            "project": {"key": "UCP"}, "issuetype": {"id": "100"}, "summary": "[Greenline] | Релиз | Greenline на мягких подписках",
            "description": "Текст", "assignee": {"name": ASSIGNEE}, "customfield_1": "2026-09-27", "customfield_2": "2026-09-28", "customfield_3": "UCP-123",
        })

    def test_required_custom_fields_are_discovered(self):
        self.jira.field_map["customfield_team"] = {"name": "Команда", "required": True, "schema": {"type": "option"}, "allowedValues": [{"id": "45", "value": "Команда пользователя"}]}
        with self.assertRaises(ValueError):
            service.create_issue(self.jira, task())
        self.assertEqual(self.jira.created, [])
        service.create_issue(self.jira, task(extraFields={"customfield_team": "45"}))
        self.assertEqual(self.jira.created[0]["fields"]["customfield_team"], {"id": "45"})

    def test_metadata_and_optional_fields(self):
        meta = service.metadata(self.jira, "ucp")
        self.assertEqual(meta["project"], "UCP")
        self.assertEqual(meta["issueType"], "100")
        self.assertEqual(len(meta["issueTypes"]), 2)
        service.create_issue(self.jira, task(plannedStart="", description=""))
        self.assertNotIn("customfield_1", self.jira.created[0]["fields"])
        self.assertNotIn("description", self.jira.created[0]["fields"])

    def test_reject_invalid_type_and_missing_epic(self):
        for changes in ({"issueType": "unknown"}, {"epic": "UCP-456"}, {"project": 'UCP" OR 1=1'}, {"epic": 'UCP-1"'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.create_issue(self.jira, task(**changes))
        self.assertEqual(self.jira.created, [])

    def test_missing_planned_field_is_not_silently_dropped(self):
        del self.jira.field_map["customfield_1"]
        with self.assertRaisesRegex(ValueError, "Planned Start"):
            service.create_issue(self.jira, task())
        self.assertEqual(self.jira.created, [])

    def test_required_numeric_field_accepts_zero(self):
        self.jira.field_map["customfield_number"] = {"name": "Оценка", "required": True, "schema": {"type": "number"}}
        service.create_issue(self.jira, task(extraFields={"customfield_number": 0}))
        self.assertEqual(self.jira.created[0]["fields"]["customfield_number"], 0)

    def test_epic_search_escapes_jql_and_supports_other_project_keys(self):
        service.search_epics(self.jira, "UCP", 'foo" OR project = ABC')
        query = self.jira.queries[-1]["jql"]
        self.assertTrue(query.startswith('project = "UCP" AND issuetype = Epic AND summary ~ "'))
        self.assertIn('\\"', query)
        service.search_epics(self.jira, "UCP", "OTHER-42")
        self.assertEqual(self.jira.queries[-1]["jql"], 'issuetype = Epic AND key = "OTHER-42" ORDER BY updated DESC')


class JiraEndpointTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "jira" / "options.json"
        patcher = patch.object(storage, "OPTIONS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def test_options_persist_and_deduplicate(self):
        self.assertEqual(self.client.get("/api/jira/options").json["result"], {"subprojects": [], "kinds": []})
        for value in ("Greenline", "greenline"):
            self.assertEqual(self.client.post("/api/jira/options", json={"category": "subprojects", "value": value}).status_code, 200)
        self.assertEqual(json.loads(self.path.read_text())["subprojects"], ["Greenline"])
        self.client.post("/api/jira/options", json={"category": "kinds", "value": "Релиз"})
        self.client.delete("/api/jira/options", json={"category": "subprojects", "value": "Greenline"})
        self.assertEqual(self.client.get("/api/jira/options").json["result"], {"subprojects": [], "kinds": ["Релиз"]})
        self.assertEqual(self.client.post("/api/jira/options", json={"category": "../bad", "value": "x"}).status_code, 400)

    def test_concurrent_option_updates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: storage.change_option("kinds", f"Вид {i}"), range(20)))
        self.assertEqual(len(storage.load_options()["kinds"]), 20)

    def test_missing_token_and_invalid_preview(self):
        with patch.dict("os.environ", {"JIRA_TOKEN": ""}):
            self.assertEqual(self.client.get("/api/jira/metadata").status_code, 401)
        self.assertEqual(self.client.post("/api/jira/preview", json=task(title="")).status_code, 400)
        self.assertEqual(self.client.post("/api/jira/preview", json=task()).json["result"], compose(task()))

    def test_routes_and_api_use_one_client_without_returning_token(self):
        fake = FakeJira()
        with patch("workspace.routes.jira.JiraClient", return_value=fake) as constructor:
            response = self.client.post("/api/jira/issues", headers={"X-Jira-Token": "private-test-token"}, json=task())
            constructor.assert_called_once_with("private-test-token")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["result"]["key"], "UCP-999")
            self.assertNotIn("private-test-token", response.text)
        for path in ("/tools/jira-creator", "/jira-creator.js", "/jira-creator.css"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)

    def test_failed_create_is_not_retried(self):
        fake = FakeJira()
        fake.request = Mock(side_effect=JiraError("Ответ не получен", uncertain=True))
        with patch("workspace.routes.jira.JiraClient", return_value=fake):
            response = self.client.post("/api/jira/issues", json=task())
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json["uncertain"])
        self.assertEqual(fake.request.call_count, 1)


class JiraTransportTest(unittest.TestCase):
    def test_pagination_and_fixed_auth(self):
        jira = JiraClient("secret")
        with patch.object(jira, "get", side_effect=[{"values": [{"id": "1"}], "isLast": False}, {"values": [{"id": "2"}], "isLast": True}]) as get:
            self.assertEqual(jira.issue_types("UCP"), [{"id": "1"}, {"id": "2"}])
            self.assertEqual(get.call_args.kwargs["startAt"], 1)
        response = io.BytesIO(b'{"key":"UCP-1"}')
        response.code = 201
        opener = Mock()
        opener.open.return_value = response
        with patch("workspace.jira.client.build_opener", return_value=opener):
            self.assertEqual(jira.request("POST", "/issue", body={"fields": {}}), {"key": "UCP-1"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.full_url, "https://jira.vk.team/rest/api/2/issue")

    def test_auth_errors_and_ambiguous_timeout(self):
        jira = JiraClient("secret")
        opener = Mock()
        opener.open.side_effect = URLError("network failure")
        with patch("workspace.jira.client.build_opener", return_value=opener), self.assertRaises(JiraError) as caught:
            jira.request("POST", "/issue", body={})
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(opener.open.call_count, 1)
        opener.open.side_effect = HTTPError("https://jira.vk.team", 401, "Unauthorized", {}, io.BytesIO(b'{}'))
        with patch("workspace.jira.client.build_opener", return_value=opener), self.assertRaises(JiraError) as caught:
            jira.get("/myself")
        self.assertEqual(caught.exception.status, 401)

    def test_bad_server_response_and_links(self):
        jira = JiraClient("secret")
        with self.assertRaises(JiraError):
            jira.issue_url("javascript:alert(1)")
        response = io.BytesIO(b'<html>Login</html>')
        response.code = 200
        with patch("workspace.jira.client.build_opener") as opener, self.assertRaises(JiraError) as caught:
            opener.return_value.open.return_value = response
            jira.request("POST", "/issue", body={})
        self.assertTrue(caught.exception.uncertain)

    def test_redirect_does_not_repeat_create_or_forward_token(self):
        jira = JiraClient("secret")
        redirect = HTTPError("https://jira.vk.team", 307, "Redirect", {"Location": "https://other.example"}, io.BytesIO(b''))
        with patch("workspace.jira.client.build_opener") as factory, self.assertRaises(JiraError) as caught:
            factory.return_value.open.side_effect = redirect
            jira.request("POST", "/issue", body={})
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(factory.return_value.open.call_count, 1)
        handler = factory.call_args.args[0]
        self.assertIsNone(handler.redirect_request(None, None, 307, "Redirect", {}, "https://other.example"))


if __name__ == "__main__":
    unittest.main()
