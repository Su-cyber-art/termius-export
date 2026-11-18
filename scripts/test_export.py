import csv
import importlib.util
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("export_termius", Path(__file__).with_name("export_termius.py"))
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def record(key, sequence, value, live=True):
    return SimpleNamespace(key=SimpleNamespace(raw_key=key), ldb_seq_no=sequence,
                           value=value, is_live=live)


def fixture():
    tables = {name: [] for name in export.TABLES}
    tables["hosts"] = [dict(local_id=1, label='示例,"主机"', address="example.invalid",
                           ssh_config={"local_id": 2}, backspace="default")]
    tables["ssh_configs"] = [dict(local_id=2, port=None, env_variables="{}")]
    tables["ssh_identities"] = [dict(local_id=3, username="alice", password='  p,"q\nend  ')]
    tables["ssh_config_identities"] = [dict(ssh_config={"local_id": 2}, identity={"local_id": 3})]
    return {"source": {"version": "test"}, "tables": tables,
            "sshId": {"softwareKeys": [], "hardwareKeys": []}}


class ExportTests(unittest.TestCase):
    def test_latest_delete_does_not_restore_older_secret(self):
        rows = [record(b"a", 1, {"password": "old"}), record(b"a", 4, None, False),
                record(b"b", 8, {"status": "DELETE_FAILED"}),
                record(b"b", 7, {"status": "SYNCHRONIZED"}),
                record(b"c", 2, {"password": "old"}),
                record(b"c", 9, {"password": "current", "status": "UPDATE_FAILED"})]
        self.assertEqual(export.latest_records(rows), [{"password": "current", "status": "UPDATE_FAILED"}])

    def test_undefined_has_javascript_json_semantics(self):
        value = {"omitted": export.Deserializer.Undefined,
                 "array": [export.Deserializer.Undefined, None, "literal"]}
        self.assertEqual(export.json_value(value), {"array": [None, None, "literal"]})

    def test_csv_round_trip_preserves_credentials_and_domain(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = export.finish_export(data, root, root)
            with (root / "hosts.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(summary["csv"]["complete"])
            self.assertEqual(rows[0]["password"], data["tables"]["ssh_identities"][0]["password"])
            self.assertEqual(rows[0]["name"], data["tables"]["hosts"][0]["label"])
            self.assertEqual(rows[0]["host"], "example.invalid")
            self.assertEqual(rows[0]["port"], "22")

    def test_ssh_id_uses_matching_key_not_saved_password(self):
        data = fixture()
        data["tables"]["ssh_identities"][0]["sshid_mode"] = "ED25519"
        data["sshId"]["softwareKeys"] = [
            {"public_key": "ssh-rsa fake", "exported_key_file": "keys/rsa"},
            {"public_key": "ssh-ed25519 fake", "exported_key_file": "keys/ed"}]
        rows, result = export.csv_rows(data, Path("/export"))
        self.assertTrue(result["complete"])
        self.assertEqual(rows[0]["key"], "/export/keys/ed")
        data["sshId"]["softwareKeys"].append(dict(data["sshId"]["softwareKeys"][1]))
        rows, result = export.csv_rows(data, Path("/export"))
        self.assertFalse(result["complete"])
        self.assertEqual(rows, [])

    def test_group_does_not_produce_partial_csv(self):
        data = fixture()
        data["tables"]["hosts"].append(dict(data["tables"]["hosts"][0], local_id=5, group={"local_id": 6}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = export.finish_export(data, root, root)
            self.assertFalse(summary["csv"]["complete"])
            self.assertFalse((root / "hosts.csv").exists())
            self.assertTrue((root / "termius-export.json").exists())

    def test_hardware_auth_is_reported(self):
        data = fixture()
        data["tables"]["ssh_identities"][0]["hardware_key"] = {"local_id": 8}
        rows, summary = export.csv_rows(data, Path("/export"))
        self.assertEqual(rows, [])
        self.assertFalse(summary["complete"])

    def test_trimmed_private_key_file_is_readable_by_openssh(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "generated"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source)], check=True)
            original = source.read_text().strip()
            data["tables"]["keys"] = [{"local_id": 8, "private_key": original}]
            export.materialize_keys(data, root)
            key = root / data["tables"]["keys"][0]["exported_key_file"]
            result = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.split()[:2], source.with_suffix(".pub").read_text().split()[:2])
            self.assertEqual(data["tables"]["keys"][0]["private_key"], original)


if __name__ == "__main__":
    unittest.main()
