#!/usr/bin/env node
// Selected local pages only; isolated offline Chromium, no user profile/server.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const args = process.argv.slice(2);
function value(flag, fallback) { const i = args.indexOf(flag); return i < 0 ? fallback : args[i+1]; }
(async () => {
  const root = path.resolve(value('--project', '.'));
  const output = path.resolve(value('--output', path.join(root, 'review-assets/page-qa')));
  const files = args.flatMap((a,i) => a === '--file' ? [args[i+1]] : []);
  assert.ok(files.length, 'select at least one --file');
  fs.mkdirSync(output, { recursive: true });
  const options = { headless: true, chromiumSandbox: true };
  if (process.env.CHROME_EXECUTABLE) options.executablePath = process.env.CHROME_EXECUTABLE;
  const browser = await chromium.launch(options); const results = [];
  try {
    const context = await browser.newContext({ offline: true });
    await context.route('**/*', route => route.abort());
    for (const file of files) {
      const source = fs.realpathSync(path.resolve(root, file));
      assert.ok(source.startsWith(fs.realpathSync(root) + path.sep), 'page outside project');
      let html = fs.readFileSync(source, 'utf8').replace(/<img\b[^>]*>/g, tag => tag.replace(/\bsrc="([^"]+)"/, (_, ref) => {
        assert.ok(!/^[a-z]+:/i.test(ref), 'expected local image');
        const asset = fs.realpathSync(path.resolve(path.dirname(source), decodeURIComponent(ref)));
        assert.ok(asset.startsWith(fs.realpathSync(root) + path.sep), 'image outside project');
        const mime = {'.gif':'image/gif','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}[path.extname(asset)];
        assert.ok(mime, 'unsupported image');
        return `src="data:${mime};base64,${fs.readFileSync(asset).toString('base64')}"`;
      }).replace(/loading="lazy"/g, 'loading="eager"'));
      for (const width of [1365, 390]) {
        const page = await context.newPage(); await page.setViewportSize({width,height:900});
        await page.setContent(html, {waitUntil:'load'});
        const metrics = await page.evaluate(() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
          badImages:[...document.images].filter(i=>!i.complete||!i.naturalWidth).length,
          corrections:document.querySelector('#corrections')?.textContent || ''}));
        assert.ok(metrics.scroll <= width, `${file}: overflow`); assert.equal(metrics.badImages,0);
        const details = page.locator('details').first();
        if (await details.count()) {
          const before = await details.getAttribute('open'); await details.locator('summary').first().click();
          assert.notEqual(await details.getAttribute('open'),before);
        }
        await page.evaluate(()=>scrollTo(0,0));
        const prefix = path.basename(file,'.html')+'-'+width;
        await page.screenshot({path:path.join(output,prefix+'.png'),fullPage:true});
        if (await page.locator('#corrections').count()) await page.locator('#corrections').screenshot({path:path.join(output,prefix+'-corrections.png')});
        results.push({file,width,...metrics}); await page.close();
      }
    }
    await context.close();
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({result:'passed',cases:results.length,output}));
})().catch(e=>{console.error(e);process.exitCode=1;});
