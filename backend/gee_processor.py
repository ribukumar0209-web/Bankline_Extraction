import ee
import traceback

is_initialized = False

def initialize_ee(credentials_path=None):
    global is_initialized
    try:
        if credentials_path:
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(credentials_path)
            scoped_credentials = credentials.with_scopes(['https://www.googleapis.com/auth/earthengine'])
            ee.Initialize(credentials=scoped_credentials)
        else:
            # Initialize with the project selected during authentication
            ee.Initialize()
            
        is_initialized = True
        print("Earth Engine initialized successfully.")
        return True
    except Exception as e:
        print("\n" + "="*50)
        print("Earth Engine initialization failed!")
        print("If you are running this locally, run 'earthengine authenticate --auth_mode=notebook' in terminal.")
        print("Otherwise, please upload a Service Account JSON key via the platform.")
        print("Error:", e)
        print("="*50 + "\n")
        is_initialized = False
        return False

import requests

def get_download_url_for_feature_collection(fc, filename):
    print(f"Requesting download URL for {filename}...")
    url = fc.getDownloadURL(
        filetype="GEOJSON",
        filename=filename
    )
    print(f"Got URL: {url[:50]}... Fetching data...")
    # Fetch the GeoJSON data directly to avoid CORS issues on the frontend
    response = requests.get(url)
    print("Data fetched successfully.")
    return response.json()

def extract_river_polygon(feature_index, start_date, end_date, asset_id, roi_type='asset', roi_geojson=None, composite_method='median'):
    try:
        print(f"Starting extract_river_polygon. Type: {roi_type}, Method: {composite_method}")
        if roi_type == 'asset':
            if not asset_id or not str(asset_id).strip():
                return {'success': False, 'error': 'Asset ID cannot be empty. Please provide one.'}
            roi_collection = ee.FeatureCollection(asset_id)
            selected_feature = ee.Feature(roi_collection.toList(roi_collection.size()).get(feature_index))
            selected_geometry = selected_feature.geometry()
        elif roi_type in ['draw', 'upload']:
            if not roi_geojson:
                raise ValueError("GeoJSON data is required for 'draw' or 'upload' ROI type.")
            if roi_geojson.get("type") == "FeatureCollection":
                features = roi_geojson.get("features", [])
                if not features:
                    raise ValueError("No features found in GeoJSON")
                selected_geometry = ee.FeatureCollection(features).geometry()
            elif roi_geojson.get("type") == "Feature":
                selected_geometry = ee.Geometry(roi_geojson.get("geometry"))
            else:
                selected_geometry = ee.Geometry(roi_geojson)
        print("Geometry constructed.")


        def apply_scale_factors(image):
            optical_bands = image.select('SR_B.*').multiply(0.0000275).add(-0.2)
            return image.addBands(optical_bands, None, True)

        bn5 = ['SR_B1', 'SR_B1', 'SR_B2', 'SR_B3', 'SR_B5', 'QA_PIXEL', 'SR_B4', 'SR_B7']
        bn7 = ['SR_B1', 'SR_B1', 'SR_B2', 'SR_B3', 'SR_B5', 'QA_PIXEL', 'SR_B4', 'SR_B7']
        bn8 = ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B6', 'QA_PIXEL', 'SR_B5', 'SR_B7']
        bn9 = ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B6', 'QA_PIXEL', 'SR_B5', 'SR_B7']
        bns = ['uBlue', 'Blue', 'Green', 'Red', 'Swir1', 'QA_PIXEL', 'Nir', 'Swir2']

        def get_filtered_collection(col_id, b_in, start, end, geom):
            return ee.ImageCollection(col_id) \
                .filterBounds(geom) \
                .filterDate(start, end) \
                .map(apply_scale_factors) \
                .select(b_in, bns)

        ls5 = get_filtered_collection("LANDSAT/LT05/C02/T1_L2", bn5, start_date, end_date, selected_geometry)
        ls7 = ee.ImageCollection("LANDSAT/LE07/C02/T1_L2") \
                .filterDate('1999-04-15', '2003-05-30') \
                .filterBounds(selected_geometry) \
                .filterDate(start_date, end_date) \
                .map(apply_scale_factors) \
                .select(bn7, bns)
                
        ls8 = get_filtered_collection("LANDSAT/LC08/C02/T1_L2", bn8, start_date, end_date, selected_geometry)
        ls9 = get_filtered_collection("LANDSAT/LC09/C02/T1_L2", bn9, start_date, end_date, selected_geometry)

        lcol = ls5.merge(ls7).merge(ls8).merge(ls9)
        print("Image collections merged.")

        def mask_clouds(image):
            qa = image.select('QA_PIXEL')
            cloud_shadow_bit_mask = 1 << 3
            clouds_bit_mask = 1 << 5
            mask = qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0).And(qa.bitwiseAnd(clouds_bit_mask).eq(0))
            return image.updateMask(mask)

        masked_lcol = lcol.map(mask_clouds)
        
        # Apply the chosen composite method
        method_map = {
            'median': masked_lcol.median,
            'mean': masked_lcol.mean,
            'max': masked_lcol.max,
            'min': masked_lcol.min,
            'mode': masked_lcol.mode
        }
        reducer_func = method_map.get(composite_method.lower(), masked_lcol.median)
        median_composite = reducer_func()
        
        print(f"Composite created using method: {composite_method}")

        mndwi = median_composite.normalizedDifference(['Green', 'Swir1']).rename('mndwi')
        
        # 1. Create a binary mask (1 for water, 0 for land)
        # Unmask with 1 (water) outside the ROI so the river doesn't erode at the edges
        water_bin = mndwi.gt(0).unmask(1)
        
        # We bypass morphological erosion/dilation here because on massive ROIs, they cause the river 
        # to mathematically disappear if Earth Engine dynamically changes the scale.
        # Python (Step 3) handles morphological closing natively, so we don't need it here!
        
        # 4. Clip back to ROI and convert to a masked image (only water pixels remain)
        water_mask = water_bin.clip(selected_geometry).selfMask()
        
        # 5. Remove small isolated water bodies
        # Crucial Memory Fix: We only need to know if it's >= 100, so counting to 120 is enough. 
        # Counting to 1024 uses exponentially more memory on large ROIs.
        connections = water_mask.connectedPixelCount(120)
        cleaned_water = water_mask.updateMask(connections.gte(100))

        print("Triggering chunked vectorization (this might take a few minutes)...")
        # To prevent Earth Engine from timing out or downscaling resolution on massive areas,
        # we split the ROI into 40km x 40km patches, process each at exactly 30m resolution, and merge them in Python!
        grid = selected_geometry.coveringGrid(ee.Projection('EPSG:3857'), 40000)
        num_patches = grid.size().getInfo()
        print(f"Area divided into {num_patches} patches. Processing chunks...")
        
        all_features = []
        grid_list = grid.toList(num_patches)
        
        for i in range(num_patches):
            print(f"Processing patch {i+1} of {num_patches}...")
            patch_geom = ee.Feature(grid_list.get(i)).geometry()
            
            patch_vector = cleaned_water.reduceToVectors(
                geometry=patch_geom,
                geometryType='polygon',
                scale=30,
                maxPixels=1e10,
                bestEffort=False, # STRICTLY enforce 30m resolution!
                eightConnected=False,
                labelProperty='water',
                reducer=ee.Reducer.countEvery(),
                tileScale=16
            )
            
            patch_vector_simplified = patch_vector.map(lambda f: f.simplify(maxError=30))
            
            try:
                # We bypass get_download_url_for_feature_collection's prints to keep logs clean
                url = patch_vector_simplified.getDownloadURL(filetype="GEOJSON", filename=f"patch_{i}")
                response = requests.get(url)
                patch_geojson = response.json()
                if patch_geojson and "features" in patch_geojson:
                    all_features.extend(patch_geojson["features"])
            except Exception as e:
                print(f"Warning: Patch {i+1} failed or had no data: {e}")
                
        print(f"Successfully processed {len(all_features)} total water features across all patches.")
        
        geojson_data = {
            "type": "FeatureCollection",
            "features": all_features
        }

        # --- Global Flow Direction & Centerline Sampling ---
        # Query global hydrography: MERIT Hydro (90m global upa/elv/dir) and NASADEM (30m elevation).
        # Water accumulates downstream (higher upa) and drops elevation (lower elv).
        upstream_coords = None
        downstream_coords = None
        flow_path = None
        flow_dir_code = None
        try:
            print("Sampling global hydrography (MERIT Hydro & NASADEM) for robust flow direction...")
            # Try MERIT Hydro 90m first
            try:
                merit = ee.Image("MERIT/Hydro/v1_0_1").select(['upa', 'elv', 'dir'])
                water_flow = merit.updateMask(cleaned_water)
                samples = water_flow.sample(
                    region=selected_geometry,
                    scale=90,
                    numPixels=200,
                    seed=42,
                    geometries=True
                ).filter(ee.Filter.gt('upa', 0.05)).sort('upa')
                
                n_samples = samples.size().getInfo()
                if n_samples >= 2:
                    up_feat = samples.first()
                    dn_feat = samples.sort('upa', False).first()
                    upstream_coords = up_feat.geometry().coordinates().getInfo()
                    downstream_coords = dn_feat.geometry().coordinates().getInfo()
                    
                    s_list = samples.toList(n_samples)
                    n_pick = min(30, n_samples)
                    step = max(1, n_samples // n_pick)
                    flow_path = [
                        ee.Feature(s_list.get(idx)).geometry().coordinates().getInfo()
                        for idx in range(0, n_samples, step)[:n_pick]
                    ]
                    print(f"MERIT Hydro sampled successfully: {len(flow_path)} points. "
                          f"Upstream: {upstream_coords}, Downstream: {downstream_coords}")

                    # Mode direction
                    dir_mode = water_flow.select('dir').reduceRegion(
                        reducer=ee.Reducer.mode(),
                        geometry=selected_geometry,
                        scale=90,
                        maxPixels=1e8
                    ).get('dir').getInfo()
                    if dir_mode is not None:
                        flow_dir_code = int(dir_mode)
            except Exception as merit_err:
                print(f"MERIT Hydro sampling note: {merit_err}. Trying NASADEM & HydroSHEDS 15ACC...")

            # Fallback to NASADEM + HydroSHEDS 15ACC if MERIT was unavailable
            if upstream_coords is None:
                dem = ee.Image("NASA/NASADEM_HGT/001").select('elevation')
                hs15 = ee.Image("WWF/HydroSHEDS/15ACC").select('b1')
                water_dem = dem.addBands(hs15).updateMask(cleaned_water)
                
                dem_samples = water_dem.sample(
                    region=selected_geometry,
                    scale=90,
                    numPixels=150,
                    seed=42,
                    geometries=True
                ).sort('b1')
                
                n_dem = dem_samples.size().getInfo()
                if n_dem >= 2:
                    up_feat = dem_samples.first()
                    dn_feat = dem_samples.sort('b1', False).first()
                    upstream_coords = up_feat.geometry().coordinates().getInfo()
                    downstream_coords = dn_feat.geometry().coordinates().getInfo()
                    s_list = dem_samples.toList(n_dem)
                    n_pick = min(30, n_dem)
                    step = max(1, n_dem // n_pick)
                    flow_path = [
                        ee.Feature(s_list.get(idx)).geometry().coordinates().getInfo()
                        for idx in range(0, n_dem, step)[:n_pick]
                    ]
                    print(f"NASADEM + HydroSHEDS 15ACC sampled successfully: {len(flow_path)} points.")

        except Exception as flow_err:
            print(f"Warning: Global hydrography sampling encountered issue: {flow_err}. "
                  "Step 3 will use direct endpoint elevation verification.")



        # Export step
        print("Getting Map IDs for visualization...")
        filename = f"step1_river_polygon_{feature_index}_{start_date}"
        
        clipped_composite = median_composite.clip(selected_geometry)
        clipped_mndwi = mndwi.clip(selected_geometry)
        
        # Calculate bounds for Leaflet ImageOverlay: [[min_lat, min_lon], [max_lat, max_lon]]
        bounds_coords = selected_geometry.bounds().coordinates().getInfo()[0]
        min_lon = bounds_coords[0][0]
        min_lat = bounds_coords[0][1]
        max_lon = bounds_coords[2][0]
        max_lat = bounds_coords[2][1]
        image_bounds = [[min_lat, min_lon], [max_lat, max_lon]]

        vis_fcc = {'bands': ['Nir', 'Red', 'Green'], 'min': 0, 'max': 0.3}
        fcc_url = clipped_composite.getThumbURL({
            'dimensions': 2048,
            'region': selected_geometry,
            'format': 'png',
            **vis_fcc
        })
        
        vis_mndwi = {'min': -1, 'max': 1, 'palette': ['00FFFF', '0000FF']}
        mndwi_url = clipped_mndwi.getThumbURL({
            'dimensions': 2048,
            'region': selected_geometry,
            'format': 'png',
            **vis_mndwi
        })
        
        # Use the precisely clipped water_mask for accurate visualization
        cleaned_water_url = water_mask.getThumbURL({
            'dimensions': 2048,
            'region': selected_geometry,
            'format': 'png',
            'palette': ['blue']
        })

        filename = f"River_MNDWI_Connected_Subregion_{feature_index}_UTM44N"
        print("Step 1 execution completed successfully.")
        
        return {
            "success": True, 
            "geojson": geojson_data, 
            "fcc_url": fcc_url,
            "mndwi_url": mndwi_url,
            "cleaned_water_url": cleaned_water_url,
            "image_bounds": image_bounds,
            "filename": f"{filename}.geojson",
            "upstream_coords": upstream_coords,      # [lng, lat] — lowest flow accumulation (Layer 2)
            "downstream_coords": downstream_coords,  # [lng, lat] — highest flow accumulation (Layer 2)
            "flow_path": flow_path,                  # [[lng, lat], ...] ordered upstream→downstream (Layer 1)
            "flow_dir_code": flow_dir_code           # int D8 code 1–128 (Layer 3)
        }

    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

def extract_minor_tributaries(desired_ofid, start_date, end_date, asset_id):
    try:
        subregions = ee.FeatureCollection(asset_id)
        selected_feature = subregions.filter(ee.Filter.eq('ORIG_FID', desired_ofid)).first()
        selected_geometry = selected_feature.geometry()

        s1_collection = ee.ImageCollection("COPERNICUS/S1_GRD") \
            .filterDate(start_date, end_date) \
            .filterBounds(selected_geometry) \
            .filter(ee.Filter.eq('instrumentMode', 'IW')) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
            .filter(ee.Filter.eq('orbitProperties_pass', 'DESCENDING')) \
            .select('VV')

        sar_median = s1_collection.median().clip(selected_geometry)
        threshold = -17
        water_mask = sar_median.lt(threshold).selfMask()

        water_vector = water_mask.reduceToVectors(
            geometry=selected_geometry,
            scale=10,
            geometryType='polygon',
            eightConnected=False,
            labelProperty='water',
            maxPixels=1e8,
            tileScale=16
        )

        # Generate Visualization Tile URLs
        vis_sar = {'min': -25, 'max': 0}
        sar_url = sar_median.getMapId(vis_sar)['tile_fetcher'].url_format
        
        vis_water = {'palette': ['blue']}
        water_url = water_mask.getMapId(vis_water)['tile_fetcher'].url_format

        filename = f"VECTOR_MINORTRIBUTARY_{desired_ofid}_{start_date}_{end_date}"
        geojson_data = get_download_url_for_feature_collection(water_vector, filename)

        return {
            "success": True, 
            "geojson": geojson_data,
            "sar_url": sar_url,
            "water_url": water_url,
            "filename": f"{filename}.geojson"
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

def extract_sidebars(feature_index, bsi_threshold, buffer_distance, start_date, end_date, asset_id):
    try:
        roi_collection = ee.FeatureCollection(asset_id)
        selected_feature = ee.Feature(roi_collection.toList(roi_collection.size()).get(feature_index))
        selected_geometry = selected_feature.geometry()

        def apply_scale_factors(image):
            optical = image.select('SR_B.*').multiply(0.0000275).add(-0.2)
            return image.addBands(optical, None, True)

        bn5 = ['SR_B1','SR_B1','SR_B2','SR_B3','SR_B5','QA_PIXEL','SR_B4','SR_B7']
        bn7 = ['SR_B1','SR_B1','SR_B2','SR_B3','SR_B5','QA_PIXEL','SR_B4','SR_B7']
        bn8 = ['SR_B1','SR_B2','SR_B3','SR_B4','SR_B6','QA_PIXEL','SR_B5','SR_B7']
        bn9 = ['SR_B1','SR_B2','SR_B3','SR_B4','SR_B6','QA_PIXEL','SR_B5','SR_B7']
        bns = ['uBlue','Blue','Green','Red','Swir1','QA_PIXEL','Nir','Swir2']

        def get_filtered_collection(col_id, b_in, start, end, geom):
            return ee.ImageCollection(col_id) \
                .filterBounds(geom) \
                .filterDate(start, end) \
                .map(apply_scale_factors) \
                .select(b_in, bns)

        ls5 = get_filtered_collection("LANDSAT/LT05/C02/T1_L2", bn5, start_date, end_date, selected_geometry)
        ls7 = get_filtered_collection("LANDSAT/LE07/C02/T1_L2", bn7, start_date, end_date, selected_geometry)
        ls8 = get_filtered_collection("LANDSAT/LC08/C02/T1_L2", bn8, start_date, end_date, selected_geometry)
        ls9 = get_filtered_collection("LANDSAT/LC09/C02/T1_L2", bn9, start_date, end_date, selected_geometry)

        def merge_mask(image):
            qa = image.select('QA_PIXEL')
            shadow = 1 << 3
            cloud = 1 << 5
            mask = qa.bitwiseAnd(shadow).eq(0).And(qa.bitwiseAnd(cloud).eq(0))
            return image.updateMask(mask)

        collection = ls5.merge(ls7).merge(ls8).merge(ls9).map(merge_mask)

        composite = collection.median().clip(selected_geometry)

        mndwi = composite.normalizedDifference(['Green','Swir1']).rename('MNDWI')
        water_mask = mndwi.gt(0).selfMask()
        connections = water_mask.connectedPixelCount()
        cleaned_water = water_mask.updateMask(connections.gte(30))

        water_vector = cleaned_water.reduceToVectors(
            geometry=selected_geometry,
            geometryType='polygon',
            scale=30,
            maxPixels=1e8,
            bestEffort=True,
            tileScale=16
        )

        # Instead of calculating geodesic .area(1) on thousands of polygons, sort by 'count'
        main_channel = water_vector.sort('count', False).limit(1)
        
        # Simplify geometry before buffering to prevent memory limit exceeded
        main_channel_geom = main_channel.geometry().simplify(maxError=30)
        
        bsi = composite.expression(
            '((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))',
            {
                'SWIR1': composite.select('Swir1'),
                'RED': composite.select('Red'),
                'NIR': composite.select('Nir'),
                'BLUE': composite.select('Blue')
            }
        ).rename('BSI')
        
        sandbar_mask = bsi.gt(bsi_threshold).selfMask()
        sandbar_mask = sandbar_mask.clip(selected_geometry)

        sandbar_vector = sandbar_mask.reduceToVectors(
            geometry=selected_geometry,
            geometryType='polygon',
            scale=30,
            reducer=ee.Reducer.countEvery(),
            maxPixels=1e10,
            bestEffort=False,
            tileScale=16
        )

        # Supply maxError=30 to the buffer to make it angular and fast
        buffer120 = main_channel_geom.buffer(buffer_distance, 30)
        
        # Simplify sandbars to save memory
        sandbar_vector_simplified = sandbar_vector.map(lambda f: f.simplify(maxError=30))
        filtered_sandbars = sandbar_vector_simplified.filterBounds(buffer120)
        
        # Generate Visualization Tile URLs
        vis_fcc = {'bands': ['Nir', 'Red', 'Green'], 'min': 0, 'max': 0.3}
        fcc_url = composite.getMapId(vis_fcc)['tile_fetcher'].url_format
        
        vis_bsi = {'palette': ['yellow']}
        bsi_url = sandbar_mask.getMapId(vis_bsi)['tile_fetcher'].url_format

        filename = f"All_Sandbars_Subregion_{feature_index}_{start_date}_{end_date}"
        geojson_data = get_download_url_for_feature_collection(filtered_sandbars, filename)

        return {
            "success": True, 
            "geojson": geojson_data, 
            "fcc_url": fcc_url,
            "bsi_url": bsi_url,
            "filename": f"{filename}.geojson"
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

def extract_active_channel(roi_coords, start_date, end_date, cloudy_score=50, percentile_reducer=33, ndvi_thresh=0.225, val_thresh=0.125, red_thresh=0.125, morph_radius=15, disconnected_size_meters=6000, island_size_meters=100, export_resolution_meters=30):
    try:
        # Assuming roi_coords is a list of lists of coordinates: [[[lng, lat], [lng, lat], ...]]
        roi = ee.Geometry.Polygon(roi_coords)

        def apply_scale_factors(image):
            optical_bands = image.select('SR_B.*').multiply(0.0000275).add(-0.2)
            return image.addBands(optical_bands, None, True)

        bn9 = ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B6', 'QA_PIXEL', 'SR_B5', 'SR_B7']
        bn8 = ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B6', 'QA_PIXEL', 'SR_B5', 'SR_B7']
        bn7 = ['SR_B1', 'SR_B1', 'SR_B2', 'SR_B3', 'SR_B5', 'QA_PIXEL', 'SR_B4', 'SR_B7']
        bn5 = ['SR_B1', 'SR_B1', 'SR_B2', 'SR_B3', 'SR_B5', 'QA_PIXEL', 'SR_B4', 'SR_B7']
        bns = ['uBlue', 'Blue', 'Green', 'Red', 'Swir1', 'QA_PIXEL', 'Nir', 'Swir2']

        def get_filtered_collection(col_id, b_in, start, end, geom):
            return ee.ImageCollection(col_id) \
                .filterBounds(geom) \
                .filterDate(start, end) \
                .map(apply_scale_factors) \
                .select(b_in, bns)

        ls5_scaled = get_filtered_collection("LANDSAT/LT05/C02/T1_L2", bn5, start_date, end_date, roi)
        ls7_scaled = ee.ImageCollection("LANDSAT/LE07/C02/T1_L2") \
                        .filterDate('1999-04-15', '2003-05-30') \
                        .filterBounds(roi) \
                        .filterDate(start_date, end_date) \
                        .map(apply_scale_factors) \
                        .select(bn7, bns)
        ls8_scaled = get_filtered_collection("LANDSAT/LC08/C02/T1_L2", bn8, start_date, end_date, roi)
        ls9_scaled = get_filtered_collection("LANDSAT/LC09/C02/T1_L2", bn9, start_date, end_date, roi)

        lcol = ls5_scaled.merge(ls7_scaled).merge(ls8_scaled).merge(ls9_scaled)

        def maskcloud(image):
            cloud_shadow_bit_mask = 1 << 3
            clouds_bit_mask = 1 << 5
            qa = image.select('QA_PIXEL')
            mask = qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0).And(qa.bitwiseAnd(clouds_bit_mask).eq(0))
            return image.updateMask(mask).clip(roi).resample('bicubic').set('system:time_start', image.get('system:time_start'))

        img_col = lcol.filter(ee.Filter.lt('CLOUD_COVER', cloudy_score)).map(maskcloud)

        def Mndwi(image):
            return image.normalizedDifference(['Green', 'Swir1']).rename(['mndwi'])

        def Ndvi(image):
            return image.normalizedDifference(['Nir', 'Red']).rename(['ndvi'])

        def Evi(image):
            evi = image.expression('2.5 * (Nir - Red) / (1 + Nir + 6 * Red - 7.5 * Blue)', {
                'Nir': image.select(['Nir']),
                'Red': image.select(['Red']),
                'Blue': image.select(['Blue'])
            })
            return evi.rename(['evi'])

        def removeDisconnected(activeImage):
            activeImage = activeImage.int()
            connectedPixels1 = ee.Number(disconnected_size_meters).divide(export_resolution_meters).int()
            connectedPixels2 = ee.Number(island_size_meters).divide(export_resolution_meters).int()

            landFilled = activeImage.addBands(activeImage).reduceConnectedComponents(ee.Reducer.median(), 'mndwi', connectedPixels1).unmask(99).eq(99).And(activeImage.neq(0))
            activeFilled = landFilled.addBands(landFilled).reduceConnectedComponents(ee.Reducer.median(), 'mndwi_1', connectedPixels2).unmask(99).eq(99).And(landFilled.neq(1))
            return activeFilled

        percentileComposite = img_col.select(bns).reduce(ee.Reducer.percentile([percentile_reducer])).regexpRename('_p' + str(percentile_reducer), '')
        percentileComposite_true = percentileComposite.select(['Red','Green','Blue'])
        percentileComposite_red = percentileComposite.select(['Red'])

        hsv_rgb = percentileComposite.select(['Red','Green','Blue']).rgbToHsv()
        mndwi_pC = Mndwi(percentileComposite)
        ndvi_pC = Ndvi(percentileComposite)
        evi_pC = Evi(percentileComposite)

        water = (mndwi_pC.gt(ndvi_pC).Or(mndwi_pC.gt(evi_pC))).And(evi_pC.lt(0.1))
        non_veg = ndvi_pC.lte(ndvi_thresh)
        bright = hsv_rgb.select('value').gte(val_thresh).And(percentileComposite_red.gte(red_thresh))
        active_channel = water.Or(non_veg).Or(bright).rename(['mndwi'])

        active_channel_original = active_channel.select(['mndwi'])

        active_channel_opened = active_channel_original.focalMax(
            radius=morph_radius,
            units='meters',
            kernelType='circle'
        ).focalMin(
            radius=morph_radius,
            units='meters',
            kernelType='circle'
        )

        active_channel_output = removeDisconnected(active_channel_opened).Not()

        analysis_year = start_date[:4]
        # Since it's an image, we export it directly using getDownloadURL
        url = active_channel_output.getDownloadURL({
            'name': f'active_channel_output_{analysis_year}',
            'bands': ['mndwi'],
            'region': roi,
            'scale': export_resolution_meters,
            'format': 'GEO_TIFF'
        })
        
        # Generate Visualization Tile URLs
        vis_true = {'min': 0, 'max': 0.3}
        true_color_url = percentileComposite_true.getMapId(vis_true)['tile_fetcher'].url_format
        
        vis_active = {'palette': ['blue']}
        active_url = active_channel_output.getMapId(vis_active)['tile_fetcher'].url_format
        
        return {
            "success": True, 
            "download_url": url, 
            "true_color_url": true_color_url,
            "active_url": active_url,
            "filename": f"active_channel_output_{analysis_year}.tif"
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def get_yearly_basemap(year, roi_type='asset', asset_id='', feature_index=0, roi_geojson=None):
    """
    Returns a global true-color median composite for a given year.
    Optimized for extremely fast tile rendering.
    """
    try:
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

        def apply_scale_factors(image):
            opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
            return image.addBands(opticalBands, None, True)

        # Simplified cloud mask for speed
        def mask_clouds(image):
            qa = image.select('QA_PIXEL')
            mask = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 1).eq(0))
            return image.updateMask(mask)

        if roi_type == 'asset':
            if not asset_id or not str(asset_id).strip():
                return {'success': False, 'error': 'Asset ID cannot be empty. Please provide one.'}
            roi_collection = ee.FeatureCollection(asset_id)
            selected_feature = ee.Feature(roi_collection.toList(roi_collection.size()).get(feature_index))
            roi = selected_feature.geometry()
        elif roi_type in ['draw', 'upload']:
            if not roi_geojson:
                raise ValueError("GeoJSON data is required for 'draw' or 'upload' ROI type.")
            if roi_geojson.get("type") == "FeatureCollection":
                features = roi_geojson.get("features", [])
                if not features:
                    raise ValueError("No features found in GeoJSON")
                roi = ee.FeatureCollection(features).geometry()
            elif roi_geojson.get("type") == "Feature":
                roi = ee.Geometry(roi_geojson.get("geometry"))
            else:
                roi = ee.Geometry(roi_geojson)

        def get_col(col_id):
            band_in = ['SR_B3', 'SR_B2', 'SR_B1'] if ('05' in col_id or '07' in col_id) else ['SR_B4', 'SR_B3', 'SR_B2']
            band_out = ['B4', 'B3', 'B2']
            return (ee.ImageCollection(col_id)
                      .filterBounds(roi.buffer(50000))  # Buffer by 50km to cover the whole screen
                      .filterDate(start_date, end_date)
                      .map(apply_scale_factors)
                      .map(mask_clouds)
                      .select(band_in, band_out))

        cols = []
        if 1984 <= year <= 2011:
            cols.append(get_col("LANDSAT/LT05/C02/T1_L2"))
        if 1999 <= year <= 2021:
            cols.append(get_col("LANDSAT/LE07/C02/T1_L2"))
        if 2013 <= year:
            cols.append(get_col("LANDSAT/LC08/C02/T1_L2"))
        if 2021 <= year:
            cols.append(get_col("LANDSAT/LC09/C02/T1_L2"))

        if not cols:
            return {"success": False, "error": "No Landsat data for this year"}

        merged = cols[0]
        for c in cols[1:]:
            merged = merged.merge(c)

        # Local median composite (fast, but covering the screen)
        composite = merged.median()

        vis_params = {
            'bands': ['B4', 'B3', 'B2'],
            'min': 0,
            'max': 0.3
        }

        map_id_dict = ee.Image(composite).getMapId(vis_params)
        return {"success": True, "tile_url": map_id_dict['tile_fetcher'].url_format}

    except Exception as e:
        print(f"Error generating yearly basemap: {e}")
        return {"success": False, "error": str(e)}
