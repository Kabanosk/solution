# Pytania do człowieka

Otwarte pytania czekają na briefing („jak tam” / `/house-status`). Po odpowiedzi status zmienia się na
`[answered YYYY-MM-DD]`, a decyzja trafia do `DECISIONS.md`.

Format wpisu: nagłówek drugiego poziomu `Q-<kolejny numer, 3 cyfry> [open] <tytuł>`, pod nim:

```
- **Kontekst:** co się stało i dlaczego to pytanie
- **Opcje:** A) … B) …
- **Rekomendacja:** … (jednym zdaniem dlaczego)
- **Blokuje:** T### | nic
- **Poziom:** krytyczny | zawsze człowiek
- **Termin:** YYYY-MM-DD HH:MM (tylko krytyczny: po tym czasie judge przyjmuje „Domyślnie”)
- **Domyślnie:** A (rekomendacja; pusta = brak domyślnej)
- **Odpowiedź:** A, doprecyzowane w grillu 2026-11-20 → D-002…D-012
```

## Q-001 [answered 2026-11-20] Zatwierdzenie intake i zakresu MVP
- **Kontekst:** spec.yaml wypełniony z challenge card, prezentacji Red Hat i whiteboardu. Wyzwanie wymaga min. jednego z: liquidity / FX / tax / investment; rekoncyliacja faktur sama nie jest na liście. Dane pizzerii nie mają faktur ani marketingu.
- **Opcje:** A) MVP = pętla rekoncyliacji (wyciąg ↔ faktury, szukanie na symulowanym dysku przez MCP, DM na Slacku) zasilająca agenta płynności (prognoza, czerwcowa dziura → propozycja linii kredytowej do zatwierdzenia), czat CEO na Slacku, prosty dashboard; marketing-anomalie jako drugi etap. Dane: pizzeria + dogenerowane faktury i wydatki marketingowe. B) wszystkie trzy części naraz. C) własna fikcyjna firma (np. SaaS) zamiast pizzerii.
- **Rekomendacja:** A — trafia w wymagany use case, jury zna dane pizzerii, reszta to rozszerzenia.
- **Blokuje:** plan (tasks)
- **Poziom:** zawsze człowiek
- **Domyślnie:** A
- **Odpowiedź:** A, doprecyzowane w grillu 2026-11-20 → D-002…D-012

## Q-002 [answered 2026-11-20] Czy wrzucamy CSV z wyciągiem pizzerii do publicznego repo?
- **Kontekst:** paczka od organizatora leży lokalnie w `challenge/` (wyłączona z gita; challenge card ma znacznik „For business use only”). Generator danych (T002) i seed na klastrze (T012) potrzebują tego CSV. Repo jest publiczne, a Red Hat je przegląda.
- **Opcje:** A) commit samego CSV do `data/source/` (dokument z danymi mówi „feel free to use this”) B) nie commitujemy; CSV trafia na klaster ręcznie (`oc create configmap --from-file`) C) zapytać organizatorów na Discordzie
- **Rekomendacja:** B teraz (zero ryzyka, 1 komenda), C równolegle; A dopiero po zgodzie
- **Blokuje:** T012 (seed na klastrze); T002 działa lokalnie z `SOURCE_CSV`
- **Poziom:** zawsze człowiek
- **Domyślnie:** B
- **Odpowiedź:** B — Grzesiu wgrał CSV i resztę sample data na klaster (PVC `sample-data` w `cfo-agent-uwur`, serwowane read-only przez `http://files:8080`; branch `infra`)
