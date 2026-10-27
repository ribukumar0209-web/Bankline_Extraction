import re

with open('src/App.jsx', 'r', encoding='utf-8') as f:
    code = f.read()

# Add turf import
if "import * as turf" not in code:
    code = code.replace("import './App.css';", "import './App.css';\nimport * as turf from '@turf/turf';")

# Update MapEditor signature
code = code.replace(
    'function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson }) {',
    'function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson, isReshaping, setIsReshaping }) {'
)

# Replace the MapEditor body
old_body = '''function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson, isReshaping, setIsReshaping }) {
  const map = useMap();
  const drawnItemsRef = useRef(null);

  useEffect(() => {
    if (!drawnItemsRef.current) {
      drawnItemsRef.current = new L.FeatureGroup();
      map.addLayer(drawnItemsRef.current);

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
        const data = drawnItemsRef.current.toGeoJSON();
        setGeojson(data.features.length > 0 ? data : null);
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
    }
  }, [map, setGeojson, setRoiCoords]);'''

new_body = '''function applyArcGISReshape(drawnItemsGroup, reshapeLayer) {
    const reshapeGeoJSON = reshapeLayer.toGeoJSON();
    let reshapedCount = 0;
    const layers = drawnItemsGroup.getLayers();
    
    for (let layer of layers) {
        if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) {
            const existingGeoJSON = layer.toGeoJSON();
            
            // For MultiLineString, we'd need to iterate over coordinates, but let's assume LineString for simplicity or handle both:
            // The pipeline flattens MultiLineStrings to LineStrings before setting to map! So we only deal with LineStrings here.
            
            const intersections = turf.lineIntersect(existingGeoJSON, reshapeGeoJSON);
            
            if (intersections.features.length >= 2) {
                let intersectionPoints = intersections.features.map(f => {
                    return {
                        point: f,
                        distOnExisting: turf.length(turf.lineSlice(turf.point(existingGeoJSON.geometry.coordinates[0]), f, existingGeoJSON)),
                        distOnReshape: turf.length(turf.lineSlice(turf.point(reshapeGeoJSON.geometry.coordinates[0]), f, reshapeGeoJSON))
                    };
                });
                
                intersectionPoints.sort((a, b) => a.distOnExisting - b.distOnExisting);
                const firstInt = intersectionPoints[0];
                const lastInt = intersectionPoints[intersectionPoints.length - 1];
                
                const existingStartPt = turf.point(existingGeoJSON.geometry.coordinates[0]);
                const existingEndPt = turf.point(existingGeoJSON.geometry.coordinates[existingGeoJSON.geometry.coordinates.length - 1]);
                
                const part1 = turf.lineSlice(existingStartPt, firstInt.point, existingGeoJSON);
                const part3 = turf.lineSlice(lastInt.point, existingEndPt, existingGeoJSON);
                
                let reshapePart = turf.lineSlice(firstInt.point, lastInt.point, reshapeGeoJSON);
                
                const part1End = part1.geometry.coordinates[part1.geometry.coordinates.length - 1];
                const reshapeStart = reshapePart.geometry.coordinates[0];
                
                if (turf.distance(turf.point(part1End), turf.point(reshapeStart)) > 0.001) {
                     reshapePart.geometry.coordinates.reverse();
                }
                
                const coords1 = part1.geometry.coordinates.slice(0, -1);
                const coords2 = reshapePart.geometry.coordinates;
                const coords3 = part3.geometry.coordinates.slice(1);
                
                const newCoords = [...coords1, ...coords2, ...coords3];
                layer.setLatLngs(newCoords.map(c => [c[1], c[0]]));
                reshapedCount++;
            }
        }
    }
    return reshapedCount;
}

function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson, isReshaping, setIsReshaping }) {
  const map = useMap();
  const drawnItemsRef = useRef(null);
  const isReshapingRef = useRef(isReshaping);

  useEffect(() => {
    isReshapingRef.current = isReshaping;
  }, [isReshaping]);

  useEffect(() => {
    if (!drawnItemsRef.current) {
      drawnItemsRef.current = new L.FeatureGroup();
      map.addLayer(drawnItemsRef.current);

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
        const data = drawnItemsRef.current.toGeoJSON();
        setGeojson(data.features.length > 0 ? data : null);
      };

      const handleCreated = (e) => {
        if (isReshapingRef.current && e.layer instanceof L.Polyline && !(e.layer instanceof L.Polygon)) {
            try {
                const reshaped = applyArcGISReshape(drawnItemsRef.current, e.layer);
                if (reshaped > 0) {
                    updateGeoJSONState();
                    alert(`Successfully reshaped ${reshaped} line(s)!`);
                } else {
                    alert("The drawn line must cross an existing line at least twice to reshape it.");
                }
            } catch (err) {
                console.error(err);
                alert("Error during reshape: " + err.message);
            }
            return;
        }

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
    }
  }, [map, setGeojson, setRoiCoords]);'''

code = code.replace(old_body, new_body)

with open('src/App.jsx', 'w', encoding='utf-8') as f:
    f.write(code)
print("Updated MapEditor successfully.")
