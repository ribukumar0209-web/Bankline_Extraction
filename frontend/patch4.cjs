const fs = require('fs');
let code = fs.readFileSync('src/App.jsx', 'utf-8');

const regex = /useEffect\(\(\) => \{\s*if \(\!drawnItemsRef\.current\) \{[\s\S]*?map\.on\(L\.Draw\.Event\.DELETED, updateGeoJSONState\);\s*\}\s*\}, \[map, setGeojson, setRoiCoords\]\);/;

const newEffect = `  useEffect(() => {
    if (!drawnItemsRef.current) {
      drawnItemsRef.current = new L.FeatureGroup();
    }
    if (!map.hasLayer(drawnItemsRef.current)) {
      map.addLayer(drawnItemsRef.current);
    }

    const drawControl = new L.Control.Draw({
      edit: {
        featureGroup: drawnItemsRef.current,
        remove: true,
      },
      draw: {
        polygon: true,
        rectangle: { showArea: false },
        polyline: true,
        circle: false,
        circlemarker: false,
        marker: false,
      }
    });
    
    map.addControl(drawControl);

    const updateGeoJSONState = () => {
      if (drawnItemsRef.current) {
        const data = drawnItemsRef.current.toGeoJSON();
        setGeojson(data.features && data.features.length > 0 ? data : null);
      }
    };

    const handleCreated = (e) => {
      drawnItemsRef.current.addLayer(e.layer);
      updateGeoJSONState();
      
      // If it's a polygon, we can try to save it as ROI just in case they are on the ROI tab
      if (e.layer instanceof L.Polygon) {
        const latlngs = e.layer.getLatLngs()[0];
        const coords = latlngs.map(latlng => [latlng.lng, latlng.lat]);
        coords.push([latlngs[0].lng, latlngs[0].lat]);
        setRoiCoords([coords]);
      }
    };

    map.on(L.Draw.Event.CREATED, handleCreated);
    map.on(L.Draw.Event.EDITED, updateGeoJSONState);
    map.on(L.Draw.Event.DELETED, updateGeoJSONState);
    
    // CLEANUP FUNCTION to remove duplicate controls on re-renders
    return () => {
      map.off(L.Draw.Event.CREATED, handleCreated);
      map.off(L.Draw.Event.EDITED, updateGeoJSONState);
      map.off(L.Draw.Event.DELETED, updateGeoJSONState);
      map.removeControl(drawControl);
    };
  }, [map, setGeojson, setRoiCoords]);`;

code = code.replace(regex, newEffect);
fs.writeFileSync('src/App.jsx', code);
