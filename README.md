# SOIA - reprodukowalna analiza zasięgu syren alarmowych

<img src="pages/assets/label-ai-modified.svg" alt="Ikona Unii Europejskiej oznaczająca treść częściowo zmodyfikowaną przez sztuczną inteligencję" width="120">

**Treść częściowo zmodyfikowana przez AI.** Część materiałów w tym repozytorium
powstała z udziałem narzędzi sztucznej inteligencji — zob.
[Oznaczenie treści AI](#oznaczenie-treści-ai).

Repozytorium publikuje analizę kierunkową Systemu Ostrzegania i Alarmowania
Ludności oraz narzędzia pozwalające odtworzyć ją dla województwa, powiatu lub
gminy wskazanej kodem TERYT.

> **Status wydania:** publiczne wydanie `v1.0.0`. Publikację repozytorium
> GitHub i pakietu danych Zenodo zatwierdzono 13 sierpnia 2026 r. Rekord
> [`21921103`](https://zenodo.org/records/21921103) zawiera cztery publiczne
> pliki (11,0 GB), a DOI
> [`10.5281/zenodo.21921103`](https://doi.org/10.5281/zenodo.21921103) jest
> aktywny. Manifest nie zawiera znaczników roboczych `REQUIRES_*`. Szczegóły:
> [audyt Zenodo](publication/zenodo-approval-audit.md) i
> [bramka publikacyjna](PUBLICATION_APPROVAL.md).

**Edycja internetowa:**
[Analiza kierunkowa SOIA — GitHub Pages](https://kgpsp.github.io/soia-analiza-zasiegu/)

## Właściciel i opracowanie

- **Właściciel:** Komenda Główna Państwowej Straży Pożarnej (KG PSP).
- **Jednostka odpowiedzialna:** Biuro Informatyki i Łączności KG PSP
  (BIŁ KG PSP).
- **Opracowanie:** zespół pod kierownictwem st. bryg. Michała Kłosińskiego.

## Najważniejsze materiały

- [Analiza kierunkowa CAP/IoT w Markdown](publication/analiza-kierunkowa.md)
- [Wnioski z realizacji](publication/analiza-kierunkowa.md#wnioski-z-realizacji)
- [Raport JST w 10 minut (instrukcja generowania raportów)](docs/tutorials/raport-jst-w-10-minut.md)
- [Aktualizacja własnej inwentaryzacji](docs/how-to/aktualizacja-inwentaryzacji.md)
- [Metodyka V9-V13](docs/explanation/metodyka-v9-v13.md)
- [Komendy, schematy i artefakty](docs/reference/interfejs-cli-i-dane.md)
- [Katalog warstw GIS V9-V13](docs/reference/warstwy-gis.md)
- [Źródła, licencje i ograniczenia](docs/reference/zrodla-i-licencje.md)
- [Przykładowy raport: gmina wiejska Ostróda](examples/ostroda/README.md)
- [Przygotowanie draftu Zenodo](docs/how-to/draft-zenodo.md)
- [Audyt zgody i statusu Zenodo](publication/zenodo-approval-audit.md)
- [Status publikacji Zenodo 2026.05](publication/zenodo-draft-status.json)
- [Walidacja zastanego snapshotu 2026.05](publication/release-snapshot-validation-2026.05.json)

## Szybki start

```bash
docker compose run --rm soia data fetch --release 2026.05
docker compose run --rm soia analyze --teryt 2815092 --output outputs/2815092
```

Generator zapisuje raport Markdown i PDF, mapy PNG, tabele CSV, GeoPackage,
manifest wykonania oraz wyniki walidacji. Szczegóły znajdują się w tutorialu.

## Dwa poziomy odtwarzalności

1. **Tryb szybki** korzysta z wersjonowanego snapshotu `2026.05` i generuje
   raport dla wybranego TERYT.
2. **Pełny pipeline** prowadzi od surowej inwentaryzacji przez V9 (jakość
   danych), V10 (zasięg), V11 (populacja), V12 (ryzyko i luki) do V13
   (rekomendacje i raport).

Pełne dane nie są przechowywane w Git. Skrypt pobierający używa manifestu,
sprawdza rozmiar i SHA-256 każdej paczki, a następnie rozpakowuje ją do
ignorowanego katalogu `data/releases/`.

## Wersje i cytowanie

- wersja oprogramowania: `1.0.0`;
- wersja danych: `2026.05`;
- dane ludności: GUS NSP 2021;
- układ obliczeniowy: EPSG:2180;
- DOI Zenodo: [10.5281/zenodo.21921103](https://doi.org/10.5281/zenodo.21921103).

## Oznaczenie treści AI

Część materiałów w tym repozytorium (opracowanie tekstu, kod, wizualizacje)
powstała z udziałem narzędzi sztucznej inteligencji. Odpowiedzialność za
publikację ponosi Komenda Główna Państwowej Straży Pożarnej.

Do oznaczenia użyto unijnej ikony „AI modified" z zestawu Komisji Europejskiej
służącego do oznaczania treści generowanych lub modyfikowanych przez AI,
wspierającego wymogi przejrzystości z art. 50 aktu o sztucznej inteligencji
(AI Act). Ikona jest publicznie dostępna, nie wymaga przypisania autorstwa
i została użyta bez zmian w grafice:
[EU icons for labelling AI generated content](https://digital-strategy.ec.europa.eu/pl/policies/eu-icons-labelling-ai-generated-content).

To samo oznaczenie widnieje w [edycji internetowej](https://kgpsp.github.io/soia-analiza-zasiegu/)
nad treścią raportu.

## Licencje

Kod źródłowy jest udostępniany na licencji MIT. Dokumentacja i własne
opracowania są udostępniane na licencji CC BY 4.0. Dane zewnętrzne zachowują
licencje swoich dostawców; szczegóły opisuje macierz źródeł i licencji.
