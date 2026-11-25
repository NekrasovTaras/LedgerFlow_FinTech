import assert from 'node:assert/strict';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const mode = process.argv[2] ?? 'headless';
assert(['headless', 'headed'].includes(mode), 'Choose headless or headed.');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const reportRoot = resolve(root, 'artifacts/product-playwright', mode);
const recordingRoot = realpathSync(resolve(reportRoot, 'test-results'));
const report = JSON.parse(readFileSync(resolve(reportRoot, 'results.json'), 'utf8'));
const flatten = (suites) => suites.flatMap((suite) => [
  ...(suite.specs ?? []).flatMap((spec) => spec.tests.flatMap((test) => test.results)),
  ...flatten(suite.suites ?? []),
]);
const results = flatten(report.suites);
assert.equal(results.length, 9, 'Run all nine product acceptance scenarios before verifying evidence.');
assert.equal(report.stats.expected, 9, 'All nine product requirements must pass.');
assert.equal(report.stats.unexpected, 0, 'Unexpected failures remain.');
assert.equal(report.stats.skipped, 0, 'Product requirements were skipped.');
const counts = { screenshot: 0, video: 0, trace: 0 };
for (const result of results) {
  assert.equal(result.status, 'passed');
  for (const kind of Object.keys(counts)) {
    const attachment = result.attachments.find((item) => item.name === kind);
    assert(attachment?.path, `Every browser test needs ${kind}.`);
    const path = realpathSync(attachment.path);
    const inside = relative(recordingRoot, path);
    assert(inside && inside !== '..' && !inside.startsWith(`..${sep}`) && !isAbsolute(inside));
    assert(statSync(path).size > 0, `Empty ${kind}.`);
    const bytes = readFileSync(path);
    if (kind === 'screenshot') assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    if (kind === 'video') assert.equal(bytes.subarray(0, 4).toString('hex'), '1a45dfa3');
    if (kind === 'trace') assert.equal(bytes.subarray(0, 4).toString('hex'), '504b0304');
    counts[kind]++;
  }
}
assert(statSync(resolve(reportRoot, 'html-report/index.html')).size > 0);
console.log(`Product ${mode}: ${results.length} passed, ${counts.screenshot} PNG, ${counts.video} WebM, ${counts.trace} trace ZIP; evidence paths and file signatures verified.`);
