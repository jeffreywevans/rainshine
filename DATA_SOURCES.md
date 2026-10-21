# Data sources and attribution

Rainshine fetches recorded observations from the U.S. National Oceanic and
Atmospheric Administration, National Centers for Environmental Information.

- [GHCN-hourly](https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly)
  provides subdaily observations. Rainshine reads the public station-year PSV
  files and their measurement, quality, and source codes. See the
  [format documentation](https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh_DOCUMENTATION.pdf).
- [GHCN-daily](https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily)
  provides daily temperatures, precipitation, snowfall, and related records.
  Rainshine reads public station `.dly` files according to NOAA's
  [README](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt).
  Dataset citation: Menne et al., Global Historical Climatology Network–Daily,
  Version 3, NOAA NCEI, [doi:10.7289/V5D21VHZ](https://doi.org/10.7289/V5D21VHZ).

NOAA flags, reporting periods, and missing values are retained or explained in
the export. A displayed daily value is the station's archive record for that
date; its reporting period is not necessarily a local midnight-to-midnight day.
Precipitation includes liquid water and the water equivalent of frozen
precipitation. Snowfall is separate. Raw hourly precipitation is not summed by
Rainshine because accumulation periods and intermediate reports can overlap.

The packaged station and location catalog is an adaptation of the
`Telegraphy_Weather_Locations.json` research crosswalk created on 2026-10-20.
NOAA archive identities and inventory records were preserved, and relevant
fields were compacted for the application. Sources for that crosswalk include:

- NOAA's GHCN-hourly station catalog and monthly inventory, and GHCN-daily
  station catalog and element inventories.
- [GeoNames](https://www.geonames.org/), place coordinates and timezones,
  licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Venue anchors were manually adapted; distances were recomputed.
- [Meteostat](https://dev.meteostat.net/data/weather-stations), metadata used
  to cross-check station identifiers in the original research; Meteostat data
  are generally CC BY 4.0. Rainshine does not fetch Meteostat weather values.
- [OurAirports](https://ourairports.com/data/), airport metadata used in the
  original crosswalk. OurAirports releases its data into the public domain.

The source catalog contains 238 locations in the United States, Canada, and
Australia. Its station selection focuses on documented airport and urban
subdaily sites plus nearby daily stations with temperature and precipitation
inventories. It is not a live worldwide station directory. Archive inventories
are advisory and may need refreshing as source histories and holdings change.
