// 真实MCP草稿的页面核对；认证通过临时会话在内存传入，不读取用户密码。
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const run=path.resolve('eval/runs/mcp-20261018');
const summary=JSON.parse(fs.readFileSync(path.join(run,'codex-live-draft-smoke.json'),'utf8'));
const browser=await chromium.launch({headless:true});
const results=[];
try {
 for(const viewport of [{width:1440,height:900},{width:390,height:844}]) {
  const context=await browser.newContext({viewport});
  await context.addCookies([{name:'tta_session',value:process.env.TTA_UI_AUTH_TOKEN,url:'http://127.0.0.1:18686',httpOnly:true,sameSite:'Strict'}]);
  const page=await context.newPage();
  const blocked=[];let generations=0;const errors=[];
  // 本次只核对同一草稿和标记，不用额外供应商请求换取截图。
  await page.route('**/api/navigation',r=>{blocked.push('navigation');return r.fulfill({status:503,contentType:'application/json',body:'{"detail":"本次页面验收未发导航请求"}'});});
  await page.route('**/api/place-details',r=>{blocked.push('details');return r.fulfill({status:503,contentType:'application/json',body:'{"detail":"本次页面验收未发详情请求"}'});});
  await page.route('**/api/place-media**',r=>{blocked.push('media');return r.fulfill({status:503,contentType:'application/json',body:'{"detail":"本次页面验收未发媒体请求"}'});});
  await page.route('**/api/meta',async r=>{const response=await r.fetch();const body=await response.json();body.map={provider:'leaflet',amap_js_key:null};await r.fulfill({response,json:body});});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/\/api\/plan(?:\/stream)?$/.test(new URL(r.url()).pathname))generations++;});
  await page.goto(summary.site_url);
  await page.getByText('测试草稿 · 待核实',{exact:true}).waitFor();
  const data=await page.evaluate(async id=>(await(await fetch('/api/trips/'+id)).json()).plan,summary.history_id);
  if(data.plan_id!==summary.plan_id||data.revision!==0||data.delivery_status!=='draft'||data.report.complete_executable!==false||generations||errors.length)throw Error('draft display mismatch');
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  if(overflow)throw Error('horizontal overflow');
  // 页面包含进入动画；先等待内容完成绘制，避免把初始透明帧当作视觉证据。
  await page.waitForTimeout(2000);
  await page.screenshot({path:path.join(run,'real-draft-'+viewport.width+'.png'),fullPage:true,animations:'disabled'});
  results.push({viewport,plan_id:data.plan_id,revision:data.revision,delivery_status:data.delivery_status,complete_executable:data.report.complete_executable,unknowns:data.report.unknowns.length,regenerations:generations,page_errors:errors,horizontal_overflow:overflow,blocked_display_calls:blocked});
  await context.close();
 }
 fs.writeFileSync(path.join(run,'real-draft-browser.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
} finally {await browser.close();}
