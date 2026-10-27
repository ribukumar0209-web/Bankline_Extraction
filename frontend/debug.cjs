const puppeteer = require('puppeteer');

(async () => {
    try {
        const browser = await puppeteer.launch({ headless: "new" });
        const page = await browser.newPage();
        
        page.on('console', msg => console.log('LOG:', msg.type(), msg.text()));
        page.on('pageerror', error => console.log('ERROR:', error.message));
        
        await page.goto('http://localhost:4173');
        await new Promise(r => setTimeout(r, 2000));
        await browser.close();
    } catch (e) {
        console.error("Puppeteer error:", e);
    }
})();
