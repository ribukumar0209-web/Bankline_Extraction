const puppeteer = require('puppeteer');

(async () => {
    try {
        const browser = await puppeteer.launch({ headless: "new" });
        const page = await browser.newPage();
        
        // Log all console messages
        page.on('console', msg => console.log('LOG:', msg.text()));
        
        // Handle alerts automatically
        page.on('dialog', async dialog => {
            console.log("ALERT:", dialog.message());
            await dialog.accept();
        });

        await page.goto('http://localhost:4173');
        await page.waitForSelector('.action-btn-blue', { timeout: 5000 }).catch(() => {});
        
        // Let's inject a fake geojson to test reshaping
        await page.evaluate(() => {
            // Find the React root and set some state if possible, 
            // actually simpler: just mock the global fetch or use the UI.
            // But we can't easily set React state from outside.
        });
        
        await browser.close();
    } catch (e) {
        console.error("Puppeteer error:", e);
    }
})();
