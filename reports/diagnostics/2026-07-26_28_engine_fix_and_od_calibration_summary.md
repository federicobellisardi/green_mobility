# Riepilogo: fix motore NOMAD + calibrazione OD Livello 2 (26-28 luglio 2026)

Documento di sintesi di tutto il lavoro svolto in questa sessione, dalla
diagnosi iniziale (validazione traffico reale su Palma) fino alla
ricalibrazione della domanda OD per 8 città. Nessun numero qui è inventato:
tutti i valori sono stati misurati direttamente o citati da fonti reali già
presenti nel repository (vedi le sezioni "fonte" per ciascun dato).

## 1. Punto di partenza

Una validazione contro il traffico reale (conteggio IMD sul Via de Cintura,
Palma) mostrava un volume giornaliero simulato implausibile (~150% della
capacità teorica). La stessa anomalia si presentava sia con
`QueueTrafficModel` che con `LtmTrafficModel` — segno che il problema non
era nella formula di delay (BPR) ma in qualcosa di più a monte.

## 2. Bug reali trovati e corretti nel motore NOMAD (branch → `main`)

Tutti confermati con test dedicati, non solo ipotizzati. Toccano
esclusivamente `LtmTrafficModel` (Cell Transmission Model) — vedi punto 5
per perché questo import ha meno effetto del previsto sulla pipeline di
produzione attuale.

| Fase | Bug | Fix |
|---|---|---|
| **1** | `handle_depart()` iniettava il primo arco di un viaggio senza mai controllare `has_capacity()` — un arco poteva ricevere occupazione oltre la sua `storage_veh` | Aggiunto `ITrafficModel::has_capacity()`, gate su `handle_depart()`, retry a `t+1s` se rifiutato |
| **2** | Stesso bug, ma su OGNI transizione arco-arco (`handle_exit_link()`): l'evento di ingresso veniva accodato invece che processato subito, quindi agenti da archi diversi convergenti sullo stesso arco nello stesso batch vedevano tutti occupazione stantia e passavano tutti il check | Ingresso reso sincrono (`enter_edge_now()`), stesso meccanismo di Fase 1 |
| **3** | Nessun vincolo sulla *velocità di scarico* (discharge rate) — solo la capacità di stoccaggio era limitata, non il flusso in uscita | Token bucket opt-in su `LtmTrafficModel::on_exit()` (`enable_discharge_cap`, default `false`) — un tentativo con parametro troppo stretto (`burst_s=1`) ha riprodotto il gridlock storico già documentato ed è stato scartato |
| **4** | Il segnale di congestione (BPR, basato su `occupancy/storage_veh`) non poteva mai superare una soglia bassa una volta che le Fasi 1-2 limitavano correttamente l'occupazione — quindi il rerouting non vedeva mai il vero collo di bottiglia da discharge-rate | Aggiunto `gate_wait_s`: segnale additivo che traccia da quanto tempo il gate di scarico blocca continuativamente le uscite, alimentando `travel_time_s`/`congestion_ema` e quindi `schedule_reroutes()` |

**Verifica empirica (Palma, sweep di scala domanda 0.4-1.0)**: con Fase 4,
teleport crollato del 98-99% a scala bassa/media (dove la rete ha margine
per deviare il traffico), miglioramento quasi nullo a scala piena (rete già
satura ovunque — atteso, non un fallimento). Nessuna ricomparsa del
gridlock storico a nessuna delle due impostazioni testate.

Merge locale in `main` del submodule NOMAD (`external/nomad`), autorizzato
esplicitamente dall'utente. **Non pushato su GitHub** — resta locale su
questa macchina (nessun remote configurato per il repo `green_mobility`
stesso; il repo `nomad` ha un remote GitHub ma non è stato pushato).

## 3. La scoperta critica: la produzione usa un modello diverso

Verificato controllando le config reali di produzione (`weekday_full_car_2022-02-08.json`
per Palma, Bilbao, Murcia): **tutte e 12 le città usano `"traffic_model": "queue"`,
non `"ltm"`**. Tutto il lavoro di Fase 1-4 sopra riguarda solo `LtmTrafficModel`.

Ispezionando `QueueTrafficModel::on_exit()`: il parametro `next` (l'arco
successivo) è letteralmente ignorato — nessun controllo di spillback, mai.
Il commento nel codice stesso lo dichiara: *"A proper kinematic-wave or CTM
model is needed before re-enabling this [discharge constraint]"* — un
limite noto e intenzionale, non un bug. `LtmTrafficModel` **è** quel
modello CTM, ma non è mai stato collegato alla pipeline di produzione.

Conferma indipendente trovata in uno script diagnostico preesistente
(`scripts/diagnostics/run_nomad_traffic_validation.py`, mai eseguito da me
prima di questa sessione ma già presente nel repo): anche a domanda 4x,
`QueueTrafficModel` mostra teleport ~0% mentre la congestione BPR cresce
regolarmente — la sua stessa funzione di classificazione automatica lo
etichetta "limite noto, non un bug".

## 4. Confronto queue vs ltm+fix su 3 città (soglie di produzione reali)

Stessa data (2022-02-08), stesse soglie di teleport reali
(`stuck_threshold_ratio=50`, `stuck_max_hours=8`):

| Città | Agenti | **queue** (produzione attuale) | **ltm+fix** (motore corretto) |
|---|---|---|---|
| Palma | 584.955 | arrivo 96,4%, teleport ~0% | arrivo naturale 36,5%, teleport 36,6%, bloccati a mezzanotte 26,9% |
| Bilbao | 562.293 | arrivo 95,5%, teleport ~0% | arrivo naturale 37,6%, teleport 34,4%, bloccati a mezzanotte 28,0% |
| Murcia | 926.342 | arrivo 94,3%, teleport ~0% | arrivo naturale 26,0%, teleport 47,6%, bloccati a mezzanotte 26,4% |

Pattern identico e sistemico sulle 3 città: con `queue`, quasi tutti i
viaggi arrivano; con `ltm+fix` (vincoli di capacità genuini), solo un
quarto-un terzo arriva naturalmente, un terzo-metà viene teleportato, e
circa un quarto degli agenti resta bloccato in rete a fine giornata.

Controllo diretto sul Via de Cintura (stessi 42 archi, metodo
Little's-Law integrato sull'intera giornata): utilizzo aggregato passato
da 141,3% (queue) a 236,4% (ltm+fix) — il fix del motore **non** ha
avvicinato la simulazione alla realtà, l'ha allontanata. Il problema non
era (solo) nel motore.

## 5. Cause a monte identificate

### 5.1 Evidenza pre-esistente nel repo (Livello 1, `calibrate_od_level1.py`)

Script già presente prima di questa sessione, con dati reali sourced
(sondaggi di mobilità, conteggi IMD, motorizzazione DGT). Conclusione
chiave già documentata: per le uniche due città con sondaggio pienamente
confrontabile (Madrid, Sevilla), la quota auto implicita era già vicina al
target reale — eppure quelle stesse due città mostravano i gap più alti
sui conteggi IMD reali. Evidenza **contro** "la domanda è troppo alta" e
**a favore** di una sotto-concentrazione spaziale/di instradamento (il
traffico reale è sempre più concentrato sui corridoi principali di quanto
lo sia il simulato, su tutti i 5 corridoi misurati — mai il contrario).

### 5.2 Causa specifica trovata questa sessione: quota transit non calibrata

`external/nomad/python/preprocessing/build_od.py`: la quota di trasporto
pubblico viene decisa da `DISTANCE_MODE_FRACTIONS`, una tabella **fissa,
nazionale**, per fascia di distanza (20%/35%/18%/10%) — mai calibrata per
città. `CITY_SHIFT` (`mode_choice.py`) calibra solo la ripartizione
auto/piedi/bici *tra i viaggi non-transit*, ma l'intera catena
(`quota_auto_finale = P(auto|non-transit) × (1-quota_transit)`) non era
mai stata verificata end-to-end: confermato rigenerando l'OD di Madrid
(bit-per-bit riproducibile, verificato via md5sum), quota transit
risultante 26,8% (artefatto della tabella fissa), quota auto finale 29,5%
contro il target reale citato dallo stesso `CITY_SHIFT` (39,0%, sondaggio
OMM 2018, confrontabile).

## 6. Livello 2: calibrazione quota transit per città (implementato)

Fonte reale: `reference/modal_split/2025-Resumen-2023-24-ENG.pdf`, pag. 5
("Modal share for all trip purposes") — la STESSA riga/fonte già citata da
`CITY_SHIFT` per il target auto delle 8 città "OMM calibrato". Estratta e
verificata sia da immagine renderizzata che da `pdftotext -layout`.

**Modifiche**:
- `mode_choice.py`: nuovo `CITY_TRANSIT_SHARE` (8 città con dato reale).
- `build_od.py` (branch `feature/logit-mode-choice`, submodule NOMAD):
  nuovo parametro opzionale `transit_frac_override`, sostituisce la quota
  transit fissa con il valore reale città-specifico quando fornito,
  riscalando auto/piedi/bici proporzionalmente. Default `None` = nessun
  cambiamento per le città non coperte.
- `scripts/diagnostics/calibrate_transit_share_level2.py` (nuovo): per le
  8 città, risolve numericamente (root-finding, `scipy.optimize.brentq`)
  un nuovo `CITY_SHIFT` scalare che riproduce il target auto reale una
  volta fissata la vera quota transit — usando la distribuzione di
  distanza REALE MITMA di ciascuna città, non un'assunzione sintetica.
- `mode_choice.py`: `CITY_SHIFT` aggiornato con gli 8 nuovi valori
  ricalcolati.
- `build.py` (`run_build_od`): passa `CITY_TRANSIT_SHARE.get(city.slug)`
  come `transit_frac_override`.

**Città esplicitamente NON toccate** (nessuna evidenza reale disponibile,
niente inventato): Cordoba, Granada, Sevilla (calibrate via ESOC11 — il
dato transit grezzo esiste probabilmente nel file originale ma manca il
codebook IECA per leggerlo in modo affidabile; verificato: nessuna
posizione di colonna è un separatore garantito in tutti i 17.190 record,
indovinare l'offset avrebbe rischiato un numero inventato) e Murcia
(nessuna fonte reale trovata, già segnalato come tale in `CITY_SHIFT`
prima di questa sessione).

### Risultato verificato (OD rigenerato per tutte e 8, con backup)

| Città | Auto: prima→dopo | Target | Scarto | Transit: prima→dopo | Target | Scarto |
|---|---|---|---|---|---|---|
| Madrid | 29,5%→38,4% | 39,0% | -0,57pp | 25,4%→24,7% | 24,3% | +0,42pp |
| Barcelona | 14,5%→19,8% | 19,7% | +0,09pp | 25,2%→23,4% | 23,1% | +0,35pp |
| Valencia | 27,5%→41,8% | 41,3% | +0,47pp | 26,5%→13,6% | 13,6% | +0,01pp |
| Bilbao | 20,6%→31,2% | 31,5% | -0,32pp | 26,0%→20,7% | 20,2% | +0,49pp |
| Palma | 34,1%→53,2% | 52,8% | +0,40pp | 26,8%→6,9% | 7,2% | -0,26pp |
| Zaragoza | 28,1%→42,7% | 42,7% | +0,01pp | 26,6%→15,4% | 15,3% | +0,05pp |
| Valladolid | 17,7%→30,1% | 30,0% | +0,08pp | 27,4%→13,0% | 13,1% | -0,10pp |
| A Coruña | 31,5%→49,4% | 49,2% | +0,22pp | 27,4%→12,2% | 12,2% | +0,04pp |

Da scarti di 10-19 punti percentuali a scarti entro ±0,6pp su tutte e 8 le
città.

OD originali salvati in
`/tmp/.../scratchpad/od_backup_pre_level2/` (percorso temporaneo di
sessione, non permanente — se serve un backup duraturo, va copiato altrove).

## 7. Stato attuale dei commit/branch

- **`green_mobility`** (repo principale, **nessun remote configurato**):
  commit `42ccd48` con le 4 modifiche di oggi (mode_choice.py, build.py,
  calibrate_transit_share_level2.py, puntatore submodule NOMAD → `b5990ac`).
  Nota: gran parte del resto del repo (`src/`, `scripts/`, `reports/`,
  `python/`, ecc.) non era mai stata committata prima d'ora — non toccata
  in questo commit, resta non tracciata.
- **`external/nomad`**: branch `main` ha i fix di Fase 1-4 (commit
  `b5990ac`), branch `feature/logit-mode-choice` ha in più il commit per
  `transit_frac_override`. Nessuno dei due pushato su GitHub — solo
  locale su questa macchina. Il checkout attuale è su `main` (necessario
  per i job SLURM lanciati con `--traffic-model ltm`).

## 8. Job SLURM lanciati

8 job `nomad_run_ltm` (Madrid, Barcelona, Valencia, Bilbao, Palma,
Zaragoza, Valladolid, A Coruña), scenario `weekday_full_car`, data
2022-02-08, `--traffic-model ltm` (Fase 1-2 attive; discharge cap Fase 3-4
non esposto da CLI, quindi non attivo in questi run — solo i gate di
capacità corretti). Girano dalla stessa home NFS condivisa
(`nfshome.ifisc.lan`), quindi vedono automaticamente OD rigenerato e
`nomad_cli` ricompilato, senza bisogno di alcun push.

## 9. Aperto / non risolto

1. **Cordoba, Granada, Sevilla, Murcia**: quota transit resta sulla
   tabella nazionale fissa, sbagliata per queste città — gap noto, non
   corretto in questa sessione.
2. **Discharge cap (Fase 3-4) non esposto via CLI** — i job appena
   lanciati usano solo Fase 1-2. Se serve il segnale completo, va aggiunto
   un flag a `gm run` o passata una config custom.
3. **Perché il traffico reale sia sempre più concentrato di quello
   simulato** (rapporto IMD 1,5x-12,9x su tutti i corridoi misurati) resta
   una domanda aperta — ipotesi principale: sotto-concentrazione spaziale
   dovuta al routing stocastico (`sigma=0.08`), mai verificata a fondo.
4. **`external/nomad` main non pushato su GitHub** — solo locale, nessun
   backup remoto del lavoro di Fase 1-4.
5. Va deciso se/quando estendere Livello 2 alle 4 città rimanenti (serve
   il codebook IECA per ESOC11, o nuove fonti reali per Murcia).
