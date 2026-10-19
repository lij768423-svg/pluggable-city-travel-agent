// 隔离账号库与估算行程，只验证授权及同浏览器免重复登录，不发模型/高德请求。
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const run=path.resolve('eval/runs/mcp-onboarding-20261018');
const fixture=JSON.parse(fs.readFileSync(path.join(run,'onboarding-ui.json'),'utf8'));
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
const results=[];
try {
 for (const viewport of [{width:1440,height:900},{width:390,height:844}]) {
  const context=await browser.newContext({viewport});
  const errors=[]; let generations=0;
  await context.route('**/api/navigation',r=>r.fulfill({status:503,json:{detail:'test display disabled'}}));
  await context.route('**/api/place-details',r=>r.fulfill({status:503,json:{detail:'test display disabled'}}));
  await context.route('**/api/place-media**',r=>r.fulfill({status:503,json:{detail:'test display disabled'}}));
  await context.route('**/api/meta',async r=>{const response=await r.fetch();const body=await response.json();body.map={provider:'leaflet',amap_js_key:null};await r.fulfill({response,json:body});});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  context.on('request',r=>{if(/\/api\/plan(?:\/stream)?$/.test(new URL(r.url()).pathname))generations++;});
  // 每个viewport独立短链接，从隔离测试服务在下方重置；首轮与次轮都实际执行授权。
  let url=fixture.verification_url;
  if(viewport.width===390) {
    // 已用链接不可重复授权；验证已连接提示与同浏览器历史恢复，而非再确认旧链接。
    const session=JSON.parse(fs.readFileSync(path.join(run,'fixture-browser-state.json'),'utf8'));
    await context.addCookies(session.cookies);
  }
  await page.goto(url);
  if(viewport.width===1440) {
    await page.getByLabel('用户名',{exact:true}).fill('onboarding_ui');
    await page.getByLabel('密码',{exact:true}).fill('fixture-password-only');
    await page.getByRole('button',{name:'登船',exact:true}).click();
    await page.getByRole('button',{name:'授权并继续',exact:true}).waitFor();
    await page.screenshot({path:path.join(run,'consent-'+viewport.width+'.png'),animations:'disabled'});
    await page.getByRole('button',{name:'授权并继续',exact:true}).click();
  }
  await page.getByRole('heading',{name:'账号已连接',exact:true}).waitFor();
  await page.screenshot({path:path.join(run,'connected-'+viewport.width+'.png'),animations:'disabled'});
  const cookie=(await context.cookies()).find(c=>c.name==='tta_session');
  if(!cookie?.httpOnly||cookie.expires<Date.now()/1000+29*86400)throw Error('browser login not persistent');
  await context.storageState({path:path.join(run,'fixture-browser-state.json')});
  // 关闭页，再开一页点深链，不再次输入密码。
  await page.close();
  const reopened=await context.newPage();
  reopened.on('pageerror',e=>errors.push(e.message));
  await reopened.goto('http://127.0.0.1:18684/?trip='+fixture.history_id);
  await reopened.getByRole('button',{name:'退出登录',exact:true}).waitFor();
  await reopened.getByRole('heading',{name:'欢迎回来',exact:true}).waitFor({state:'hidden'});
  const loaded=await reopened.evaluate(async id=>(await(await fetch('/api/trips/'+id)).json()).plan,fixture.history_id);
  if(loaded.history_id!==fixture.history_id||generations||errors.length)throw Error('resume mismatch');
  const overflow=await reopened.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  if(overflow)throw Error('horizontal overflow');
  await reopened.screenshot({path:path.join(run,'reopened-'+viewport.width+'.png'),animations:'disabled'});
  results.push({viewport,authorized:true,browser_cookie_days:Math.round((cookie.expires-Date.now()/1000)/86400),reopened_without_login:true,history_id:loaded.history_id,generations,page_errors:errors,horizontal_overflow:overflow});
  await context.close();
 }
 fs.writeFileSync(path.join(run,'browser-results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
} finally {await browser.close(); fs.rmSync(path.join(run,'fixture-browser-state.json'),{force:true});}
