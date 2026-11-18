const fs = require('node:fs');
const path = require('node:path');

const encryptedFields = {
  hosts: ['address', 'label', 'cloud_instance_id', 'cloud_instance_type'],
  groups: ['label'],
  ssh_configs: ['env_variables', 'mosh_server_command'],
  ssh_identities: ['label', 'username', 'password'],
  keys: ['label', 'passphrase', 'private_key', 'public_key'],
  ssh_certificates: ['certificate', 'label'],
  proxies: ['hostname'],
  pf_rules: ['hostname', 'label'],
  host_chains: [],
  ssh_config_identities: [],
  ssh_config_identities_shared: [],
};

function decryptSnapshot(snapshot, lib, cryptor) {
  function decrypt(value, context) {
    if (value == null) return value;
    let plain;
    try {
      plain = cryptor.decrypt(Buffer.from(value, 'base64'));
    } catch {
      throw new Error(`Decryption failed: ${context}`);
    }
    if (plain == null) throw new Error(`Decryption failed: ${context}`);
    return plain.toString('utf8');
  }
  for (const [table, fields] of Object.entries(encryptedFields)) {
    for (const row of snapshot.tables[table]) {
      for (const field of fields) {
        if (Object.hasOwn(row, field)) {
          row[field] = decrypt(row[field], `${table}/${row.local_id}/${field}`);
        }
      }
      delete row.content;
    }
  }
  for (const key of snapshot.tables.keys) {
    if (key.private_key && !lib.validatePrivateKeyFormat(key.private_key)) {
      throw new Error(`Invalid private key: keys/${key.local_id}`);
    }
  }
  const softwareKeys = [], hardwareKeys = [];
  for (const [i, key] of snapshot.deviceBoundPasskeys.entries()) {
    const meta = {public_key: key.public_key, created_at: key.created_at};
    if (key.biometric_type != null) {
      hardwareKeys.push({...meta, biometric_type: key.biometric_type});
      continue;
    }
    const privateKey = decrypt(key.private_key, `deviceBoundPasskeys/${i}/private_key`);
    if (!privateKey || !lib.validatePrivateKeyFormat(privateKey)) {
      throw new Error(`Invalid software SSH ID key: deviceBoundPasskeys/${i}`);
    }
    softwareKeys.push({...meta, private_key: privateKey});
  }
  delete snapshot.deviceBoundPasskeys;
  snapshot.sshId = {softwareKeys, hardwareKeys};
  return snapshot;
}

async function main() {
  if (process.platform !== 'darwin') throw new Error('This exporter supports macOS only');
  const [app, output] = process.argv.slice(2);
  const root = path.join(app, 'Contents/Resources/app.asar.unpacked/node_modules/@termius');
  const lib = require(path.join(root, `libtermius/mac-${process.arch}/termius.node`));
  const keytar = require(path.join(root, `keytar/mac-${process.arch}/keytar.node`));
  const snapshot = JSON.parse(fs.readFileSync(0, 'utf8'));
  let localKey;
  try {
    localKey = await keytar.getPassword('Termius', 'localKey');
  } catch {
    throw new Error('Cannot read Termius/localKey from macOS Keychain; resolve the system access prompt');
  }
  if (!localKey) throw new Error('Termius/localKey is missing from macOS Keychain');
  lib.crypto.init();
  const cryptor = lib.crypto.systems.FromEncryptionKey(Buffer.from(localKey, 'base64'));
  const result = decryptSnapshot(snapshot, lib, cryptor);
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n', {mode: 0o600, flag: 'wx'});
}

module.exports = {decryptSnapshot};
if (require.main === module) {
  main().catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
