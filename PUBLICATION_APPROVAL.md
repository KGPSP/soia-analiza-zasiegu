# Bramka publikacyjna danych operacyjnych

Publiczne wysłanie repozytorium i publikacja plików Zenodo są kontrolowane
oddzielnie. Zgoda właściciela z 13 sierpnia 2026 r. zamyka bramkę publikacji
repozytorium GitHub. Czynności dotyczące Zenodo pozostają osobną bramką.

- [x] Właściciel danych — KG PSP — zatwierdził publikację pełnych współrzędnych syren.
- [x] Właściciel danych — KG PSP — zatwierdził publikację statusów GSM i SK PSP.
- [x] BIŁ KG PSP zatwierdziło ostateczną postać opracowania przygotowanego przez
      zespół pod kierownictwem st. bryg. Michała Kłosińskiego.
- [x] Zatwierdzono macierz licencji materiałów umieszczanych w repozytorium GitHub.
- [x] Wykonano kontrolę danych osobowych i informacji nieprzeznaczonych do publikacji.
- [x] Wykonano skan sekretów repozytorium.
- [x] Zatwierdzono edycję Markdown i wynikowy PDF.
- [x] Zatwierdzono metadane, autorów i opis publikacji.

## Oddzielna bramka Zenodo

Stan zweryfikowany 13 sierpnia 2026 r. w podglądzie zalogowanego draftu
`21921103`:

- [x] Potwierdzono zgodę właściciela — KG PSP — na publikację danych
      operacyjnych wskazanych w części GitHub powyżej.
- [x] Przesłano trzy archiwa ZIP64 i `data-manifest.json` — cztery pliki,
      łącznie 10 972 456 771 bajtów (11,0 GB w interfejsie Zenodo).
- [x] Sumy MD5 czterech plików w Zenodo są zgodne z lokalnymi artefaktami;
      manifest przechowuje także SHA-256.
- [x] Wykonano skan sekretów trzech pełnych paczek danych Zenodo.
- [x] Zarezerwowano DOI i wpisano go do `CITATION.cff`, `README.md` i manifestu.
- [x] W metadanych rekordu dodano prawa CC BY 4.0, MIT, ODbL 1.0 oraz wpis
      odsyłający do praw danych zewnętrznych w `data-manifest.json`.
- [ ] Zastąpiono wszystkie znaczniki robocze w manifeście prawami właściwymi
      dla konkretnego źródła. Obecnie pozostaje 70 wpisów
      `REQUIRES_LICENSE_REVIEW` i jeden wpis `REQUIRES_OWNER_APPROVAL`.
- [ ] Zatwierdzono na piśmie końcową macierz praw dla paczek Zenodo po
      aktualizacji manifestu.
- [ ] Ustawiono publiczny dostęp do plików i wykonano nieodwracalną operację
      `Publish`. Ten krok wolno wykonać dopiero po zamknięciu dwóch punktów
      bezpośrednio powyżej.

## Decyzja

Publiczną publikację GitHub zatwierdzono 13 sierpnia 2026 r. Tego samego dnia
utworzono ograniczony draft Zenodo `21921103` i zarezerwowano DOI
`10.5281/zenodo.21921103`.

**Decyzja audytu Zenodo: NIE ZATWIERDZONO JESZCZE PUBLICZNEJ PUBLIKACJI
PLIKÓW.** Draft pozostaje nieopublikowany, a pliki mają dostęp ograniczony.
Najpierw trzeba zamknąć znaczniki praw na poziomie pliku, ponownie przesłać
poprawiony `data-manifest.json` i uzyskać końcową akceptację macierzy. Pełny
protokół audytu znajduje się w
[`publication/zenodo-approval-audit.md`](publication/zenodo-approval-audit.md).
