# Źródła, licencje i status publikacyjny

Ta macierz jest kontrolą wydania, a nie opinią prawną. Musi zostać zatwierdzona
przed publicznym opublikowaniem plików Zenodo. Wpis `otwarte` nie zezwala na
publikację, jeżeli w `data-manifest.json` dla danego pliku nadal widnieje
znacznik roboczy.

Właścicielem opracowania jest Komenda Główna Państwowej Straży Pożarnej
(KG PSP), jednostką odpowiedzialną Biuro Informatyki i Łączności KG PSP
(BIŁ KG PSP), a materiał opracował zespół pod kierownictwem st. bryg. Michała
Kłosińskiego.

| Zbiór | Dostawca | Warunki zidentyfikowane w audycie | Status dla Zenodo |
|---|---|---|---|
| Inwentaryzacja syren 2026-05-05 | KG PSP/JST | KG PSP zatwierdziła 13.08.2026 publikację pełnych współrzędnych oraz statusów GSM i SK PSP | zgoda właścicielska jest potwierdzona; trzeba zastąpić historyczny wpis `REQUIRES_OWNER_APPROVAL` jednoznacznym opisem praw do publikacji i ponownego wykorzystania |
| OpenStreetMap PBF | autorzy OpenStreetMap/Geofabrik | [ODbL 1.0](https://www.openstreetmap.org/copyright): oznaczenie OpenStreetMap i autorów, wskazanie licencji; pochodna baza podlega warunkom share-alike | wpis `ODbL-1.0` obecny; utrzymać wymagane oznaczenia w danych i mapach |
| Siatka ludności NSP 2021 | GUS | [Portal Geostatystyczny](https://portal.geo.stat.gov.pl/regulamin/) dopuszcza powszechny, nieodpłatny dostęp i ponowne wykorzystanie z podaniem `geo.stat.gov.pl` oraz daty pobrania; [BIP GUS](https://bip.stat.gov.pl/kontakt/ponowne-wykorzystywanie-informacji-sektora-publicznego/) wymaga także czasu wytworzenia/pozyskania i informacji o przetworzeniu | warunki źródła zidentyfikowane; cztery wpisy w manifeście nadal wymagają zastąpienia znacznika roboczego pełnym zapisem obowiązków |
| PRG | GUGiK | metadane dane.gov.pl wskazują CC BY 4.0 i wymagają formuły: „Wykorzystano/opracowano na podstawie materiałów państwowego zasobu geodezyjnego i kartograficznego” | wpis `CC-BY-4.0` obecny; dodać wymaganą formułę do materiałów wykorzystujących PRG |
| TERYT i `TERYT_nrJOP.xlsx` | GUS / KG PSP | [Rejestr TERYT](https://eteryt.stat.gov.pl/eTeryt/rejestr_teryt/udostepnianie_danych/formy_i_zasady_udostepniania/formy_i_zasady_udostepniania.aspx) jest jawny i udostępniany bezpłatnie; dla części GUS obowiązują warunki ponownego wykorzystania GUS, a część KG PSP podlega zgodzie właścicielskiej | dwa wpisy wymagają rozdzielenia pochodzenia i zastąpienia `REQUIRES_LICENSE_REVIEW` |
| MRP RiskZone | Wody Polskie/ISOK | [regulamin Hydroportalu](https://www.isok.gov.pl/regulamin.html) wymaga podania `wody.isok.gov.pl` i daty pobrania oraz wskazuje poglądowy charakter danych | sześć wpisów pozostaje otwartych: przed publikacją surowej, scalonej warstwy trzeba potwierdzić, że warunki obejmują jej redystrybucję w ZIP, a nie wyłącznie wykorzystanie informacji |
| SRTM | NASA/USGS | USGS opisuje SRTM jako dane dostępne w otwartej dystrybucji i oznacza materiał jako [Public Domain](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm) | 58 kafli ma znacznik roboczy; przed jego zastąpieniem trzeba zapisać dokładny produkt, wersję, adres pobrania/DOI i wymaganą atrybucję |
| Kod projektu | BIŁ KG PSP / KG PSP | MIT | zatwierdzone |
| Dokumentacja i własne opracowania | BIŁ KG PSP / KG PSP | CC BY 4.0 | zatwierdzone |

## Wynik kontroli manifestu 2026.05

W zweryfikowanym `data-manifest.json` pozostaje 71 znaczników roboczych:

| Źródło | Znacznik | Liczba plików |
|---|---|---:|
| GUS NSP 2021 | `REQUIRES_LICENSE_REVIEW` | 4 |
| GUS TERYT | `REQUIRES_LICENSE_REVIEW` | 1 |
| GUS/KG PSP | `REQUIRES_LICENSE_REVIEW` | 1 |
| Wody Polskie/ISOK | `REQUIRES_LICENSE_REVIEW` | 6 |
| NASA/USGS SRTM | `REQUIRES_LICENSE_REVIEW` | 58 |
| KG PSP/JST | `REQUIRES_OWNER_APPROVAL` | 1 |

Zgoda KG PSP zamyka decyzję właścicielską, ale nie aktualizuje automatycznie
już wygenerowanego manifestu ani nie ustanawia licencji ponownego wykorzystania.
Przed publikacją trzeba przypisać warunki na poziomie pliku, przebudować
`data-manifest.json`, przesłać go ponownie do draftu i uzyskać końcową
akceptację macierzy. Jeżeli jeden element nadal ma znacznik roboczy, rekord nie
może zostać opublikowany.
