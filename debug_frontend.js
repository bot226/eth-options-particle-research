const puppeteer = require('puppeteer');

(async () => {
    const browser = await puppeteer.launch({ headless: true });
    const page = await browser.newPage();

    page.on('console', msg => {
        if (msg.type() === 'error') {
            console.error('BROWSER ERROR:', msg.text());
        } else {
            console.log('BROWSER LOG:', msg.text());
        }
    });

    page.on('pageerror', err => {
        console.error('BROWSER PAGE ERROR:', err.toString());
    });

    await page.goto('http://localhost:5173/research', { waitUntil: 'networkidle0' });
    
    // wait for 2 seconds to allow charts to render
    await new Promise(r => setTimeout(r, 2000));
    
    await browser.close();
})();
