#!/usr/bin/env python3
"""Read a local Termius profile and export SSH data without changing its stores."""
import argparse
import csv
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ccl_chromium_reader.ccl_chromium_indexeddb import WrappedIndexDB
from ccl_chromium_reader.serialization_formats.ccl_v8_value_deserializer import Deserializer

TABLES = (
    "hosts", "groups", "ssh_configs", "ssh_identities", "keys",
    "ssh_certificates", "proxies", "pf_rules", "host_chains",
    "ssh_config_identities", "ssh_config_identities_shared",
)
CSV_FIELDS = ("name", "host", "port", "user", "password", "key",
              "key_passphrase", "folder", "notes")
SOFTWARE_KEY_TYPES = {
    "ssh-ed25519": "ED25519", "ssh-rsa": "RSA",
    "ecdsa-sha2-nistp256": "ECDSA", "ecdsa-sha2-nistp384": "ECDSA",
    "ecdsa-sha2-nistp521": "ECDSA",
}


def json_value(value):
    """Use JSON.stringify's handling of the reader's JavaScript undefined."""
    if value is Deserializer.Undefined:
        return None
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items() if v is not Deserializer.Undefined}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def latest_records(records):
    latest = {}
    for record in records:
        key = record.key.raw_key
        if key not in latest or record.ldb_seq_no > latest[key].ldb_seq_no:
            latest[key] = record
    result = []
    for record in latest.values():
        if not record.is_live or record.value is None:
            continue
        row = json_value(record.value)
        if row.get("status") == "DELETE_FAILED":
            continue
        if row.get("status") not in (None, "SYNCHRONIZED", "CREATE_FAILED", "UPDATE_FAILED"):
            raise ValueError("Unrecognized Termius record status; inspect this application version")
        result.append(row)
    return result


def file_state(root):
    return {str(p.relative_to(root)): (s.st_size, s.st_mtime_ns)
            for p in root.rglob("*") if p.is_file() for s in [p.stat()]}


def snapshot_database(profile, destination):
    parent = profile / "IndexedDB"
    source = parent / "file__0.indexeddb.leveldb"
    if not source.is_dir():
        raise ValueError(f"Termius IndexedDB not found: {source}")
    roots = [source]
    blob = parent / "file__0.indexeddb.blob"
    if blob.exists():
        roots.append(blob)
    before = {p.name: file_state(p) for p in roots}
    for p in roots:
        shutil.copytree(p, destination / p.name)
    after = {p.name: file_state(p) for p in roots}
    if before != after:
        raise ValueError("Termius database changed during copying; pause editing/sync and retry")
    return destination / source.name, destination / blob.name if blob.exists() else None


def read_snapshot(database, blob):
    tables = {}
    with WrappedIndexDB(database, blob) as wrapper:
        for name in TABLES:
            if name not in wrapper:
                raise ValueError(f"Expected Termius database is missing: {name}")
            tables[name] = latest_records(wrapper[name][name].iterate_records())
        if "sshid" not in wrapper or "deviceBoundPasskeys" not in wrapper["sshid"]:
            raise ValueError("Expected SSH ID deviceBoundPasskeys store is missing")
        passkeys = latest_records(wrapper["sshid"]["deviceBoundPasskeys"].iterate_records())
    return {"tables": tables, "deviceBoundPasskeys": passkeys}


def write_text(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def materialize_keys(data, output):
    key_dir = output / "keys"
    key_dir.mkdir(mode=0o700)
    key_count = 0
    for number, key in enumerate(data["tables"]["keys"], 1):
        stem = f"key-{number}"
        if key.get("private_key"):
            key["exported_key_file"] = f"keys/{stem}"
            write_text(output / key["exported_key_file"], key["private_key"].rstrip("\r\n") + "\n")
            key_count += 1
        if key.get("public_key"):
            write_text(key_dir / (stem + ".pub"), key["public_key"])
    for number, key in enumerate(data["sshId"]["softwareKeys"], 1):
        key["exported_key_file"] = f"keys/sshid-{number}"
        write_text(output / key["exported_key_file"], key["private_key"].rstrip("\r\n") + "\n")
        write_text(output / (key["exported_key_file"] + ".pub"), key["public_key"])
        key_count += 1
    for number, certificate in enumerate(data["tables"]["ssh_certificates"], 1):
        certificate["exported_certificate_file"] = f"keys/certificate-{number}.pub"
        write_text(output / certificate["exported_certificate_file"], certificate["certificate"])
    return key_count


def csv_rows(data, final_output):
    """Flatten only connections whose authentication and options fit this CSV."""
    t = data["tables"]
    configs = {r["local_id"]: r for r in t["ssh_configs"]}
    identities = {r["local_id"]: r for r in t["ssh_identities"]}
    keys = {r["local_id"]: r for r in t["keys"]}
    links = t["ssh_config_identities"] + t["ssh_config_identities_shared"]
    rows, issues, missing, non_ssh = [], [], [], []
    authentication = {"password": 0, "private_key": 0, "ssh_id": 0}
    for host in t["hosts"]:
        host_id = host["local_id"]
        try:
            if host.get("group"):
                raise ValueError("分组与继承需要目标格式转换")
            if not host.get("ssh_config"):
                if host.get("telnet_config"):
                    non_ssh.append(host_id)
                    continue
                raise ValueError("没有可直接展开的 SSH 配置")
            config_id = host["ssh_config"]["local_id"]
            config = configs[config_id]
            if host.get("credentials_mode") is not None:
                raise ValueError("该凭据模式需要核对继承规则")
            if config.get("proxycommand") or config.get("proxy"):
                raise ValueError("代理无法用本 CSV 表达")
            if any(r["ssh_config"]["local_id"] == config_id for r in t["host_chains"]):
                raise ValueError("跳板链无法用本 CSV 表达")
            if any(r["host"]["local_id"] == host_id for r in t["pf_rules"]):
                raise ValueError("端口转发无法用本 CSV 表达")
            if config.get("startup_snippet"):
                raise ValueError("启动脚本仅保留引用，脚本内容未导出")
            if (config.get("agent_forwarding") or config.get("use_mosh")
                    or config.get("charset") not in (None, "", "utf-8")
                    or host.get("backspace") not in (None, "default")
                    or config.get("env_variables") not in (None, "", "{}", "[]")):
                raise ValueError("非默认 SSH/终端选项需要目标格式转换")
            matching = [r for r in links if r["ssh_config"]["local_id"] == config_id]
            if len(matching) != 1:
                raise ValueError("需要恰好一个明确的身份关联")
            identity = identities[matching[0]["identity"]["local_id"]]
            user, address = identity.get("username"), host.get("address")
            if not user or not address:
                raise ValueError("用户名或地址缺失，未自动补值")
            port = 22 if config.get("port") is None else config["port"]
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("SSH 端口无效")
            if identity.get("hardware_key"):
                raise ValueError("硬件私钥不能导出成普通私钥文件")
            key_file, passphrase = "", ""
            mode = identity.get("sshid_mode")
            if mode:
                candidates = [k for k in data["sshId"]["softwareKeys"]
                              if SOFTWARE_KEY_TYPES.get(k["public_key"].split()[0]) == mode]
                if len(candidates) != 1:
                    raise ValueError("SSH ID 软件密钥无法唯一匹配；未改用密码")
                key_file = candidates[0]["exported_key_file"]
                authentication["ssh_id"] += 1
            elif identity.get("ssh_key"):
                key = keys[identity["ssh_key"]["local_id"]]
                if not key.get("exported_key_file"):
                    raise ValueError("选定密钥缺少可导出的私钥")
                if any(c["ssh_key"]["local_id"] == key["local_id"] for c in t["ssh_certificates"]):
                    raise ValueError("SSH 证书需要目标格式转换")
                key_file, passphrase = key["exported_key_file"], key.get("passphrase") or ""
                authentication["private_key"] += 1
            else:
                authentication["password"] += 1
                if not identity.get("password"):
                    missing.append(host_id)
            rows.append(dict(zip(CSV_FIELDS, (
                host.get("label") or address, address, port, user,
                identity.get("password") or "", str(final_output / key_file) if key_file else "",
                passphrase, "", "未保存认证凭据" if host_id in missing else "",
            ))))
        except KeyError:
            issues.append({"host_id": host_id, "reason": "配置或身份关系缺失，请核对源数据"})
        except ValueError as error:
            issues.append({"host_id": host_id, "reason": str(error)})
    return rows, {
        "complete": not issues, "exported_rows": len(rows) if not issues else 0,
        "issues": issues, "non_ssh_host_ids": non_ssh,
        "missing_saved_credentials": missing, "resolved_authentication": authentication,
    }


def finish_export(data, staging, destination):
    key_count = materialize_keys(data, staging)
    rows, csv_summary = csv_rows(data, destination)
    if csv_summary["complete"]:
        with (staging / "hosts.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    write_json(staging / "termius-export.json", data)
    summary = {
        "source_version": data["source"]["version"],
        "hosts": len(data["tables"]["hosts"]),
        "groups": len(data["tables"]["groups"]),
        "private_key_files": key_count,
        "software_ssh_id_keys": len(data["sshId"]["softwareKeys"]),
        "hardware_ssh_id_keys_not_exportable": len(data["sshId"]["hardwareKeys"]),
        "identity_hardware_key_references": sum(bool(i.get("hardware_key")) for i in data["tables"]["ssh_identities"]),
        "csv": csv_summary,
    }
    write_json(staging / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/Applications/Termius.app"))
    parser.add_argument("--profile", type=Path, default=Path.home() / "Library/Application Support/Termius")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path.cwd() / "work")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise ValueError("This exporter supports macOS only")
    os.umask(0o077)
    app, profile, destination = (p.expanduser().absolute() for p in (args.app, args.profile, args.output))
    if destination.exists():
        raise ValueError(f"Output already exists; choose a new directory: {destination}")
    with (app / "Contents/Info.plist").open("rb") as f:
        info = plistlib.load(f)
    if info["CFBundleIdentifier"] != "com.termius-dmg.mac":
        raise ValueError("Unverified Termius distribution; inspect its Keychain service before exporting")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="termius-snapshot-", dir=args.work_dir) as scratch:
        db, blob = snapshot_database(profile, Path(scratch))
        snapshot = read_snapshot(db, blob)
        snapshot["format"] = "termius-local-ssh-export-v1"
        snapshot["source"] = {"version": info["CFBundleShortVersionString"],
                              "exported_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory(prefix=".termius-export-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            decrypted = staging / "termius-export.json"
            helper = Path(__file__).with_name("decrypt_termius.cjs")
            result = subprocess.run(["node", str(helper), str(app), str(decrypted)],
                                    input=json.dumps(snapshot), text=True, capture_output=True)
            if result.returncode:
                raise ValueError(f"Termius decryption step failed: {result.stderr.strip()}")
            data = json.loads(decrypted.read_text(encoding="utf-8"))
            summary = finish_export(data, staging, destination)
            if destination.exists():
                raise ValueError("Output was created during export; choose a new directory")
            staging.rename(destination)
    print(json.dumps({"output": str(destination), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        sys.exit(1)
