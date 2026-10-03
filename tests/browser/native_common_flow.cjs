const {chromium} = require('playwright');
const assert = require('assert'), fs = require('fs');
const {createResponseBodyCapture} = require('./response_body_capture.cjs');
const [base, out, offer, mode, expectedPath] = process.argv.slice(2);
const fixture = JSON.parse(fs.readFileSync(expectedPath, 'utf8')), expected = fixture.getter;
const allowedMedia = new Set(fixture.test_only_media_urls);
const fixtureImage = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#d9bca0"/><text x="35" y="210" font-size="24">SYNTHETIC IMAGE</text></svg>';
(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  const page = await context.newPage(), errors = [], posts = [], captured = [];
  const owned = request => new URL(request.url()).pathname === '/api/product-workspace/publication-stages';
  const bodies = createResponseBodyCapture((response, raw) => {
    assert.strictEqual(response.status(), 200);
    captured.push(JSON.parse(raw.toString('utf8')));
  });
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request();
    if (request.method() !== 'GET') {
      posts.push({method: request.method(), url: request.url()});
      await route.abort();
      return;
    }
    if (!request.url().startsWith(base + '/')) {
      assert(request.resourceType() === 'image' && allowedMedia.has(request.url()),
        'No unregistered foreign request');
      return route.fulfill({status: 200, contentType: 'image/svg+xml', body: fixtureImage});
    }
    return route.continue();
  });
  page.on('request', request => { if (owned(request)) bodies.requestStarted(request); });
  page.on('requestfinished', request => { if (owned(request)) bodies.requestFinished(request); });
  page.on('requestfailed', request => { if (owned(request)) bodies.requestFailed(request); });
  page.on('response', response => { if (owned(response.request())) bodies.capture(response); });
  const screenshots = [];
  try {
    await page.goto(base + '/product-workspace?offer_id=' + offer + '&round=final');
    assert.strictEqual(await page.locator('#originalPublicationReview').isVisible(), false);
    await page.locator('#tab-release').click();
    await page.locator('#pane-release').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#publicationFlowReview')?.textContent.includes('COMMON 来源与执行条件'));
    await bodies.drain();
    assert(captured.length > 0, 'Real stage getter must have been requested');
    for (const row of captured) {
      assert.strictEqual(row.offer_id, offer);
      assert.deepStrictEqual(row.common.technical_admission, expected.common.technical_admission);
    }
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.locator('#publicationFlowReview').scrollIntoViewIfNeeded();
      const text = await page.locator('#publicationFlowReview').innerText();
      assert(text.includes(expected.common.user_status.summary));
      assert(text.includes('准备来源待核对'));
      assert(!text.includes('完整候选资料已恢复'));
      const status = await page.locator('#publicationFlowStatus').innerText();
      for (const item of expected.common.user_status.pending_items) assert(status.includes(item));
      assert(!/READ|EDIT|schema|APPROVAL_REQUIRED/.test(status));
      const detail = page.locator('#publicationFlowReview > details').first();
      assert.strictEqual(await detail.locator('summary').innerText(), '当前检查原因与阶段身份 · 完整详情');
      assert.strictEqual(await detail.evaluate(element => element.open), false);
      await detail.locator('summary').click();
      const evidence = JSON.parse(await detail.locator('pre').textContent());
      assert.deepStrictEqual(evidence.common_technical_admission, expected.common.technical_admission);
      const source = evidence.common_technical_admission.source_facts;
      const authority = evidence.common_technical_admission.authority_facts;
      assert.strictEqual(evidence.common_technical_admission.status, 'BLOCKED');
      assert.strictEqual(authority.account_authority, 'UNKNOWN');
      assert.strictEqual(authority.coverage_authority, 'UNKNOWN');
      assert.strictEqual(authority.schema_installation, 'UNKNOWN');
      assert.strictEqual(evidence.common_technical_admission.execution_authority, false);
      assert.strictEqual(evidence.common_technical_admission.final_review_available, false);
      assert(evidence.common_technical_admission.blockers.includes('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN'));
      if (mode === 'retained') {
        assert.strictEqual(source.status, 'RETAINED_IDENTITY_VERIFIED');
        assert.strictEqual(source.native_local_census.status, 'LOCAL_PERSISTED_CENSUS');
        assert.strictEqual(source.native_local_census.local_observed_confirmed_writes, 1);
        assert.strictEqual(source.native_local_census.local_observed_readonly_reuses, 1);
        assert.strictEqual(source.native_local_census.confirmed_write_count, 'UNKNOWN');
        assert.strictEqual(source.native_local_census.budget_authority, 'UNKNOWN');
      } else {
        assert.strictEqual(mode, 'unknown');
        assert.strictEqual(source.status, 'UNKNOWN');
        assert(!Object.hasOwn(source, 'native_local_census'));
        assert(!text.includes('本地账本记录确认写入'));
        assert(!text.includes('剩余可写次数 1'));
      }
      await detail.locator('summary').click();
      assert.strictEqual(await detail.evaluate(element => element.open), false);
      const ledger = await page.locator('#publicationFlowLedger').innerText();
      assert(ledger.includes('COMMON 准备条件待核对'));
      assert.strictEqual(await page.locator('#publicationFlowActions button').count(), 0);
      assert.strictEqual(await page.locator('#originalFinalActions button').count(), 0);
      assert.strictEqual(await page.locator('#releasePlanApprovalForm').isVisible(), false);
      assert.strictEqual(await page.locator('#legacyReleaseActionPanels').isVisible(), false);
      assert.strictEqual(await page.locator('#approveReleasePlanButton').isDisabled(), true);
      assert.strictEqual(await page.locator('#prepareMiaoshouButton').isDisabled(), true);
      assert.strictEqual(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const file = out + '/native-common-flow-' + mode + '-' + width + '.png';
      await page.screenshot({path: file, fullPage: true});
      screenshots.push(file);
    }
    assert.deepStrictEqual(errors, []);
    assert.deepStrictEqual(posts, []);
    await bodies.drain();
    assert.strictEqual(bodies.pendingCount, 0);
    assert.strictEqual(bodies.pendingRequestCount, 0);
    fs.writeFileSync(out + '/real-flow-stage-responses.json', JSON.stringify(captured, null, 2));
    console.log(JSON.stringify({source_mode: mode, errors, posts, screenshots,
      viewport_widths: [1440, 390], authority: 'UNKNOWN_SYNTHETIC_RETAINED_IO_NO_EXECUTION'}));
  } catch (error) {
    fs.writeFileSync(out + '/real-flow-failure-dom.html', await page.content());
    await page.screenshot({path: out + '/real-flow-failure.png', fullPage: true});
    throw error;
  } finally {
    try {
      await bodies.drain();
      assert.strictEqual(bodies.pendingCount, 0);
      assert.strictEqual(bodies.pendingRequestCount, 0);
    } finally {
      await browser.close();
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
