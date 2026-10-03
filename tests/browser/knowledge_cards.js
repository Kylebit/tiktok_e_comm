const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

(async () => {
  const [base, output] = process.argv.slice(2);
  const errors = [], denied = [], writes = [];
  const browser = await chromium.launch({headless:true, executablePath:'C:/Users/Windows11/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe', args:['--disable-background-networking','--no-first-run']});
  const context = await browser.newContext({viewport:{width:1360,height:920},serviceWorkers:'block'});
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(base+'/') || url.startsWith('data:')) return route.continue();
    denied.push(url);
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {if (!['GET','HEAD'].includes(request.method())) writes.push(request.method()+' '+request.url());});
  try {
    await page.goto(base+'/knowledge');
    await page.locator('[data-knowledge-key="tool:prepare-product-publication"]').click();
    const facts = page.locator('.knowledge-brief');
    assert((await facts.textContent()).includes('输入'));
    assert((await facts.textContent()).includes('产出与证据'));
    assert((await facts.textContent()).includes('操作边界'));
    assert((await facts.textContent()).includes('失败恢复'));
    assert((await facts.textContent()).includes('skills/prepare-product-publication'));
    assert((await page.locator('.knowledge-workflow').innerText()).includes('执行顺序'));
    assert.strictEqual(await page.locator('.knowledge-workflow li').count(), 3);
    assert((await page.locator('.knowledge-readiness').innerText()).includes('账号与 API'));
    assert((await page.locator('.knowledge-readiness').innerText()).includes('本页未验证'));
    assert(await page.locator('.knowledge-example').evaluate(node => node.open));
    assert((await page.locator('#knowledgeExample').inputValue()).includes('Offer ID'));
    await page.screenshot({path:path.join(output,'knowledge-skill-1360.png'),fullPage:true});
    await page.locator('[data-knowledge-key="tool:prepare-product-images"]').click();
    assert((await page.locator('#knowledgeExample').inputValue()).includes('第一轮技术冻结快照'));
    assert(!(await page.locator('#knowledgeExample').inputValue()).includes('第一轮已批准'));
    assert((await page.locator('[data-knowledge-key="tool:manage-seaya-replenishment"] .knowledge-row-status').innerText()).includes('来源待核'));
    await page.locator('[data-knowledge-key="tool:manage-seaya-replenishment"]').click();
    assert((await page.locator('.knowledge-workflow').innerText()).includes('有效订单'));
    assert((await page.locator('.knowledge-source-notice').innerText()).includes('版本不一致'));
    assert((await page.locator('.knowledge-source-notice').innerText()).includes('旧快照'));
    assert((await page.locator('.knowledge-readiness').innerText()).includes('Skill 来源冲突'));
    await page.locator('.knowledge-source-checksums > summary').click();
    const checksums = await page.locator('.knowledge-source-checksums').innerText();
    assert(checksums.includes('本机安装版原始字节'));
    assert(checksums.includes('~/.codex/skills/manage-seaya-replenishment/SKILL.md'));
    // Current project bytes are independent of the dated personal-install
    // evidence. Keep this assertion after any legitimate repo Skill update.
    const currentProjectSkill=fs.readFileSync(path.join(__dirname,'../../domains/supply_chain_operations/skills/manage-seaya-replenishment/SKILL.md'));
    const currentProjectDigest=require('crypto').createHash('sha256').update(currentProjectSkill).digest('hex');
    assert(checksums.includes(currentProjectDigest));
    await page.screenshot({path:path.join(output,'knowledge-supply-source-1360.png'),fullPage:true});
    await page.locator('[data-knowledge-key="tool:manage-profit-settlement"]').click();
    assert((await page.locator('.knowledge-workflow').innerText()).includes('结算'));
    assert.strictEqual(await page.locator('.knowledge-source-notice').count(), 0);
    for (const id of ['prepare-product-publication','prepare-product-images','publish-approved-product','delist-products-by-sku','manage-seaya-replenishment','manage-profit-settlement']) {
      await page.locator(`[data-knowledge-key="tool:${id}"]`).click();
      const detail = await page.locator('#knowledgeDetail').innerText();
      for (const label of ['可用性与权限','工程文件','账号与 API','业务执行','用途','输入','产出与证据','操作边界','原文入口']) assert(detail.includes(label), `${id}: ${label}`);
    }
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path:path.join(output,'knowledge-skill-390.png'),fullPage:true});
    assert.deepStrictEqual(errors,[]);
    assert.deepStrictEqual(denied,[]);
    assert.deepStrictEqual(writes,[]);
    fs.writeFileSync(path.join(output,'knowledge-browser-result.json'),JSON.stringify({passed:true,errors,denied,writes},null,2));
  } catch (error) {
    await page.screenshot({path:path.join(output,'knowledge-failure.png'),fullPage:true});
    fs.writeFileSync(path.join(output,'knowledge-browser-result.json'),JSON.stringify({passed:false,error:String(error),errors,denied,writes},null,2));
    throw error;
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
