---
name: termius-export
description: 从 macOS 本机 Termius 导出 SSH 连接、已保存密码、普通私钥和 SSH ID 软件密钥，生成结构化 JSON、CSV 与密钥文件。用于备份 Termius 或为迁移准备导出数据；不负责写入其他 SSH 客户端。
---

# Termius SSH 导出

使用本机 Termius 的 `localKey` 和它自带的 `libtermius` 解密 IndexedDB。已在 macOS Apple Silicon、Termius 10.4.0 官网版验证。导出过程中不需要 Termius 账号密码，也不需要调用云端接口。

## 执行

默认导出当前本机保存的连接及认证资料。用户已要求导出时，直接完成本地导出；用户要求排除密码、限制主机范围或指定格式时，先按要求调整导出字段，不能把完整凭据包当成不含密码的清单交付。

1. 使用用户指定的应用和数据目录。常规位置是 `/Applications/Termius.app` 和 `~/Library/Application Support/Termius`。导出目的地使用用户指定位置，否则放在当前工作区的 `outputs/termius-export-<时间>/`。目的目录必须尚不存在。
2. 用已有 Python 环境运行脚本；缺依赖时，在当前工作区 `work/` 创建隔离环境。需要 Python 3.10+、Node.js，以及 `scripts/requirements.txt` 中的读取库。例如：

   ```sh
   termius_skill_dir="${CODEX_HOME:-$HOME/.codex}/skills/termius-export"
   uv venv work/termius-export-venv --python 3.13
   uv pip install --python work/termius-export-venv/bin/python -r "$termius_skill_dir/scripts/requirements.txt"
   work/termius-export-venv/bin/python "$termius_skill_dir/scripts/export_termius.py" \
     --output outputs/termius-export-20261118-120000
   ```

   将示例时间换成实际时间。应用或数据目录不在默认位置时，传入 `--app`、`--profile`；中间文件默认放在当前目录的 `work/`，可用 `--work-dir` 指定。
3. 脚本复制一份稳定的数据库快照，按 LevelDB 序号选择最新记录，排除已删除记录；密钥只留在解密子进程内存中。若源数据库在复制时发生变化，暂停编辑/同步后再运行，不要自动退出有活动 SSH 会话的 Termius。
4. 检查脚本输出的数量和 `summary.json`。只有 `csv.complete` 为真时才会生成 `hosts.csv`；复杂连接的原始字段和关系保留在 JSON 中，CSV 无法表达的设置会具体列出。不能把“生成了 JSON”说成“所有连接可直接导入任意客户端”。

## 导出结果

- `termius-export.json`：解密后的连接表、凭据、密钥及原有 ID 关系。密码和私钥口令按原样保留，不裁剪空白。云端同步用的 `content` 密文副本不输出。
- `keys/`：普通私钥和本机 SSH ID 软件私钥，以及已有的公钥、证书。JSON 中的密钥路径相对导出目录；CSV 使用导出完成后的绝对路径。
- `hosts.csv`：可完整展开的直接 SSH 连接，包含 `name,host,port,user,password,key,key_passphrase,folder,notes`。保留原域名，不将域名解析成 IP。
- `summary.json`：主机和密钥数量、CSV 可用性、非 SSH 记录、硬件密钥及缺失凭据说明，不包含密码或私钥正文。

JSON 和 CSV 都可能含明文凭据。目录权限为 `0700`，文件为 `0600`；聊天中只报告数量、路径和实际限制，不粘贴文件内容或密钥。导出包是用户请求的交付物，不能在交付前把它当临时文件删除；临时快照由脚本自动清理。

## 已知边界

- 普通私钥和 SSH ID 的软件 ED25519/ECDSA/RSA 密钥可以导出。Touch ID、Secure Enclave、硬件安全钥匙的私钥不能按普通文件导出；仅保留可读的元数据并报告限制。
- 分组继承、多身份、跳板机、代理、隧道、SSH 证书等关系保留在 JSON 中，当前 CSV 转换器不会猜测其含义。遇到这些情况先读 [格式与字段说明](references/storage.md)，按实际目标格式处理。
- 若系统钥匙串要求用户确认访问，交给用户完成系统验证；不要读取整个钥匙串、修改其访问控制或绕过验证。读取失败就报告具体步骤，不能输出空密码并声称导出成功。
- 原生模块加载失败、版本/字段变化或解密失败时，按 [格式与字段说明](references/storage.md) 核对当前安装包。不要套用网上的旧版固定密钥，也不要把未知密文当作明文导出。

这是导出技能。导入其他客户端、修改服务器认证、联网测试 SSH、上传或发送导出包，需要对应的用户请求；不要从本技能推导额外授权。
