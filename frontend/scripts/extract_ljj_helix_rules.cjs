// 从 https://ljj.world/projects 线上页面导出命中双螺旋页元素的全部 CSS 规则（make_ljj_helix_css.py 再加作用域生成 src/ljj-helix.css）。用法：node scripts/extract_ljj_helix_rules.cjs，需 playwright。
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const fs = require('fs');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('https://ljj.world/projects', { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);
  const out = await page.evaluate(() => {
    const root = document.querySelector('.project-index');
    const els = [root, ...root.querySelectorAll('*')];
    let a = root.parentElement; while (a) { els.push(a); a = a.parentElement; }
    const strip = s => s.replace(/::?(before|after|placeholder|selection)/g, '').replace(/:(hover|focus-visible|focus-within|focus|active|visited)/g, '').trim() || '*';
    const splitSel = s => { const parts = []; let depth = 0, cur = ''; for (const ch of s) { if (ch === '(') depth++; if (ch === ')') depth--; if (ch === ',' && !depth) { parts.push(cur); cur = ''; } else cur += ch; } parts.push(cur); return parts; };
    const matches = sel => splitSel(sel).some(part => { try { const t = strip(part); return els.some(e => e.matches(t)); } catch { return false; } });
    const kept = [], keyframes = new Set(), vars = new Set();
    const walk = (rules, wrap) => {
      for (const r of rules) {
        if (r instanceof CSSStyleRule) {
          if (r.selectorText && matches(r.selectorText) && !/^(html|body|\*|:root)$/.test(r.selectorText.trim()) ) {
            let text = r.cssText;
            for (const w of [...wrap].reverse()) text = `${w} { ${text} }`;
            kept.push(text);
            (r.cssText.match(/var\(--[a-z0-9-]+/gi) || []).forEach(v => vars.add(v.slice(4)));
            const an = r.style.animationName || r.style.animation; if (an) an.split(/[ ,]+/).forEach(n => keyframes.add(n));
          }
          if (r.cssRules && r.cssRules.length) walk(r.cssRules, [...wrap, r.selectorText]);
        } else if (r instanceof CSSMediaRule) walk(r.cssRules, [...wrap, '@media ' + r.conditionText]);
        else if (r instanceof CSSSupportsRule) walk(r.cssRules, [...wrap, '@supports ' + r.conditionText]);
        else if (r.cssRules && r.constructor.name === 'CSSLayerBlockRule') walk(r.cssRules, [...wrap, '@layer ' + r.name]);
        else if (r.cssRules && r.constructor.name === 'CSSContainerRule') walk(r.cssRules, [...wrap, '@container ' + r.conditionText]);
      }
    };
    for (const sheet of document.styleSheets) { try { walk(sheet.cssRules, []); } catch (e) {} }
    const kf = [];
    for (const sheet of document.styleSheets) { try { for (const r of sheet.cssRules) if (r instanceof CSSKeyframesRule && keyframes.has(r.name)) kf.push(r.cssText); } catch {} }
    const rootStyle = getComputedStyle(document.documentElement);
    const varVals = [...vars].map(v => `${v}: ${rootStyle.getPropertyValue(v).trim()};`);
    return { rules: kept, kf, varVals, theme: document.documentElement.getAttribute('data-theme'), html: root.outerHTML.slice(0, 4000) };
  });
  fs.writeFileSync(process.env.HOME + '/pano-check/ljj-rules.json', JSON.stringify(out, null, 1));
  console.log(out.rules.length, 'rules', out.kf.length, 'keyframes', out.varVals.length, 'vars', 'theme', out.theme);
  await browser.close();
})();
