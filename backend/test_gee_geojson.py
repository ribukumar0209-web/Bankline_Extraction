import ee
ee.Initialize()
gj = {
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "coordinates": [
          [
            [81.1, 28.1],
            [81.1, 28.2],
            [81.2, 28.2],
            [81.2, 28.1],
            [81.1, 28.1]
          ]
        ],
        "type": "Polygon"
      }
    }
  ]
}
geom = ee.FeatureCollection(gj['features']).geometry()
print("Geometry type:", geom.type().getInfo())
