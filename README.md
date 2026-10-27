# Bankline_Extraction

A full-stack geospatial platform for automated river bankline extraction, analysis, and visualization using Google Earth Engine, FastAPI, and React Leaflet.

---

## 📌 Features

- **Google Earth Engine Integration**: Authenticate and extract multi-temporal satellite imagery for river channels and regions of interest (ROI).
- **Automated Bankline Extraction**: Geospatial processing algorithms for water extraction, channel contouring, hull generation, and bankline vectorization.
- **Interactive Map Interface**: Built with React, Vite, Leaflet, and Leaflet Draw for interactive ROI selection, bankline inspection, and layer toggling.
- **FastAPI Backend**: High-performance backend providing endpoints for Earth Engine authentication, ROI processing, geojson export, and static serving.

---

## 🏗️ Project Structure

```text
Bankline_extraction/
├── backend/
│   ├── auth.py              # Earth Engine authentication helper
│   ├── auth_fix.py          # SSL certificate patch for Earth Engine auth
│   ├── gee_processor.py     # Google Earth Engine data fetching & image processing
│   ├── geo_processor.py     # Geospatial vectorization & bankline extraction
│   ├── main.py              # FastAPI application & API endpoints
│   ├── requirements.txt     # Python backend dependencies
│   └── static/              # Compiled frontend assets for standalone deployment
├── frontend/
│   ├── src/                 # React frontend source (App.jsx, styles, components)
│   ├── public/              # Icons and public assets
│   ├── package.json         # Frontend dependencies and scripts
│   └── vite.config.js       # Vite build configuration
├── .gitignore
└── README.md
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.9+
- Node.js 18+ & npm
- A Google Earth Engine account / cloud project

### 2. Backend Setup
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\activate
   # Linux/macOS:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Authenticate Earth Engine:
   ```bash
   python auth.py
   ```
5. Run the backend server:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### 3. Frontend Setup
1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install npm packages:
   ```bash
   npm install
   ```
3. Start the Vite development server:
   ```bash
   npm run dev
   ```

---

## 📜 License
This project is open-source. Feel free to contribute and customize for your hydrological and remote sensing research.
