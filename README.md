# SOIA - reprodukowalna analiza zasięgu syren alarmowych

Repozytorium publikuje analizę kierunkową Systemu Ostrzegania i Alarmowania
Ludności oraz narzędzia pozwalające odtworzyć ją dla województwa, powiatu lub
gminy wskazanej kodem TERYT.

> **Status wydania:** publiczny kandydat `v1.0.0-rc`. Publikację repozytorium
> GitHub zatwierdzono 13 sierpnia 2026 r. Rekord Zenodo `21921103` jest
> nieopublikowanym draftem, a jego cztery pliki (11,0 GB) pozostają objęte
> dostępem ograniczonym. DOI `10.5281/zenodo.21921103` jest zarezerwowany, ale
> nieaktywny do chwili publikacji. **Publiczna publikacja plików Zenodo nie jest
> jeszcze zatwierdzona**: manifest zawiera 70 wpisów
> `REQUIRES_LICENSE_REVIEW` i jeden historyczny wpis
> `REQUIRES_OWNER_APPROVAL`, który trzeba zastąpić zapisem uzyskanej zgody KG
> PSP. Szczegóły: [audyt Zenodo](publication/zenodo-approval-audit.md) i
> [bramka publikacyjna](PUBLICATION_APPROVAL.md).

## Właściciel i opracowanie

- **Właściciel:** Komenda Główna Państwowej Straży Pożarnej (KG PSP).
- **Jednostka odpowiedzialna:** Biuro Informatyki i Łączności KG PSP
  (BIŁ KG PSP).
- **Opracowanie:** zespół pod kierownictwem st. bryg. Michała Kłosińskiego.

## Najważniejsze materiały

- [Analiza kierunkowa CAP/IoT w Markdown](publication/analiza-kierunkowa.md)
- [Raport JST w 10 minut](docs/tutorials/raport-jst-w-10-minut.md)
- [Aktualizacja własnej inwentaryzacji](docs/how-to/aktualizacja-inwentaryzacji.md)
- [Metodyka V9-V13](docs/explanation/metodyka-v9-v13.md)
- [Komendy, schematy i artefakty](docs/reference/interfejs-cli-i-dane.md)
- [Katalog warstw GIS V9-V13](docs/reference/warstwy-gis.md)
- [Źródła, licencje i ograniczenia](docs/reference/zrodla-i-licencje.md)
- [Przykładowy raport: gmina wiejska Ostróda](examples/ostroda/README.md)
- [Przygotowanie draftu Zenodo](docs/how-to/draft-zenodo.md)
- [Audyt zgody i statusu Zenodo](publication/zenodo-approval-audit.md)
- [Status draftu Zenodo 2026.05](publication/zenodo-draft-status.json)
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

## Licencje

Kod źródłowy jest udostępniany na licencji MIT. Dokumentacja i własne
opracowania są udostępniane na licencji CC BY 4.0. Dane zewnętrzne zachowują
licencje swoich dostawców; szczegóły opisuje macierz źródeł i licencji.
