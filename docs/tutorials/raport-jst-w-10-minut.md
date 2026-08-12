# Raport JST w 10 minut

Ten tutorial prowadzi od pustego katalogu do raportu dla jednej jednostki
samorządu terytorialnego. Nie wymaga lokalnej instalacji bibliotek GIS.

## 1. Wymagania

- Docker Desktop albo Docker Engine z Compose;
- co najmniej 15 GB wolnego miejsca dla pakietu szybkiego startu i wyników;
- połączenie z internetem podczas pierwszego pobrania danych.

## 2. Pobierz repozytorium

```bash
git clone https://github.com/<organizacja>/soia-analiza-zasiegu.git
cd soia-analiza-zasiegu
```

Adres organizacji zostanie uzupełniony w wydaniu publicznym. W lokalnym
kandydacie do wydania przejdź bezpośrednio do katalogu `PUBLIKACJA_GITHUB`.

## 3. Pobierz i sprawdź dane

```bash
docker compose run --rm soia data fetch --release 2026.05
```

Polecenie pobiera paczkę szybkiego startu, porównuje jej rozmiar i SHA-256 z
manifestem, a następnie rozpakowuje dane. Uszkodzona lub niekompletna paczka
nie jest używana.

## 4. Wygeneruj raport

Przykład dla gminy wiejskiej Ostróda (`2815092`):

```bash
docker compose run --rm soia analyze \
  --teryt 2815092 \
  --output outputs/2815092
```

Kod TERYT jednoznacznie określa poziom:

- 2 cyfry - województwo;
- 4 cyfry - powiat;
- 7 cyfr - gmina.

## 5. Otwórz wyniki

- `outputs/2815092/raport.md` - wersja do GitHub;
- `outputs/2815092/raport.pdf` - wersja do obiegu dokumentów;
- `outputs/2815092/layers/soia_2815092.gpkg` - warstwy do QGIS;
- `outputs/2815092/validation.json` - wynik kontroli spójności;
- `outputs/2815092/manifest.json` - dane i parametry użyte w obliczeniu.

## 6. Jak czytać wynik

Wartość `>=65 dB(A)` jest podstawową metryką planistyczną. Model nie jest
pomiarem terenowym i nie zastępuje certyfikowanej analizy propagacji.
Rekomendacje zakupowe wymagają walidacji lokalizacji, własności gruntu,
zasilania, łączności i pomiaru tła akustycznego.

