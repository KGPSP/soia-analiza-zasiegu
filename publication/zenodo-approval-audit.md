# Audyt zgody i statusu Zenodo

## Wynik

**Publiczna publikacja plików Zenodo nie jest jeszcze zatwierdzona.**

13 sierpnia 2026 r. zweryfikowano zalogowany podgląd rekordu
[`21921103`](https://zenodo.org/records/21921103?preview=1), zawartość lokalnego
manifestu oraz bramkę publikacyjną. Rekord powinien pozostać draftem z
ograniczonym dostępem do plików.

## Stan potwierdzony w Zenodo

| Element | Stan |
|---|---|
| Rekord | nowy rekord w trybie `Preview`, jeszcze nieopublikowany |
| Identyfikator | `21921103` |
| DOI wersji | `10.5281/zenodo.21921103`, zarezerwowany; rejestracja nastąpi przy pierwszej publikacji |
| Wersja danych | `2026.05` |
| Dostęp do plików | `Restricted` |
| Właściciel / twórca instytucjonalny | Komenda Główna Państwowej Straży Pożarnej — Biuro Informatyki i Łączności |
| Kierownik opracowania | st. bryg. Michał Kłosiński |
| Repozytorium | `https://github.com/KGPSP/soia-analiza-zasiegu` |
| Prawa wpisane w rekordzie | CC BY 4.0, MIT, ODbL 1.0 i odesłanie do praw danych zewnętrznych w `data-manifest.json` |

## Pliki

| Plik | Rozmiar | MD5 w Zenodo |
|---|---:|---|
| `data-manifest.json` | 217,2 kB | `6f4879d0afa73995b92deaefdcf56ac5` |
| `soia-derived-v9-v13-2026.05.zip` | 5,5 GB | `00a1a34f3ecd53664c3b1f5af6e5c2ac` |
| `soia-quickstart-2026.05.zip` | 323,6 MB | `601917db895e5aabd683cc281d63e981` |
| `soia-sources-2026.05.zip` | 5,1 GB | `623415309b136bde250c6f8e2f5ef38d` |

Łączny rozmiar lokalny czterech artefaktów wynosi 10 972 456 771 bajtów.
Sumy MD5 w Zenodo są zgodne z lokalnymi artefaktami, a lokalny status zapisuje
również SHA-256. Skan sekretów trzech pełnych paczek został wykonany.

## Dlaczego bramka pozostaje otwarta

Samo dodanie kilku praw do metadanych rekordu nie rozstrzyga praw każdego
pliku w paczce mieszanej. W przesłanym `data-manifest.json` nadal znajduje się:

- 70 wpisów `REQUIRES_LICENSE_REVIEW` — GUS NSP 2021, TERYT, Wody
  Polskie/ISOK oraz NASA/USGS SRTM;
- jeden wpis `REQUIRES_OWNER_APPROVAL` dla inwentaryzacji KG PSP/JST.

Zgoda KG PSP na publikację współrzędnych oraz statusów GSM i SK PSP została
potwierdzona, więc drugi znacznik jest historyczny. Trzeba go zastąpić
jednoznacznym opisem praw. Nie wolno jednak utożsamiać zgody na publikację z
nadaniem odbiorcom otwartej licencji do ponownego wykorzystania.

## Warunki końcowej akceptacji

Przed naciśnięciem `Publish` należy:

1. zastąpić wszystkie 71 znaczników roboczych prawami i obowiązkami właściwymi
   dla każdego źródła;
2. przebudować i ponownie przesłać `data-manifest.json`;
3. zatwierdzić na piśmie końcową macierz praw do paczek Zenodo;
4. dopiero potem zmienić dostęp do plików na publiczny i ponownie sprawdzić
   podgląd rekordu;
5. wykonać nieodwracalną operację `Publish`.

Do czasu wykonania tych punktów prawidłowym opisem na GitHubie jest:
„draft Zenodo przygotowany i technicznie zweryfikowany; publiczna publikacja
plików niezatwierdzona”.
