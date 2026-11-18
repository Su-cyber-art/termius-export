# 本地格式与已验证调用

以下来自 Termius 10.4.0 macOS 官网版的实际本地数据和随包代码。读取不同版本前核对实际结构，不能把这些字段当成永久协议。

## 数据和密钥

- IndexedDB：`<profile>/IndexedDB/file__0.indexeddb.leveldb`。
- 对应外部 blob（存在时）：`<profile>/IndexedDB/file__0.indexeddb.blob`。
- macOS 通用密码项：service `Termius`，account `localKey`。仅读取这一项。App Store 版的服务名可能不同，当前脚本拒绝未验证的 bundle ID。
- 原生组件：`<app>/Contents/Resources/app.asar.unpacked/node_modules/@termius/libtermius/mac-<arch>/termius.node` 和 `@termius/keytar/mac-<arch>/keytar.node`。
- 密钥和字段密文均用 Base64 编码。调用组件 `crypto.init()`，再用 `crypto.systems.FromEncryptionKey(Buffer.from(localKey, 'base64'))` 创建解密器；`decrypt(Buffer.from(value, 'base64'))` 返回明文 Buffer，失败返回 null 或抛错。使用它自身的认证解密，不另造算法或旧版 RNCryptor 回退。

`ccl_chromium_reader` 的 `live_only` 不足以表示当前有效记录：先按 `record.key.raw_key` 分组，选最大 `ldb_seq_no`，再判断 `is_live` 和 `value`。保留最新的删除标记参与选择，避免恢复旧值。Termius 的 `DELETE_FAILED` 表示本地已删除、等待同步，不应导出；`SYNCHRONIZED`、`CREATE_FAILED` 和 `UPDATE_FAILED` 都可能是当前有效记录。

Python 库把 JavaScript `undefined` 反序列化成自己的单例。与 `JSON.stringify` 一致，输出时省略对象中的 undefined 属性，把数组中的 undefined 转成 null。不要用通用 `default=str`，否则会把无法解码的数据伪装成有效字符串。

## 表与加密字段

| 数据库/同名 object store | 需要本地解密的字段 |
| --- | --- |
| hosts | address, label, cloud_instance_id, cloud_instance_type |
| groups | label |
| ssh_configs | env_variables, mosh_server_command |
| ssh_identities | label, username, password |
| keys | label, passphrase, private_key, public_key |
| ssh_certificates | certificate, label |
| proxies | hostname |
| pf_rules | hostname, label |
| host_chains | 无；保存配置和跳板主机的关系 |
| ssh_config_identities | 无；配置与身份的关联 |
| ssh_config_identities_shared | 无；共享身份关联 |

`content` 是另一份同步密文，不是这些本地明文字段的替代品。导出省略它，保留其它源字段和关系。不导出账号登录 token、设置库、会话日志或命令历史。

Termius 的 `SshKey.trimPrivateAndPublic()` 会裁剪密钥首尾空白。写独立私钥文件时补一个末尾换行，否则 OpenSSH 可能报 `invalid format`；JSON 中保留原始字段值。

关系通过 `local_id` 连接：`hosts.ssh_config → ssh_configs`，`ssh_config_identities.ssh_config / identity → ssh_configs / ssh_identities`，`ssh_identities.ssh_key → keys`。不要用 UI 顺序或名称猜测关系；同名连接可以有不同地址。

主机的 `group` 和组的 `parent_group`、`ssh_config` 参与继承。`ssh_config.identity` 是旧字段，当前直接连接优先使用明确的关联表；没有恰好一个身份时，CSV 转换器报告未展开。`port == null` 对直接 SSH 连接表示默认 22，用户名为空则不能猜成 root。

## SSH ID

`sshid` 数据库中的 `deviceBoundPasskeys` 保存当前设备的密钥。`public_key` 为明文，`private_key` 和存在的 `credential_id` 使用本地密钥加密。

- `biometric_type == null`：软件密钥，按私钥字段解密并导出。
- `biometric_type != null`：设备/生物识别认证，保留公钥和类型元数据，不宣称能导出硬件私钥。
- 身份的 `sshid_mode` 指定 ED25519、ECDSA、RSA 等类型；按公钥类型匹配本机对应的软件密钥。多个候选或没有候选都要明确报告，不能随意选一个，也不能静默改用保存的密码。

脚本导出本机软件密钥，不读取其它设备的远端 passkey 列表。普通 `hardware_key` 引用保留在原始 JSON 中，相关连接不会生成误导性的 CSV。

## CSV 的实际能力

CSV 可以表达本次验证过的直接连接、密码和普通软件密钥。只要存在无法完整展开的 SSH 连接，就不生成部分 CSV；`summary.json` 列出相关记录 ID 和原因，结构化 JSON 和密钥文件仍然有效。

分组继承、代理、跳板链、端口转发、非默认终端设置、启动脚本引用、多身份、证书或硬件认证需要目标客户端专属转换。当前导出保留已有设置和引用；启动脚本内容以及硬件私钥不在该包内。不要声称 JSON 是可直接恢复 Termius 账号或完整应用状态的官方备份格式。
