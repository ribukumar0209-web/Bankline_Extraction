import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { MapContainer, TileLayer, LayersControl, useMap, useMapEvents, ImageOverlay, LayerGroup, ScaleControl } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw';
import 'leaflet-draw/dist/leaflet.draw.css';
import './App.css';
import { lineIntersect, lineSlice, length, point, distance, nearestPointOnLine } from '@turf/turf';







function DynamicLandsatLayer({ data, mapGeojson }) {
  const [url, setUrl] = useState(null);
  const year = data.start_date ? data.start_date.substring(0, 4) : new Date().getFullYear();
  
  useMapEvents({
    baselayerchange(e) {
      if (e.name === `Landsat (${year})` && !url) {
        fetchBasemap(year);
      }
    }
  });

  useEffect(() => {
    setUrl(null);
  }, [year, data.asset_id, data.feature_index, data.roi_type, mapGeojson]);

  const fetchBasemap = async (y) => {
    try {
      const payload = {
        year: parseInt(y),
        roi_type: data.roi_type,
        asset_id: data.asset_id,
        feature_index: Number(data.feature_index),
        roi_geojson: (data.roi_type === 'draw' || data.roi_type === 'upload') ? mapGeojson : null
      };
      const res = await axios.post(`${API_BASE}/yearly_basemap`, payload);
      if (res.data.success) {
        setUrl(res.data.tile_url);
      }
    } catch (e) {
      console.error(e);
    }
  };

  return url ? <TileLayer url={url} attribution={`© USGS / NASA Landsat ${year}`} /> : null;
}






const API_BASE = `http://${window.location.hostname}:8000/api`;

// Custom component to handle Leaflet Draw and GeoJSON layers natively
function applyArcGISReshape(drawnItemsGroup, reshapeLayer) {
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

function MapEditor({ roiCoords, setRoiCoords, geojson, setGeojson, isReshaping, setIsReshaping }) {
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

      // Block interaction with other Leaflet draw tools when reshaping is active
      setTimeout(() => {
          const drawContainer = drawControl.getContainer();
          if (drawContainer) {
              drawContainer.addEventListener('click', function(e) {
                  if (isReshapingRef.current) {
                      alert("The Reshape Bankline tool is active! Please click 'Finish Reshaping' before using other tools.");
                      e.stopPropagation();
                      e.preventDefault();
                  }
              }, true); // use capture phase
          }
      }, 100);

      // Inject custom Zoom to Layer button between Draw and Edit toolbars
      setTimeout(() => {
          const drawContainer = drawControl.getContainer();
          if (drawContainer) {
              const drawSection = drawContainer.querySelector('.leaflet-draw-section');
              if (drawSection) {
                  const customSection = L.DomUtil.create('div', 'leaflet-draw-section');
                  const customToolbar = L.DomUtil.create('div', 'leaflet-draw-toolbar leaflet-bar', customSection);
                  const zoomBtn = L.DomUtil.create('a', 'leaflet-draw-draw-zoom', customToolbar);
                  
                  zoomBtn.href = '#';
                  zoomBtn.title = 'Zoom to Layer';
                  zoomBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#464646" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-top: 7px;"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>'; // Magnifying glass
                  zoomBtn.style.display = 'block';
                  zoomBtn.style.textAlign = 'center';
                  zoomBtn.style.textDecoration = 'none';
                  zoomBtn.style.backgroundImage = 'none';
                  
                  const measureBtn = L.DomUtil.create('a', 'leaflet-draw-draw-measure', customToolbar);
                  measureBtn.href = '#';
                  measureBtn.title = 'Measure Distance';
                  measureBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#464646" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-top: 7px;"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.29 7 12 12 20.71 7"></polyline><line x1="12" y1="22" x2="12" y2="12"></line></svg>'; // Ruler/cube icon
                  measureBtn.style.display = 'block';
                  measureBtn.style.textAlign = 'center';
                  measureBtn.style.textDecoration = 'none';
                  measureBtn.style.backgroundImage = 'none';
                  
                  L.DomEvent.on(zoomBtn, 'click', function(e) {
                      L.DomEvent.stopPropagation(e);
                      L.DomEvent.preventDefault(e);
                      try {
                          const bounds = drawnItemsRef.current.getBounds();
                          if (bounds.isValid()) {
                              map.fitBounds(bounds, { padding: [20, 20] });
                          }
                      } catch (err) {}
                  });

                  L.DomEvent.on(measureBtn, 'click', function(e) {
                      L.DomEvent.stopPropagation(e);
                      L.DomEvent.preventDefault(e);
                      window._toggleMeasureMode();
                  });
                  
                  drawSection.insertAdjacentElement('afterend', customSection);
              }
          }
      }, 100);

      // Measure Tool Logic
      const measureLayer = new L.LayerGroup();
      map.addLayer(measureLayer);
      let measurePoints = [];
      let measurePolyline = null;
      let isMeasuring = false;

      window._toggleMeasureMode = () => {
          isMeasuring = !isMeasuring;
          const container = document.querySelector('.leaflet-container');
          const measureBtn = document.querySelector('.leaflet-draw-draw-measure');
          if (isMeasuring) {
              if (measureBtn) measureBtn.style.backgroundColor = '#ddd';
              if (container) container.style.cursor = 'crosshair';
          } else {
              if (measureBtn) measureBtn.style.backgroundColor = '';
              if (container) container.style.cursor = '';
              measureLayer.clearLayers();
              measurePoints = [];
              measurePolyline = null;
          }
      };

      const handleMapClick = (e) => {
          if (!isMeasuring) return;
          measurePoints.push(e.latlng);
          if (!measurePolyline) {
              measurePolyline = L.polyline(measurePoints, {color: '#2c3e50', weight: 3, dashArray: '5, 5'}).addTo(measureLayer);
          } else {
              measurePolyline.setLatLngs(measurePoints);
          }
          
          let totalDistance = 0;
          for (let i = 0; i < measurePoints.length - 1; i++) {
              totalDistance += measurePoints[i].distanceTo(measurePoints[i+1]);
          }
          
          const m = L.circleMarker(e.latlng, {radius: 4, color: '#e74c3c', fillOpacity: 1}).addTo(measureLayer);
          if (measurePoints.length > 1) {
              let text = (totalDistance > 1000) ? (totalDistance/1000).toFixed(2) + ' km' : totalDistance.toFixed(0) + ' m';
              m.bindTooltip(text, {permanent: true, direction: 'right', offset: [5, 0]}).openTooltip();
          } else {
              m.bindTooltip('Start', {permanent: true, direction: 'right', offset: [5, 0]}).openTooltip();
          }
      };

      map.on('click', handleMapClick);

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
      
      return () => {
        map.off('click', handleMapClick);
        map.removeLayer(measureLayer);
        map.off(L.Draw.Event.CREATED, handleCreated);
        map.off(L.Draw.Event.EDITED, updateGeoJSONState);
        map.off(L.Draw.Event.DELETED, updateGeoJSONState);
        map.removeControl(drawControl);
        map.removeLayer(drawnItemsRef.current);
        drawnItemsRef.current = null;
      };
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
        if (geojson._shouldZoom) {
            try {
                const bounds = drawnItemsRef.current.getBounds();
                if (bounds.isValid()) {
                    map.fitBounds(bounds, { padding: [20, 20] });
                }
            } catch (e) {
                console.log("Could not fit bounds");
            }
            geojson._shouldZoom = false;
        }
      }
    }
  }, [geojson, map]);

  return null;
}

function App() {
  const [roiCoords, setRoiCoords] = useState(null);
  const [isReshaping, setIsReshaping] = useState(false);
  
  // The master GeoJSON state representing the current editable map data
  const [mapGeojson, setMapGeojson] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [tileLayers, setTileLayers] = useState({}); // Store dynamic tile URLs
  const [visibleLayers, setVisibleLayers] = useState({}); // Store visibility
  const [imageBounds, setImageBounds] = useState(null); // Store ImageOverlay bounds
  const [activeTooltip, setActiveTooltip] = useState(null);

  const enforceReshapingRule = (e) => {
      if (isReshaping) {
          alert("The Reshape Bankline tool is active! Please click 'Finish Reshaping' before using other tools.");
          if (e) {
              e.stopPropagation();
              e.preventDefault();
          }
          return true;
      }
      return false;
  };





  // Authentication State
  const [isAuthenticated, setIsAuthenticated] = useState(true); // Assume true initially to avoid flicker

  const [data, setData] = useState({
    feature_index: 0,
    start_date: '',
    end_date: '',
    asset_id: '',
    roi_type: 'draw',
    composite_method: 'median'
  });

  // Yearly dropdown state
  const [activeLandsatYear, setActiveLandsatYear] = useState("");
  const [activeLandsatUrl, setActiveLandsatUrl] = useState("");
  const [landsatLoading, setLandsatLoading] = useState(false);

  useEffect(() => {
    if (!activeLandsatYear) {
      setActiveLandsatUrl("");
      return;
    }
    const fetchUrl = async () => {
      setLandsatLoading(true);
      try {
        const payload = {
          year: parseInt(activeLandsatYear),
          roi_type: data.roi_type,
          asset_id: data.asset_id,
          feature_index: Number(data.feature_index),
          roi_geojson: (data.roi_type === 'draw' || data.roi_type === 'upload') ? mapGeojson : null
        };
        const res = await axios.post(`${API_BASE}/yearly_basemap`, payload);
        if (res.data.success) {
          setActiveLandsatUrl(res.data.tile_url);
        }
      } catch (err) {
        console.error(err);
      }
      setLandsatLoading(false);
    };
    fetchUrl();
  }, [activeLandsatYear, data.asset_id, data.feature_index, data.roi_type, mapGeojson]);







  // Check auth status on load
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const res = await axios.get(`${API_BASE}/auth_status`);
        setIsAuthenticated(res.data.is_authenticated);
      } catch (err) {
        console.error("Failed to check auth status", err);
      }
    };
    checkAuth();
  }, []);

  const handleAuthUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setLoading(true);
    setLoadingMsg("Authenticating Earth Engine...");
    try {
      const res = await axios.post(`${API_BASE}/auth_service_account`, formData, {
         headers: { 'Content-Type': 'multipart/form-data' }
      });
      if (res.data.success) {
         setIsAuthenticated(true);
         alert("Google Earth Engine Authenticated Successfully!");
      } else {
         alert("Authentication failed: " + res.data.message);
      }
    } catch (err) {
      alert("Auth error: " + err.message);
    }
    setLoading(false);
    setLoadingMsg("");
    // Clear the file input
    e.target.value = null;
  };


  const flattenMultiLineStrings = (data) => {
    if (!data || !data.features) return data;
    const newFeatures = [];
    data.features.forEach(f => {
      if (f.geometry && f.geometry.type === 'MultiLineString') {
        f.geometry.coordinates.forEach(lineCoords => {
          newFeatures.push({
            type: 'Feature',
            properties: { ...f.properties },
            geometry: { type: 'LineString', coordinates: lineCoords }
          });
        });
      } else {
        newFeatures.push(f);
      }
    });
    return { ...data, features: newFeatures };
  };

  const handleFileUpload = async (e) => {

    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setLoading(true);
    setLoadingMsg("Uploading Shapefile...");
    try {
      const res = await axios.post(`${API_BASE}/upload_roi`, formData, {
         headers: { 'Content-Type': 'multipart/form-data' }
      });
      if (res.data.success) {
         setMapGeojson(Object.assign(flattenMultiLineStrings(res.data.geojson), { _shouldZoom: true }));
      } else {
         alert("Upload failed: " + res.data.error);
      }
    } catch (err) {
      alert("Upload error: " + err.message);
    }
    setLoading(false);
    setLoadingMsg("");
  };

  const runPipeline = async (e) => {
    e.preventDefault();
    if (enforceReshapingRule(e)) return;
    
    const currentMapGeojson = mapGeojson;
    if ((data.roi_type === 'draw' || data.roi_type === 'upload') && !currentMapGeojson) {
       return alert("Please provide an ROI (draw on map or upload a shapefile).");
    }
    
    setLoading(true);
    setTileLayers({});
    setVisibleLayers({});
    setImageBounds(null);
    setMapGeojson(null);
    
    try {
      // STEP 1
      setLoadingMsg("Extracting Main River Polygon...");
      const step1Payload = {
        ...data,
        feature_index: Number(data.feature_index)
      };
      
      if (data.roi_type === 'draw' || data.roi_type === 'upload') {
         step1Payload.roi_geojson = currentMapGeojson;
      }

      const res1 = await axios.post(`${API_BASE}/step1`, step1Payload);
      if (!res1.data.success) throw new Error("Step 1 failed: " + res1.data.error);
      const geojson1 = res1.data.geojson;

      // Capture ALL HydroSHEDS flow direction data returned by Step 1
      const upstreamCoords   = res1.data.upstream_coords   || null;
      const downstreamCoords = res1.data.downstream_coords || null;
      const flowPath         = res1.data.flow_path         || null;   // dense ordered path (Layer 1)
      const flowDirCode      = res1.data.flow_dir_code     ?? null;   // D8 direction code (Layer 3)
      if (flowPath) {
        console.log(`Flow path sampled: ${flowPath.length} ordered points.`);
      } else if (upstreamCoords && downstreamCoords) {
        console.log("Flow direction (sparse HydroSHEDS):", upstreamCoords, "→", downstreamCoords);
      } else {
        console.warn("HydroSHEDS flow direction unavailable; Step 3 will use PCA geometric fallback.");
      }


      const tiles1 = {
        "FCC Raster": res1.data.fcc_url,
        "MNDWI Raster": res1.data.mndwi_url,
        "Cleaned Water": res1.data.cleaned_water_url
      };
      setTileLayers(tiles1);
      setImageBounds(res1.data.image_bounds);
      setVisibleLayers({
        "FCC Raster": true,
        "MNDWI Raster": false,
        "Cleaned Water": false
      });
      setMapGeojson(Object.assign(flattenMultiLineStrings(geojson1), { _shouldZoom: true })); // Show intermediate

      // STEP 3
      setLoadingMsg("Separating Left and Right Banks...");
      const step3Payload = {
        geojson: geojson1,
        year: String(data.start_date.substring(0, 4)),
        upstream_coords:   upstreamCoords,    // [lng, lat] HydroSHEDS upstream (Layer 2)
        downstream_coords: downstreamCoords,  // [lng, lat] HydroSHEDS downstream (Layer 2)
        flow_path:         flowPath,          // [[lng, lat], ...] ordered path (Layer 1)
        flow_dir_code:     flowDirCode        // D8 integer 1-128 (Layer 3)
      };

      const res3 = await axios.post(`${API_BASE}/step3`, step3Payload);
      if (!res3.data.success) throw new Error("Step 3 failed: " + res3.data.error);
      const geojson3 = res3.data.geojson;

      // Final Output (Left/Right Banks)
      setMapGeojson(Object.assign(flattenMultiLineStrings(geojson3), { _shouldZoom: true }));
      alert("Banklines Extracted Successfully!");
    } catch (err) {
      const errorMsg = err.response?.data ? JSON.stringify(err.response.data) : err.message;
      alert("Pipeline Error: " + errorMsg);
    }
    
    setLoading(false);
    setLoadingMsg("");
  };

  const handleBulkErase = () => {
    if (enforceReshapingRule()) return;
    if (!mapGeojson || !mapGeojson.features) return;
    
    const features = mapGeojson.features;
    const eraserPolygons = [];
    const banklines = [];
    
    features.forEach(f => {
      // Treat any Polygon without a 'bank' property as an eraser
      if ((!f.properties || !f.properties.bank) && f.geometry.type === 'Polygon') {
        eraserPolygons.push(f.geometry.coordinates);
      } else {
        banklines.push(f);
      }
    });
    
    if (eraserPolygons.length === 0) {
      return alert("Please draw a rectangle or polygon over the points you want to delete first!");
    }
    
    let erasedCount = 0;
    
    const isInsideAnyEraser = (pt) => {
      for (let poly of eraserPolygons) {
        const ring = poly[0];
        let x = pt[0], y = pt[1];
        let inside = false;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          let xi = ring[i][0], yi = ring[i][1];
          let xj = ring[j][0], yj = ring[j][1];
          let intersect = ((yi > y) != (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
          if (intersect) inside = !inside;
        }
        if (inside) return true;
      }
      return false;
    };
    
    banklines.forEach(f => {
      if (f.geometry.type === 'LineString') {
        const originalLen = f.geometry.coordinates.length;
        f.geometry.coordinates = f.geometry.coordinates.filter(pt => !isInsideAnyEraser(pt));
        erasedCount += (originalLen - f.geometry.coordinates.length);
      } else if (f.geometry.type === 'MultiLineString') {
        f.geometry.coordinates.forEach((line, idx) => {
          const originalLen = line.length;
          f.geometry.coordinates[idx] = line.filter(pt => !isInsideAnyEraser(pt));
          erasedCount += (originalLen - f.geometry.coordinates[idx].length);
        });
        f.geometry.coordinates = f.geometry.coordinates.filter(line => line.length > 1);
      }
    });
    
    const validBanklines = banklines.filter(f => {
        if (f.geometry.type === 'LineString') return f.geometry.coordinates.length > 1;
        if (f.geometry.type === 'MultiLineString') return f.geometry.coordinates.length > 0;
        return true;
    });
    
    setMapGeojson(flattenMultiLineStrings({
      type: "FeatureCollection",
      features: validBanklines
    }));
    
    alert(`Erased ${erasedCount} points successfully!`);
  };

  const handleMergeExtensions = () => {
    if (enforceReshapingRule()) return;
    if (!mapGeojson || !mapGeojson.features) return;
    const features = [...mapGeojson.features];
    
    const extensions = features.filter(f => f.geometry.type === 'LineString' && (!f.properties || !f.properties.bank));
    const banklines = features.filter(f => (f.geometry.type === 'LineString' || f.geometry.type === 'MultiLineString') && f.properties && f.properties.bank);
    
    if (extensions.length === 0) {
        return alert("Please draw a new line (extension) using the polyline tool first!");
    }
    
    const distance = (p1, p2) => Math.sqrt(Math.pow(p1[0] - p2[0], 2) + Math.pow(p1[1] - p2[1], 2));
    let mergedCount = 0;
    
    extensions.forEach(ext => {
        const extCoords = ext.geometry.coordinates;
        if (extCoords.length < 2) return;
        const extStart = extCoords[0];
        const extEnd = extCoords[extCoords.length - 1];
        
        let bestMatch = null;
        let minDistance = Infinity;
        
        banklines.forEach(bank => {
            const lines = bank.geometry.type === 'LineString' ? [bank.geometry.coordinates] : bank.geometry.coordinates;
            
            lines.forEach(bCoords => {
                if (bCoords.length < 2) return;
                const bStart = bCoords[0];
                const bEnd = bCoords[bCoords.length - 1];
                
                // Compare endpoints
                const d1 = distance(extStart, bEnd);
                if (d1 < minDistance) { minDistance = d1; bestMatch = { bCoords, mode: 'append', extOrder: 'normal' }; }
                
                const d2 = distance(extEnd, bEnd);
                if (d2 < minDistance) { minDistance = d2; bestMatch = { bCoords, mode: 'append', extOrder: 'reverse' }; }
                
                const d3 = distance(extStart, bStart);
                if (d3 < minDistance) { minDistance = d3; bestMatch = { bCoords, mode: 'prepend', extOrder: 'reverse' }; }
                
                const d4 = distance(extEnd, bStart);
                if (d4 < minDistance) { minDistance = d4; bestMatch = { bCoords, mode: 'prepend', extOrder: 'normal' }; }
            });
        });
        
        // 0.05 degrees is approx 5km, generous snapping tolerance
        if (bestMatch && minDistance < 0.05) {
            const coordsToAdd = bestMatch.extOrder === 'reverse' ? [...extCoords].reverse() : extCoords;
            if (bestMatch.mode === 'append') {
                coordsToAdd.shift(); // remove overlapping first point
                bestMatch.bCoords.push(...coordsToAdd);
            } else {
                coordsToAdd.pop(); // remove overlapping last point
                bestMatch.bCoords.unshift(...coordsToAdd);
            }
            mergedCount++;
            const idx = features.indexOf(ext);
            if (idx > -1) features.splice(idx, 1);
        }
    });
    
    if (mergedCount > 0) {
        setMapGeojson(flattenMultiLineStrings({
            type: "FeatureCollection",
            features: features
        }));
        alert(`Successfully merged ${mergedCount} drawn extensions into the banklines!`);
    } else {
        alert("Could not find any bankline endpoints close enough to your drawn lines. Try drawing them closer to the ends of the banklines.");
    }
  };

  const handleDownloadMapData = async () => {
    if (enforceReshapingRule()) return;
    if (!mapGeojson || !mapGeojson.features || mapGeojson.features.length === 0) return alert("Nothing on the map to download.");
    
    let year = data.start_date ? data.start_date.substring(0, 4) : "";
    if (!year) {
        year = prompt("Please enter the year for the bankline shapefiles (e.g., 2024):", new Date().getFullYear());
        if (!year) return; // User cancelled
    }

    setLoading(true);
    setLoadingMsg("Generating Shapefile...");
    try {
        const response = await axios.post(`${API_BASE}/export_shapefile`, { 
            geojson: mapGeojson,
            year: year
        }, { responseType: 'blob' });
        
        // Create a blob from the response
        const blob = new Blob([response.data], { type: 'application/zip' });
        const url = window.URL.createObjectURL(blob);
        
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${year}_banklines.zip`);
        document.body.appendChild(link);
        link.click();
        
        // Clean up
        link.parentNode.removeChild(link);
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert("Failed to export shapefile: " + err.message);
    }
    setLoading(false);
  };

  return (
    <div className="App split-pane">
      {/* LEFT PANE: Controls */}
      <div className="sidebar" style={{width: '350px', flexShrink: 0, overflowY: 'auto'}}>
        <h2 style={{
          fontSize: '26px',
          fontWeight: '800',
          background: 'linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          margin: '0 0 10px 0',
          letterSpacing: '0.5px',
          lineHeight: '1.2'
        }}>
          Automated Bankline Extraction
        </h2>

        {!isAuthenticated && (
          <div className="step-card" style={{marginTop: '10px', marginBottom: '20px', border: '2px solid #e74c3c', backgroundColor: '#fdf3f2'}}>
            <h3 style={{color: '#c0392b', marginTop: 0}}>Google Earth Engine Auth Required</h3>
            <p style={{fontSize: '13px'}}>Upload your Service Account JSON key to authenticate.</p>
            <div className="form-group">
              <label>Service Account Key (.json):</label>
              <input type="file" accept=".json" onChange={handleAuthUpload} />
            </div>
          </div>
        )}
        
        <div style={{marginTop: '0px'}}>
          <div className="step-card" style={{ opacity: isAuthenticated ? 1 : 0.5, pointerEvents: isAuthenticated ? 'auto' : 'none' }}>
            <p>Automatically extracts the main river and splits it into left and right banks.</p>
            <form onSubmit={runPipeline}>
              <div className="form-group">
                <label>ROI Selection Method:</label>
                <select value={data.roi_type} onChange={e => setData({...data, roi_type: e.target.value})}>
                  <option value="asset">GEE Asset ID</option>
                  <option value="draw">Draw on Map</option>
                  <option value="upload">Upload Shapefile (.zip)</option>
                </select>
              </div>
              
              {data.roi_type === 'asset' && (
                <div className="form-group"><label>Asset ID:</label><input type="text" placeholder="e.g., projects/ee-user/assets/my_shape" value={data.asset_id} onChange={e => setData({...data, asset_id: e.target.value})} /></div>
              )}

              {data.roi_type === 'asset' && (
                <div className="form-group"><label>Feature Index:</label><input type="number" value={data.feature_index} onChange={e => setData({...data, feature_index: e.target.value})} /></div>
              )}

              {data.roi_type === 'upload' && (
                <div className="form-group"><label>Upload Shapefile (ZIP):</label><input type="file" accept=".zip" onChange={handleFileUpload} /></div>
              )}
              
              {data.roi_type === 'draw' && (
                <div className="form-group"><p style={{fontSize: '13px', color: '#666', marginBottom: '0'}}>Use the toolbar on the map to draw your ROI.</p></div>
              )}
              <div className="form-group"><label>Start Date:</label><input type="date" value={data.start_date} onChange={e => setData({...data, start_date: e.target.value})} /></div>
              <div className="form-group"><label>End Date:</label><input type="date" value={data.end_date} onChange={e => setData({...data, end_date: e.target.value})} /></div>
              <div className="form-group">
                <label>Composite Method:</label>
                <select value={data.composite_method} onChange={e => setData({...data, composite_method: e.target.value})}>
                  <option value="median">Median</option>
                  <option value="mean">Mean</option>
                  <option value="max">Max</option>
                  <option value="min">Min</option>
                  <option value="mode">Mode</option>
                </select>
              </div>
              <button type="submit" disabled={loading || !isAuthenticated} className="action-btn action-btn-green" style={{marginTop: "10px", fontSize: "1.05rem", padding: "12px"}}>{loading ? 'Extracting Banklines...' : 'Extract Banklines'}</button>
            </form>
          </div>
          
          {Object.keys(tileLayers).length > 0 && (
            <div className="step-card" style={{marginTop: '20px', backgroundColor: '#eaf4f4', border: '1px solid #b2d8d8'}}>
              <h2 style={{color: '#006666'}}>Extraction Layers</h2>
              <p style={{fontSize: '12px', color: '#555', marginTop: '0', marginBottom: '10px'}}>These layers were cached during extraction for instant switching.</p>
              {Object.entries(tileLayers).map(([name, url]) => (
                <div key={name} style={{ display: 'flex', alignItems: 'center', marginBottom: '8px' }}>
                  <input 
                    type="checkbox" 
                    id={name}
                    checked={!!visibleLayers[name]} 
                    onChange={(e) => setVisibleLayers({...visibleLayers, [name]: e.target.checked})} 
                    style={{cursor: 'pointer', transform: 'scale(1.2)', marginRight: '8px'}}
                  />
                  <label htmlFor={name} style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '14px', color: '#333' }}>{name}</label>
                </div>
              ))}
            </div>
          )}

          <div className="step-card" style={{marginTop: '20px'}}>
            <h2>Map Actions</h2>
            <button onClick={(e) => { if (enforceReshapingRule(e)) return; setMapGeojson(null); setTileLayers({}); setVisibleLayers({}); setImageBounds(null); }} className="action-btn action-btn-red">
              Clear Map
              <span onClick={(e) => { e.stopPropagation(); setActiveTooltip(activeTooltip === "clear" ? null : "clear"); }} className="info-icon">&#9432;</span>
              {activeTooltip === 'clear' && (
                <div onClick={(e) => e.stopPropagation()} className="tooltip-popup">
                  Removes all currently drawn shapes, banklines, and generated data from the map view.
                  
                </div>
              )}
            </button>
            <button onClick={() => setIsReshaping(!isReshaping)} disabled={!mapGeojson} className={`action-btn ${isReshaping ? "action-btn-green" : "action-btn-blue"}`} style={{border: isReshaping ? "2px solid #27ae60" : "none"}}>
              {isReshaping ? 'Finish Reshaping' : 'Reshape Banklines'}
              <span onClick={(e) => { e.stopPropagation(); setActiveTooltip(activeTooltip === "reshape" ? null : "reshape"); }} className="info-icon">&#9432;</span>
              {activeTooltip === 'reshape' && (
                <div onClick={(e) => e.stopPropagation()} className="tooltip-popup">
                  Click this to activate the reshape tool. Then, sketch a line across the segment of the bankline you want to replace. The tool snaps to the line for accuracy! Click 'Finish Reshaping' when done.
                  
                </div>
              )}
            </button>

            <button onClick={handleBulkErase} disabled={!mapGeojson} className="action-btn action-btn-orange">
              Erase Points Inside Shape
              <span onClick={(e) => { e.stopPropagation(); setActiveTooltip(activeTooltip === "erase" ? null : "erase"); }} className="info-icon">&#9432;</span>
              {activeTooltip === 'erase' && (
                <div onClick={(e) => e.stopPropagation()} className="tooltip-popup">
                  Use the map toolbar to draw a rectangle or polygon over unwanted points. Then click this to bulk-delete them.
                  
                </div>
              )}
            </button>

            <button onClick={handleMergeExtensions} disabled={!mapGeojson} className="action-btn action-btn-purple">
              Merge Line Extensions
              <span onClick={(e) => { e.stopPropagation(); setActiveTooltip(activeTooltip === "merge" ? null : "merge"); }} className="info-icon">&#9432;</span>
              {activeTooltip === 'merge' && (
                <div onClick={(e) => e.stopPropagation()} className="tooltip-popup">
                  Draw a new line using the polyline tool starting near the end of an existing bankline. Click this button to seamlessly merge them into one line!
                  
                </div>
              )}
            </button>

            <button onClick={handleDownloadMapData} disabled={!mapGeojson} className="action-btn action-btn-darkblue">
              Download Banklines
              <span onClick={(e) => { e.stopPropagation(); setActiveTooltip(activeTooltip === "download" ? null : "download"); }} className="info-icon">&#9432;</span>
              {activeTooltip === 'download' && (
                <div onClick={(e) => e.stopPropagation()} className="tooltip-popup">
                  Downloads all the generated banklines currently shown on the map as a ZIP file containing an ESRI Shapefile.
                  
                </div>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* RIGHT PANE: Persistent Map */}
      <div className="map-pane" style={{flex: 1, position: 'relative'}}>
                <MapContainer center={[28.14, 81.12]} zoom={11} style={{ height: "100%", width: "100%" }} attributionControl={false}>
          <ScaleControl position="bottomright" imperial={false} />
          <LayersControl position="topright">
            <LayersControl.BaseLayer checked name="Google Satellite">
              <TileLayer
                url="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
                attribution="&copy; Google"
              />
            </LayersControl.BaseLayer>

            <LayersControl.BaseLayer name="Google Hybrid">
              <TileLayer
                url="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}"
                attribution="&copy; Google"
              />
            </LayersControl.BaseLayer>

            <LayersControl.BaseLayer name="OpenStreetMap">
              <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="&copy; OpenStreetMap contributors"
              />
            </LayersControl.BaseLayer>

            <LayersControl.BaseLayer name="None">
              <LayerGroup />
            </LayersControl.BaseLayer>


          </LayersControl>

          {/* Mount extraction layers outside LayersControl so they can be cached by the browser instantly during extraction! */}
          {tileLayers && Object.entries(tileLayers).map(([name, url]) => (
            imageBounds ? (
              <ImageOverlay key={name} url={url} bounds={imageBounds} opacity={visibleLayers[name] ? 0.7 : 0} />
            ) : (
              <TileLayer key={name} url={url} opacity={visibleLayers[name] ? 0.7 : 0} />
            )
          ))}

          <MapEditor roiCoords={roiCoords} setRoiCoords={setRoiCoords} geojson={mapGeojson} setGeojson={setMapGeojson} isReshaping={isReshaping} setIsReshaping={setIsReshaping} />
        </MapContainer>
        {loading && (
          <div className="map-overlay-loading">
            <h2>{loadingMsg || "Processing on Backend..."}</h2>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
