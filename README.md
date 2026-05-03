# Audio Forensics GUI

> Strumento Python con interfaccia grafica per l'**analisi forense automatica di file audio**. Pensato per investigatori digitali, periti forensi e chiunque necessiti di verificare l'integrità e l'autenticità di registrazioni audio.

---

## Indice

- [Descrizione](#descrizione)
- [Funzionalità principali](#funzionalità-principali)
- [Analisi implementate](#analisi-implementate)
- [Interfaccia grafica](#interfaccia-grafica)
- [Output generati](#output-generati)
- [Formati supportati](#formati-supportati)
- [Requisiti di sistema](#requisiti-di-sistema)
- [Installazione](#installazione)
- [Avvio](#avvio)
- [Utilizzo passo per passo](#utilizzo-passo-per-passo)
- [Compilazione in EXE distribuibile](#compilazione-in-exe-distribuibile)
- [Risoluzione problemi](#risoluzione-problemi)
- [Struttura del progetto](#struttura-del-progetto)
- [Roadmap](#roadmap)

---

## Descrizione

`audio_forensics_gui.py` è un'applicazione desktop standalone che esegue un'analisi forense completa su file audio digitali. L'obiettivo principale è rilevare eventuali manipolazioni, alterazioni, clipping, giunte anomale o incongruenze tra metadati e contenuto reale del file.

Il software è progettato per l'uso professionale in ambito forense: ogni analisi produce un **verdetto di integrità** espresso su una scala da 0 a 100, accompagnato da un report HTML tematico in formato dark e da un dump JSON con tutti i dati grezzi.

L'interfaccia è stata realizzata con **Tkinter** in stile Windows 11 dark, con pannelli ridimensionabili, tab multipli, log in tempo reale a colori e visualizzazione dei grafici direttamente nella finestra dell'applicazione.

---

## Funzionalità principali

- Analisi forense multi-file in parallelo con barra di avanzamento
- Calcolo degli hash crittografici (MD5, SHA-1, SHA-256) con supporto a file di grandi dimensioni (calcolo a blocchi)
- Verifica dei magic bytes per rilevare rinomina fraudolenta dell'estensione
- Parsing diretto degli header OGG/Vorbis/Opus senza dipendenze esterne
- Estrazione metadati embedded (ID3, Vorbis Comment, FLAC Tags, MP4 Atoms)
- Analisi completa della forma d'onda con rilevamento automatico delle anomalie
- Analisi spettrale con MFCC, centroide, bandwidth e rolloff
- Report forense HTML con tema dark e verdetto INTEGRO / SOSPETTO / NON ATTENDIBILE
- Esportazione JSON con dump grezzo di tutti i parametri
- Generazione automatica di 5 grafici per ogni file analizzato
- Verifica della catena di custodia tramite confronto hash

---

## Analisi implementate

### Hash crittografici
Vengono calcolati MD5, SHA-1 e SHA-256 tramite lettura a blocchi da 64 KB, garantendo la corretta gestione di file audio di grandi dimensioni senza saturare la memoria.

### Magic bytes
Il software legge i primi byte del file e li confronta con le signature note per ogni formato (WAV, MP3, OGG, FLAC, M4A, AIFF, ecc.). Se l'estensione dichiarata non corrisponde al formato reale, viene generato un avviso forense come potenziale indizio di manomissione.

### Header OGG raw
Per i file con estensione `.ogg`, `.oga` e `.opus`, viene eseguito il parsing diretto delle pagine OGG e dell'header Vorbis/Opus senza ricorrere a librerie esterne, estraendo parametri quali version, audio channels, sample rate, bitrate e vendor string.

### Metadati embedded
Tramite la libreria **Mutagen**, vengono estratti tutti i tag incorporati nel file: supporta ID3v1/v2 (MP3), Vorbis Comment (OGG/FLAC), FLAC Tags, MP4 Atoms (M4A/AAC) e AIFF metadata. Vengono riportati titolo, artista, album, data, encoder e qualsiasi tag personalizzato.

### Analisi della forma d'onda
Vengono calcolati i seguenti parametri statistici sull'intera forma d'onda:

- **RMS** (Root Mean Square): valore di potenza media del segnale
- **Peak**: valore massimo assoluto dei campioni
- **DC Offset**: spostamento del segnale dal valore zero, indicatore di problemi hardware
- **Crest Factor**: rapporto tra picco e RMS, indicatore della dinamica del segnale
- **Dynamic Range**: differenza in dB tra il massimo e il livello di rumore
- **Kurtosis**: misura della curtosi della distribuzione dei campioni (rileva impulsi anomali)
- **Skewness**: asimmetria della distribuzione dei campioni
- **ZCR** (Zero Crossing Rate): tasso di attraversamento dello zero, utile per classificare il tipo di segnale

### Rilevamento automatico delle anomalie
Il sistema analizza automaticamente la presenza di:

- **Clipping**: campioni con valore assoluto >= 0.999, indicativi di saturazione della registrazione
- **DC Offset significativo**: valore medio del segnale superiore a una soglia critica
- **Silenzi anomali**: segmenti di silenzio con durata superiore a 1 secondo, con riportato il timestamp esatto; possibile indizio di taglio e giuntura della registrazione
- **Discontinuità brusca di ampiezza**: variazioni improvvise del livello RMS tra finestre temporali adiacenti, potenziale splice point (punto di montaggio)
- **Kurtosis fuori range**: valori di curtosi anomalmente elevati o bassi, indicativi di click, impulsi o artefatti di compressione

### Analisi spettrale
Tramite la libreria **librosa** vengono calcolati:

- **Centroide spettrale**: frequenza baricentrica del contenuto spettrale
- **Bandwidth spettrale**: larghezza di banda del segnale intorno al centroide
- **Rolloff spettrale**: frequenza al di sotto della quale è concentrata una percentuale fissa di energia
- **MFCC** (Mel-Frequency Cepstral Coefficients): 13 coefficienti cepstrali su scala Mel, standard per la caratterizzazione timbrica del segnale audio

---

## Interfaccia grafica

L'applicazione è costruita con **Tkinter** e si presenta in stile **Windows 11 dark** con i seguenti componenti:

### Barra dei menu
Contiene i menu principali: **File** (aggiungi file/cartella, pulisci lista, esci), **Analisi** (avvia/interrompi), **Report** (apri HTML, apri cartella output), **?** (informazioni, guida rapida).

### Toolbar
Pulsanti ad accesso rapido: **Aggiungi file**, **Aggiungi cartella**, **Analizza**, **Stop**, **Report**, **Output** e un campo di testo per specificare rapidamente il percorso della cartella di destinazione.

### Pannello sinistro — Lista file
Un Treeview Tkinter mostra i file in coda con le seguenti colonne: nome file, dimensione, formato, stato. Le icone di stato evolvono in tempo reale durante l'analisi:

- In attesa
- In elaborazione
- Analisi completata con successo
- Analisi completata con avvertimenti
- File sospetto o non attendibile

Un **menu contestuale** (tasto destro) permette di rimuovere file, aprire il report individuale o copiare il percorso. Il doppio clic apre direttamente il report HTML associato.

### Pannello destro — 4 Tab principali

**Tab Sommario:** mostra il banner del verdetto colorato (verde/giallo/rosso), il punteggio di integrità su 100, quattro card rapide con i valori principali (durata, sample rate, picco, RMS) e la tabella delle anomalie rilevate con relativa descrizione.

**Tab Grafici:** visualizza direttamente nella finestra i 5 grafici Matplotlib generati per ogni file:
1. Forma d'onda (ampiezza nel tempo)
2. Spettrogramma (frequenza nel tempo)
3. RMS nel tempo (energia istantanea)
4. FFT (spettro di frequenza in magnitudine)
5. Distribuzione dell'ampiezza (istogramma)

**Tab Dettagli:** tabella completa di tutti i parametri numerici estratti, suddivisi per categoria: informazioni file, parametri forma d'onda, parametri spettrali, header OGG raw.

**Tab Hash & Metadati:** mostra i valori SHA-1, SHA-256 e MD5 con pulsante Copia, un campo di testo per inserire un hash di riferimento e confrontarlo ai fini della **verifica della catena di custodia**, e la lista completa dei metadati embedded.

### Log Console
Pannello inferiore con output colorato in tempo reale:
- Verde: operazioni completate con successo
- Arancione: avvisi e anomalie rilevate
- Rosso: errori critici
- Azzurro: informazioni di stato e progresso

### Barra di stato
In fondo alla finestra mostra informazioni sul numero di file in coda, lo stato dell'analisi corrente e informazioni sulla piattaforma.

---

## Output generati

Per ogni file analizzato, il software produce tre file di output nella cartella specificata:

| File | Contenuto |
|------|-----------|
| `nomefile_forensics.png` | Immagine con i 5 grafici di analisi |
| `nomefile_report.html` | Report forense dark-themed con verdetto e score /100 |
| `nomefile_forensics.json` | Dump grezzo di tutti i dati e parametri estratti |

Il report HTML contiene: intestazione forense con data/ora e hash del file, banner del verdetto (INTEGRO / SOSPETTO / NON ATTENDIBILE), score di integrità visuale, sezione anomalie, parametri tecnici completi e grafico embeddato come immagine base64.

---

## Formati supportati

| Formato | Estensione | Note |
|---------|------------|------|
| WAV | `.wav` | Supporto nativo completo |
| MP3 | `.mp3` | Richiede FFmpeg o pydub |
| OGG Vorbis | `.ogg`, `.oga` | Header raw parsing |
| Opus | `.opus` | Header raw parsing |
| FLAC | `.flac` | Supporto nativo completo |
| M4A / AAC | `.m4a` | Tramite librosa |
| AIFF | `.aiff`, `.aif` | Tramite soundfile |

---

## Requisiti di sistema

- Python 3.8 o superiore
- Windows 10 / 11 (64-bit raccomandato)
- Tkinter (incluso nella distribuzione standard di Python per Windows)
- Almeno 4 GB di RAM consigliati per file audio di grandi dimensioni
- FFmpeg installato e aggiunto al PATH (necessario per MP3 e alcuni formati compressi)

---

## Installazione

### 1. Clona il repository

```bash
git clone https://github.com/marcko80/audio_forensics_gui.git
cd audio_forensics_gui
```

### 2. Installa le dipendenze Python

```bash
pip install librosa soundfile mutagen matplotlib numpy scipy
```

### 3. (Facoltativo) Installa FFmpeg

Per la decodifica di file MP3 e altri formati compressi, scarica FFmpeg da https://ffmpeg.org e aggiungilo al PATH di sistema.

---

## Avvio

```bash
python audio_forensics_gui.py
```

All'avvio si apre la finestra principale dell'applicazione in stile dark. Non sono necessarie configurazioni aggiuntive.

---

## Utilizzo passo per passo

1. **Aggiunta dei file**: clicca su Aggiungi file nella toolbar per selezionare uno o più file audio singoli, oppure su Aggiungi cartella per importare tutti i file audio presenti in una cartella (ricerca non ricorsiva).

2. **Verifica della lista**: i file vengono elencati nel pannello sinistro con nome, dimensione e formato. E possibile rimuovere file indesiderati tramite il menu contestuale (tasto destro).

3. **Impostazione cartella output**: nel campo di testo della toolbar e possibile specificare la cartella dove salvare i report. Se non specificata, i file di output vengono salvati nella stessa cartella del file originale.

4. **Avvio analisi**: clicca su Analizza o usa il menu Analisi. L'analisi viene eseguita in un thread separato per mantenere l'interfaccia reattiva. Il log console mostra il progresso in tempo reale.

5. **Lettura dei risultati**: al termine dell'analisi di ciascun file, lo stato nella lista si aggiorna con l'icona corrispondente. Clicca sul file per visualizzarne i risultati nei tab del pannello destro.

6. **Apertura del report**: doppio clic sul file nella lista o pulsante Report nella toolbar per aprire il report HTML nel browser predefinito.

7. **Verifica catena di custodia**: nel tab Hash e Metadati, incolla un hash di riferimento nel campo apposito e clicca Confronta hash per verificare l'integrita rispetto a un valore precedentemente registrato.

8. **Interruzione**: e possibile interrompere l'analisi in corso cliccando su Stop. I file gia analizzati mantengono i risultati.

---

## Compilazione in EXE distribuibile

Per distribuire il programma su macchine Windows senza richiedere l'installazione di Python, e possibile compilarlo in un eseguibile autonomo tramite **PyInstaller**.

### Installazione di PyInstaller

```bash
pip install pyinstaller
```

### Compilazione rapida

```bash
pyinstaller --onefile --windowed --name AudioForensicsAnalyzer audio_forensics_gui.py
```

L'opzione --windowed sopprime la finestra console nera. Il file .exe risultante si trova in dist/.

Nota: con --onefile l'avvio e piu lento (5-10 secondi) perche il programma decomprime i moduli in una directory temporanea. Per uso forense professionale e consigliato usare la modalita cartella (--onedir) per avvii piu rapidi.

### Struttura output distribuibile

```
dist/
  AudioForensicsAnalyzer/
    AudioForensicsAnalyzer.exe
    _internal/
```

Comprimi la cartella AudioForensicsAnalyzer in uno ZIP e distribuiscila su qualsiasi Windows 10/11 senza richiedere Python installato.

---

## Risoluzione problemi

| Problema | Soluzione |
|----------|-----------|
| ModuleNotFoundError: librosa a runtime | Aggiungi librosa agli hiddenimports nel file .spec di PyInstaller |
| Finestra console nera all'avvio | Usa --windowed o imposta console=False nello .spec |
| Errore con numba/llvmlite | Esegui pip install numba prima del build |
| L'antivirus blocca l'exe | Firma il binario con un certificato code signing |
| Dimensione exe superiore a 300 MB | Aggiungi excludes=['PyQt5','wx'] nello spec e usa UPX |
| File MP3 non riconosciuti | Installa FFmpeg e aggiungilo al PATH di sistema |
| Tkinter non trovato | Reinstalla Python selezionando l'opzione tcl/tk and IDLE durante il setup |

---

## Struttura del progetto

```
audio_forensics_gui/
|-- audio_forensics_gui.py     # Script principale (unico file da distribuire)
|-- README.md                  # Documentazione del progetto
|-- .gitignore                 # File ignorati da Git (Python standard)
```

---

## Licenza

Leggasi sezione LICENSE

---

*Progetto sviluppato per uso forense professionale. L'autore non si assume responsabilita per utilizzi impropri del software o per interpretazioni errate dei risultati prodotti.*


---

## Roadmap

Questa sezione documenta le future implementazioni pianificate per il progetto, organizzate per fasi di sviluppo.

---

### Fase 2 — Robustezza e Qualità (breve termine)

**v1.1 — Stabilità e compatibilità**

- Supporto ricorsivo alle cartelle (attualmente la ricerca è non ricorsiva)
- - Supporto nativo MP3 senza FFmpeg obbligatorio tramite `pydub` con fallback automatico
  - - Gestione errori più granulare con messaggi localizzati in italiano
    - - Progress bar per singolo file oltre che per il batch complessivo
      - - Test automatizzati (unittest/pytest) su campioni audio sintetici
        - - Aggiunta file `.spec` PyInstaller pre-configurato nel repository
         
          - **v1.2 — Estensione formati**
         
          - - Supporto WebM/Ogg con traccia Opus
            - - Supporto AAC nativo senza passare da contenitore M4A
              - - Supporto WMA (forense su materiale Windows legacy)
                - - Supporto AMR (telefonia, intercettazioni)
                 
                  - ---

                  ### Fase 3 — Analisi Forense Avanzata (medio termine)

                  **v2.0 — Motore di analisi potenziato**

                  - **Rilevamento steganografia audio**: analisi LSB (Least Significant Bit) per individuare payload nascosti in file WAV/FLAC
                  - - **Analisi ENF (Electric Network Frequency)**: confronto della frequenza di rete (50/60 Hz) registrata ambientalmente per geolocalizzare o datare una registrazione
                    - - **Rilevamento voce sintetica / deepfake audio**: integrazione con modelli leggeri (ONNX) per distinguere voce umana da TTS o voice cloning
                      - - **Speaker diarization**: identificazione automatica dei turni di parola e del numero di speaker distinti nel file
                        - - **Analisi doppia compressione (double compression)**: rilevamento di file MP3 ricodificati (artifact ghosting), indizio di manomissione
                          - - **Confronto inter-file**: rilevamento di sovrapposizioni, copie parziali o rielaborazioni dello stesso contenuto originale
                           
                            - **v2.1 — Report e Chain of Custody**
                           
                            - - Generazione report in formato **PDF firmato digitalmente** (reportlab + firma PKCS#7)
                              - - Log immutabile con timestamp certificato tramite TSA (Time Stamping Authority)
                                - - Sezione "note del perito" editabile nell'applicazione prima dell'export
                                  - - Supporto export in formato **DFXML** (Digital Forensics XML) per interoperabilità con Autopsy e FTK
                                   
                                    - ---

                                    ### Fase 4 — Architettura e Distribuzione (lungo termine)

                                    **v3.0 — Refactoring architetturale**

                                    - Separazione netta tra engine forense (`core/`) e GUI (`ui/`) per uso headless
                                    - - Modalità **CLI** completa: `audio_forensics --input file.wav --output ./report --format json,html,pdf`
                                      - - Plugin system per moduli di analisi esterni senza modificare il core
                                        - - Configurazione tramite file YAML/JSON (soglie anomalie, parametri analisi, formato output)
                                          - - Database SQLite locale per storico delle analisi e ricerca per hash
                                           
                                            - **v3.1 — Interfaccia e UX**
                                           
                                            - - Migrazione GUI da Tkinter a **CustomTkinter** o **PyQt6** per un look più moderno
                                              - - Tema chiaro/scuro selezionabile dall'utente
                                                - - Visualizzatore audio integrato con player e zoom interattivo sulla forma d'onda
                                                  - - Timeline interattiva con evidenziazione grafica delle anomalie (click su anomalia → zoom sul segnale)
                                                    - - Localizzazione multilingua (IT/EN come minimo)
                                                     
                                                      - **v3.2 — Integrazione ed ecosistema**
                                                     
                                                      - - Packaging come installatore Windows (Inno Setup) con upgrade automatico
                                                        - - Integrazione opzionale con **VirusTotal API** per cross-check dell'hash del file
                                                          - - Integrazione con **Autopsy** come plugin (tramite Jython bridge)
                                                            - - Versione web-app leggera (FastAPI + frontend) per uso in ambienti lab condivisi
                                                              - - Supporto firma elettronica del perito tramite smart card (PKCS#11)
                                                               
                                                                - ---

                                                                ### Priorità di sviluppo suggerita

                                                                1. Test automatizzati e supporto ricorsivo cartelle (v1.1) — solidità senza riscrivere l'architettura
                                                                2. 2. Analisi ENF e rilevamento doppia compressione MP3 (v2.0) — feature più richieste in ambito forense
                                                                   3. 3. Modalità CLI e separazione core/GUI (v3.0) — apre il progetto a pipeline automatizzate
                                                                      4. 4. Report PDF firmato e supporto DFXML (v2.1) — completano il profilo per uso peritale certificato
