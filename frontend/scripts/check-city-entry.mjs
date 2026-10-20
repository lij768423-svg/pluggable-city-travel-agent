// 中性入口与城市过渡浏览器回归：运行 npm run build 后执行。Playwright 可通过外部安装路径提供。
// TTA_PLAYWRIGHT_MODULE=/path/to/playwright/index.js node scripts/check-city-ui.mjs
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'node:http'
import { readFile, mkdir, writeFile, copyFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)(process.env.TTA_PLAYWRIGHT_MODULE || 'playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const out = path.resolve(process.env.TTA_C3_EVIDENCE || path.join(root, 'data/city-entry-20261019'))
await mkdir(out, { recursive: true })
const metaHZ = JSON.parse(await readFile(path.join(out, 'meta-hangzhou.json')))
const metaNJ = JSON.parse(await readFile(path.join(out, 'meta-nanjing.json')))
const hz = metaHZ.cities.find(c => c.key === 'hangzhou')
const nj = metaHZ.cities.find(c => c.key === 'nanjing')
const servers = []
async function serve(dist, port) {
  const server = createServer(async (req, res) => {
    try {
      const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
      const target = path.resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname))
      if (!target.startsWith(dist + '/')) throw Error('Invalid path')
      const data = await readFile(target)
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.woff2':'font/woff2' }
      res.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream')
      res.end(data)
    } catch { res.writeHead(404); res.end() }
  })
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve))
  servers.push(server)
  return `http://127.0.0.1:${port}`
}
const base = await serve(path.join(root, 'frontend/dist'), 18688)
const before = process.env.TTA_C3_BASELINE_DIST ? await serve(path.resolve(process.env.TTA_C3_BASELINE_DIST), 18689) : null
const browser = await chromium.launch({ headless:true, ...(process.env.TTA_CHROMIUM ? {executablePath: process.env.TTA_CHROMIUM} : {}) })
const results = []
const errors = []
function fixture(city, legacy = false) {
  return {
    city_info: city, profile: { ...(legacy ? {} : {city:city.key}), days:2, budget:800, pace:'normal', interests:['人文历史'], dietary:[], companions:'', transport:'transit', must_visit:[], avoid:[], notes:'', hotel:city.default_origin, hotel_gcj:city.map_center },
    days:[{day:1,items:[],return_travel_min:0,notes:[]}], report:{passed:true,experience_status:'needs_attention',data_status:'partial',unknowns:[],violations:[],totals:{total:0}},
    candidates:[], dropped:[], plan_id:null, revision:null, change:null,log:[], usage:{calls:0,cached_calls:0,total_tokens:0,seconds:0},seconds:0,
    meta:{travel:{source:'estimate'},select_mode:'rule',enable_repair:true,repair_rounds:0,llm_model:'mock',llm_thinking:null,tag_source:'rule'},history_readonly:true,
  }
}
async function setup({single=false, onlyNJ=false, signed=false, delayed=false, failCity=false, holdPlan=false, navigationStops=false, loginGate=false, motion='reduce', url=base, mobile=false}={}) {
  const context = await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},reducedMotion:motion,...(motion==='no-preference'?{recordVideo:{dir:out,size:{width:1440,height:1000}}}:{})})
  const page = await context.newPage()
  const requests = []; const pendingSearch = []; let captured; let navigationBody; let logged=signed; let cityFailed = failCity
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/*', async route => {
    const u = new URL(route.request().url())
    if (u.origin !== new URL(url).origin) return route.abort()
    if (!u.pathname.startsWith('/api/')) return route.continue()
    requests.push(u.pathname+u.search)
    const json = value => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)})
    if (u.pathname === '/api/auth/me') return json({user:logged?{id:'tester',username:'city_tester'}:null})
    if (u.pathname === '/api/auth/login') { logged=true; return json({user:{id:'tester',username:'city_tester'}}) }
    if (u.pathname.startsWith('/api/mcp/connect/')) return json({approved:false,client_name:'测试旅行助手'})
    if (u.pathname === '/api/meta') {
      const city = u.searchParams.get('city') || (onlyNJ ? 'nanjing' : 'hangzhou')
      if (city === 'nanjing' && cityFailed) { cityFailed=false; return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'临时不可用'})}) }
      if (city === 'nanjing' && delayed) await new Promise(resolve => setTimeout(resolve,150))
      const meta = structuredClone(city==='nanjing'?metaNJ:metaHZ)
      if(loginGate)meta.access.login_to_plan=true
      if (single || onlyNJ) meta.cities=[onlyNJ?nj:hz]
      return json(meta)
    }
    if (u.pathname === '/api/places/search') {
      if (delayed) await new Promise(resolve=>pendingSearch.push(resolve))
      return json({places:[{...hz.default_origin,id:'old-search',name:'杭州迟到结果',address:'旧请求',source:'amap',coordinate_system:'BD-09'}]})
    }
    if (u.pathname === '/api/plan/stream') {
      captured=route.request().postDataJSON()
      if(holdPlan === 'animate') await new Promise(resolve=>setTimeout(resolve,4500))
      if(holdPlan) return route.fulfill({status:200,contentType:'application/x-ndjson',body:[{type:'progress',stage:'retrieve',status:'running',round:0,detail:''},{type:'error',message:'浏览器回归中的模拟断流'}].map(e=>JSON.stringify(e)).join('\n')+'\n'})
      const plan=fixture(captured.form.city==='nanjing'?nj:hz)
      if(navigationStops) {
        plan.plan_id='fixture-plan'
        plan.days[0].items=[{kind:'poi',ref_id:1,name:'测试地点',start:600,end:660,start_hm:'10:00',end_hm:'11:00',travel_min:10,travel_mode:'公交/地铁',cost:0,reason:'测试导航请求',meal:null,type:'人文历史',lat:32.02,lon:118.78,source:'rule',score:null}]
      }
      return route.fulfill({status:200,contentType:'application/x-ndjson',body:JSON.stringify({type:'result',plan})+'\n'})
    }
    if (u.pathname === '/api/history') return json({items:[{id:'old',title:'杭州旧行程',created:1,updated:1},{id:'nj',title:'南京历史',created:1,updated:1}],has_more:false})
    if (u.pathname.startsWith('/api/history/') || u.pathname.startsWith('/api/trips/')) {
      const legacy = u.pathname.endsWith('/old'); const city=legacy?hz:nj
      return json({id:legacy?'old':'nj',title:'历史',created:1,updated:1,request:{query:legacy?'旧行程说明':'南京历史说明',form:{...(legacy?{}:{city:city.key}),days:2,hotel:city.default_origin},select_mode:'rule',enable_repair:true},plan:fixture(city,legacy)})
    }
    if (u.pathname === '/api/place-details') return json({places:[],queries:0})
    if (u.pathname === '/api/navigation') { navigationBody=route.request().postDataJSON();return json({legs:[],queries:0,coordinate_system:'GCJ-02'}) }
    return json({})
  })
  await page.goto(url)
  await page.getByRole('link',{name:'行记首页'}).waitFor()
  if(!url.includes('?')) await page.getByRole('heading',{name:/下一程，\s*想去哪里？/}).waitFor()
  await page.evaluate(()=>document.fonts.ready)
  return {page,context,requests,pendingSearch,get captured(){return captured},get navigationBody(){return navigationBody}}
}
async function next(page) { await page.getByRole('button',{name:'继续',exact:true}).click() }
async function lastStep(page) { await next(page); await next(page) }
async function capture(page,file) { await page.screenshot({path:path.join(out,file),animations:'disabled'}) }
async function check(name, fn) { try { await fn() } catch(error) { console.error('PAGE ERRORS', JSON.stringify(errors)); throw error } results.push(name); console.log('PASS',name) }
async function enter(p,city='杭州') {
  await p.getByRole('button',{name:`选择${city}`,exact:true}).click()
  await p.locator('.plan-form').waitFor({state:'visible'})
  await p.locator('.brand-name').filter({hasText:city+'行记'}).waitFor()
}
async function back(p) {
  await p.getByRole('button',{name:'更换目的地',exact:true}).click()
  await p.getByRole('heading',{name:/下一程，\s*想去哪里？/}).waitFor()
  await p.locator('.brand-name').filter({hasText:/^行记$/}).waitFor()
}
try {
  await check('neutral destination entry precedes city brand and questionnaire',async()=>{
    const t=await setup({signed:true});const p=t.page
    assert.equal(await p.title(),'行记 · 选择你的下一程')
    await p.locator('.brand-name').filter({hasText:/^行记$/}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'行记')
    assert.equal(await p.locator('.destination-card').count(),2)
    assert.equal(await p.locator('.questionnaire-backdrop').count(),0)
    assert(!(await p.locator('.plan-form').isVisible()))
    await capture(p,'entry-desktop.png');await t.context.close()
  })
  await check('Nanjing choice opens own scene; search and generation carry correct city and origin',async()=>{
    const t=await setup();const p=t.page;await enter(p,'南京')
    assert.equal(await p.title(),'南京行记 · 定制你的南京旅程')
    await p.locator('.brand-name').filter({hasText:'南京行记'}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'南京行记')
    assert.equal(await p.locator('.city-picker').count(),0)
    assert.equal(await p.locator('.city-change-notice').count(),0)
    await p.locator('.qb-west[data-scene="明城与梧桐"] svg').waitFor()
    await capture(p,'nanjing-questionnaire.png')
    await next(p);assert.match(await p.locator('.qform-body').innerText(),/往返南京交通/)
    await next(p);assert.match(await p.locator('.origin-current').innerText(),/新街口/)
    await p.getByRole('button',{name:'更换',exact:true}).click()
    await p.getByRole('textbox',{name:'每天的起点与终点'}).fill('地铁站')
    await p.getByRole('button',{name:'搜索',exact:true}).click()
    await p.getByText('杭州迟到结果',{exact:true}).waitFor()
    assert(t.requests.some(url=>url.startsWith('/api/places/search?')&&url.endsWith('&city=nanjing')))
    await p.getByRole('button',{name:'取消更换'}).click()
    await p.getByRole('button',{name:'生成行程',exact:true}).click();await p.locator('.trip-head').waitFor()
    assert.equal(t.captured.form.city,'nanjing')
    assert.deepEqual(t.captured.form.hotel,{name:nj.default_origin.name,lat:nj.default_origin.lat,lon:nj.default_origin.lon})
    assert.match(await p.locator('.trip-head h1').innerText(),/南京/)
    await p.getByRole('link',{name:'行记首页',exact:true}).click();await enter(p,'杭州')
    assert(await p.locator('.plan-form').isVisible())
    await t.context.close()
  })
  await check('same-city return preserves draft; another city clears local requirements and keeps general preferences',async()=>{
    const t=await setup();const p=t.page;await enter(p)
    await p.getByRole('button',{name:'人文历史',exact:true}).click()
    await lastStep(p);await p.locator('#trip-query').fill('一定去灵隐寺，不去宋城')
    await back(p);await enter(p)
    assert.equal(await p.locator('#trip-query').inputValue(),'一定去灵隐寺，不去宋城')
    await back(p);await enter(p,'南京')
    assert.equal(await p.locator('#trip-query').inputValue(),'')
    assert.match(await p.locator('.city-change-notice').innerText(),/已切换至南京/)
    assert.equal(await p.getByRole('button',{name:'人文历史',exact:true}).getAttribute('aria-pressed'),'true')
    assert.match(await p.locator('.origin-current').innerText(),/新街口/)
    await t.context.close()
  })
  await check('late origin results never enter the newly chosen city',async()=>{
    const t=await setup({delayed:true});const p=t.page;await enter(p)
    await lastStep(p);await p.getByRole('button',{name:'更换',exact:true}).click()
    await p.getByRole('textbox',{name:'每天的起点与终点'}).fill('杭州酒店');await p.getByRole('button',{name:'搜索',exact:true}).click()
    await p.waitForFunction(()=>document.querySelector('.origin-go').textContent==='搜索中')
    await back(p);await enter(p,'南京');t.pendingSearch.forEach(resolve=>resolve())
    assert.match(await p.locator('.origin-current').innerText(),/新街口/)
    assert.equal(await p.getByText('杭州迟到结果',{exact:true}).count(),0)
    await t.context.close()
  })
  await check('meta failure remains on neutral entry and can retry',async()=>{
    const t=await setup({failCity:true});const p=t.page
    await p.getByRole('button',{name:'选择南京',exact:true}).click()
    await p.getByRole('alert').waitFor();await p.locator('.brand-name').filter({hasText:/^行记$/}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'行记')
    assert(!(await p.locator('.plan-form').isVisible()))
    await enter(p,'南京');await p.locator('.brand-name').filter({hasText:'南京行记'}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'南京行记')
    await t.context.close()
  })
  await check('old history keeps Hangzhou context after a Nanjing draft and city roundtrip',async()=>{
    const t=await setup({signed:true});const p=t.page;await enter(p,'南京')
    await p.getByRole('button',{name:'历史行程',exact:true}).click();await p.getByRole('button',{name:'杭州旧行程',exact:true}).click()
    await p.locator('.trip-head').waitFor();await p.locator('.brand-name').filter({hasText:'杭州行记'}).waitFor();await p.locator('.brand-name').filter({hasText:'杭州行记'}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'杭州行记')
    await p.getByRole('button',{name:'按这些偏好重新规划'}).click();await lastStep(p)
    assert.equal(await p.locator('#trip-query').inputValue(),'旧行程说明')
    await back(p);await enter(p,'南京');await p.getByRole('button',{name:'返回行程',exact:true}).click()
    await p.getByRole('button',{name:'修改',exact:true}).click();await p.getByRole('button',{name:'继续',exact:true}).waitFor()
    await lastStep(p);assert.equal(await p.locator('#trip-query').inputValue(),'旧行程说明')
    await t.context.close()
  })
  await check('disabled history is viewable and reports replan restriction',async()=>{
    const t=await setup({single:true,signed:true});const p=t.page
    await p.getByRole('button',{name:'历史行程',exact:true}).click()
    await p.locator('.history-list li').filter({hasText:'南京历史'}).getByRole('button',{name:'重新规划',exact:true}).click()
    await p.getByText('这座城市暂未开放，历史行程仍可查看。',{exact:true}).waitFor()
    await p.locator('.brand-name').filter({hasText:'南京行记'}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'南京行记')
    assert(await p.locator('.trip-head').isVisible());await t.context.close()
  })
  await check('anonymous entry stays neutral; selected city is retained through required login',async()=>{
    const t=await setup({loginGate:true});const p=t.page
    assert.equal(await p.locator('.auth-page').count(),0)
    await p.getByRole('button',{name:'登录 / 注册',exact:true}).click();await p.locator('.auth-page').waitFor()
    assert.equal(await p.locator('.banner-text strong').innerText(),'行记')
    assert.equal(await p.locator('.auth-art').count(),0)
    await p.getByRole('button',{name:'返回',exact:true}).click()
    await p.getByRole('button',{name:'选择杭州',exact:true}).click();await p.locator('.auth-page').waitFor()
    await p.getByRole('button',{name:'返回',exact:true}).click()
    await p.getByRole('heading',{name:/下一程，\s*想去哪里？/}).waitFor()
    await p.getByRole('button',{name:'选择南京',exact:true}).click();await p.locator('.auth-page').waitFor()
    assert.equal(await p.locator('.banner-text strong').innerText(),'南京行记')
    await p.getByLabel('用户名',{exact:true}).fill('city_tester')
    await p.getByLabel('密码',{exact:true}).fill('mock-password-only')
    await p.getByRole('button',{name:'登船',exact:true}).click()
    await p.getByRole('heading',{name:'这趟旅行，想怎么过？'}).waitFor()
    await p.locator('.brand-name').filter({hasText:'南京行记'}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'南京行记');await t.context.close()
  })
  await check('single-city still confirms a destination; mobile entry and city page have no horizontal overflow',async()=>{
    const t=await setup({onlyNJ:true,mobile:true});const p=t.page
    assert.equal(await p.locator('.destination-card').count(),1)
    await p.locator('.brand-name').filter({hasText:/^行记$/}).waitFor();assert.equal(await p.locator('.brand-name').innerText(),'行记')
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
    await capture(p,'entry-mobile-single.png');await enter(p,'南京')
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
    await t.context.close()
    const two=await setup({mobile:true});await capture(two.page,'entry-mobile.png');await enter(two.page,'南京');await capture(two.page,'nanjing-mobile.png');await two.context.close()
  })
  await check('trip and MCP authorization deep links bypass destination selection',async()=>{
    const t=await setup({signed:true,url:base+'/?trip=old'});const p=t.page
    await p.locator('.trip-head').waitFor();assert.equal(await p.locator('.destination-page').count(),0);await t.context.close()
    const m=await setup({signed:true,url:base+'/?mcp_connect=mock-connection'});await m.page.getByRole('heading',{name:'连接你的旅行账号'}).waitFor();assert.equal(await m.page.locator('.destination-page').count(),0);await m.context.close()
  })
  await check('normal motion includes entrance, card hover, departure and scenery arrival',async()=>{
    const t=await setup({motion:'no-preference'});const p=t.page
    const animationNames=await p.evaluate(()=>Array.from(document.querySelectorAll('.destination-heading > *, .destination-card')).map(el=>getComputedStyle(el).animationName))
    assert(animationNames.includes('destination-rise'))
    await p.getByRole('button',{name:'选择南京',exact:true}).hover()
    await p.getByRole('button',{name:'选择南京',exact:true}).click()
    await p.locator('.destination-transit:not(.destination-boot)').waitFor()
    assert(await p.getByRole('button',{name:'选择杭州',exact:true}).isDisabled())
    await p.getByRole('heading',{name:'这趟旅行，想怎么过？'}).waitFor()
    await back(p);await p.getByRole('heading',{name:/下一程，\s*想去哪里？/}).waitFor()
    const video = p.video();await t.context.close();if(video)await copyFile(await video.path(),path.join(out,'city-transition.webm'))
  })
  await check('city progress and interrupted request preserve Nanjing context',async()=>{
    const t=await setup({holdPlan:true});const p=t.page;await enter(p,'南京')
    await lastStep(p);await p.getByRole('button',{name:'生成行程',exact:true}).click()
    await p.getByText('浏览器回归中的模拟断流',{exact:true}).waitFor()
    assert.match(await p.locator('.progress-eyebrow').innerText(),/南京行记/)
    assert.match(await p.locator('.panel-scroll').innerText(),/整理你的南京行记/)
    assert(!(await p.locator('.panel-scroll').innerText()).includes('杭州'))
    assert.equal(t.captured.form.city,'nanjing')
    await p.getByRole('button',{name:'返回修改',exact:true}).click()
    assert.match(await p.locator('.origin-current').innerText(),/新街口/)
    await t.context.close()
  })
  await check('Nanjing navigation carries result city and plan identity',async()=>{
    const t=await setup({navigationStops:true});const p=t.page;await enter(p,'南京')
    const navigationDone=p.waitForResponse(r=>r.url().endsWith('/api/navigation'))
    await lastStep(p);await p.getByRole('button',{name:'生成行程',exact:true}).click()
    await p.locator('.trip-head').waitFor()
    await navigationDone
    assert.equal(t.navigationBody.city,'nanjing');assert.equal(t.navigationBody.plan_id,'fixture-plan')
    assert.equal(t.navigationBody.legs.length,2)
    assert(t.navigationBody.legs.every(leg=>leg.origin.lat>31 && leg.destination.lat>31))
    await t.context.close()
  })
  await check('city-specific generation scenery, real-step marker, pause and reduced motion',async()=>{
    for(const city of ['南京','杭州']) {
      const t=await setup({holdPlan:true});const p=t.page;await enter(p,city)
      await lastStep(p);await p.getByRole('button',{name:'生成行程',exact:true}).click()
      await p.getByText('浏览器回归中的模拟断流',{exact:true}).waitFor()
      const key=city==='南京'?'nanjing':'hangzhou'
      assert.equal(await p.locator('.generation-landscape').getAttribute('data-city'),key)
      assert.equal(await p.locator(`[data-landscape="${key}"]`).count(),1)
      assert.equal(await p.locator('[data-landscape]').count(),1)
      assert.equal(await p.locator('#pg-pagoda').count(),city==='杭州'?1:0)
      assert.equal(await p.locator(`[data-city-mark="${key}"]`).count(),1)
      assert.equal(await p.locator('.pg-stop').count(),7)
      assert.equal(await p.locator('.generation-art.paused').count(),1)
      assert.equal(await p.locator('.pg-cloud').evaluate(e=>getComputedStyle(e).animationName),'none')
      await capture(p,`generation-${key}.png`);await t.context.close()
    }
    const t=await setup({holdPlan:true,mobile:true});const p=t.page;await enter(p,'南京')
    await lastStep(p);await p.getByRole('button',{name:'生成行程',exact:true}).click()
    await p.getByText('浏览器回归中的模拟断流',{exact:true}).waitFor()
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
    await capture(p,'generation-nanjing-mobile.png');await t.context.close()
  })
  await check('Nanjing scenery animates while busy and pauses on failure',async()=>{
    const t=await setup({holdPlan:'animate',motion:'no-preference'});const p=t.page;await enter(p,'南京')
    await lastStep(p);await p.getByRole('button',{name:'生成行程',exact:true}).click()
    await p.locator('[data-landscape="nanjing"]').waitFor()
    assert.equal(await p.locator('#pg-pagoda').count(),0)
    assert.match(await p.locator('.pg-tree').first().evaluate(e=>getComputedStyle(e).animationName),/^pg-sway/)
    assert.equal(await p.locator('.pg-qinhuai-boat').evaluate(e=>getComputedStyle(e).animationPlayState),'running')
    await p.locator('.questionnaire-backdrop').waitFor({state:'detached'})
    await p.waitForFunction(()=>Number(getComputedStyle(document.querySelector('.plan-progress')).opacity)>.99 && Number(getComputedStyle(document.querySelector('.pg-wall')).opacity)>.99)
    await p.getByRole('heading',{name:'正在安排你的行程',exact:true}).waitFor()
    await p.screenshot({path:path.join(out,'generation-nanjing-busy.png')})
    await p.getByText('浏览器回归中的模拟断流',{exact:true}).waitFor()
    assert.equal(await p.locator('.pg-qinhuai-boat').evaluate(e=>getComputedStyle(e).animationPlayState),'paused')
    const video=p.video();await t.context.close();if(video)await copyFile(await video.path(),path.join(out,'generation-nanjing.webm'))
  })
  await check('reduced motion enters without departure wait and focuses questionnaire heading',async()=>{
    const t=await setup();const p=t.page;await enter(p,'南京')
    assert.equal(await p.locator('.destination-transit').count(),0)
    assert.equal(await p.evaluate(()=>document.activeElement?.textContent),'这趟旅行，想怎么过？')
    await t.context.close()
  })
  assert.deepEqual(errors,[])
  await writeFile(path.join(out,'browser-results.json'),JSON.stringify({passed:results.length,cases:results,page_errors:errors,external_api_calls:0,plan_response:'mock'},null,2))
} finally {
  await browser.close()
  await Promise.all(servers.map(server=>new Promise(resolve=>server.close(resolve))))
}
