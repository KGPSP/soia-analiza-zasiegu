# Interfejs CLI i artefakty

## `soia data fetch`

```text
soia data fetch --release RELEASE [--role quickstart|sources|derived]
                [--manifest PATH] [--data-dir PATH] [--force]
```

Pobiera paczkę wybranej roli (domyślnie `quickstart`), weryfikuje `size_bytes` i
`sha256`, a następnie bezpiecznie rozpakowuje ZIP. Bez `--force` istniejący,
zweryfikowany pakiet nie jest pobierany ponownie. `pipeline full` pobiera role
`sources` i `derived` zadeklarowane w konfiguracji.

## `soia analyze`

```text
soia analyze --teryt CODE --output DIR
             [--data-dir DIR] [--inventory-updates CSV] [--force]
             [--skip-pdf]
```

- `CODE`: 2, 4 albo 7 cyfr;
- `DIR`: nowy katalog wynikowy;
- `--inventory-updates`: opcjonalna nakładka `add/update/disable`;
- `--force`: świadoma zgoda na zastąpienie istniejącego wyniku;
- `--skip-pdf`: generuje pozostałe artefakty bez PDF.

## `soia pipeline full`

```text
soia pipeline full --config configs/poland-2026.05.yaml [--resume] [--force]
```

Orkiestrator wykonuje etapy w kolejności określonej w konfiguracji. Każdy
etap zapisuje manifest wejść i wyników. `--resume` pomija wyłącznie etap,
którego manifest i wszystkie sumy kontrolne są nadal zgodne.

Pełne przeliczenie Polski jest testem wydania wykonywanym poza CI:

```text
docker compose run --rm --entrypoint python soia -m tools.run_release_test \
  --config configs/poland-2026.05.yaml \
  --result-root reproduction/2026.05
```

Raport `release-artifacts/full-recalculation-2026.05.json` zapisuje czasy
etapów, łączny czas, szczytowe użycie pamięci procesów potomnych, rozmiar
wyniku, zmianę wolnego miejsca oraz wszystkie krajowe kryteria akceptacji.
Walidacja zastanego snapshotu jest dostępna osobno w
[`release-snapshot-validation-2026.05.json`](../../publication/release-snapshot-validation-2026.05.json)
i nie jest przedstawiana jako dowód ponownego przeliczenia.

## Pakiet wyniku JST

| Plik | Znaczenie |
|---|---|
| `README.md` | indeks wyniku i najważniejsze liczby |
| `raport.md` | pełny raport źródłowy |
| `raport.pdf` | eksport tego samego raportu |
| `maps/` | mapy PNG użyte w raporcie |
| `tables/` | wybrane rekordy CSV dla TERYT |
| `layers/soia_<TERYT>.gpkg` | granice, syreny, zasięgi, luki i rekomendacje |
| `inventory-changes.csv` | audyt opcjonalnej nakładki |
| `manifest.json` | wersje, parametry, źródła i SHA-256 |
| `validation.json` | kontrole spójności wyniku |

## Determinizm

Przy identycznym snapshotcie, konfiguracji i nakładce CSV tabele, warstwy i
metryki muszą być identyczne. Pola czasu wykonania są przechowywane oddzielnie
od danych podlegających porównaniu.
