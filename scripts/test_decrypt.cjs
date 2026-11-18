const assert = require('node:assert/strict');
const test = require('node:test');
const {decryptSnapshot} = require('./decrypt_termius.cjs');

function fixture() {
  const tables = Object.fromEntries([
    'hosts', 'groups', 'ssh_configs', 'ssh_identities', 'keys',
    'ssh_certificates', 'proxies', 'pf_rules', 'host_chains',
    'ssh_config_identities', 'ssh_config_identities_shared',
  ].map(name => [name, []]));
  return {tables, deviceBoundPasskeys: []};
}

test('preserves credential whitespace and drops the sync ciphertext copy', () => {
  const snapshot = fixture();
  const password = '  p,"q\nend  ';
  snapshot.tables.ssh_identities.push({local_id: 1, password: 'YQ==', content: 'cloud-copy'});
  const result = decryptSnapshot(snapshot, {}, {decrypt: () => Buffer.from(password)});
  assert.equal(result.tables.ssh_identities[0].password, password);
  assert.equal(Object.hasOwn(result.tables.ssh_identities[0], 'content'), false);
});

test('a decryption failure reports the field instead of inventing an empty credential', () => {
  const snapshot = fixture();
  snapshot.tables.ssh_identities.push({local_id: 2, password: 'YQ=='});
  assert.throws(
    () => decryptSnapshot(snapshot, {}, {decrypt: () => null}),
    /Decryption failed: ssh_identities\/2\/password/,
  );
});

test('retains hardware metadata without attempting to export a hardware private key', () => {
  const snapshot = fixture();
  snapshot.deviceBoundPasskeys.push({
    public_key: 'hardware-public-key', biometric_type: 'TOUCHID', private_key: 'YQ==',
  });
  const result = decryptSnapshot(snapshot, {}, {
    decrypt: () => { throw new Error('Hardware keys must not be decrypted'); },
  });
  assert.equal(result.sshId.hardwareKeys[0].biometric_type, 'TOUCHID');
  assert.equal(result.sshId.softwareKeys.length, 0);
  assert.equal(Object.hasOwn(result.sshId.hardwareKeys[0], 'private_key'), false);
});
