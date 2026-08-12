# Przygotowanie draftu Zenodo

## Cel

Repozytorium przygotowuje trzy archiwa ZIP64 i metadane rekordu. Narzędzie
nie zawiera operacji publikacji, dzięki czemu nie może przypadkowo wykonać
nieodwracalnego kroku `publish`.

Automat tworzy draft z ograniczonym dostępem. Paczki łączą materiały na MIT,
CC BY 4.0, ODbL i innych warunkach, a stare API Zenodo przypisuje pojedynczą
licencję do wszystkich plików. Po zatwierdzeniu macierzy należy w interfejsie
Zenodo dodać wiele licencji/praw i dopiero wtedy zmienić widoczność na
publiczną. [Zenodo opisuje ten wariant jako mixed license upload](https://help.zenodo.org/docs/deposit/describe-records/licenses/).

## Przygotowanie lokalne

```bash
python -m tools.build_release --build-archives
python -m tools.zenodo_draft
```

Drugie polecenie sprawdza obecność trzech archiwów i `data-manifest.json`,
sumuje ich rozmiar oraz zapisuje `publication/zenodo-metadata.json`.

Zenodo przyjmuje obecnie do 100 plików i łącznie 50 000 000 000 bajtów na
rekord. Przy większej liczbie plików zaleca ich spakowanie. Zobacz oficjalną
[instrukcję zarządzania plikami](https://help.zenodo.org/docs/deposit/manage-files/).

## Utworzenie nieopublikowanego draftu

Utwórz token wyłącznie z zakresem `deposit:write`, ustaw go w bieżącej sesji
powłoki jako `ZENODO_TOKEN`, a następnie wykonaj:

```bash
python -m tools.zenodo_draft --create-draft
```

Dodanie dużych archiwów może zostać wykonane od razu podczas tworzenia draftu:

```bash
python -m tools.zenodo_draft --create-draft --upload-files
```

Jeżeli draft utworzono wcześniej bez plików, uzupełnij ten sam rekord zamiast
tworzyć drugi. Identyfikator znajduje się w `release-artifacts/zenodo-draft.json`:

```bash
python -m tools.zenodo_draft --create-draft --deposition-id 1234567 --upload-files
```

Token nie może trafić do pliku, historii poleceń z wartością ani repozytorium.
Do prób można dodać `--sandbox`. Interfejs opiera się na oficjalnym
[Zenodo REST API](https://developers.zenodo.org/).

## DOI

Utworzony draft zwraca zarezerwowany DOI. Zenodo rejestruje go dopiero przy
publikacji; usunięcie draftu powoduje utratę rezerwacji. Szczegóły opisuje
[instrukcja rezerwowania DOI](https://help.zenodo.org/docs/deposit/describe-records/reserve-doi/).

Po akceptacji draftu wpisz DOI do repozytorium:

```bash
python -m tools.apply_reserved_doi --doi 10.5281/zenodo.XXXXXXXX
```

Polecenie aktualizuje także specyfikację budowy, więc ponowne wygenerowanie
manifestu nie usunie DOI. Gdy rekord otrzyma stały identyfikator, dodaj go, aby
wpisać publiczne adresy pobierania archiwów:

```bash
python -m tools.apply_reserved_doi \
  --doi 10.5281/zenodo.XXXXXXXX --record-id XXXXXXXX
```

Następnie ponownie zbuduj manifest. Jeżeli zawartość paczek nie uległa zmianie,
nie ma potrzeby ponownego kompresowania 11 GB danych; trzeba natomiast ponownie
przesłać `data-manifest.json` do draftu.

## Publikacja

Publikacja jest wykonywana ręcznie w interfejsie Zenodo dopiero po zamknięciu
wszystkich bramek z `PUBLICATION_APPROVAL.md`. Każdą późniejszą aktualizację
należy utworzyć jako nową wersję rekordu.
