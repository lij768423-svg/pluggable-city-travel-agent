// 隔离fixture浏览器回归。PLAYWRIGHT_MODULE 指向既有安装，不修改其他项目。
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve('eval/runs/mcp-20261018');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'ui-fixture.json'), 'utf8'));
const browser = await chromium.launch({ headless: true });
const records = [];
try {
  for (const viewport of [{width:1440,height:900}, {width:390,height:844}]) {
    const context = await browser.newContext({viewport});
    const page = await context.newPage();
    let generations = 0;
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', request => { if (/\/api\/plan(?:\/stream)?$/.test(new URL(request.url()).pathname)) generations++; });
    await page.goto(`http://127.0.0.1:18682/?trip=${encodeURIComponent(fixture.history_id)}`);
    await page.locator('input[autocomplete="username"]').fill('mcp_ui');
    await page.locator('input[autocomplete="current-password"]').fill('fixture-password-only');
    await page.locator('form button[type="submit"]').click();
    await page.locator('.history-status').waitFor();
    await page.locator('.timeline').first().waitFor().catch(() => {});
    const state = await page.evaluate(async (history_id) => {
      const response = await fetch(`/api/trips/${history_id}`);
      const plan = (await response.json()).plan;
      return {plan_id: plan.plan_id, revision: plan.revision, history_id: plan.history_id,
              readonly: !!plan.history_readonly,
              horizontal_overflow: document.documentElement.scrollWidth > innerWidth,
              day_names: plan.days.flatMap(d => d.items.map(i => i.name))};
    }, fixture.history_id);
    if (state.plan_id !== fixture.plan_id || state.revision !== 0 || state.readonly || generations !== 0 || errors.length || state.horizontal_overflow)
      throw new Error(JSON.stringify({state,generations,errors}));
    await page.screenshot({path:path.join(output,`linked-trip-${viewport.width}.png`),fullPage:true});
    if (viewport.width === 390) {
      const map = page.getByRole('button', {name:'地图',exact:true});
      if (await map.count()) {
        await map.click();
        await page.screenshot({path:path.join(output,'linked-trip-mobile-map.png'),fullPage:true});
      }
    }
    records.push({viewport,...state,generations,page_errors:errors});
    await context.close();
  }
  fs.writeFileSync(path.join(output,'browser-smoke.json'),JSON.stringify(records,null,2));
  console.log(JSON.stringify(records));
} finally { await browser.close(); }
