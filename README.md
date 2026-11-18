# Termius Export

从 macOS 本机 Termius 导出 SSH 连接、已保存密码和软件私钥。既可以作为 Codex 技能使用，也可以独立运行命令行脚本。

Export SSH hosts, saved passwords, and software private keys from a local Termius installation on macOS. Includes a Codex skill and a standalone CLI.

## 支持范围

- 已验证：macOS Apple Silicon、Termius **10.4.0 官网版**。
- 导出主机、SSH 配置、身份、普通私钥，以及本机 SSH ID 的软件 ED25519 / ECDSA / RSA 密钥。
- 保留原始域名和记录关系，正确处理本地删除记录；密码中的空白、逗号、引号和换行保持原样。
- 输出结构化 JSON、私钥文件，以及可完整表达直接 SSH 连接时的 CSV。
- Touch ID、Secure Enclave 和硬件安全钥匙的私钥不可导出；导出结果会报告这些限制。

其它 Termius 版本、Intel Mac、App Store 版、Windows 和 Linux 尚未验证。CSV 不自动展开分组继承、多身份、代理、跳板机、隧道、证书和非默认终端设置；相关字段和关系保留在 JSON 中，限制列在 `summary.json`。这不是 Termius 官方备份格式。

## 安装为 Codex 技能

```sh
git clone https://github.com/Su-cyber-art/termius-export.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/termius-export"
```

已有同名目录时，先检查已有版本，再决定更新方式。

调用示例：

> 使用 $termius-export 导出本机 Termius 的 SSH 连接、密码和软件私钥。

技能入口见 [SKILL.md](SKILL.md)。

## 独立运行

需要：macOS、已安装并在本机保存数据的 Termius 官网版、Python 3.10+、Node.js 22+、Git。Node.js 的架构需要与安装包中的原生组件一致。脚本使用 Termius 随包组件，本仓库不包含 Termius 二进制文件。

```sh
git clone https://github.com/Su-cyber-art/termius-export.git
cd termius-export

python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt

.venv/bin/python scripts/export_termius.py \
  --output "outputs/termius-export-$(date +%Y%m%d-%H%M%S)"
```

`--output` 必填，目标目录必须尚不存在。默认读取 `/Applications/Termius.app` 和 `~/Library/Application Support/Termius`，临时快照放在当前目录的 `work/`。

需要指定其它位置时：

```sh
.venv/bin/python scripts/export_termius.py \
  --app /Applications/Termius.app \
  --profile "$HOME/Library/Application Support/Termius" \
  --work-dir work \
  --output outputs/termius-backup
```

如果 macOS 弹出钥匙串访问提示，完成系统验证即可。脚本只读取 `Termius / localKey` 这一项，不修改钥匙串。若复制期间数据库发生变化，脚本会停止；暂停 Termius 中的编辑/同步后重试。

## 输出

| 文件 | 内容 |
| --- | --- |
| `termius-export.json` | 解密后的 SSH 数据、凭据、密钥和原始 ID 关系 |
| `keys/` | 普通私钥、SSH ID 软件私钥，以及已有的公钥和证书 |
| `hosts.csv` | 可完整展开的直接 SSH 连接，仅在 `csv.complete` 为 `true` 时生成 |
| `summary.json` | 数量、CSV 可用性、缺失凭据和不可导出硬件密钥的说明 |

CSV 列为 `name,host,port,user,password,key,key_passphrase,folder,notes`。JSON 中密钥文件路径相对导出目录，CSV 中使用最终绝对路径；移动导出目录后，导入前需要更新 CSV 的 `key` 列。其它客户端是否支持私钥口令等列，需要按该客户端的导入格式确认。

**导出文件包含明文密码和私钥。** 脚本将导出目录权限设为 `0700`、文件设为 `0600`，自动清理临时快照。默认导出的凭据材料也包括密钥库中未被主机引用的普通软件密钥，以及当前设备的 SSH ID 软件密钥。请将导出包保存在自己控制的位置，不要作为 Issue 附件或提交到 Git。仓库的 `.gitignore` 已排除默认输出和临时目录。

## 工作原理

1. 复制一份本地 IndexedDB 快照，按 LevelDB 序号选择每个键的最新记录，排除删除标记和待同步的删除记录。
2. 使用 Termius 自带的 `keytar` 读取 macOS 钥匙串中的 `localKey`。
3. 调用 Termius 自带 `libtermius.crypto.systems.FromEncryptionKey()` 和 `decrypt()`，在本机解密连接字段。
4. 导出 JSON 和密钥文件；只有所有 SSH 连接都能完整映射到当前 CSV 格式时才生成 CSV。

解密过程无需 Termius 账号密码，也不请求 Termius 云端接口。主密钥不会写入导出包。具体字段与边界见 [本地格式说明](references/storage.md)。

## 开发与验证

测试使用合成数据和临时生成的测试密钥，不读取本机 Termius 数据或钥匙串：

```sh
.venv/bin/python -m unittest discover -s scripts -p 'test_*.py' -v
node --test scripts/test_decrypt.cjs
node --check scripts/decrypt_termius.cjs
```

测试覆盖旧记录与删除标记、JavaScript `undefined`、CSV 凭据保真、SSH ID 匹配、硬件认证、解密失败，以及私钥文件的 OpenSSH 可读性。GitHub Actions 执行这些测试；实际应用解密仍需在支持的 macOS 环境验证。

## 许可证与依赖

本项目代码和技能采用 [MIT License](LICENSE)。

- IndexedDB 读取使用 [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader)，依赖版本固定在 `scripts/requirements.txt`。
- Termius 及其随包组件属于各自权利人；使用时从用户本机安装中加载，不随本仓库分发。
- 本项目独立开发，与 Termius 官方无关联。
