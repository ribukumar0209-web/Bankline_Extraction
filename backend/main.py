import os
import tempfile
import shutil
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import gee_processor
import geo_processor

# Try to initialize with service account if present, otherwise default credentials
if os.path.exists("service_account.json"):
    gee_processor.initialize_ee("service_account.json")
else:
    gee_processor.initialize_ee()

app = FastAPI(title="Bankline Extraction API")

@app.get("/api/auth_status")
def get_auth_status():
    return {"is_authenticated": gee_processor.is_initialized}

@app.post("/api/auth_service_account")
async def auth_service_account(file: UploadFile = File(...)):
    try:
        # Save the uploaded JSON key
        key_path = "service_account.json"
        with open(key_path, "wb") as f:
            f.write(await file.read())
        
        # Attempt to initialize
        success = gee_processor.initialize_ee(key_path)
        if success:
            return {"success": True, "message": "Earth Engine authenticated successfully!"}
        else:
            return {"success": False, "message": "Failed to authenticate with the provided key."}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = tempfile.mkdtemp()

from typing import Optional

class Step1Request(BaseModel):
    feature_index: int = 0
    start_date: str = '2024-09-01'
    end_date: str = '2024-12-31'
    roi_type: str = 'asset'
    asset_id: str = 'projects/ee-shobhit007/assets/Ayodhya_window'
    roi_geojson: Optional[dict] = None
    composite_method: str = 'median'

# Endpoint for Step 1
@app.post("/api/step1")
def extract_river_polygon(req: Step1Request):
    res = gee_processor.extract_river_polygon(
        req.feature_index, 
        req.start_date, 
        req.end_date, 
        req.asset_id, 
        req.roi_type, 
        req.roi_geojson,
        req.composite_method
    )
    if res.get("success"):
        features = res.get("geojson", {}).get("features", [])
        print(f"Step 1 GeoJSON exported successfully with {len(features)} features.")
    return res

@app.post("/api/upload_roi")
async def upload_roi(file: UploadFile = File(...)):
    try:
        tmp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(tmp_dir, file.filename)
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        
        import geopandas as gpd
        gdf = gpd.read_file(zip_path)
        gdf = gdf.to_crs("EPSG:4326")
        geojson_str = gdf.to_json()
        import json
        
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"success": True, "geojson": json.loads(geojson_str)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

from typing import Dict, Any
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import tempfile
import os
import shutil
import geopandas as gpd

class Step3Request(BaseModel):
    year: str
    geojson: Dict[Any, Any]
    upstream_coords: Optional[list] = None    # [lng, lat] from HydroSHEDS — lowest flow accumulation
    downstream_coords: Optional[list] = None  # [lng, lat] from HydroSHEDS — highest flow accumulation
    flow_path: Optional[list] = None          # [[lng, lat], ...] ordered upstream→downstream (Layer 1)
    flow_dir_code: Optional[int] = None       # D8 direction integer 1–128 (Layer 3)


class ExportRequest(BaseModel):
    geojson: Dict[Any, Any]
    year: str

from fastapi import BackgroundTasks, HTTPException
import uuid

@app.post("/api/export_shapefile")
def export_shapefile(req: ExportRequest, background_tasks: BackgroundTasks):
    try:
        # Create a temporary directory
        tmp_dir = tempfile.mkdtemp()
        
        # Load GeoJSON into GeoDataFrame
        features = req.geojson.get("features", [])
        if not features:
            raise ValueError("No features to export.")
            
        gdf = gpd.GeoDataFrame.from_features(features)
        gdf = gdf.set_crs("EPSG:4326")
        
        # Ensure 'bank' column exists
        if 'bank' not in gdf.columns:
            gdf['bank'] = None
            
        # Separate features into Left and Right
        left_mask = gdf['bank'].str.lower() == 'left'
        right_mask = gdf['bank'].str.lower() == 'right'
        
        left_gdf = gdf[left_mask].copy()
        right_gdf = gdf[right_mask].copy()
        unknown_gdf = gdf[~(left_mask | right_mask)].copy()
        
        import pandas as pd
        
        # For newly drawn lines without a 'bank' property, assign based on proximity
        if not unknown_gdf.empty:
            left_new = []
            right_new = []
            for idx, row in unknown_gdf.iterrows():
                geom = row.geometry
                dist_to_left = left_gdf.distance(geom).min() if not left_gdf.empty else float('inf')
                dist_to_right = right_gdf.distance(geom).min() if not right_gdf.empty else float('inf')
                
                if dist_to_left < dist_to_right:
                    left_new.append(row.to_frame().T)
                else:
                    right_new.append(row.to_frame().T)
            
            if left_new:
                left_gdf = pd.concat([left_gdf] + left_new, ignore_index=True)
            if right_new:
                right_gdf = pd.concat([right_gdf] + right_new, ignore_index=True)
                    
        # Save as separate Shapefiles
        year_str = str(req.year) if req.year else "UnknownYear"
        
        if not left_gdf.empty:
            left_gdf.to_file(os.path.join(tmp_dir, year_str + "_L.shp"))
        if not right_gdf.empty:
            right_gdf.to_file(os.path.join(tmp_dir, year_str + "_R.shp"))
            
        # Zip the Shapefile components with a unique name to avoid Windows file locks
        unique_id = str(uuid.uuid4())
        zip_filename = year_str + "_banklines.zip"
        zip_path = os.path.join(tempfile.gettempdir(), f"{unique_id}_{zip_filename}")
        shutil.make_archive(zip_path.replace('.zip', ''), 'zip', tmp_dir)
        
        # Cleanup tasks to remove the temp directory and the zip file after sending
        def cleanup():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            try:
                os.remove(zip_path)
            except:
                pass
                
        background_tasks.add_task(cleanup)
        
        return FileResponse(zip_path, media_type="application/zip", filename=zip_filename)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint for Step 3
@app.post("/api/step3")
def separate_banks(req: Step3Request):
    try:
        res_geojson = geo_processor.process_separate_banks_geojson(
            req.geojson,
            req.year,
            upstream_lonlat=req.upstream_coords,
            downstream_lonlat=req.downstream_coords,
            flow_path=req.flow_path,
            flow_dir_code=req.flow_dir_code
        )
        return {"success": True, "geojson": res_geojson}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}



class YearlyBasemapRequest(BaseModel):
    year: int
    roi_type: str = 'asset'
    asset_id: str = ''
    feature_index: int = 0
    roi_geojson: Optional[dict] = None

@app.post("/api/yearly_basemap")
def yearly_basemap(req: YearlyBasemapRequest):
    """
    Generate a cloud-free annual Landsat true-color composite tile URL for a given year.
    The returned tile_url can be used directly as a Leaflet TileLayer basemap.
    """
    res = gee_processor.get_yearly_basemap(
        req.year,
        req.roi_type,
        req.asset_id,
        req.feature_index,
        req.roi_geojson
    )
    return res





# --- EXE CONFIGURATION ---
import sys
import threading
import webbrowser
import time

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Running in a PyInstaller bundle
    base_dir = sys._MEIPASS
else:
    # Running in a normal Python environment
    base_dir = os.path.dirname(os.path.abspath(__file__))

static_dir = os.path.join(base_dir, "static")

if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
    
    @app.get("/")
    @app.get("/{catchall:path}")
    def serve_react_app(catchall: str = ""):
        if catchall.startswith("api/"):
            return {"error": "API route not found"}
        return FileResponse(os.path.join(static_dir, "index.html"))

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    import uvicorn
    
    # Fix for PyInstaller --windowed mode where stdout/stderr are None
    class DummyStream:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
        def isatty(self): return False
        
    if sys.stdout is None:
        sys.stdout = DummyStream()
    if sys.stderr is None:
        sys.stderr = DummyStream()
    
    # Start browser opening thread
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Run the server
    uvicorn.run(app, host="127.0.0.1", port=8000)
