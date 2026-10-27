const fs = require('fs');

let code = fs.readFileSync('src/App.jsx', 'utf8');

// Replace turf import to include nearestPointOnLine
code = code.replace("import { lineIntersect, lineSlice, length, point, distance } from '@turf/turf';", "import { lineIntersect, lineSlice, length, point, distance, nearestPointOnLine } from '@turf/turf';");

const oldApplyReshape = code.substring(code.indexOf('function applyArcGISReshape'), code.indexOf('function MapEditor'));
const oldMapEditor = code.substring(code.indexOf('function MapEditor'), code.indexOf('function App() {'));

const newApplyReshape = `function applyArcGISReshape(drawnItemsGroup, reshapeLayer) {
    const reshapeGeoJSON = reshapeLayer.toGeoJSON();
    let reshapedCount = 0;
    const layers = drawnItemsGroup.getLayers();
    
    // First pass: try standard intersection (crosses feature >= 2 times)
    for (let layer of layers) {
        if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) {
            const existingGeoJSON = layer.toGeoJSON();
            let currentReshape = JSON.parse(JSON.stringify(reshapeGeoJSON));
            let intersections = lineIntersect(existingGeoJSON, currentReshape).features;
            
            const sketchStart = point(currentReshape.geometry.coordinates[0]);
            const sketchEnd = point(currentReshape.geometry.coordinates[currentReshape.geometry.coordinates.length - 1]);
            
            // Allow endpoints to snap if they are very close (within 25 meters / 0.025 km)
            const snapStart = nearestPointOnLine(existingGeoJSON, sketchStart);
            if (snapStart.properties.dist <= 0.025) {
                if (!intersections.some(i => distance(i, snapStart) < 0.001)) {
                    intersections.push(point(snapStart.geometry.coordinates));
                    currentReshape.geometry.coordinates.unshift(snapStart.geometry.coordinates);
                }
            }
            
            const snapEnd = nearestPointOnLine(existingGeoJSON, sketchEnd);
            if (snapEnd.properties.dist <= 0.025) {
                if (!intersections.some(i => distance(i, snapEnd) < 0.001)) {
                    intersections.push(point(snapEnd.geometry.coordinates));
                    currentReshape.geometry.coordinates.push(snapEnd.geometry.coordinates);
                }
            }
            
            if (intersections.length >= 2) {
                try {
                    let intersectionPoints = intersections.map(f => {
                        return {
                            point: f,
                            distOnExisting: length(lineSlice(point(existingGeoJSON.geometry.coordinates[0]), f, existingGeoJSON))
                        };
                    });
                    
                    intersectionPoints.sort((a, b) => a.distOnExisting - b.distOnExisting);
                    const firstInt = intersectionPoints[0];
                    const lastInt = intersectionPoints[intersectionPoints.length - 1];
                    
                    const existingStartPt = point(existingGeoJSON.geometry.coordinates[0]);
                    const existingEndPt = point(existingGeoJSON.geometry.coordinates[existingGeoJSON.geometry.coordinates.length - 1]);
                    
                    const part1 = lineSlice(existingStartPt, firstInt.point, existingGeoJSON);
                    const part3 = lineSlice(lastInt.point, existingEndPt, existingGeoJSON);
                    
                    let reshapePart = lineSlice(firstInt.point, lastInt.point, currentReshape);
                    
                    const part1End = part1.geometry.coordinates[part1.geometry.coordinates.length - 1];
                    const reshapeStart = reshapePart.geometry.coordinates[0];
                    
                    if (distance(point(part1End), point(reshapeStart)) > 0.001) {
                         reshapePart.geometry.coordinates.reverse();
                    }
                    
                    const coords1 = part1.geometry.coordinates.slice(0, -1);
                    const coords2 = reshapePart.geometry.coordinates;
                    const coords3 = part3.geometry.coordinates.slice(1);
                    
                    const newCoords = [...coords1, ...coords2, ...coords3];
                    layer.setLatLngs(newCoords.map(c => [c[1], c[0]]));
                    reshapedCount++;
                } catch(err) {
                    console.error("Reshape geometry error:", err);
                }
            }
        }
    }
    return reshapedCount;
}

`;

const newMapEditor = `function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson, isReshaping, setIsReshaping }) {
  const map = useMap();
  const drawnItemsRef = useRef(null);
  const isReshapingRef = useRef(isReshaping);

  // Programmatically enable Polyline tool when reshaping is turned on
  useEffect(() => {
    isReshapingRef.current = isReshaping;
    
    if (isReshaping) {
        if (!window.reshapePolylineDrawer) {
            window.reshapePolylineDrawer = new L.Draw.Polyline(map, {
                shapeOptions: { color: '#27ae60', weight: 4 },
                repeatMode: true // Like ArcGIS, stays active until you finish!
            });
        }
        window.reshapePolylineDrawer.enable();
    } else {
        if (window.reshapePolylineDrawer) {
            window.reshapePolylineDrawer.disable();
        }
    }
  }, [isReshaping, map]);

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
            const reshaped = applyArcGISReshape(drawnItemsRef.current, e.layer);
            if (reshaped > 0) {
                updateGeoJSONState();
                // Silently reshape, just like ArcGIS Pro! (No alert popups)
            }
            // Discard the sketched line, don't add to map.
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
  }, [map, setGeojson, setRoiCoords]);

  // Sync incoming GeoJSON to the editable FeatureGroup
  useEffect(() => {
    if (drawnItemsRef.current) {
      drawnItemsRef.current.clearLayers();
      if (geojson) {
        const geoJsonLayer = L.geoJSON(geojson, {
          style: function(feature) {
            if (feature.properties && feature.properties.bank === 'Left') {
              return { color: 'red', weight: 4 };
            } else if (feature.properties && feature.properties.bank === 'Right') {
              return { color: 'black', weight: 4 };
            }
            return { color: '#3388ff', weight: 4 };
          }
        });
        geoJsonLayer.eachLayer(layer => {
          drawnItemsRef.current.addLayer(layer);
        });
        
        // Auto-zoom to fit the new data
        try {
            const bounds = drawnItemsRef.current.getBounds();
            if (bounds.isValid()) {
                map.fitBounds(bounds, { padding: [20, 20] });
            }
        } catch (e) {
            console.log("Could not fit bounds");
        }
      }
    }
  }, [geojson, map]);

  return null;
}

`;

code = code.replace(oldApplyReshape, newApplyReshape);
code = code.replace(oldMapEditor, newMapEditor);

// Remove the obsolete tooltip instructions about "use the Polyline tool"
code = code.replace("ArcGIS-style Reshape: Click this, then use the Polyline tool to draw a line that crosses an existing bankline at least twice. The segment between intersections will be replaced by your sketch! Click 'Finish Reshaping' when done.",
"ArcGIS-style Reshape: Click this to activate the reshape tool. Then, sketch a line across the segment of the bankline you want to replace. The tool snaps to the line for accuracy! Click 'Finish Reshaping' when done.");

fs.writeFileSync('src/App.jsx', code);
