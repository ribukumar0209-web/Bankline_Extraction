const fs = require('fs');
let code = fs.readFileSync('src/App.jsx', 'utf-8');

if (!code.includes('@geoman-io/leaflet-geoman-free')) {
  code = code.replace(
    "import 'leaflet-draw/dist/leaflet-draw.css';",
    "import 'leaflet-draw/dist/leaflet-draw.css';\nimport '@geoman-io/leaflet-geoman-free';\nimport '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';"
  );
}

const mapEditorReshapeEffectOld = `
  useEffect(() => {
    if (drawnItemsRef.current) {
      drawnItemsRef.current.eachLayer(layer => {
        if (layer.editing) {
          if (isReshaping) layer.editing.enable();
          else layer.editing.disable();
        }
      });
      // Important: save changes to geojson state when finishing
      if (!isReshaping && geojson) {
        const data = drawnItemsRef.current.toGeoJSON();
        if (data.features && data.features.length > 0) {
           setGeojson(data);
        }
      }
    }
  }, [isReshaping, setGeojson]);
`;

const mapEditorReshapeEffectNew = `
  useEffect(() => {
    if (drawnItemsRef.current) {
      drawnItemsRef.current.eachLayer(layer => {
        // Disable leaflet-draw editing if active
        if (layer.editing && layer.editing.enabled()) {
           layer.editing.disable();
        }
        
        if (layer.pm) {
          if (isReshaping) {
            layer.pm.enable({
              allowSelfIntersection: true,
              preventMarkerRemoval: false,
              addVertexOn: 'click' // allows adding vertex by clicking the line
            });
            // We can also allow dragging the whole layer if they want, but usually it's editing.
          } else {
            layer.pm.disable();
          }
        }
      });
      // Important: save changes to geojson state when finishing
      if (!isReshaping && geojson) {
        const data = drawnItemsRef.current.toGeoJSON();
        if (data.features && data.features.length > 0) {
           setGeojson(data);
        }
      }
    }
  }, [isReshaping, setGeojson]);
`;

code = code.replace(mapEditorReshapeEffectOld, mapEditorReshapeEffectNew);

// Update tooltip text to mention dragging anywhere on the edge
code = code.replace(
  "Click to enable drag handles on the banklines. Drag the handles or midpoints to reshape the line wherever you want. Click 'Finish Reshaping' when done.",
  "Click to enable advanced Geoman edit mode. You can now click and drag ANYWHERE on the edge of the line to reshape it, not just the midpoints! Click 'Finish Reshaping' when done."
);

fs.writeFileSync('src/App.jsx', code);
