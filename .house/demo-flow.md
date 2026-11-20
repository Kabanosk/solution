# Golden path (produkt) — Autonomous CFO for Pizzeria Amalfi

Demo/pitch: Paweł + Piotrek. Ten plik opisuje, co produkt musi umieć pokazać end-to-end. Dane: D-002.

Kto: Vincenzo (CEO/właściciel), Grażyna (office manager, firmowy folder + skrzynka), Rosa (szefowa kuchni, managerka kosztów jedzenia).

1. Start: baza ma historię 01–11.2025. Dashboard (front) pokazuje saldo, prognozę płynności 90 dni, koszty per zespół i 3 pary koszt↔metryka (D-006).
   API: `/v1/kpis`, `/v1/forecast/liquidity`, `/v1/costs/by-team`, `/v1/cost-metrics`.
2. Symulator puszcza grudzień. Przychodzi przelew „Invoice AS-69664 …” bez dokumentu w bazie.
   Agent (sam, poziom AUTO): szuka na komputerze Grażyny (MCP), znajduje PDF w `Downloads/`, wyciąga pozycje, kwota się zgadza → zapis, transakcja „udokumentowana”, DM do Grażyny: „wziąłem fakturę AS-69664 z Twojego folderu Downloads, dodałem do bazy”.
3. Następny przelew FLV-… — dokumentu nigdzie nie ma. DM do Grażyny z prośbą. Grażyna ma urlop (HR) → eskalacja do Vincenza od razu, z wyjaśnieniem (Belegpflicht, ryzyko podatkowe).
4. Faktura za chmurę apki: +50% m/m, zamówienia online +3% → alert do CEO (poziom NOTIFY) z wyjaśnieniem: która pozycja urosła, koszt/zamówienie.
5. Prognoza (XGBoost, MLflow) pokazuje, że w czerwcu 2026 saldo spadnie do ok. −20 tys. € (jak w 2025). Agent przygotowuje wniosek o linię kredytową (poziom APPROVAL): DM do CEO z wyjaśnieniem i przyciskami Zatwierdź/Odrzuć.
6. Vincenzo klika Zatwierdź → wykonanie oznaczone SIMULATED, wpis w historii decyzji, widoczny w API `/v1/decisions`.
7. Vincenzo pisze na Slacku: „sprzęt zawsze zatwierdzam sam” → nowa reguła w polityce, widoczna w `/v1/rules`.

Każdy krok pokazuje poziom: AUTO (robi sam) / NOTIFY (robi i informuje) / APPROVAL (czeka na zgodę) / SIMULATED (wykonanie).
