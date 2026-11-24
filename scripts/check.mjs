import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const requiredPaths = [
  '.gitignore', '.gitattributes', '.editorconfig', 'AGENTS.md', 'README.md',
  'GOALS.md', 'ROADMAP.md', 'PLANS.md', 'PROGRESS.md', 'CHANGELOG.md',
  '.codex/config.toml', 'package.json', 'package-lock.json', 'playwright.config.mjs',
  'scripts/preflight.ps1', 'scripts/harness-server.mjs', 'scripts/playwright.mjs', '.github/workflows/bootstrap.yml',
];

for (const path of requiredPaths) {
  assert(existsSync(join(root, path)), `Required bootstrap file is missing: ${path}`);
}

const expectedRoles = new Map([
  ['architect.toml', 'architect'],
  ['backend-engineer.toml', 'backend_engineer'],
  ['database-engineer.toml', 'database_engineer'],
  ['frontend-engineer.toml', 'frontend_engineer'],
  ['qa-engineer.toml', 'qa_engineer'],
  ['security-engineer.toml', 'security_engineer'],
  ['devops-engineer.toml', 'devops_engineer'],
  ['technical-writer.toml', 'technical_writer'],
]);
const roleDirectory = join(root, '.codex/agents');
const actualRoleFiles = readdirSync(roleDirectory).filter((name) => name.endsWith('.toml')).sort();
assert.deepEqual(actualRoleFiles, [...expectedRoles.keys()].sort(), 'Expected exactly the eight configured specialist roles.');
for (const [filename, roleName] of expectedRoles) {
  const source = readFileSync(join(roleDirectory, filename), 'utf8');
  assert(source.includes(`name = "${roleName}"`), `${filename}: role name mismatch.`);
  assert(/^description\s*=\s*".+"\s*$/m.test(source), `${filename}: description missing.`);
  assert(/^developer_instructions\s*=\s*"""[\s\S]+"""\s*$/m.test(source), `${filename}: instructions missing.`);
}

// This checks the repository's deliberately small config contract. The installed
// Codex CLI performs authoritative schema validation separately during preflight.
const agentConfig = readFileSync(join(root, '.codex/config.toml'), 'utf8');
assert(/^\[agents\]$/m.test(agentConfig), 'Project agent settings are missing.');
assert(/^enabled\s*=\s*true\s*$/m.test(agentConfig), 'Subagents must be enabled.');
assert(/^max_concurrent_threads_per_session\s*=\s*3\s*$/m.test(agentConfig), 'Runtime concurrency must stay at three subagents.');

const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const lock = JSON.parse(readFileSync(join(root, 'package-lock.json'), 'utf8'));
assert.equal(manifest.private, true, 'Bootstrap tooling must not be published as a package.');
for (const script of ['check', 'preflight', 'qa:serve', 'browser:install', 'test:e2e', 'test:e2e:headed', 'test:report']) {
  assert.equal(typeof manifest.scripts?.[script], 'string', `Missing npm script: ${script}`);
}
const playwrightVersion = manifest.devDependencies?.['@playwright/test'];
assert(/^\d+\.\d+\.\d+$/.test(playwrightVersion), 'Playwright must use an exact version.');
assert.equal(lock.packages?.['']?.devDependencies?.['@playwright/test'], playwrightVersion, 'Manifest and lockfile differ.');
assert.equal(lock.packages?.['node_modules/@playwright/test']?.version, playwrightVersion, 'Locked Playwright version differs.');

function sourceFiles(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(fullPath);
    return entry.isFile() && /\.(?:mjs|cjs|js)$/.test(entry.name) ? [fullPath] : [];
  });
}

const sources = [
  join(root, 'playwright.config.mjs'),
  ...sourceFiles(join(root, 'scripts')),
  ...sourceFiles(join(root, 'tests')),
];
assert(sources.some((path) => /\.spec\.(?:mjs|js)$/.test(path)), 'At least one browser behavior test is required.');
assert(existsSync(join(root, 'tests/fixtures')), 'QA fixture directory is missing.');
for (const file of sources) {
  const result = spawnSync(process.execPath, ['--check', file], {
    cwd: root, encoding: 'utf8', windowsHide: true, timeout: 15_000,
  });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, `${relative(root, file)} syntax failed:\n${result.stderr}`);
}
console.log(`Bootstrap checks passed: ${requiredPaths.length} required files, ${expectedRoles.size} role contracts, pinned dependency lock and ${sources.length} JavaScript syntax checks.`);
console.log('Product requirements, financial scenarios and Chaos Lab remain pending the full specification.');
