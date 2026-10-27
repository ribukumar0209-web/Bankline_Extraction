const fs = require('fs');
let code = fs.readFileSync('src/App.jsx', 'utf-8');

if (!code.includes('@geoman-io/leaflet-geoman-free')) {
  code = code.replace(
    "import 'leaflet-draw/dist/leaflet.draw.css';",
    "import 'leaflet-draw/dist/leaflet.draw.css';\nimport '@geoman-io/leaflet-geoman-free';\nimport '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';"
  );
}
fs.writeFileSync('src/App.jsx', code);
