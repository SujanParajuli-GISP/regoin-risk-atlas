import ee
import geemap

# Initialize the library
try:
    ee.Initialize()
except Exception as e:
    ee.Authenticate()
    ee.Initialize()

# 1. Setup Assets and Constants
blocks = ee.FeatureCollection("projects/xward-481405/assets/blocks_bihar")
start_date = '2019-01-01'
end_date = '2024-12-31'

# Initialize Map
Map = geemap.Map()
Map.centerObject(blocks, 7)
Map.addLayer(blocks, {'color': 'red'}, "Bihar Blocks")

# --- 2. Sentinel-2 NDVI ---
def calculate_s2_ndvi(img):
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    return ndvi.copyProperties(img, ['system:time_start'])

s2 = (ee.ImageCollection("COPERNICUS/S2_SR")
      .filterBounds(blocks)
      .filterDate(start_date, end_date)
      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
      .map(calculate_s2_ndvi))

def reduce_s2_regions(img):
    date = ee.Date(img.get('system:time_start'))
    return img.reduceRegions(
        collection=blocks,
        reducer=ee.Reducer.mean(),
        scale=10
    ).map(lambda f: f.set({
        'year': date.get('year'),
        'month': date.get('month')
    }))

ndvi_table_s2 = s2.map(reduce_s2_regions).flatten()

# Export Sentinel-2 NDVI
task_s2 = ee.batch.Export.table.toDrive(
    collection=ndvi_table_s2,
    description='Bihar_Block_S2_NDVI',
    fileFormat='CSV'
)
# task_s2.start() # Uncomment to run

# --- 3. CHIRPS Rainfall ---
chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY") \
    .filterBounds(blocks) \
    .filterDate(start_date, end_date)

years = ee.List.sequence(2019, 2024)
months = ee.List.sequence(1, 12)

def create_monthly_rain(y):
    def month_loop(m):
        start = ee.Date.fromYMD(y, m, 1)
        end = start.advance(1, 'month')
        rain = chirps.filterDate(start, end).sum().rename('Rainfall')
        return rain.set({
            'year': y,
            'month': m,
            'system:time_start': start.millis()
        })
    return months.map(month_loop)

monthly_rain = ee.ImageCollection(years.map(create_monthly_rain).flatten())

def reduce_rain_regions(img):
    return img.reduceRegions(
        collection=blocks,
        reducer=ee.Reducer.mean(),
        scale=5000
    ).map(lambda f: f.set({
        'year': img.get('year'),
        'month': img.get('month')
    }))

rain_table = monthly_rain.map(reduce_rain_regions).flatten()

# Export Rainfall
task_rain = ee.batch.Export.table.toDrive(
    collection=rain_table,
    description='Bihar_Block_Rainfall',
    fileFormat='CSV'
)
# task_rain.start()

# --- 4. MODIS ET (Evapotranspiration) ---
def process_et(img):
    return img.multiply(0.1).rename('ET').copyProperties(img, ['system:time_start'])

et_col = (ee.ImageCollection("MODIS/061/MOD16A2")
          .filterBounds(blocks)
          .filterDate(start_date, end_date)
          .select('ET')
          .map(process_et))

def reduce_et_regions(img):
    date = ee.Date(img.get('system:time_start'))
    return img.reduceRegions(
        collection=blocks,
        reducer=ee.Reducer.mean(),
        scale=500
    ).map(lambda f: f.set({
        'year': date.get('year'),
        'month': date.get('month')
    }))

et_table = et_col.map(reduce_et_regions).flatten()

# Export ET
task_et = ee.batch.Export.table.toDrive(
    collection=et_table,
    description='Bihar_Block_ET',
    fileFormat='CSV'
)
# task_et.start()

# --- 5. MODIS NDVI ---
def process_modis_ndvi(img):
    return img.multiply(0.0001).rename('NDVI').copyProperties(img, ['system:time_start'])

modis_ndvi = (ee.ImageCollection("MODIS/061/MOD13Q1")
              .filterBounds(blocks)
              .filterDate(start_date, end_date)
              .select('NDVI')
              .map(process_modis_ndvi))

def reduce_modis_regions(img):
    date = ee.Date(img.get('system:time_start'))
    return img.reduceRegions(
        collection=blocks,
        reducer=ee.Reducer.mean(),
        scale=250
    ).map(lambda f: f.set({
        'year': date.get('year'),
        'month': date.get('month')
    }))

ndvi_table_modis = modis_ndvi.map(reduce_modis_regions).flatten()

# Export MODIS NDVI
task_modis_ndvi = ee.batch.Export.table.toDrive(
    collection=ndvi_table_modis,
    description='Bihar_Block_MODIS_NDVI',
    fileFormat='CSV'
)
# task_modis_ndvi.start()

# Display the map (if using Jupyter)
Map