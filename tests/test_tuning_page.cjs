/* Older-RH fallback page, using intercepted HTTP requests and no physical gate. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

test('fallback refresh, manual save, failed save and receiver removal', async t => {
  const options = {headless: true};
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) options.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch(options);
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const html = fs.readFileSync(path.resolve(__dirname, '../custom_plugins/fpvgate/templates/split_tuning.html'), 'utf8');
  const node = {index: 0, frequency: 5658, enter: 100, exit: 50, rssi: 60,
    inside: false, history: [[0, 60, false], [0.5, 62, false]]};
  const split = {split: 0, address: 'mock-only', connected: true, nodes: [node]};
  const data = {splits: [split]};
  let failWrite = false;
  let writes = 0;
  await page.route('**/*', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/fpvgate/api/split-tuning')) {
      if (request.method() === 'POST') {
        writes++;
        if (failWrite) return route.fulfill({status: 503, json: {ok: false, message: 'Mock write failed'}});
        const values = request.postDataJSON();
        node.enter = values.enter;
        node.exit = values.exit;
        return route.fulfill({json: {ok: true, enter: node.enter, exit: node.exit}});
      }
      return route.fulfill({json: data});
    }
    return route.fulfill({contentType: 'text/html; charset=utf-8', body: html});
  });
  await page.goto('http://mock.rh/fpvgate/split-tuning');
  await page.waitForFunction(() => document.querySelector('input')?.value === '100');
  const enter = page.locator('.controls input').nth(0);
  node.enter = 110;
  await page.waitForFunction(() => document.querySelector('input')?.value === '110');
  await enter.fill('120');
  node.enter = 115;
  await page.waitForResponse(response => response.url().endsWith('/fpvgate/api/split-tuning'));
  assert.equal(await enter.inputValue(), '120', 'Polling must preserve an unsaved edit');
  await page.getByRole('button', {name: 'Save levels'}).click();
  await page.getByText('Saved to gate.', {exact: true}).waitFor();
  assert.equal(node.enter, 120);
  failWrite = true;
  await enter.fill('121');
  await page.getByRole('button', {name: 'Save levels'}).click();
  await page.getByText('Mock write failed', {exact: true}).waitFor();
  assert.equal(await enter.inputValue(), '120', 'Failed writes must restore confirmed thresholds');
  assert.equal(writes, 2);
  split.nodes = [];
  await page.waitForFunction(() => document.querySelectorAll('.node').length === 0);
  data.splits = [];
  await page.getByText('No split gates are configured.', {exact: false}).waitFor();
  split.nodes = [node];
  data.splits = [split];
  await page.waitForFunction(() => document.querySelector('input')?.value === '120');
  assert.equal(await page.locator('.empty').count(), 0);
  assert.deepEqual(errors, []);
});
