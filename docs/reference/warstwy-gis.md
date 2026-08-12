# Warstwy GIS i tabele etapów V9–V13

Pełny wykaz plików, rozmiarów, sum SHA-256, źródeł i licencji znajduje się
w [`data-manifest.json`](../../data/manifests/data-manifest.json). Poniższa
tabela jest referencją semantyczną do najważniejszych artefaktów.

| Etap | Warstwa lub tabela | Format / CRS | Rola |
|---|---|---|---|
| V9 | `inventory-v9` | CSV, Parquet / EPSG:4326 | finalna inwentaryzacja i parametry syren |
| V9 | `corrections-v5-v9` | CSV | 14 944 jawne zmiany od V5 do V9 |
| V10 | `coverage_ranges` | GeoPackage / EPSG:2180 | 61 110 niedublujących poligonów 65/70/75 dB(A) |
| V10 | `coverage-class-v10` | COG / EPSG:2180 | klasa pokrycia na rastrze 100 m |
| V10 | `sound-level-v10` | COG / EPSG:2180 | modelowany poziom dźwięku |
| V10 | `winner-siren-v10` | COG / EPSG:2180 | syrena dominująca w komórce |
| V11 | `building_population_V11` | GeoPackage / EPSG:2180 | ludność GUS rozdzielona do budynków OSM |
| V11 | `grid_residual_population_V11` | GeoPackage / EPSG:2180 | populacja rezydualna siatki |
| V12 | `wojewodztwa`, `powiaty`, `gminy` | GeoPackage / EPSG:2180 | granice PRG po uzgodnieniu z TERYT |
| V12 | `RiskZone` | SHP, raster / EPSG:2180 | klasy ryzyka MRP/ISOK |
| V12 | `priority_gaps` | GeoPackage / EPSG:2180 | 13 050 luk wraz z rankingiem |
| V13 | `candidates` | GeoPackage / EPSG:2180 | 2 501 kandydackich lokalizacji |
| V13 | `sensitive_objects` | GeoPackage / EPSG:2180 | 85 317 obiektów wrażliwych |
| V13 | `A01`–`A11` | CSV, Parquet | wyniki krajowe i przekroje administracyjne |

## Quickstart

Archiwum `soia-quickstart-2026.05.zip` zawiera jeden przestrzennie
indeksowany plik `layers/soia-quickstart.gpkg`, trzy rastry COG z piramidami
oraz równoległe tabele CSV i Parquet. Po opublikowaniu draftu bezpośredni URL
archiwum zostanie wpisany w sekcji `archives` manifestu.

## Pakiet jednostki

Lokalny `layers/soia_<TERYT>.gpkg` zawiera warstwy `admin_boundary`, `sirens`,
`coverage_ranges`, `priority_gaps`, `candidates` i `sensitive_objects`.
Geometrie kontekstowe są wybierane dla granicy JST powiększonej o 10 km;
metryki tabelaryczne odnoszą się wyłącznie do wskazanego TERYT.
