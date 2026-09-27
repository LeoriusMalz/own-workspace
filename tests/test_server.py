from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class YtChild(str):
    def __new__(cls, value: str, attributes: dict):
        child = super().__new__(cls, value)
        child.attributes = attributes
        return child


class YsonSchemaList(list):
    def __init__(self, values: list, attributes: dict | None = None):
        super().__init__(values)
        self.attributes = attributes or {"strict": True, "unique_keys": True}


class FakeYtClient:
    def __init__(self, path: str, attributes: dict, children: list | None = None):
        self.path = path
        self.attributes = attributes
        self.children = children or []

    def exists(self, path: str) -> bool:
        return path == self.path or path in self.attributes

    def get(self, path: str):
        return self.attributes[path]

    def list(self, path: str, attributes: list[str]):
        if path != self.path:
            raise KeyError(path)
        return self.children


class RecordingYtClient:
    def __init__(self, cluster: str):
        self.cluster = cluster
        self.calls: list[tuple] = []

    def create(self, node_type: str, path: str | None = None, attributes: dict | None = None):
        self.calls.append(("create", node_type, path, attributes))
        if node_type == "table_replica":
            return f"replica-{attributes['cluster_name']}"
        return f"node-{self.cluster}"

    def alter_table_replica(self, replica_id: str, **kwargs):
        self.calls.append(("alter_table_replica", replica_id, kwargs))

    def mount_table(self, path: str, **kwargs):
        self.calls.append(("mount_table", path, kwargs))


class DirectoryYtClient:
    def __init__(self, nodes: dict[str, str]):
        self.nodes = dict(nodes)
        self.calls: list[tuple] = []

    def exists(self, path: str) -> bool:
        return path in self.nodes

    def get(self, path: str):
        node_path, attribute = path.rsplit("/@", 1)
        if attribute != "type":
            raise KeyError(path)
        return self.nodes[node_path]

    def create(self, node_type: str, path: str, attributes: dict | None = None):
        if path in self.nodes:
            raise RuntimeError(f"Node already exists: {path}")
        self.nodes[path] = node_type
        self.calls.append(("create", node_type, path, attributes or {}))
        return f"node-{len(self.calls)}"


class ManagerYtClient:
    def __init__(self, nodes: dict[str, dict] | None = None):
        self.nodes = {path: dict(attributes) for path, attributes in (nodes or {}).items()}
        self.calls: list[tuple] = []

    @staticmethod
    def _split_attribute(path: str) -> tuple[str, str] | None:
        if "/@" not in path:
            return None
        node_path, attribute = path.rsplit("/@", 1)
        return node_path.removesuffix("&"), attribute

    def exists(self, path: str) -> bool:
        split = self._split_attribute(path)
        if split:
            node_path, attribute = split
            return node_path in self.nodes and attribute in self.nodes[node_path]
        return path.removesuffix("&") in self.nodes

    def get(self, path: str):
        split = self._split_attribute(path)
        if not split:
            raise KeyError(path)
        node_path, attribute = split
        return self.nodes[node_path][attribute]

    def set(self, path: str, value):
        node_path, attribute = self._split_attribute(path)
        self.nodes[node_path][attribute] = value
        self.calls.append(("set", path, value))

    def remove(self, path: str, **kwargs):
        split = self._split_attribute(path)
        if split:
            node_path, attribute = split
            self.nodes[node_path].pop(attribute, None)
        else:
            self.nodes.pop(path.removesuffix("&"), None)
        self.calls.append(("remove", path, kwargs))

    def copy(self, source: str, destination: str, **kwargs):
        self.nodes[destination] = dict(self.nodes[source])
        self.calls.append(("copy", source, destination, kwargs))

    def move(self, source: str, destination: str, **kwargs):
        self.nodes[destination] = self.nodes.pop(source)
        self.calls.append(("move", source, destination, kwargs))

    def link(self, source: str, destination: str, **kwargs):
        self.nodes[destination] = {"type": "link", "target_path": source, "broken": False}
        self.calls.append(("link", source, destination, kwargs))

    def mount_table(self, path: str, **kwargs):
        self.nodes[path]["tablet_state"] = "mounted"
        self.calls.append(("mount_table", path, kwargs))

    def unmount_table(self, path: str, **kwargs):
        self.nodes[path]["tablet_state"] = "unmounted"
        self.calls.append(("unmount_table", path, kwargs))

    def alter_table_replica(self, replica_id: str, **kwargs):
        object_path = f"#{replica_id}"
        if object_path in self.nodes:
            if kwargs.get("enabled") is not None:
                self.nodes[object_path]["state"] = "enabled" if kwargs["enabled"] else "disabled"
            if kwargs.get("mode") is not None:
                self.nodes[object_path]["mode"] = kwargs["mode"]
        self.calls.append(("alter_table_replica", replica_id, kwargs))

    def search(self, root: str, **_kwargs):
        return [
            YtChild(path, {"type": attributes.get("type")})
            for path, attributes in sorted(self.nodes.items())
            if path == root or path.startswith(f"{root}/")
        ]


class MutatorYtClient(ManagerYtClient):
    def __init__(self, path: str, *, dynamic: bool = True, mounted: bool = True,
                 row_count: int = 0, strict: bool = True, rows: list[dict] | None = None):
        schema = YsonSchemaList([
            {
                "name": "itemId",
                "type": "uint64",
                "required": True,
                "type_v3": "uint64",
                "sort_order": "ascending",
            },
            {
                "name": "actual",
                "type": "boolean",
                "required": True,
                "type_v3": "bool",
            },
            {
                "name": "configMeta",
                "type": "any",
                "required": False,
                "type_v3": {"type_name": "optional", "item": "yson"},
            },
        ], {"strict": strict, "unique_keys": True})
        super().__init__({
            path: {
                "type": "table",
                "dynamic": dynamic,
                "tablet_state": "mounted" if mounted else "unmounted",
                "schema": schema,
                "schema_mode": "strong",
                "row_count": row_count,
                "optimize_for": "lookup",
            },
        })
        self.path = path
        self.rows = [dict(row) for row in (rows or [])]

    def alter_table(self, path: str, *, schema):
        self.nodes[path]["schema"] = schema
        self.nodes[path]["row_count"] = len(self.rows)
        self.calls.append(("alter_table", path, schema))

    def select_rows(self, query: str, **kwargs):
        self.calls.append(("select_rows", query, kwargs))
        if query.startswith("count(*)"):
            return [{"count": len(self.rows)}]
        return iter([dict(row) for row in self.rows])

    def lookup_rows(self, path: str, keys: list[dict], **kwargs):
        self.calls.append(("lookup_rows", path, keys, kwargs))
        result = []
        for key in keys:
            row = next(
                (candidate for candidate in self.rows if all(candidate.get(name) == value for name, value in key.items())),
                None,
            )
            result.append(dict(row) if row is not None else None)
        return result

    class _Transaction:
        def __init__(self, owner, transaction_type: str):
            self.owner = owner
            self.transaction_type = transaction_type

        def __enter__(self):
            self.owner.calls.append(("transaction_enter", self.transaction_type))
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            self.owner.calls.append(("transaction_exit", self.transaction_type, exc_type is None))
            return False

    def Transaction(self, *, type: str):
        return self._Transaction(self, type)

    def delete_rows(self, path: str, keys: list[dict]):
        self.calls.append(("delete_rows", path, keys))
        self.rows = [
            row for row in self.rows
            if not any(all(row.get(name) == value for name, value in key.items()) for key in keys)
        ]

    def insert_rows(self, path: str, rows: list[dict], *, update: bool = False):
        self.calls.append(("insert_rows", path, rows, update))
        for row in rows:
            existing = next((candidate for candidate in self.rows if candidate.get("itemId") == row.get("itemId")), None)
            if existing is not None and update:
                existing.update(row)
            else:
                self.rows.append(dict(row))


class DevToolboxServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        server.DRAFTS_DIR = root / "drafts"
        server.ARTICLES_DIR = root / "articles"
        server.UPLOADS_DIR = root / "uploads"
        server.ensure_directories()
        self.client = server.app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_spa_routes(self) -> None:
        for path in (
            "/",
            "/tools/yt-god",
            "/tools/yt-observer",
            "/tools/yt-creator",
            "/tools/yt-manager",
            "/tools/yt-mutator",
            "/notes",
            "/notes/example",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()

        for path in (
            "/yt-creator.css", "/yt-creator.js", "/yt-manager.css", "/yt-manager.js",
            "/yt-mutator.css", "/yt-mutator.js",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(response.data), 1000)
            response.close()

    def test_manager_detects_link_without_following_it(self) -> None:
        path = "//home/alias"
        yt_client = ManagerYtClient({
            path: {"type": "link", "target_path": "//home/real-table", "broken": False},
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/inspect",
                json={"path": path, "cluster": "jupiter"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["kind"], "link")
        self.assertEqual(result["link"]["targetPath"], "//home/real-table")
        self.assertEqual(
            [name for name, enabled in result["capabilities"].items() if enabled],
            ["delete"],
        )

    def test_manager_loads_dynamic_attributes_and_state(self) -> None:
        path = "//home/dynamic"
        yt_client = ManagerYtClient({
            path: {
                "type": "table",
                "dynamic": True,
                "tablet_state": "mounted",
                "primary_medium": "default",
                "tablet_cell_bundle": "vkvideo",
                "optimize_for": "lookup",
                "compression_codec": "zstd_5",
                "replication_factor": 3,
                "annotation": "Hot config",
            },
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/inspect",
                json={"path": path, "cluster": "saturn"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["kind"], "dynamic_table")
        self.assertEqual(result["tabletState"], "mounted")
        self.assertEqual(result["initialAttributes"]["compressionCodec"], "zstd_5")
        self.assertTrue(result["capabilities"]["mount"])

    def test_manager_destination_validation_rejects_occupied_path(self) -> None:
        yt_client = ManagerYtClient({
            "//home": {"type": "map_node"},
            "//home/source": {"type": "table", "dynamic": False},
            "//home/target": {"type": "map_node"},
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/validate-destination",
                json={
                    "cluster": "miranda",
                    "sourcePath": "//home/source",
                    "destinationPath": "//home/target",
                    "operation": "copy",
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertFalse(result["valid"])
        self.assertTrue(result["targetExists"])

    def test_manager_destination_validation_accepts_special_ancestor(self) -> None:
        yt_client = ManagerYtClient({
            "//home": {"type": "portal_entrance"},
            "//home/team": {"type": "map_node"},
            "//home/team/source": {"type": "table", "dynamic": False},
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/validate-destination",
                json={
                    "cluster": "miranda",
                    "sourcePath": "//home/team/source",
                    "destinationPath": "//home/team/copy",
                    "operation": "copy",
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertTrue(result["valid"])
        self.assertEqual(result["missingDirectories"], [])
        self.assertEqual(result["nonDirectoryNodes"], [])

    def test_mutator_inspect_normalizes_bool_and_yson(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, row_count=2)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "jupiter", "path": path},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        columns = {column["name"]: column for column in result["columns"]}
        self.assertEqual(columns["actual"]["baseType"], "bool")
        self.assertFalse(columns["actual"]["optional"])
        self.assertEqual(columns["configMeta"]["baseType"], "yson")
        self.assertTrue(columns["configMeta"]["optional"])

    def test_mutator_frozen_table_is_read_only(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path)
        yt_client.nodes[path]["tablet_state"] = "frozen"
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "jupiter", "path": path},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertTrue(result["capabilities"]["readRows"])
        self.assertFalse(result["capabilities"]["writeRows"])
        self.assertIn("Frozen", result["restrictions"]["writeRows"])

    def test_mutator_add_optional_column_to_nonempty_strict_table(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, row_count=5, strict=True)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter",
                    "path": path,
                    "change": {"action": "add", "name": "commercial", "baseType": "bool", "optional": True},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        added = response.get_json()["result"]["newSchema"][-1]
        self.assertEqual(added["type"], "boolean")
        self.assertFalse(added["required"])
        self.assertEqual(added["type_v3"], {"type_name": "optional", "item": "bool"})

    def test_mutator_rejects_required_column_on_nonempty_table(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, row_count=5)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter",
                    "path": path,
                    "change": {"action": "add", "name": "commercial", "baseType": "bool", "optional": False},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("только optional", response.get_json()["error"])

    def test_mutator_strict_schema_blocks_column_delete_even_when_empty(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, dynamic=False, row_count=0, strict=True)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {"action": "delete", "name": "actual"},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("strict", response.get_json()["error"].lower())

    def test_mutator_static_strict_toggle_obeys_table_contents(self) -> None:
        path = "//home/configs"
        strict_client = MutatorYtClient(path, dynamic=False, row_count=3, strict=True)
        with patch.object(server, "build_yt_client", return_value=strict_client):
            disable = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {"action": "set_strict", "strict": False},
                },
                headers={"X-YT-Token": "token"},
            )

        nonstrict_client = MutatorYtClient(path, dynamic=False, row_count=3, strict=False)
        with patch.object(server, "build_yt_client", return_value=nonstrict_client):
            enable = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {"action": "set_strict", "strict": True},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(disable.status_code, 200)
        self.assertFalse(disable.get_json()["result"]["schemaAttributes"]["strict"])
        self.assertEqual(enable.status_code, 409)
        self.assertIn("Merge", enable.get_json()["error"])

    def test_mutator_dynamic_table_cannot_disable_strict(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, dynamic=True, strict=True)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {"action": "set_strict", "strict": False},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("динамической", response.get_json()["error"])

    def test_mutator_type_change_allows_only_lossless_integer_widening_on_empty_table(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, dynamic=False, row_count=0)
        yt_client.nodes[path]["schema"].append({
            "name": "score", "type": "int8", "required": True, "type_v3": "int8",
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            widening = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {
                        "action": "change_type", "name": "score",
                        "baseType": "int32", "optional": False,
                    },
                },
                headers={"X-YT-Token": "token"},
            )
            signedness_change = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {
                        "action": "change_type", "name": "score",
                        "baseType": "uint64", "optional": False,
                    },
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(widening.status_code, 200)
        self.assertEqual(signedness_change.status_code, 409)
        self.assertIn("Небезопасное преобразование", signedness_change.get_json()["error"])

    def test_mutator_optional_can_be_enabled_but_not_disabled(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, dynamic=True, row_count=4)
        yt_client.nodes[path]["schema"].append({
            "name": "note", "type": "string", "required": False,
            "type_v3": {"type_name": "optional", "item": "string"},
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            enable = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {
                        "action": "change_type", "name": "actual",
                        "baseType": "bool", "optional": True,
                    },
                },
                headers={"X-YT-Token": "token"},
            )
            disable = self.client.post(
                "/api/yt/mutator/schema/plan",
                json={
                    "cluster": "jupiter", "path": path,
                    "change": {
                        "action": "change_type", "name": "note",
                        "baseType": "string", "optional": False,
                    },
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(enable.status_code, 200)
        self.assertEqual(disable.status_code, 409)
        self.assertIn("нельзя выключить", disable.get_json()["error"])

    def test_mutator_schema_apply_unmounts_and_restores_dynamic_table(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, mounted=True)
        with patch.object(server, "build_yt_client", return_value=yt_client):
            inspect_response = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "saturn", "path": path},
                headers={"X-YT-Token": "token"},
            )
            schema_hash = inspect_response.get_json()["result"]["schemaHash"]
            response = self.client.post(
                "/api/yt/mutator/schema/apply",
                json={
                    "confirmed": True,
                    "cluster": "saturn",
                    "path": path,
                    "expectedSchemaHash": schema_hash,
                    "change": {"action": "add", "name": "note", "baseType": "string", "optional": True},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        operations = [call[0] for call in yt_client.calls if call[0] in {"unmount_table", "alter_table", "mount_table"}]
        self.assertEqual(operations, ["unmount_table", "alter_table", "mount_table"])
        self.assertEqual(yt_client.nodes[path]["tablet_state"], "mounted")

    def test_mutator_schema_apply_restores_mount_after_alter_failure(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, mounted=True)

        def fail_alter(_path: str, *, schema):
            yt_client.calls.append(("alter_table_failed", _path, schema))
            raise RuntimeError("schema rejected")

        yt_client.alter_table = fail_alter
        with patch.object(server, "build_yt_client", return_value=yt_client):
            inspect_response = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "saturn", "path": path},
                headers={"X-YT-Token": "token"},
            )
            schema_hash = inspect_response.get_json()["result"]["schemaHash"]
            response = self.client.post(
                "/api/yt/mutator/schema/apply",
                json={
                    "confirmed": True,
                    "cluster": "saturn",
                    "path": path,
                    "expectedSchemaHash": schema_hash,
                    "change": {"action": "add", "name": "note", "baseType": "string", "optional": True},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "partial_action")
        self.assertEqual(yt_client.nodes[path]["tablet_state"], "mounted")
        self.assertEqual(
            [call[0] for call in yt_client.calls if call[0] in {"unmount_table", "alter_table_failed", "mount_table"}],
            ["unmount_table", "alter_table_failed", "mount_table"],
        )

    def test_mutator_validates_range_and_duplicate_keys(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, rows=[{"itemId": 7, "actual": True, "configMeta": None}])
        with patch.object(server, "build_yt_client", return_value=yt_client):
            invalid = self.client.post(
                "/api/yt/mutator/data/validate-inserts",
                json={
                    "cluster": "jupiter", "path": path,
                    "rows": [{
                        "itemId": {"value": str(2 ** 64), "isNull": False},
                        "actual": {"value": "true", "isNull": False},
                        "configMeta": {"value": "", "isNull": True},
                    }],
                },
                headers={"X-YT-Token": "token"},
            )
            duplicate = self.client.post(
                "/api/yt/mutator/data/validate-inserts",
                json={
                    "cluster": "jupiter", "path": path,
                    "rows": [{
                        "itemId": {"value": "7", "isNull": False},
                        "actual": {"value": "false", "isNull": False},
                        "configMeta": {"value": "", "isNull": True},
                    }],
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(invalid.status_code, 400)
        self.assertIn("18446744073709551615", invalid.get_json()["error"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.get_json()["result"]["existingIndexes"], [0])

    def test_mutator_inserts_a_valid_record_package(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, rows=[])
        with patch.object(server, "build_yt_client", return_value=yt_client):
            inspected = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "jupiter", "path": path},
                headers={"X-YT-Token": "token"},
            ).get_json()["result"]
            response = self.client.post(
                "/api/yt/mutator/data/insert",
                json={
                    "confirmed": True, "cluster": "jupiter", "path": path,
                    "expectedSchemaHash": inspected["schemaHash"],
                    "rows": [{
                        "itemId": {"value": "17", "isNull": False},
                        "actual": {"value": "true", "isNull": False},
                        "configMeta": {"value": "", "isNull": True},
                    }],
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["result"]["inserted"], 1)
        self.assertEqual(yt_client.rows[0]["itemId"], 17)
        self.assertTrue(any(call[0] == "lookup_rows" for call in yt_client.calls))
        self.assertTrue(any(call[0] == "insert_rows" for call in yt_client.calls))

    def test_mutator_key_search_uses_typed_placeholders_and_limit(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, rows=[{"itemId": 7, "actual": True, "configMeta": None}])
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/mutator/data/search",
                json={
                    "cluster": "jupiter", "path": path, "mode": "keys", "limit": 10,
                    "applyLimit": True,
                    "filters": [{
                        "name": "itemId", "enabled": True, "negated": False,
                        "operator": "IN", "values": ["7", "8"],
                    }],
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["total"], 1)
        self.assertIn("[itemId] IN ({m0}, {m1})", result["query"])
        self.assertTrue(result["query"].endswith("LIMIT 10"))
        select_calls = [call for call in yt_client.calls if call[0] == "select_rows"]
        self.assertEqual(int(select_calls[0][2]["placeholder_values"]["m0"]), 7)

    def test_mutator_applies_update_and_delete_in_tablet_transaction(self) -> None:
        path = "//home/configs"
        yt_client = MutatorYtClient(path, rows=[
            {"itemId": 1, "actual": True, "configMeta": None},
            {"itemId": 2, "actual": False, "configMeta": None},
        ])
        with patch.object(server, "build_yt_client", return_value=yt_client):
            inspect_response = self.client.post(
                "/api/yt/mutator/inspect",
                json={"cluster": "miranda", "path": path},
                headers={"X-YT-Token": "token"},
            )
            schema_hash = inspect_response.get_json()["result"]["schemaHash"]
            response = self.client.post(
                "/api/yt/mutator/data/apply",
                json={
                    "confirmed": True, "cluster": "miranda", "path": path,
                    "expectedSchemaHash": schema_hash,
                    "deletes": [{"itemId": {"value": "1", "isNull": False}}],
                    "updates": [{
                        "keys": {"itemId": {"value": "2", "isNull": False}},
                        "changes": {"actual": {"value": "true", "isNull": False}},
                    }],
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["itemId"] for row in yt_client.rows], [2])
        self.assertTrue(yt_client.rows[0]["actual"])
        self.assertIn(("transaction_enter", "tablet"), yt_client.calls)
        self.assertIn(("transaction_exit", "tablet", True), yt_client.calls)

    def test_manager_mount_rechecks_current_state(self) -> None:
        path = "//home/dynamic"
        yt_client = ManagerYtClient({
            path: {"type": "table", "dynamic": True, "tablet_state": "mounted"},
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/action",
                json={"confirmed": True, "action": "mount", "path": path, "cluster": "jupiter"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "state_conflict")
        self.assertFalse(any(call[0] == "mount_table" for call in yt_client.calls))

    def test_manager_updates_only_allowed_attributes(self) -> None:
        path = "//home/static"
        yt_client = ManagerYtClient({path: {"type": "table", "dynamic": False}})
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/manager/action",
                json={
                    "confirmed": True,
                    "action": "update_attributes",
                    "path": path,
                    "cluster": "saturn",
                    "changes": {"compressionCodec": "zstd_7", "annotation": "Archive"},
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(yt_client.nodes[path]["compression_codec"], "zstd_7")
        self.assertEqual(yt_client.nodes[path]["annotation"], "Archive")

    def test_manager_physical_replica_delete_follows_safe_plan(self) -> None:
        path = "//home/replica"
        replica_id = "1-2-3-4"
        child_client = ManagerYtClient({
            path: {
                "type": "table",
                "dynamic": True,
                "tablet_state": "mounted",
                "upstream_replica_id": replica_id,
            },
        })
        meta_client = ManagerYtClient({
            f"#{replica_id}": {
                "cluster_name": "jupiter",
                "replica_path": path,
                "table_path": "//home/logical",
                "state": "enabled",
                "mode": "sync",
            },
        })
        clients = {"jupiter": child_client, "miranda": meta_client}
        with patch.object(server, "build_yt_client", side_effect=lambda _token, cluster: clients[cluster]):
            response = self.client.post(
                "/api/yt/manager/action",
                json={
                    "confirmed": True,
                    "doubleConfirmed": True,
                    "confirmationText": path,
                    "action": "delete",
                    "path": path,
                    "cluster": "jupiter",
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(meta_client.calls[0][0], "alter_table_replica")
        self.assertEqual(meta_client.calls[1][0:2], ("remove", f"#{replica_id}"))
        self.assertEqual(child_client.calls[0][0], "unmount_table")
        self.assertEqual(child_client.calls[1][0:2], ("remove", path))

    def test_manager_logical_replica_delete_removes_every_layer(self) -> None:
        path = "//home/logical"
        replica_id = "5-6-7-8"
        replica_path = "//home/physical"
        meta_client = ManagerYtClient({
            path: {
                "type": "replicated_table",
                "dynamic": True,
                "tablet_state": "mounted",
                "replicas": {
                    replica_id: {
                        "cluster_name": "saturn",
                        "replica_path": replica_path,
                        "table_path": path,
                        "state": "enabled",
                        "mode": "sync",
                    },
                },
                "replicated_table_options": {"enable_replicated_table_tracker": True},
            },
            f"#{replica_id}": {
                "cluster_name": "saturn",
                "replica_path": replica_path,
                "table_path": path,
                "state": "enabled",
                "mode": "sync",
            },
        })
        child_client = ManagerYtClient({
            replica_path: {"type": "table", "dynamic": True, "tablet_state": "mounted"},
        })
        clients = {"miranda": meta_client, "saturn": child_client}
        with patch.object(server, "build_yt_client", side_effect=lambda _token, cluster: clients[cluster]):
            response = self.client.post(
                "/api/yt/manager/action",
                json={
                    "confirmed": True,
                    "doubleConfirmed": True,
                    "confirmationText": path,
                    "action": "delete",
                    "path": path,
                    "cluster": "miranda",
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(f"#{replica_id}", meta_client.nodes)
        self.assertNotIn(replica_path, child_client.nodes)
        self.assertNotIn(path, meta_client.nodes)
        completed = response.get_json()["result"]["completed"]
        self.assertEqual(
            [step["action"] for step in completed],
            ["disable_replica", "remove_replica_object", "unmount", "remove", "unmount", "remove"],
        )

    def test_cluster_allow_list(self) -> None:
        response = self.client.post(
            "/api/yt/table-info",
            json={"path": "//home/example", "cluster": "earth"},
            headers={"X-YT-Token": "not-a-real-token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("jupiter", response.get_json()["error"])

    def test_directory_is_not_reported_as_static_table(self) -> None:
        path = "//home/example"
        client = FakeYtClient(
            path,
            {
                f"{path}/@type": "map_node",
                f"{path}/@owner": "lev",
            },
            [
                YtChild("dynamic_table", {"type": "table", "dynamic": True}),
                YtChild("static_table", {"type": "table", "dynamic": False}),
                YtChild("nested_folder", {"type": "map_node"}),
            ],
        )
        with patch.object(server, "build_yt_client", return_value=client):
            response = self.client.post(
                "/api/yt/table-info",
                json={"path": path, "cluster": "miranda"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["kind"], "directory")
        self.assertEqual(result["tableCount"], 2)
        self.assertEqual(
            [table["path"] for table in result["tables"]],
            [f"{path}/dynamic_table", f"{path}/static_table"],
        )

    def test_access_denied_has_dedicated_error(self) -> None:
        class ForbiddenClient:
            def exists(self, _: str) -> bool:
                raise RuntimeError("Access denied for this user")

        with patch.object(server, "build_yt_client", return_value=ForbiddenClient()):
            response = self.client.post(
                "/api/yt/table-info",
                json={"path": "//secret/table", "cluster": "saturn"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "access_denied")

    def test_schema_json_conversion_normalizes_key_prefix(self) -> None:
        response = self.client.post(
            "/api/yt/creator/schema/convert",
            json={
                "format": "json",
                "text": """[
                    {"name": "value", "type": "string", "required": false, "type_v3": {"type_name": "optional", "item": "string"}},
                    {"name": "itemId", "type_v3": "int64", "sort_order": "ascending"}
                ]""",
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual([column["name"] for column in result["schema"]], ["itemId", "value"])
        self.assertTrue(result["friendlyConvertible"])
        self.assertTrue(result["friendlyColumns"][1]["optional"])
        self.assertEqual(result["schema"][1]["type"], "string")
        self.assertFalse(result["schema"][1]["required"])

    def test_directory_validation_marks_every_missing_path(self) -> None:
        yt_client = DirectoryYtClient({"//home": "map_node"})
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/creator/directory/validate",
                json={"cluster": "jupiter", "path": "//home/team/project"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertTrue(result["valid"])
        self.assertEqual(
            [(step["path"], step["status"]) for step in result["steps"]],
            [
                ("//home", "exists"),
                ("//home/team", "create"),
                ("//home/team/project", "create"),
            ],
        )

    def test_directory_validation_is_blocked_by_table(self) -> None:
        yt_client = DirectoryYtClient({
            "//home": "map_node",
            "//home/configs": "table",
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/creator/directory/validate",
                json={"cluster": "saturn", "path": "//home/configs/archive"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertFalse(result["valid"])
        self.assertEqual(result["blockedBy"]["path"], "//home/configs")
        self.assertEqual(result["blockedBy"]["nodeType"], "table")

    def test_existing_target_directory_is_not_recreated(self) -> None:
        yt_client = DirectoryYtClient({
            "//home": "map_node",
            "//home/team": "map_node",
        })
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/creator/directory/validate",
                json={"cluster": "jupiter", "path": "//home/team"},
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "Целевая директория уже существует")
        self.assertEqual(result["steps"][-1]["status"], "blocked")

    def test_directory_create_builds_chain_in_order(self) -> None:
        yt_client = DirectoryYtClient({"//home": "map_node"})
        with patch.object(server, "build_yt_client", return_value=yt_client):
            response = self.client.post(
                "/api/yt/creator/directory/create",
                json={
                    "confirmed": True,
                    "cluster": "miranda",
                    "path": "//home/team/project",
                    "inheritAcl": False,
                    "annotation": "Promo tools",
                },
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            [call[2] for call in yt_client.calls],
            ["//home/team", "//home/team/project"],
        )
        self.assertEqual(yt_client.calls[0][3], {})
        self.assertEqual(
            yt_client.calls[1][3],
            {"inherit_acl": False, "annotation": "Promo tools"},
        )

    def test_dynamic_creation_requires_sorted_column(self) -> None:
        response = self.client.post(
            "/api/yt/creator/create",
            json={
                "confirmed": True,
                "tableKind": "dynamic",
                "replicated": False,
                "cluster": "miranda",
                "path": "//home/example/table",
                "bundle": "vkvideo",
                "schema": [{"name": "value", "type_v3": "string"}],
            },
            headers={"X-YT-Token": "token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("сортированная", response.get_json()["error"])

    def test_replicated_creator_builds_meta_and_replica_tables(self) -> None:
        clients = {cluster: RecordingYtClient(cluster) for cluster in ("miranda", "jupiter", "saturn")}
        payload = {
            "confirmed": True,
            "tableKind": "dynamic",
            "replicated": True,
            "cluster": "miranda",
            "replicaTargets": ["jupiter", "saturn"],
            "path": "//home/example/table",
            "bundle": "vkvideo",
            "schema": [{"name": "itemId", "type_v3": "int64", "sort_order": "ascending"}],
            "enableReplicatedTableTracker": True,
            "minSyncReplicaCount": 1,
            "maxSyncReplicaCount": 1,
            "preferredSyncReplicaClusters": ["jupiter"],
            "leaveUnmounted": True,
        }
        with (
            patch.object(server, "build_yt_client", side_effect=lambda _token, cluster: clients[cluster]),
            patch.object(server, "validate_creator_destinations", return_value={"valid": True, "checks": []}),
        ):
            response = self.client.post(
                "/api/yt/creator/create",
                json=payload,
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 201)
        miranda_creates = [call for call in clients["miranda"].calls if call[0] == "create"]
        self.assertEqual(miranda_creates[0][1], "replicated_table")
        self.assertEqual([call[1] for call in miranda_creates[1:]], ["table_replica", "table_replica"])
        self.assertNotIn("enabled", miranda_creates[1][3])
        self.assertEqual(clients["jupiter"].calls[0][1], "table")
        self.assertEqual(clients["saturn"].calls[0][1], "table")
        self.assertEqual(
            clients["jupiter"].calls[0][3]["upstream_replica_id"],
            "replica-jupiter",
        )

    def test_replicated_creation_reports_partial_objects(self) -> None:
        clients = {cluster: RecordingYtClient(cluster) for cluster in ("miranda", "jupiter", "saturn")}

        def failing_create(node_type: str, path: str | None = None, attributes: dict | None = None):
            raise RuntimeError("replica cluster is unavailable")

        clients["jupiter"].create = failing_create
        payload = {
            "confirmed": True,
            "tableKind": "dynamic",
            "replicated": True,
            "replicaTargets": ["jupiter", "saturn"],
            "path": "//home/example/table",
            "bundle": "vkvideo",
            "schema": [{"name": "itemId", "type_v3": "int64", "sort_order": "ascending"}],
            "minSyncReplicaCount": 1,
            "maxSyncReplicaCount": 1,
            "preferredSyncReplicaClusters": ["jupiter"],
        }
        with (
            patch.object(server, "build_yt_client", side_effect=lambda _token, cluster: clients[cluster]),
            patch.object(server, "validate_creator_destinations", return_value={"valid": True, "checks": []}),
        ):
            response = self.client.post(
                "/api/yt/creator/create",
                json=payload,
                headers={"X-YT-Token": "token"},
            )

        self.assertEqual(response.status_code, 502)
        result = response.get_json()
        self.assertEqual(result["code"], "partial_creation")
        self.assertEqual(
            [item["nodeType"] for item in result["created"]],
            ["replicated_table", "table_replica"],
        )

    def test_note_lifecycle(self) -> None:
        created = self.client.post("/api/notes/drafts")
        self.assertEqual(created.status_code, 201)
        draft = created.get_json()["result"]

        updated = self.client.put(
            f"/api/notes/drafts/{draft['slug']}",
            json={
                "title": "Как устроен Promo Planner",
                "content": "<h1>Архитектура</h1><p>Текст</p>",
            },
        )
        self.assertEqual(updated.status_code, 200)

        published = self.client.post(
            f"/api/notes/drafts/{draft['slug']}/publish",
            json={"title": "Как устроен Promo Planner"},
        )
        self.assertEqual(published.status_code, 200)
        article = published.get_json()["result"]
        self.assertEqual(article["slug"], "kak-ustroen-promo-planner")

        edited = self.client.post(f"/api/notes/articles/{article['slug']}/edit")
        self.assertEqual(edited.status_code, 201)
        edit_draft = edited.get_json()["result"]
        self.assertEqual(edit_draft["sourceArticleSlug"], article["slug"])

        cancelled = self.client.post(f"/api/notes/drafts/{edit_draft['slug']}/cancel")
        self.assertEqual(cancelled.status_code, 200)

        listing = self.client.get("/api/notes").get_json()
        self.assertEqual(len(listing["articles"]), 1)
        self.assertEqual(listing["drafts"], [])


if __name__ == "__main__":
    unittest.main()
