# Livello 1 -- calibrazione OD auto, stato per città

Nessuna correzione viene qui applicata ai file OD -- questo è il quadro di evidenza disponibile per città, con livello di confidenza esplicito. Vedi lo script per le fonti di ogni numero.

## Fatto chiave

Per le uniche due città con sondaggio di ambito confrontabile (Madrid, Sevilla: tutti gli spostamenti, tutte le finalità), la quota auto implicita dalla pipeline attuale è già entro pochi punti percentuali dal dato reale -- eppure quelle due città mostrano gap fra i più alti sui conteggi IMD reali. Questo è evidenza contro l'ipotesi che il problema principale sia la magnitudine della domanda auto, e a favore di una causa spaziale/di instradamento (vedi corridoi noti in `run_nomad_traffic_validation.py`-style matching).

## Per città

### palma_de_mallorca
- quota auto implicita: 34.1%
- sondaggio reale: 70.0% (PDSMIB (Consell de Mallorca), 2018) -- ambito: solo spostamenti lavoro [non confrontabile 1:1]
- rapporto IMD reale/simulato: 1.5x su Via de Cintura
- motorizzazione DGT: 821.0 veic./1000 ab.
- **livello: parziale (solo conteggio IMD, segnale composito)**
- nota: rapporto reale/simulato 1.5x su Via de Cintura -- NON è un fattore di correzione pulito della quota auto: riflette anche instradamento, traffico di attraversamento e merci (assenti da MITMA). Non usare direttamente come moltiplicatore della domanda.

### madrid
- quota auto implicita: 29.5%
- sondaggio reale: 39.0% (EDM2018 (CRTM, crtm.es/media/987215/edm18_doc4_aspectos_modales.pdf), 2018) -- ambito: tutti gli spostamenti, Comunidad de Madrid [CONFRONTABILE]
- rapporto IMD reale/simulato: 8.3x su M-30 (La Paz + A-3)
- motorizzazione DGT: 530.6 veic./1000 ab.
- **fattore di correzione (da sondaggio): 1.32x**
- **livello: calibrato (sondaggio, correzione applicabile)**
- nota: sondaggio reale (39.0%) si discosta da quella implicita (29.5%) di +9.5 punti -- fattore di correzione 1.32x applicabile alla domanda auto.

### valencia
- quota auto implicita: 27.5%
- sondaggio reale: 41.0% (PMOME -- fonte SECONDARIA, non verificata sul PDF primario, 2018) -- ambito: presunti tutti gli spostamenti, ma fonte non primaria [non confrontabile 1:1]
- rapporto IMD reale/simulato: 6.2x su Ronda Nord/Sud
- motorizzazione DGT: 599.1 veic./1000 ab.
- **livello: parziale (solo conteggio IMD, segnale composito)**
- nota: rapporto reale/simulato 6.2x su Ronda Nord/Sud -- NON è un fattore di correzione pulito della quota auto: riflette anche instradamento, traffico di attraversamento e merci (assenti da MITMA). Non usare direttamente come moltiplicatore della domanda.

### murcia
- quota auto implicita: 57.6%
- motorizzazione DGT: 755.6 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (755.6 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.

### sevilla
- quota auto implicita: 64.1%
- sondaggio reale: 40.0% (PMUS Sevilla 2030, 2021) -- ambito: tutti gli spostamenti dei residenti [CONFRONTABILE]
- motorizzazione DGT: 698.0 veic./1000 ab.
- **fattore di correzione (da sondaggio): 0.62x**
- **livello: calibrato (sondaggio, correzione applicabile)**
- nota: sondaggio reale (40.0%) si discosta da quella implicita (64.1%) di -24.1 punti -- fattore di correzione 0.62x applicabile alla domanda auto.

### cordoba
- quota auto implicita: 54.5%
- motorizzazione DGT: 712.9 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (712.9 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.

### zaragoza
- quota auto implicita: 28.1%
- sondaggio reale: 42.2% (PMUS Zaragoza (zaragoza.es/contenidos/bici/plan/CAPITULO05.pdf), 2017) -- ambito: periferia -- ambiguo se confrontabile con tutta la città [non confrontabile 1:1]
- motorizzazione DGT: 551.1 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (551.1 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.

### valladolid
- quota auto implicita: 17.7%
- motorizzazione DGT: 607.6 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (607.6 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.

### granada
- quota auto implicita: 58.2%
- motorizzazione DGT: 749.9 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (749.9 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.

### bilbao
- quota auto implicita: 20.6%
- sondaggio reale: 11.0% (PMUS Bilbao (encuesta Gobierno Vasco 2011), 2016) -- ambito: mobilità interna (fino a 0.40 per spostamenti lavoro, soli uomini) -- ambiti misti, non confrontabili [non confrontabile 1:1]
- rapporto IMD reale/simulato: 3.6x su A-8 (Basauri)
- motorizzazione DGT: 534.5 veic./1000 ab.
- **livello: parziale (solo conteggio IMD, segnale composito)**
- nota: rapporto reale/simulato 3.6x su A-8 (Basauri) -- NON è un fattore di correzione pulito della quota auto: riflette anche instradamento, traffico di attraversamento e merci (assenti da MITMA). Non usare direttamente come moltiplicatore della domanda.

### a_coruna
- quota auto implicita: 31.5%
- rapporto IMD reale/simulato: 12.9x su Alfonso Molina (AC-11)
- motorizzazione DGT: 568.6 veic./1000 ab.
- **livello: parziale (solo conteggio IMD, segnale composito)**
- nota: rapporto reale/simulato 12.9x su Alfonso Molina (AC-11) -- NON è un fattore di correzione pulito della quota auto: riflette anche instradamento, traffico di attraversamento e merci (assenti da MITMA). Non usare direttamente come moltiplicatore della domanda.

### barcelona
- quota auto implicita: 14.5%
- motorizzazione DGT: 532.9 veic./1000 ab.
- **livello: non calibrato (nessuna evidenza locale diretta)**
- nota: nessun sondaggio di mode-share né conteggio IMD reale trovato per questa città in questo progetto. Indice di motorizzazione DGT disponibile (532.9 veic./1000 ab.) ma la correlazione con il gap osservato nelle 5 città con dato reale è debole (r=-0.58, n=5) -- riportato come contesto, NON usato per stimare un fattore di correzione.
