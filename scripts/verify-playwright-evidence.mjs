import assert from 'node:assert/strict';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const mode = process.argv[2] ?? 'headless';
assert(['headless', 'headed'].includes(mode), 'Choose headless or headed evidence.');
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const reportRoot = resolve(projectRoot, 'artifacts/playwright', mode);
const recordingsRoot = realpathSync(resolve(reportRoot, 'test-results'));
const report = JSON.parse(readFileSync(resolve(reportRoot, 'results.json'), 'utf8'));
assert(report.stats.expected > 0 && report.stats.unexpected === 0, 'The run must contain passing tests and no unexpected results.');

function results(suites) {
  return suites.flatMap((suite) => [
    ...(suite.specs ?? []).flatMap((spec) => spec.tests.flatMap((test) => test.results)),
    ...results(suite.suites ?? []),
  ]);
}

const counts = { screenshot: 0, video: 0, trace: 0 };
const testResults = results(report.suites);
assert(testResults.length > 0, 'No recorded test results.');
for (const result of testResults) {
  assert.equal(result.status, 'passed', 'Evidence verification requires a passing run without retries.');
  const attachments = result.attachments ?? [];
  assert(attachments.some((item) => item.name === 'trace'), 'Every test must retain a trace.');
  if (attachments.some((item) => item.name === 'video')) {
    assert(attachments.some((item) => item.name === 'screenshot'), 'Browser tests must retain a screenshot.');
  }
  for (const attachment of attachments.filter((item) => item.name in counts)) {
    assert(attachment.path, `Missing file path for ${attachment.name}.`);
    const path = realpathSync(attachment.path);
    const inside = relative(recordingsRoot, path);
    assert(inside && inside !== '..' && !inside.startsWith(`..${sep}`) && !isAbsolute(inside), `${mode} recording escaped its own output directory: ${attachment.name}`);
    assert(statSync(path).isFile() && statSync(path).size > 0, `Empty recording: ${attachment.name}`);
    counts[attachment.name]++;
  }
}
assert(counts.screenshot > 0 && counts.video > 0, 'At least one browser test must retain screenshot and video evidence.');
assert(statSync(resolve(reportRoot, 'html-report/index.html')).size > 0, 'HTML report is missing.');
console.log(`${mode} evidence verified: ${testResults.length} passed tests; ${counts.screenshot} screenshots, ${counts.video} videos, ${counts.trace} traces in the correct directory.`);
