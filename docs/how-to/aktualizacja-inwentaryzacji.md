# Jak przeliczyć analizę po aktualizacji syren

Własny plik nie zastępuje całej krajowej inwentaryzacji. Jest kontrolowaną
nakładką na snapshot `2026.05`, dzięki czemu model zachowuje syreny sąsiednich
JST wpływające na wynik przy granicy jednostki.

## Przygotuj CSV

Minimalny przykład:

```csv
operation,nr_ref,teryt_gmi,lat,lon,rodzaj_syreny,moc_w,wysokosc_nad_terenem_m,reason
add,LOKALNA-001,2815092,53.7001,20.0012,cyfrowa,600,12,planowana lokalizacja
update,SOiA-20260421-16328,,,,,900,,wymiana głowicy
disable,SOiA-20260422-34072,,,,,,,demontaż
```

Zapisz plik jako UTF-8. Nagłówki i reguły opisuje
`docs/reference/inventory-updates.schema.json`.

## Uruchom analizę

```bash
docker compose run --rm soia analyze \
  --teryt 2815092 \
  --inventory-updates input/zmiany_syren.csv \
  --output outputs/2815092-zmiany
```

Generator najpierw waliduje cały plik. Jeżeli choć jeden wiersz jest
niepoprawny, nie rozpoczyna obliczeń i nie tworzy częściowego wyniku.
Zakres V9 dopuszcza moc od 300 do 5500 W, wysokość montażu od 3 do 30 m
oraz współrzędne w kontrolnej ramce Polski. Operacja `disable` przyjmuje
wyłącznie identyfikator i opcjonalne uzasadnienie.

## Sprawdź ślad zmian

`inventory-changes.csv` zawiera wartości przed i po zmianie, typ operacji,
uzasadnienie oraz wynik zastosowania. `validation.json` zapisuje kontrole
spójności gotowego pakietu. GeoPackage zachowuje kontekst syren i warstw
z obszaru jednostki powiększonego o 10 km.

## Typowe błędy

- `unknown nr_ref` - próba zmiany lub wyłączenia syreny spoza snapshotu;
- `duplicate operation` - więcej niż jedna operacja dla tego samego `nr_ref`;
- `invalid TERYT` - kod gminy nie występuje w opublikowanym rejestrze;
- `coordinates outside Poland` - współrzędne nie mieszczą się w zakresie kontrolnym Polski;
- `missing add fields` - nowa syrena nie ma kompletu parametrów modelu V9.
