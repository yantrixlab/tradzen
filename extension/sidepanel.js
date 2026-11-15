// Loads the dashboard into the side panel with a unique address every time the panel opens, so Chrome can never show an old cached copy of the page.
document.getElementById('dash').src = 'http://127.0.0.1:8787/?embed=1&t=' + Date.now();
