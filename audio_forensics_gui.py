#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        AUDIO FORENSICS ANALYZER  —  GUI Edition  v4.0                  ║
║        Strumento forense per l'analisi di file audio                    ║
║        Conforme: ENFSI BPM-FSA-002 / SWGDE 1.2                         ║
║        Supporta: WAV · MP3 · OGG · FLAC · AIFF · M4A · OPUS · WMA     ║
╚══════════════════════════════════════════════════════════════════════════╝

CHANGELOG v4.0 — vedi CHANGELOG.md
  Nuovi moduli (alta priorità ENFSI BPM-FSA-002):
  · Codec frame analysis OGG Vorbis (§5.4.3, ref [33][34][35])
  · Double-encoding detection MP3/lossy (§5.4.4, ref [69][70])
  · Inter-sample dependency / resampling detection (§5.4.4, ref [67][68])
  · Copy-move forgery detection cross-correlazione (§5.4.4, ref [62][63])

Dipendenze (installa con pip):
    pip install librosa soundfile mutagen matplotlib numpy scipy

Per OGG/MP3 senza FFmpeg nativo:
    Windows: scarica FFmpeg da https://ffmpeg.org e aggiungilo al PATH
    Linux:   sudo apt install ffmpeg
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
import os, sys, hashlib, json, struct, datetime, platform, threading
import subprocess, webbrowser, shutil, warnings
from pathlib import Path
from tkinter import (
    Tk, Frame, Label, Button, Entry, Text, Scrollbar, Listbox,
    Menu, StringVar, IntVar, BooleanVar, PhotoImage,
    filedialog, messagebox, ttk, font as tkfont
)
import tkinter as tk

# ── Verifica dipendenze scientifiche ─────────────────────────────────────────
MISSING = []
try:
    import numpy as np
except ImportError:
    MISSING.append("numpy")
try:
    import librosa
    import librosa.display
except ImportError:
    MISSING.append("librosa")
try:
    import soundfile as sf
except ImportError:
    MISSING.append("soundfile")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError:
    MISSING.append("matplotlib")
try:
    from mutagen import File as MutagenFile
except ImportError:
    MISSING.append("mutagen")
try:
    from scipy.stats import kurtosis, skew
    from scipy.signal import butter, filtfilt, stft as scipy_stft
except ImportError:
    MISSING.append("scipy")

# ─────────────────────────────────────────────────────────────────────────────
# COSTANTI
# ─────────────────────────────────────────────────────────────────────────────
VERSION       = "4.0.0"
BPM_REF       = "ENFSI BPM-FSA-002 Issue 000"
SUPPORTED_EXT = {".wav",".mp3",".ogg",".flac",".aiff",".aif",
                 ".m4a",".wma",".opus",".oga",".mp4",".3gp"}

# Soglie analisi
SILENCE_DB          = -60.0
CLIPPING_THR        = 0.999
SILENCE_MIN_SEC     = 1.0
DC_THR              = 0.01
DC_LOCAL_WIN_SEC    = 2.0      # finestra DC locale (§5.4.2)
BUTT_SPLICE_MULT    = 6.0      # moltiplicatore soglia butt-splice (§5.4.2)
ENF_NOMINAL_EU      = 50.0     # Hz nominali Europa (§5.4.1)
ENF_NOMINAL_US      = 60.0     # Hz nominali USA/JP
ENF_BAND_HZ         = 1.0      # ±Hz intorno al nominale
ENF_WIN_SEC         = 4.0      # finestra STFT per ENF
ENF_HOP_SEC         = 0.5      # hop ENF
ENF_JUMP_SIGMA      = 4.0      # σ per rilevare discontinuità ENF

# ─────────────────────────────────────────────────────────────────────────────
# COLORI  (palette Windows 11 dark)
# ─────────────────────────────────────────────────────────────────────────────
C = {
    "bg":        "#1c1c1e", "panel":    "#2c2c2e", "panel2":  "#3a3a3c",
    "border":    "#48484a", "accent":   "#0a84ff", "accent2": "#30d158",
    "warn":      "#ff9f0a", "danger":   "#ff453a", "text":    "#f2f2f7",
    "text2":     "#aeaeb2", "text3":    "#636366", "toolbar": "#252527",
    "log_bg":    "#111113", "log_ok":   "#30d158", "log_warn":"#ff9f0a",
    "log_err":   "#ff453a", "log_info": "#64d2ff", "listbg":  "#232325",
    "statusbar": "#1c1c1e",
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def compute_hashes(filepath):
    h = {"md5": hashlib.md5(), "sha1": hashlib.sha1(), "sha256": hashlib.sha256()}
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            for v in h.values(): v.update(chunk)
    return {k: v.hexdigest().upper() for k, v in h.items()}

def format_bytes(sz):
    for u in ["B","KB","MB","GB"]:
        if sz < 1024: return f"{sz:.2f} {u}"
        sz /= 1024
    return f"{sz:.2f} TB"

def format_dur(s):
    h=int(s//3600); m=int((s%3600)//60); sec=s%60
    return f"{h:02d}:{m:02d}:{sec:06.3f}" if h>0 else f"{m:02d}:{sec:06.3f}"

def db_to_lin(db): return 10**(db/20)
def lin_to_db(v):
    if v<=0: return -float("inf")
    return 20*np.log10(abs(v))

def detect_magic(filepath):
    MAP = {
        b"RIFF": "WAV (RIFF)", b"OggS": "OGG", b"fLaC": "FLAC",
        b"\xff\xfb": "MP3 (CBR)", b"ID3": "MP3 (ID3)", b"FORM": "AIFF",
    }
    try:
        hdr = open(filepath,"rb").read(32)
        for magic, fmt in MAP.items():
            if hdr[:len(magic)]==magic: return fmt
        if hdr[0]==0xFF and (hdr[1]&0xE0)==0xE0: return "MP3"
        return f"Sconosciuto ({hdr[:4].hex()})"
    except: return "Errore lettura"

# ─────────────────────────────────────────────────────────────────────────────
# METADATI
# ─────────────────────────────────────────────────────────────────────────────

def extract_metadata(filepath):
    """Estrae e categorizza metadati secondo BPM §5.4.4:
    functional / library-related / software-related."""
    meta = {"_functional": {}, "_library": {}, "_software": {}, "_raw": {}}

    # Tag funzionali noti
    FUNCTIONAL_KEYS = {
        "length","bitrate","sample_rate","channels","bits_per_sample",
        "codec","encoding","mode","bit_depth","duration"
    }
    # Tag software-related comuni
    SOFTWARE_KEYS = {
        "encoder","encoded_by","software","tool","comment","encodersettings",
        "encodingapplication","tsse","tenc"
    }

    try:
        af = MutagenFile(filepath, easy=False)
        if af:
            for k, v in af.tags.items() if af.tags else []:
                vstr = str(v[0]) if isinstance(v, list) and v else str(v)
                kl = k.lower()
                meta["_raw"][k] = vstr
                if kl in FUNCTIONAL_KEYS:
                    meta["_functional"][k] = vstr
                elif kl in SOFTWARE_KEYS:
                    meta["_software"][k] = vstr
                else:
                    meta["_library"][k] = vstr

            if hasattr(af, "info"):
                info = af.info
                for a in ["length","bitrate","sample_rate","channels",
                          "bits_per_sample","codec","encoder"]:
                    if hasattr(info, a):
                        meta["_functional"][f"[info] {a}"] = getattr(info, a)
    except Exception as e:
        meta["_error"] = str(e)

    return meta

def ogg_header_check(filepath):
    res = {"valid":False,"pages":0,"serials":[]}
    try:
        data = open(filepath,"rb").read(65536)
        off=0; pages=0; serials=set()
        while off<len(data)-27:
            if data[off:off+4]==b"OggS" and data[off+4]==0:
                pages+=1
                serials.add(struct.unpack_from("<I",data,off+14)[0])
                sc=data[off+26]
                if off+27+sc<=len(data):
                    ps=sum(data[off+27:off+27+sc])
                    off+=27+sc+ps
                else: break
            else: off+=1
        res.update({"valid":pages>0,"pages":pages,"serials":list(serials)})
    except Exception as e: res["error"]=str(e)
    return res

# ─────────────────────────────────────────────────────────────────────────────
# CARICAMENTO AUDIO
# ─────────────────────────────────────────────────────────────────────────────

def load_audio(filepath):
    try:
        y, sr = sf.read(filepath, always_2d=False)
        if y.ndim > 1: y = y.mean(axis=1)
        return y.astype(np.float32), sr
    except:
        y, sr = librosa.load(filepath, sr=None, mono=True)
        return y, sr

# ─────────────────────────────────────────────────────────────────────────────
# ANALISI FORMA D'ONDA
# ─────────────────────────────────────────────────────────────────────────────

def analyze_waveform(y, sr):
    dur = len(y)/sr
    rms = float(np.sqrt(np.mean(y**2)))
    peak = float(np.max(np.abs(y)))
    dc = float(np.mean(y))
    cf = peak/rms if rms>0 else 0
    blk = int(sr*0.05)
    if blk>0 and len(y)>=blk:
        blist = [y[i:i+blk] for i in range(0,len(y)-blk,blk)]
        brms = [np.sqrt(np.mean(b**2)) for b in blist if len(b)==blk]
        bdb  = [lin_to_db(r) for r in brms if r>0]
        dr   = max(bdb)-min(bdb) if bdb else 0.0
    else: dr=0.0
    return {
        "duration_sec":      round(dur,4),
        "duration_fmt":      format_dur(dur),
        "sample_rate":       sr,
        "num_samples":       len(y),
        "rms_db":            round(lin_to_db(rms),2),
        "rms_linear":        round(rms,6),
        "peak_db":           round(lin_to_db(peak),2),
        "peak_linear":       round(peak,6),
        "dc_offset_global":  round(dc,6),
        "crest_factor_db":   round(lin_to_db(cf),2),
        "kurtosis":          round(float(kurtosis(y)),4),
        "skewness":          round(float(skew(y)),4),
        "zcr":               round(float(np.mean(librosa.feature.zero_crossing_rate(y))),6),
        "dynamic_range_db":  round(dr,2),
    }

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v3.0 — ENF ANALYSIS (ENFSI BPM §5.4.1)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_enf(y, sr, nominal_hz=ENF_NOMINAL_EU):
    """
    Estrae e analizza la componente Electric Network Frequency.
    Rileva discontinuità di fase/frequenza che indicano possibile editing.
    Riferimento: ENFSI BPM-FSA-002 §5.4.1, Grigoras [47], Michałek [51].
    """
    result = {
        "nominal_hz":     nominal_hz,
        "enf_present":    False,
        "enf_mean_hz":    None,
        "enf_std_hz":     None,
        "nominal_match":  None,
        "phase_jumps":    0,
        "jump_timestamps":[],
        "snr_db":         None,
        "note":           "",
    }

    # Serve almeno 10 secondi per analisi significativa
    if len(y)/sr < 10:
        result["note"] = "Durata insufficiente per analisi ENF (<10s)"
        return result

    # Nyquist: ENF rilevabile solo se sr > 2*(nominal+band)
    if sr < 2*(nominal_hz + ENF_BAND_HZ + 5):
        result["note"] = f"Sample rate {sr}Hz insufficiente per ENF {nominal_hz}Hz"
        return result

    try:
        # Filtro passabanda Butterworth ordine 8 intorno al nominale
        low  = (nominal_hz - ENF_BAND_HZ) / (sr/2)
        high = (nominal_hz + ENF_BAND_HZ) / (sr/2)
        low  = max(0.001, min(low, 0.999))
        high = max(0.001, min(high, 0.999))
        if low >= high:
            result["note"] = "Parametri filtro ENF non validi"
            return result

        b, a = butter(8, [low, high], btype='band')
        enf_filtered = filtfilt(b, a, y.astype(np.float64))

        # STFT ad alta risoluzione temporale
        nperseg = int(sr * ENF_WIN_SEC)
        hop     = int(sr * ENF_HOP_SEC)
        nperseg = min(nperseg, len(y))
        if nperseg < 64:
            result["note"] = "Segnale troppo breve per STFT ENF"
            return result

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f_arr, t_arr, Zxx = scipy_stft(
                enf_filtered, fs=sr,
                nperseg=nperseg, noverlap=nperseg-hop,
                window='hann'
            )

        # Selezione banda spettrale ENF
        band_mask = (f_arr >= nominal_hz - ENF_BAND_HZ) & \
                    (f_arr <= nominal_hz + ENF_BAND_HZ)
        if not np.any(band_mask):
            result["note"] = "Banda ENF non trovata nello spettro"
            return result

        enf_spectrum = np.abs(Zxx[band_mask, :])

        # Verifica presenza ENF: rapporto segnale/rumore nella banda
        enf_power  = np.mean(enf_spectrum**2)
        total_power = np.mean(np.abs(Zxx)**2) + 1e-20
        snr_db = lin_to_db(enf_power / total_power) if total_power > 0 else -99
        result["snr_db"] = round(float(snr_db), 2)

        # Traiettoria frequenza istantanea (picco per frame)
        f_band = f_arr[band_mask]
        enf_freq = f_band[np.argmax(enf_spectrum, axis=0)]

        result["enf_present"]   = float(snr_db) > -40
        result["enf_mean_hz"]   = round(float(np.mean(enf_freq)), 5)
        result["enf_std_hz"]    = round(float(np.std(enf_freq)), 6)
        result["nominal_match"] = abs(np.mean(enf_freq) - nominal_hz) < 0.5

        # Rilevamento discontinuità di fase/frequenza
        enf_diff = np.diff(enf_freq)
        std_diff  = np.std(enf_diff)
        if std_diff > 0:
            jump_idx = np.where(np.abs(enf_diff) > ENF_JUMP_SIGMA * std_diff)[0]
            result["phase_jumps"]     = int(len(jump_idx))
            result["jump_timestamps"] = [round(float(t_arr[j]), 2) for j in jump_idx]

    except Exception as e:
        result["note"] = f"Errore analisi ENF: {e}"

    return result

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v3.0 — BUTT-SPLICE DETECTION (ENFSI BPM §5.4.2, Cooper [23])
# ─────────────────────────────────────────────────────────────────────────────

def detect_butt_splices(y, sr, multiplier=BUTT_SPLICE_MULT):
    """
    Rileva tagli bruschi PCM tramite differenziale 1° e 2° ordine.
    Metodo Cooper (AES 2010) — ENFSI BPM-FSA-002 §5.4.2 ref [23].
    Nota: efficace solo su file PCM non ricodificati dopo l'editing.
    """
    result = {"splices": [], "count": 0, "method": "Cooper diff2 PCM"}

    if len(y) < 100:
        return result

    try:
        y64 = y.astype(np.float64)
        diff1 = np.diff(y64)
        diff2 = np.diff(diff1)

        # Finestra locale adattiva: 10ms
        win = max(10, int(sr * 0.01))
        splices = []

        # Scorriamo con hop di 1ms per non perdere eventi
        hop = max(1, int(sr * 0.001))
        i = win
        while i < len(diff2) - win:
            local = diff2[i-win:i+win]
            local_std = np.std(local)
            if local_std > 0:
                if abs(diff2[i]) > multiplier * local_std:
                    t_sec = i / sr
                    # Evita duplicati (raggruppa entro 50ms)
                    if not splices or (t_sec - splices[-1]["timestamp_sec"]) > 0.05:
                        splices.append({
                            "timestamp_sec": round(t_sec, 4),
                            "timestamp_fmt": format_dur(t_sec),
                            "magnitude":     round(float(abs(diff2[i])), 8),
                            "local_std":     round(float(local_std), 8),
                            "ratio":         round(float(abs(diff2[i]) / local_std), 2),
                        })
            i += hop

        result["splices"] = splices
        result["count"]   = len(splices)

    except Exception as e:
        result["error"] = str(e)

    return result

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v3.0 — DC OFFSET LOCALE (ENFSI BPM §5.4.2)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_dc_offset_local(y, sr, window_sec=DC_LOCAL_WIN_SEC):
    """
    Confronto DC offset locale (per finestre scorrevoli) vs globale.
    Finestre con DC molto diverso dal globale indicano possibile editing.
    Riferimento: ENFSI BPM-FSA-002 §5.4.2, Koenig & Lacey [20][21][22].
    """
    global_dc = float(np.mean(y))
    win = int(sr * window_sec)

    if win < 10 or len(y) < win * 2:
        return {
            "global_dc": round(global_dc, 6),
            "local_std": None,
            "anomalies": [],
            "note": "File troppo breve per analisi DC locale",
        }

    dc_local, timestamps = [], []
    hop = win // 2
    for i in range(0, len(y) - win, hop):
        dc_local.append(float(np.mean(y[i:i+win])))
        timestamps.append(i / sr)

    dc_arr   = np.array(dc_local)
    local_std = float(np.std(dc_arr))
    # Soglia: max tra DC_THR assoluto e 3σ della distribuzione locale
    threshold = max(DC_THR, 3.0 * local_std) if local_std > 0 else DC_THR

    anomalies = []
    for dc, t in zip(dc_local, timestamps):
        delta = abs(dc - global_dc)
        if delta > threshold:
            anomalies.append({
                "timestamp_sec": round(t, 2),
                "timestamp_fmt": format_dur(t),
                "dc_local":      round(dc, 6),
                "dc_global":     round(global_dc, 6),
                "delta":         round(delta, 6),
                "threshold":     round(threshold, 6),
            })

    return {
        "global_dc":   round(global_dc, 6),
        "local_std":   round(local_std, 8),
        "threshold":   round(threshold, 6),
        "window_sec":  window_sec,
        "anomalies":   anomalies,
    }

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v3.0 — QUANTIZATION LEVEL ANALYSIS (ENFSI BPM §5.4.4, §5.4.5)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_quantization_levels(y, sr, bit_depth=16):
    """
    Analisi gap e periodicità nell'istogramma di quantizzazione.
    Gap periodici indicano gain digitale applicato post-registrazione.
    Riferimento: ENFSI BPM-FSA-002 §5.4.4, Grigoras [24][25].
    """
    result = {
        "bit_depth_declared": bit_depth,
        "used_levels":        0,
        "total_levels":       0,
        "fill_ratio":         0.0,
        "central_gaps":       0,
        "periodic_gap":       None,
        "suspected_gain":     False,
        "real_bit_depth":     None,
    }

    try:
        scale = 2 ** (bit_depth - 1)
        y_int = np.clip((y * scale).astype(np.int64), -scale, scale-1)

        # Istogramma completo
        counts = np.bincount(y_int + scale, minlength=2*scale)
        used   = int(np.sum(counts > 0))
        total  = int(2 * scale)
        result["used_levels"]  = used
        result["total_levels"] = total
        result["fill_ratio"]   = round(used / total, 5)

        # Real bit-depth: cerca il livello di significato più basso usato
        # (valori pari → bit meno significativo non usato)
        nonzero = np.where(counts > 0)[0] - scale
        if len(nonzero) > 10:
            for bd in range(bit_depth, 7, -1):
                step = 2 ** (bit_depth - bd)
                if np.all(nonzero % step == 0):
                    result["real_bit_depth"] = bd
                    break

        # Analisi gap nella zona centrale (dove il gain digitale è rilevabile)
        center = scale
        half   = min(4096, scale // 4)
        central_zone = counts[center-half:center+half]
        gaps   = np.where(central_zone == 0)[0]
        result["central_gaps"] = int(len(gaps))

        # Periodicità nei gap → indicatore di gain digitale applicato
        if len(gaps) > 3:
            gap_diffs = np.diff(gaps)
            if len(gap_diffs) > 0:
                # Cerca la spaziatura più comune
                vals, cnts = np.unique(gap_diffs, return_counts=True)
                mode_diff = int(vals[np.argmax(cnts)])
                mode_freq = int(np.max(cnts))
                if mode_diff > 0 and mode_freq > len(gap_diffs) * 0.5:
                    result["periodic_gap"]   = mode_diff
                    result["suspected_gain"] = True

    except Exception as e:
        result["error"] = str(e)

    return result

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v3.0 — LTAS (Long-Term Average Spectrum) (ENFSI BPM §5.4.5)
# ─────────────────────────────────────────────────────────────────────────────

def compute_ltas(y, sr, n_bands=24):
    """
    Long-Term Average Spectrum: profilo spettrale del dispositivo.
    Usato per confronto tra registrazioni (§5.4.5 ref [24][28]).
    """
    try:
        # Mel-filterbank per approssimare percezione
        S = np.abs(librosa.stft(y, n_fft=4096, hop_length=1024))
        # Energia media per banda di frequenza (scala log)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
        band_edges = np.logspace(np.log10(max(20, freqs[1])),
                                 np.log10(sr//2), n_bands+1)
        ltas = []
        for i in range(n_bands):
            mask = (freqs >= band_edges[i]) & (freqs < band_edges[i+1])
            if np.any(mask):
                ltas.append(round(float(lin_to_db(np.mean(S[mask, :]))), 2))
            else:
                ltas.append(-99.0)
        return {
            "n_bands":    n_bands,
            "ltas_db":    ltas,
            "ltas_range": round(max(ltas) - min(ltas), 2),
        }
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v4.0 — CODEC FRAME ANALYSIS OGG VORBIS (ENFSI BPM §5.4.3)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_codec_frames_ogg(filepath):
    """
    Analisi della struttura delle pagine OGG e del framing grid Vorbis.
    Discontinuità nel framing offset indicano possibile editing.
    Riferimento: ENFSI BPM-FSA-002 §5.4.3, Gärtner [33], Korycki [34], Yang [35].

    Analisi eseguita:
    - Parsing raw di tutte le pagine OggS nel file
    - Verifica monotonica dei granule position (timestamp interno OGG)
    - Rilevamento salti anomali nella sequenza di granule
    - Verifica continuità del sequence number per serial stream
    - Analisi della dimensione delle pagine (anomalie indicano editing)
    """
    result = {
        "applicable":       False,
        "total_pages":      0,
        "serial_streams":   [],
        "granule_jumps":    [],
        "seq_gaps":         [],
        "page_size_anomalies": [],
        "framing_ok":       None,
        "note":             "",
    }

    ext = Path(filepath).suffix.lower()
    if ext not in (".ogg", ".oga", ".opus"):
        result["note"] = "Non applicabile (formato non OGG)"
        return result

    result["applicable"] = True

    try:
        pages = []
        with open(filepath, "rb") as f:
            raw = f.read()

        offset = 0
        while offset < len(raw) - 27:
            if raw[offset:offset+4] != b"OggS":
                offset += 1
                continue
            if offset + 27 > len(raw):
                break

            version    = raw[offset+4]
            header_type= raw[offset+5]
            # granule position: int64 little-endian (può essere -1 = 0xFFFFFFFFFFFFFFFF)
            granule_raw= struct.unpack_from("<Q", raw, offset+6)[0]
            granule    = granule_raw if granule_raw != 0xFFFFFFFFFFFFFFFF else -1
            serial     = struct.unpack_from("<I", raw, offset+14)[0]
            seq_num    = struct.unpack_from("<I", raw, offset+18)[0]
            checksum   = struct.unpack_from("<I", raw, offset+22)[0]
            seg_count  = raw[offset+26]

            if offset + 27 + seg_count > len(raw):
                break

            seg_table  = raw[offset+27:offset+27+seg_count]
            page_data_size = sum(seg_table)
            page_total = 27 + seg_count + page_data_size

            pages.append({
                "offset":     offset,
                "header_type":header_type,
                "granule":    granule,
                "serial":     serial,
                "seq_num":    seq_num,
                "seg_count":  seg_count,
                "data_size":  page_data_size,
                "is_bos":     bool(header_type & 0x02),  # Beginning Of Stream
                "is_eos":     bool(header_type & 0x04),  # End Of Stream
                "is_cont":    bool(header_type & 0x01),  # Continuation
            })
            offset += page_total

        result["total_pages"] = len(pages)
        if not pages:
            result["note"] = "Nessuna pagina OGG trovata"
            result["framing_ok"] = False
            return result

        # Raggruppa per serial stream
        streams = {}
        for p in pages:
            s = p["serial"]
            if s not in streams:
                streams[s] = []
            streams[s].append(p)
        result["serial_streams"] = list(streams.keys())

        granule_jumps = []
        seq_gaps      = []
        page_size_anoms = []

        for serial, stream_pages in streams.items():
            granules = [(p["granule"], p["offset"]) for p in stream_pages if p["granule"] >= 0]
            seqs     = [(p["seq_num"], p["offset"]) for p in stream_pages]
            sizes    = [p["data_size"] for p in stream_pages]

            # ── Analisi granule position (deve essere monotonicamente crescente) ──
            for i in range(1, len(granules)):
                prev_g, prev_off = granules[i-1]
                curr_g, curr_off = granules[i]
                delta = curr_g - prev_g
                if delta < 0:
                    granule_jumps.append({
                        "serial":     serial,
                        "offset_hex": hex(curr_off),
                        "prev_granule": prev_g,
                        "curr_granule": curr_g,
                        "delta":      delta,
                        "tipo":       "INVERSIONE",
                    })
                elif delta == 0 and i > 1:
                    granule_jumps.append({
                        "serial":     serial,
                        "offset_hex": hex(curr_off),
                        "prev_granule": prev_g,
                        "curr_granule": curr_g,
                        "delta":      delta,
                        "tipo":       "STALLO",
                    })
                elif len(granules) > 4:
                    # Salto anomalo: delta > 5x mediana degli altri delta
                    all_deltas = [granules[j][0]-granules[j-1][0]
                                  for j in range(1, len(granules))
                                  if granules[j][0]-granules[j-1][0] > 0]
                    if all_deltas:
                        median_d = float(np.median(all_deltas))
                        if median_d > 0 and delta > 5 * median_d:
                            granule_jumps.append({
                                "serial":       serial,
                                "offset_hex":   hex(curr_off),
                                "prev_granule": prev_g,
                                "curr_granule": curr_g,
                                "delta":        delta,
                                "tipo":         f"SALTO ({delta/median_d:.1f}x mediana)",
                            })

            # ── Analisi sequence number (deve essere consecutivo) ──
            for i in range(1, len(seqs)):
                prev_s, _ = seqs[i-1]
                curr_s, off = seqs[i]
                expected = (prev_s + 1) & 0xFFFFFFFF
                if curr_s != expected:
                    seq_gaps.append({
                        "serial":     serial,
                        "offset_hex": hex(off),
                        "expected":   expected,
                        "found":      curr_s,
                        "gap":        (curr_s - prev_s - 1) & 0xFFFFFFFF,
                    })

            # ── Anomalie dimensione pagina ──
            if len(sizes) > 5:
                arr = np.array(sizes, dtype=float)
                mean_s = float(np.mean(arr))
                std_s  = float(np.std(arr))
                if std_s > 0:
                    for i, (pg, sz) in enumerate(zip(stream_pages, sizes)):
                        z = abs(sz - mean_s) / std_s
                        if z > 4.0 and sz not in (0, 255*255):
                            page_size_anoms.append({
                                "serial":     serial,
                                "offset_hex": hex(pg["offset"]),
                                "size":       sz,
                                "mean":       round(mean_s, 1),
                                "z_score":    round(z, 2),
                            })

        result["granule_jumps"]       = granule_jumps
        result["seq_gaps"]            = seq_gaps
        result["page_size_anomalies"] = page_size_anoms[:10]
        result["framing_ok"] = (len(granule_jumps) == 0 and len(seq_gaps) == 0)

    except Exception as e:
        result["note"]       = f"Errore parsing OGG: {e}"
        result["framing_ok"] = None

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v4.0 — DOUBLE ENCODING DETECTION (ENFSI BPM §5.4.4)
# ─────────────────────────────────────────────────────────────────────────────

def detect_double_encoding(y, sr, filepath):
    """
    Rilevamento di doppia codifica lossy (es. MP3→MP3, OGG→MP3).
    Metodo: analisi degli artefatti spettrali tipici della codifica lossy
    tramite MDCT-like residual e analisi LTAS differenziale.
    Riferimento: ENFSI BPM-FSA-002 §5.4.4, Bianchi [69], Korycki [71].

    Indicatori analizzati:
    1. Cut-off frequency anomala (codec lowpass filter lascia traccia)
    2. Distribuzione energia sub-band: pattern periodici tipici MDCT
    3. Notches spettrali periodici (firma doppia codifica)
    4. Analisi varianza spettrale temporale (lossy riduce varianza)
    """
    ext = Path(filepath).suffix.lower()
    result = {
        "applicable":          True,
        "cutoff_freq_hz":      None,
        "cutoff_anomaly":      False,
        "spectral_notches":    [],
        "mdct_periodicity":    None,
        "double_enc_suspected": False,
        "confidence":          "BASSA",
        "note":                "",
    }

    # Solo su file abbastanza lunghi
    if len(y) / sr < 3.0:
        result["applicable"] = False
        result["note"]       = "Durata insufficiente (<3s)"
        return result

    try:
        n_fft   = 4096
        hop     = 1024
        S       = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
        freqs   = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        # Energia media per bin di frequenza
        mean_spec = np.mean(S, axis=1)

        # ── 1. Cut-off frequency ──────────────────────────────────────────────
        # I codec lossy applicano un lowpass; cerchiamo dove l'energia crolla
        mean_db = 20 * np.log10(mean_spec + 1e-12)
        # Normalizza rispetto al picco nella banda 1-5kHz
        ref_band = (freqs >= 1000) & (freqs <= 5000)
        if np.any(ref_band):
            ref_db = np.max(mean_db[ref_band])
        else:
            ref_db = np.max(mean_db)

        # Cerca il punto da cui l'energia scende stabilmente sotto -40dB rispetto ref
        cutoff_hz = None
        high_band = freqs > 8000
        if np.any(high_band):
            high_db  = mean_db[high_band]
            high_f   = freqs[high_band]
            for i in range(len(high_db)-10, 0, -1):
                window_db = high_db[max(0,i-5):i+5]
                if np.mean(window_db) > ref_db - 40:
                    cutoff_hz = float(high_f[i])
                    break

        result["cutoff_freq_hz"] = round(cutoff_hz, 1) if cutoff_hz else None

        # Anomalia: cut-off < sr/2 * 0.85 (atteso che vada fino a Nyquist)
        nyquist = sr / 2
        if cutoff_hz and cutoff_hz < nyquist * 0.85:
            result["cutoff_anomaly"] = True
            result["spectral_notches"].append({
                "freq_hz": round(cutoff_hz, 1),
                "tipo":    "LOWPASS CUTOFF",
                "desc":    f"Energia dimezzata a {cutoff_hz:.0f}Hz (Nyquist={nyquist:.0f}Hz)"
            })

        # ── 2. Notches periodici (firma MDCT doppia codifica) ────────────────
        # MP3 usa blocchi MDCT da 576 campioni → periodicità spettrali a multipli
        # di sr/576 nell'asse temporale dello spettrogramma
        S_var = np.var(S, axis=1)  # varianza temporale per bin
        if len(S_var) > 20:
            # Cerca dip nella varianza (punti di energia costante = artefatti lossy)
            var_norm = S_var / (np.max(S_var) + 1e-12)
            dip_thresh = 0.05
            dip_idx = np.where(
                (var_norm < dip_thresh) &
                (freqs[:len(var_norm)] > 1000) &
                (freqs[:len(var_norm)] < nyquist * 0.9)
            )[0]
            if len(dip_idx) > 10:
                # Raggruppa in cluster di frequenza
                clusters = []
                if len(dip_idx) > 0:
                    cluster_start = dip_idx[0]
                    for i in range(1, len(dip_idx)):
                        if dip_idx[i] - dip_idx[i-1] > 5:
                            f_center = freqs[int(np.mean([cluster_start, dip_idx[i-1]]))]
                            clusters.append(round(float(f_center), 1))
                            cluster_start = dip_idx[i]
                    f_center = freqs[int(np.mean([cluster_start, dip_idx[-1]]))]
                    clusters.append(round(float(f_center), 1))

                if len(clusters) >= 3:
                    result["spectral_notches"].extend([
                        {"freq_hz": f, "tipo": "MDCT DIP",
                         "desc": "Varianza temporale nulla (artefatto lossy)"}
                        for f in clusters[:5]
                    ])

        # ── 3. Periodicità MDCT temporale ────────────────────────────────────
        # MP3: frame = 1152 campioni → modulazione a 1152/sr Hz
        # OGG Vorbis: blocchi 256/2048 campioni
        # Analizziamo autocorrelazione dell'energia per finestre
        frame_energy = np.sum(S**2, axis=0)
        if len(frame_energy) > 64:
            # Autocorrelazione normalizzata
            fe_norm = frame_energy - np.mean(frame_energy)
            if np.std(fe_norm) > 0:
                autocorr = np.correlate(fe_norm, fe_norm, mode='full')
                autocorr = autocorr[len(autocorr)//2:]
                autocorr /= (autocorr[0] + 1e-12)
                # Cerca picchi di autocorrelazione (escluso lag 0)
                search = autocorr[2:min(200, len(autocorr))]
                if len(search) > 10:
                    peak_lags = []
                    for i in range(1, len(search)-1):
                        if search[i] > search[i-1] and search[i] > search[i+1] and search[i] > 0.3:
                            # Converti lag in campioni audio
                            lag_samples = (i+2) * hop
                            # MP3: 1152, OGG: 256 o 2048
                            for frame_size, codec in [(1152,"MP3"),(2048,"OGG-large"),(256,"OGG-small")]:
                                if abs(lag_samples - frame_size) < frame_size * 0.1:
                                    peak_lags.append({"codec": codec,
                                                      "lag_samples": lag_samples,
                                                      "correlation": round(float(search[i]), 3)})
                    if peak_lags:
                        result["mdct_periodicity"] = peak_lags[0]

        # ── Scoring finale ────────────────────────────────────────────────────
        score = 0
        if result["cutoff_anomaly"]:      score += 3
        if len(result["spectral_notches"]) >= 3: score += 2
        if result["mdct_periodicity"]:    score += 3

        if score >= 6:
            result["double_enc_suspected"] = True
            result["confidence"]           = "ALTA"
        elif score >= 3:
            result["double_enc_suspected"] = True
            result["confidence"]           = "MEDIA"
        else:
            result["confidence"]           = "BASSA"

        if ext not in (".mp3", ".ogg", ".oga", ".m4a", ".wma", ".aac"):
            result["note"] = "File non compresso: doppia codifica improbabile ma verifica utile"

    except Exception as e:
        result["note"] = f"Errore analisi double encoding: {e}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v4.0 — INTER-SAMPLE DEPENDENCY / RESAMPLING DETECTION (§5.4.4)
# ─────────────────────────────────────────────────────────────────────────────

def detect_resampling(y, sr):
    """
    Rileva tracce di ricampionamento digitale tramite periodicità nelle
    dipendenze inter-campione (residui di predizione lineare).
    Il ricampionamento per fattore P/Q introduce correlazioni periodiche
    ogni Q campioni nel residuo LP.
    Riferimento: ENFSI BPM-FSA-002 §5.4.4, Vázquez-Padín [67][68].

    Metodo:
    1. Calcola residuo di predizione lineare (ordine 2)
    2. Calcola l'autocorrelazione del residuo al quadrato
    3. Cerca picchi periodici nell'autocorrelazione (lag 2-100)
    4. Identifica il fattore di ricampionamento più probabile
    """
    result = {
        "resampling_detected":  False,
        "period_samples":       None,
        "period_time_ms":       None,
        "estimated_ratio":      None,
        "original_sr_estimate": None,
        "confidence":           "BASSA",
        "autocorr_peak":        None,
        "note":                 "",
    }

    if len(y) < sr * 2:
        result["note"] = "File troppo breve (<2s) per analisi resampling"
        return result

    try:
        # Usa un segmento rappresentativo (max 30s dal centro)
        center = len(y) // 2
        seg_len = min(int(sr * 30), len(y))
        start   = max(0, center - seg_len // 2)
        seg     = y[start:start + seg_len].astype(np.float64)

        # ── 1. Residuo di predizione lineare ordine 2 ─────────────────────────
        # res[n] = y[n] - a1*y[n-1] - a2*y[n-2]
        # Stima coefficienti via autocorrelazione (metodo Yule-Walker semplificato)
        r0 = np.dot(seg, seg)
        r1 = np.dot(seg[1:], seg[:-1])
        r2 = np.dot(seg[2:], seg[:-2])
        if r0 > 0:
            # Solve 2x2 system [r0 r1; r1 r0] [a1; a2] = [r1; r2]
            denom = r0**2 - r1**2
            if abs(denom) > 1e-10:
                a1 = (r1*r0 - r2*r1) / denom
                a2 = (r2*r0 - r1**2) / denom
            else:
                a1, a2 = 0.0, 0.0
        else:
            a1, a2 = 0.0, 0.0

        residual = seg[2:] - a1*seg[1:-1] - a2*seg[:-2]

        # ── 2. Autocorrelazione del residuo al quadrato ───────────────────────
        res_sq   = residual**2
        res_norm = res_sq - np.mean(res_sq)
        max_lag  = min(200, len(res_norm) // 4)
        if max_lag < 10:
            result["note"] = "Segmento troppo breve per autocorrelazione"
            return result

        # Calcola autocorrelazione per lag 2..max_lag
        autocorr = np.array([
            float(np.dot(res_norm[:len(res_norm)-lag], res_norm[lag:]))
            for lag in range(2, max_lag + 1)
        ])
        # Normalizza
        norm_val = autocorr[0] if abs(autocorr[0]) > 1e-15 else 1.0
        autocorr /= norm_val

        # ── 3. Ricerca picchi nell'autocorrelazione ───────────────────────────
        best_lag   = None
        best_corr  = 0.0
        threshold  = 0.08  # soglia conservativa

        for i in range(1, len(autocorr) - 1):
            val = autocorr[i]
            if (val > autocorr[i-1] and val > autocorr[i+1] and val > threshold):
                if val > best_corr:
                    best_corr = val
                    best_lag  = i + 2  # +2 perché partiamo da lag=2

        if best_lag is not None:
            result["resampling_detected"] = True
            result["period_samples"]      = best_lag
            result["period_time_ms"]      = round(1000 * best_lag / sr, 4)
            result["autocorr_peak"]       = round(best_corr, 4)

            # ── 4. Stima del fattore P/Q di ricampionamento ───────────────────
            # Rapporti comuni: 44100→48000 (P/Q=160/147),
            # 22050→44100 (2/1), 48000→44100 (147/160), etc.
            COMMON_RATIOS = [
                (160, 147, 44100, 48000),  # 44100→48000
                (147, 160, 48000, 44100),  # 48000→44100
                (2,   1,   22050, 44100),  # 22050→44100
                (1,   2,   44100, 22050),  # 44100→22050
                (3,   2,   32000, 48000),  # 32000→48000
                (4,   3,   24000, 32000),  # 24000→32000
                (80,  441, 44100, 8000),   # 44100→8000
                (6,   5,   40000, 48000),  # 40000→48000
            ]
            best_match = None
            for P, Q, from_sr, to_sr in COMMON_RATIOS:
                if abs(best_lag - Q) <= max(1, int(Q * 0.05)):
                    best_match = {"P": P, "Q": Q,
                                  "from_sr": from_sr, "to_sr": to_sr,
                                  "ratio": f"{P}/{Q}"}
                    break

            if best_match:
                result["estimated_ratio"]      = best_match["ratio"]
                result["original_sr_estimate"] = best_match["from_sr"]
                result["note"] = (f"Ricampionamento stimato {best_match['from_sr']}Hz"
                                  f" → {best_match['to_sr']}Hz (P/Q={best_match['ratio']})")
            else:
                result["note"] = f"Periodo={best_lag} campioni non corrisponde a ratio noti"

            if best_corr >= 0.20:   result["confidence"] = "ALTA"
            elif best_corr >= 0.10: result["confidence"] = "MEDIA"

    except Exception as e:
        result["note"] = f"Errore analisi resampling: {e}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ▶ NUOVO v4.0 — COPY-MOVE FORGERY DETECTION (ENFSI BPM §5.4.4)
# ─────────────────────────────────────────────────────────────────────────────

def detect_copy_move(y, sr, segment_sec=1.0, top_matches=5):
    """
    Rilevamento di intervalli temporali identici (copy-paste) tramite
    cross-correlazione di segmenti e confronto di fingerprint spettrali.
    Riferimento: ENFSI BPM-FSA-002 §5.4.4, Imran [62], Maksimović [63].

    Metodo:
    1. Divide l'audio in segmenti sovrapposti di 1s
    2. Estrae fingerprint spettrale compatto (MFCC 13 coefficienti)
    3. Calcola distanza coseno tra tutti i coppie di segmenti
    4. Segmenti con distanza < soglia = candidati copy-paste
    5. Verifica tramite cross-correlazione diretta sulla forma d'onda
    """
    result = {
        "segments_analyzed": 0,
        "matches_found":     [],
        "copy_move_suspected": False,
        "max_similarity":    0.0,
        "note":              "",
    }

    min_dur = segment_sec * 3
    if len(y) / sr < min_dur:
        result["note"] = f"File troppo breve (<{min_dur:.0f}s) per copy-move detection"
        return result

    try:
        seg_len = int(sr * segment_sec)
        hop     = seg_len // 2  # 50% overlap

        # ── 1. Estrai fingerprint MFCC per ogni segmento ─────────────────────
        segments  = []
        seg_times = []
        n_mfcc    = 13

        i = 0
        while i + seg_len <= len(y):
            seg = y[i:i+seg_len]
            # Fingerprint: MFCC mean + delta mean (26 features totali)
            mfcc_vals = librosa.feature.mfcc(y=seg, sr=sr, n_mfcc=n_mfcc)
            fp = np.concatenate([
                np.mean(mfcc_vals, axis=1),
                np.std(mfcc_vals,  axis=1),
            ])
            segments.append(fp)
            seg_times.append(i / sr)
            i += hop

        n_segs = len(segments)
        result["segments_analyzed"] = n_segs

        if n_segs < 4:
            result["note"] = "Troppo pochi segmenti per analisi"
            return result

        # ── 2. Matrice di similarità coseno ──────────────────────────────────
        S = np.array(segments)
        # Normalizza ogni vettore
        norms = np.linalg.norm(S, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        S_norm = S / norms
        # Matrice similarità (dot product di vettori normalizzati)
        sim_matrix = np.dot(S_norm, S_norm.T)

        # ── 3. Trova coppie simili ─────────────────────────────────────────────
        COSINE_THRESHOLD = 0.985  # soglia alta: vuol dire >98.5% similarità
        matches = []

        for i in range(n_segs):
            for j in range(i + 2, n_segs):  # gap minimo di 2 segmenti
                sim = float(sim_matrix[i, j])
                if sim >= COSINE_THRESHOLD:
                    t1 = seg_times[i]
                    t2 = seg_times[j]
                    # Distanza minima: almeno 2s tra inizio segmenti
                    if abs(t2 - t1) < 2.0:
                        continue

                    # ── 4. Verifica con cross-correlazione diretta ────────────
                    seg_i = y[int(t1*sr):int(t1*sr)+seg_len]
                    seg_j = y[int(t2*sr):int(t2*sr)+seg_len]
                    if len(seg_i) == seg_len and len(seg_j) == seg_len:
                        # Correlazione normalizzata
                        n_i = np.linalg.norm(seg_i)
                        n_j = np.linalg.norm(seg_j)
                        if n_i > 0 and n_j > 0:
                            xcorr = np.correlate(seg_i/n_i, seg_j/n_j, mode='full')
                            xcorr_peak = float(np.max(np.abs(xcorr)))
                        else:
                            xcorr_peak = 0.0
                    else:
                        xcorr_peak = sim  # fallback

                    matches.append({
                        "t1_sec":     round(t1, 3),
                        "t1_fmt":     format_dur(t1),
                        "t2_sec":     round(t2, 3),
                        "t2_fmt":     format_dur(t2),
                        "cosine_sim": round(sim, 5),
                        "xcorr_peak": round(xcorr_peak, 4),
                        "gap_sec":    round(abs(t2-t1), 2),
                    })

        # Ordina per similarità decrescente, mantieni top N
        matches.sort(key=lambda x: x["cosine_sim"], reverse=True)
        matches = matches[:top_matches]

        result["matches_found"]       = matches
        result["copy_move_suspected"] = len(matches) > 0
        result["max_similarity"]      = matches[0]["cosine_sim"] if matches else 0.0

        if matches:
            best = matches[0]
            result["note"] = (
                f"{len(matches)} coppia/e sospette. "
                f"Massima similarità {best['cosine_sim']:.4f} "
                f"tra t={best['t1_fmt']} e t={best['t2_fmt']}"
            )

    except Exception as e:
        result["note"] = f"Errore copy-move detection: {e}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ANALISI ANOMALIE (aggiornata v4.0)
# ─────────────────────────────────────────────────────────────────────────────

def detect_anomalies(y, sr, wf, enf, dc_local, butt, quant,
                     codec_frames=None, double_enc=None,
                     resampling=None, copy_move=None):
    """
    Rileva anomalie forensi integrando tutti i moduli v4.0.
    Riferimenti ENFSI: §5.1.3 (principio fondamentale), §5.4.1-5.4.4.
    """
    anoms = []

    # ── 1. Clipping ──────────────────────────────────────────────────────────
    cc = int(np.sum(np.abs(y) >= CLIPPING_THR))
    if cc > 0:
        pct = 100*cc/len(y)
        sev = "ALTA" if pct>1 else "MEDIA" if pct>0.1 else "BASSA"
        anoms.append({"tipo":"CLIPPING","severità":sev,
            "dettaglio":f"{cc} campioni ({pct:.3f}%)",
            "forense":"Saturazione o manipolazione del livello",
            "ref":"§5.4.2 BPM-FSA-002"})

    # ── 2. DC Offset globale ─────────────────────────────────────────────────
    dc_g = wf["dc_offset_global"]
    if abs(dc_g) > DC_THR:
        anoms.append({"tipo":"DC OFFSET GLOBALE","severità":"MEDIA",
            "dettaglio":f"Offset DC = {dc_g:.4f}",
            "forense":"Problema hardware microfonico o editing",
            "ref":"§5.4.2 BPM-FSA-002, Koenig [20]"})

    # ── 3. DC Offset locale ───────────────────────────────────────────────────
    if dc_local and dc_local.get("anomalies"):
        for a in dc_local["anomalies"]:
            anoms.append({"tipo":"DC OFFSET LOCALE","severità":"ALTA",
                "dettaglio":f"ΔDC={a['delta']:.5f} @ t={a['timestamp_fmt']} "
                            f"(locale={a['dc_local']:.5f} vs globale={a['dc_global']:.5f})",
                "forense":"Porzione audio con dispositivo diverso o editing",
                "ref":"§5.4.2 BPM-FSA-002, Koenig [20][21][22]"})

    # ── 4. Silenzio anomalo ───────────────────────────────────────────────────
    fl=int(sr*0.1); hl=fl//2; thr=db_to_lin(SILENCE_DB)
    if len(y)>=fl:
        frames=librosa.util.frame(y,frame_length=fl,hop_length=hl)
        is_sil=np.sqrt(np.mean(frames**2,axis=0))<thr
        in_s=False; st=0
        for i,s in enumerate(is_sil):
            if s and not in_s: in_s=True; st=i
            elif not s and in_s:
                in_s=False
                d=((i-st)*hl)/sr
                if d>=SILENCE_MIN_SEC:
                    anoms.append({"tipo":"SILENZIO ANOMALO",
                        "severità":"MEDIA" if d>3 else "BASSA",
                        "dettaglio":f"{d:.2f}s @ t={format_dur(st*hl/sr)}",
                        "forense":"Taglio/cancellazione/pausa non documentata",
                        "ref":"§5.4.1 BPM-FSA-002"})
        if in_s:
            d=((len(is_sil)-st)*hl)/sr
            if d>=SILENCE_MIN_SEC:
                anoms.append({"tipo":"SILENZIO ANOMALO","severità":"BASSA",
                    "dettaglio":f"{d:.2f}s @ t={format_dur(st*hl/sr)}",
                    "forense":"Taglio/cancellazione/pausa non documentata",
                    "ref":"§5.4.1 BPM-FSA-002"})

    # ── 5. Discontinuità ampiezza (grossolana) ────────────────────────────────
    bs=int(sr*0.5)
    if len(y)>=bs*2:
        brms=[np.sqrt(np.mean(y[i:i+bs]**2)) for i in range(0,len(y)-bs,bs)]
        ba=np.array(brms); diffs=np.abs(np.diff(ba)); m=np.mean(ba)
        if m>0:
            for idx in np.where(diffs/m>5.0)[0]:
                anoms.append({"tipo":"DISCONTINUITÀ AMPIEZZA","severità":"ALTA",
                    "dettaglio":f"Salto @ t={format_dur(idx*0.5)}s (ratio={diffs[idx]/m:.1f}x)",
                    "forense":"Sospetto punto di splice/editing",
                    "ref":"§5.4.2 BPM-FSA-002"})

    # ── 6. Butt-splice PCM ───────────────────────────────────────────────────
    if butt and butt.get("splices"):
        for sp in butt["splices"][:10]:
            anoms.append({"tipo":"BUTT-SPLICE PCM","severità":"ALTA",
                "dettaglio":f"Taglio netto @ t={sp['timestamp_fmt']} "
                            f"(ratio={sp['ratio']:.1f}σ)",
                "forense":"Taglio diretto tra campioni PCM — forte indice di editing",
                "ref":"§5.4.2 BPM-FSA-002, Cooper [23]"})

    # ── 7. ENF discontinuità ─────────────────────────────────────────────────
    if enf and enf.get("enf_present") and enf.get("phase_jumps", 0) > 0:
        for ts in enf["jump_timestamps"][:5]:
            anoms.append({"tipo":"ENF DISCONTINUITÀ","severità":"ALTA",
                "dettaglio":f"Salto ENF @ t={format_dur(ts)} "
                            f"(totale: {enf['phase_jumps']} salti)",
                "forense":"Possibile inserimento/rimozione di contenuto audio",
                "ref":"§5.4.1 BPM-FSA-002, Grigoras [47], Michałek [51]"})

    if enf and enf.get("enf_present") and enf.get("nominal_match")==False:
        anoms.append({"tipo":"ENF FREQUENZA ANOMALA","severità":"ALTA",
            "dettaglio":f"ENF rilevato a {enf.get('enf_mean_hz','?')}Hz "
                        f"(nominale: {enf.get('nominal_hz')}Hz)",
            "forense":"Registrazione da rete elettrica diversa o file manipolato",
            "ref":"§5.4.1 BPM-FSA-002, Grigoras [53]"})

    # ── 8. Quantizzazione ────────────────────────────────────────────────────
    if quant and quant.get("suspected_gain"):
        anoms.append({"tipo":"GAIN DIGITALE SOSPETTO","severità":"MEDIA",
            "dettaglio":f"Gap periodici nell'istogramma (periodo={quant['periodic_gap']})",
            "forense":"Possibile modifica del guadagno digitale post-registrazione",
            "ref":"§5.4.4 BPM-FSA-002, Grigoras [24]"})

    if quant and quant.get("real_bit_depth") and \
       quant["real_bit_depth"] < quant.get("bit_depth_declared",16) - 2:
        anoms.append({"tipo":"BIT-DEPTH REALE RIDOTTO","severità":"BASSA",
            "dettaglio":f"Bit-depth reale stimato: {quant['real_bit_depth']} "
                        f"(dichiarato: {quant['bit_depth_declared']})",
            "forense":"ADC con risoluzione inferiore al dichiarato",
            "ref":"§5.4.5 BPM-FSA-002, Grigoras [24]"})

    # ── 9. Kurtosis ──────────────────────────────────────────────────────────
    if wf["kurtosis"] > 10:
        anoms.append({"tipo":"KURTOSIS ELEVATA","severità":"BASSA",
            "dettaglio":f"k={wf['kurtosis']} (atteso 0-6 per audio naturale)",
            "forense":"Impulsi/click o manipolazione del segnale",
            "ref":"§5.2 BPM-FSA-002"})

    # ── 10. ▶ NUOVO v4.0 — Codec frame OGG ──────────────────────────────────
    if codec_frames and codec_frames.get("applicable"):
        for gj in codec_frames.get("granule_jumps", [])[:5]:
            anoms.append({"tipo":"OGG GRANULE ANOMALY","severità":"ALTA",
                "dettaglio":f"Granule {gj['tipo']} @ {gj['offset_hex']} "
                            f"(Δ={gj['delta']})",
                "forense":"Discontinuità nel timestamp interno OGG — editing sospetto",
                "ref":"§5.4.3 BPM-FSA-002, Gärtner [33], Korycki [34]"})
        for sg in codec_frames.get("seq_gaps", [])[:5]:
            anoms.append({"tipo":"OGG SEQUENCE GAP","severità":"ALTA",
                "dettaglio":f"Seq gap @ {sg['offset_hex']} "
                            f"(atteso={sg['expected']}, trovato={sg['found']})",
                "forense":"Gap nella sequenza pagine OGG — pagine mancanti o inserite",
                "ref":"§5.4.3 BPM-FSA-002, Yang [35]"})
        if codec_frames.get("framing_ok") == False and \
           not codec_frames.get("granule_jumps") and not codec_frames.get("seq_gaps"):
            anoms.append({"tipo":"OGG FRAMING ANOMALY","severità":"MEDIA",
                "dettaglio":"Struttura OGG non conforme attesa",
                "forense":"Possibile manipolazione della struttura del file",
                "ref":"§5.4.3 BPM-FSA-002"})

    # ── 11. ▶ NUOVO v4.0 — Double encoding ───────────────────────────────────
    if double_enc and double_enc.get("double_enc_suspected"):
        conf = double_enc.get("confidence","BASSA")
        sev  = "ALTA" if conf=="ALTA" else "MEDIA"
        detail = f"Confidenza {conf}"
        if double_enc.get("cutoff_freq_hz"):
            detail += f" | cutoff={double_enc['cutoff_freq_hz']}Hz"
        if double_enc.get("mdct_periodicity"):
            mp = double_enc["mdct_periodicity"]
            detail += f" | MDCT match {mp.get('codec','?')}"
        anoms.append({"tipo":"DOPPIA CODIFICA LOSSY","severità":sev,
            "dettaglio":detail,
            "forense":"File ricompresso dopo editing — mascheramento delle tracce",
            "ref":"§5.4.4 BPM-FSA-002, Bianchi [69], Korycki [71]"})

    if double_enc and double_enc.get("cutoff_anomaly"):
        hz = double_enc.get("cutoff_freq_hz","?")
        anoms.append({"tipo":"CUTOFF FREQUENZA ANOMALO","severità":"MEDIA",
            "dettaglio":f"Energia cade a {hz}Hz (atteso fino a Nyquist)",
            "forense":"Lowpass codec applicato — indicatore di codifica lossy",
            "ref":"§5.4.2-5.4.4 BPM-FSA-002"})

    # ── 12. ▶ NUOVO v4.0 — Resampling ────────────────────────────────────────
    if resampling and resampling.get("resampling_detected"):
        conf = resampling.get("confidence","BASSA")
        sev  = "ALTA" if conf=="ALTA" else "MEDIA"
        detail = (f"Periodo={resampling['period_samples']} campioni "
                  f"({resampling['period_time_ms']}ms)")
        if resampling.get("estimated_ratio"):
            detail += f" | ratio stimato {resampling['estimated_ratio']}"
        if resampling.get("original_sr_estimate"):
            detail += f" | SR originale ~{resampling['original_sr_estimate']}Hz"
        anoms.append({"tipo":"RESAMPLING RILEVATO","severità":sev,
            "dettaglio":detail,
            "forense":"Ricampionamento digitale post-registrazione — traccia di post-processing",
            "ref":"§5.4.4 BPM-FSA-002, Vázquez-Padín [67][68]"})

    # ── 13. ▶ NUOVO v4.0 — Copy-move ─────────────────────────────────────────
    if copy_move and copy_move.get("copy_move_suspected"):
        for match in copy_move.get("matches_found", [])[:3]:
            anoms.append({"tipo":"COPY-MOVE SOSPETTO","severità":"ALTA",
                "dettaglio":f"Segmento simile @ t={match['t1_fmt']} ≈ t={match['t2_fmt']} "
                            f"(sim={match['cosine_sim']:.4f}, xcorr={match['xcorr_peak']:.3f})",
                "forense":"Intervalli temporali quasi identici — copia/incolla sospetta",
                "ref":"§5.4.4 BPM-FSA-002, Imran [62], Maksimović [63]"})

    return anoms


def spectral_features(y, sr):
    try:
        sc  = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        sb  = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
        sr_ = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
        mfcc= librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        return {
            "centroid_mean_hz":  round(float(np.mean(sc)),2),
            "centroid_std_hz":   round(float(np.std(sc)),2),
            "bandwidth_mean_hz": round(float(np.mean(sb)),2),
            "rolloff_mean_hz":   round(float(np.mean(sr_)),2),
            "mfcc_means": [round(float(m),4) for m in np.mean(mfcc,axis=1)],
        }
    except Exception as e: return {"error":str(e)}


def integrity_verdict(anoms, fmt, ext, enf, butt, dc_local, quant,
                      codec_frames=None, double_enc=None,
                      resampling=None, copy_move=None):
    """Calcola verdetto integrità con scoring ENFSI-aligned."""
    score = 100; flags = []
    sev_map = {"ALTA":20, "MEDIA":10, "BASSA":5}

    for a in anoms:
        score -= sev_map.get(a["severità"], 5)
        flags.append(f"{a['tipo']} [{a['severità']}]")

    # Penalità mismatch formato (§5.4.4)
    ext_l = ext.lower()
    mismatch = (
        (ext_l==".ogg"  and "ogg"  not in fmt.lower()) or
        (ext_l==".mp3"  and "mp3"  not in fmt.lower()) or
        (ext_l==".wav"  and "riff" not in fmt.lower()) or
        (ext_l==".flac" and "flac" not in fmt.lower())
    )
    if mismatch:
        score -= 30
        flags.append(f"MISMATCH FORMATO ({ext_l} vs {fmt})")

    score = max(0, score)

    if score >= 85:   verdict, color = "INTEGRO",          C["accent2"]
    elif score >= 60: verdict, color = "SOSPETTO",          C["warn"]
    else:             verdict, color = "NON ATTENDIBILE",   C["danger"]

    return {
        "score":    score,
        "verdict":  verdict,
        "color":    color,
        "flags":    flags,
        "mismatch": mismatch,
        "bpm_ref":  BPM_REF,
    }

# ─────────────────────────────────────────────────────────────────────────────
# GRAFICI
# ─────────────────────────────────────────────────────────────────────────────

def generate_plots_fig(y, sr, enf_data=None, butt_data=None):
    """Genera figura matplotlib con grafici forensi (aggiornata v3.0)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        plt.style.use("dark_background")

    fig = plt.figure(figsize=(15,10), facecolor="#1c1c1e")
    gs  = gridspec.GridSpec(3, 3, figure=fig)
    TC="#e0e0f0"; AC="#0a84ff"; WC="#ff453a"; GC="#30d158"
    dur = len(y)/sr
    tax = np.linspace(0, dur, len(y))

    # ── 1. Waveform (full width) ──────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_facecolor("#111113")
    ax1.plot(tax, y, color=AC, lw=0.35, alpha=0.85)
    cm = np.abs(y) >= CLIPPING_THR
    if np.any(cm):
        ax1.scatter(tax[cm], y[cm], color=WC, s=3, zorder=5, label="Clipping")
    # Butt-splice markers (NUOVO)
    if butt_data and butt_data.get("splices"):
        for sp in butt_data["splices"][:20]:
            ax1.axvline(sp["timestamp_sec"], color="#ff9f0a", lw=0.8,
                        alpha=0.7, linestyle="--")
    ax1.axhline(0,  color="#444", lw=0.5, ls="--")
    ax1.axhline( CLIPPING_THR, color=WC, lw=0.6, ls=":", alpha=0.7)
    ax1.axhline(-CLIPPING_THR, color=WC, lw=0.6, ls=":", alpha=0.7)
    ax1.set_xlabel("Tempo (s)", color=TC, fontsize=8)
    ax1.set_ylabel("Ampiezza", color=TC, fontsize=8)
    ax1.set_title("FORMA D'ONDA  [arancio=butt-splice, rosso=clipping]",
                  color=AC, fontsize=10, fontweight="bold", pad=6)
    ax1.tick_params(colors=TC, labelsize=7)
    for sp in ax1.spines.values(): sp.set_edgecolor("#333")

    # ── 2. Spettrogramma ──────────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor("#111113")
    D = librosa.amplitude_to_db(np.abs(librosa.stft(y)), ref=np.max)
    img = librosa.display.specshow(D, sr=sr, x_axis="time", y_axis="hz",
                                   ax=ax2, cmap="magma")
    fig.colorbar(img, ax=ax2, format="%+2.0f dB", pad=0.02)
    ax2.set_title("SPETTROGRAMMA", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax2.tick_params(colors=TC, labelsize=7)
    ax2.set_xlabel("Tempo (s)", color=TC, fontsize=7)
    ax2.set_ylabel("Freq (Hz)", color=TC, fontsize=7)

    # ── 3. RMS nel tempo ──────────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_facecolor("#111113")
    hop = 512
    rms_f = librosa.feature.rms(y=y, hop_length=hop)[0]
    rdb   = librosa.amplitude_to_db(rms_f, ref=np.max)
    trms  = librosa.frames_to_time(np.arange(len(rms_f)), sr=sr, hop_length=hop)
    ax3.fill_between(trms, rdb, -80, alpha=0.5, color="#7b2d8b")
    ax3.plot(trms, rdb, color="#d080ff", lw=0.8)
    ax3.axhline(SILENCE_DB, color=WC, lw=0.8, ls="--", alpha=0.8,
                label=f"Soglia silenzio ({SILENCE_DB}dB)")
    ax3.set_ylim(-80, 5)
    ax3.set_title("RMS NEL TEMPO", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax3.tick_params(colors=TC, labelsize=7)
    ax3.set_xlabel("Tempo (s)", color=TC, fontsize=7)
    ax3.set_ylabel("RMS (dB)",  color=TC, fontsize=7)

    # ── 4. ENF traiettoria (NUOVO v3.0) ───────────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 2])
    ax4.set_facecolor("#111113")
    enf_plotted = False
    if enf_data and enf_data.get("enf_present"):
        try:
            # Ricalcola per il plot
            nominal = enf_data.get("nominal_hz", ENF_NOMINAL_EU)
            low  = (nominal - ENF_BAND_HZ) / (sr/2)
            high = (nominal + ENF_BAND_HZ) / (sr/2)
            low  = max(0.001, min(low, 0.999))
            high = max(0.001, min(high, 0.999))
            if low < high:
                b_f, a_f = butter(8, [low, high], btype='band')
                enf_filt  = filtfilt(b_f, a_f, y.astype(np.float64))
                nperseg   = min(int(sr*ENF_WIN_SEC), len(y))
                hop_enf   = int(sr*ENF_HOP_SEC)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    f_e, t_e, Zxx_e = scipy_stft(
                        enf_filt, fs=sr,
                        nperseg=nperseg, noverlap=nperseg-hop_enf, window='hann')
                band_m = (f_e >= nominal-ENF_BAND_HZ) & (f_e <= nominal+ENF_BAND_HZ)
                if np.any(band_m):
                    spec_e  = np.abs(Zxx_e[band_m, :])
                    f_band  = f_e[band_m]
                    enf_tr  = f_band[np.argmax(spec_e, axis=0)]
                    ax4.plot(t_e, enf_tr, color=GC, lw=1.2, label="ENF (Hz)")
                    ax4.axhline(nominal, color="#888", lw=0.8, ls="--",
                                label=f"Nominale {nominal}Hz")
                    # Markers sui jump
                    for ts in enf_data.get("jump_timestamps",[]):
                        ax4.axvline(ts, color=WC, lw=1.0, alpha=0.8)
                    ax4.legend(fontsize=6, facecolor="#1c1c1e")
                    ax4.set_title(f"ENF TRAIETTORIA (rosso=discontinuità)",
                                  color=AC, fontsize=9, fontweight="bold", pad=5)
                    enf_plotted = True
        except: pass
    if not enf_plotted:
        ax4.text(0.5, 0.5, "ENF non rilevabile\n(insufficiente o assente)",
                 ha="center", va="center", color=TC, fontsize=9,
                 transform=ax4.transAxes)
        ax4.set_title("ENF TRAIETTORIA", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax4.tick_params(colors=TC, labelsize=7)
    ax4.set_xlabel("Tempo (s)", color=TC, fontsize=7)
    ax4.set_ylabel("Hz",        color=TC, fontsize=7)

    # ── 5. FFT media ──────────────────────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 0])
    ax5.set_facecolor("#111113")
    fs  = min(4096, len(y)); nw = max(1, len(y)//fs)
    sp_acc = []
    for i in range(nw):
        ch = y[i*fs:(i+1)*fs]
        if len(ch)==fs:
            w = np.hanning(fs)
            sp_acc.append(np.abs(np.fft.rfft(ch*w)))
    if sp_acc:
        avg  = np.mean(sp_acc, axis=0)
        adb  = 20*np.log10(avg/(np.max(avg)+1e-12)+1e-12)
        freqs= np.fft.rfftfreq(fs, 1/sr)
        ax5.semilogx(freqs[1:], adb[1:], color=GC, lw=0.8)
        ax5.fill_between(freqs[1:], adb[1:], -120, alpha=0.3, color="#007a30")
    ax5.set_xlim(20, sr//2); ax5.set_ylim(-80, 5)
    ax5.set_title("SPETTRO FFT (LTAS)", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax5.tick_params(colors=TC, labelsize=7)
    ax5.set_xlabel("Frequenza (Hz)", color=TC, fontsize=7)
    ax5.set_ylabel("dB",             color=TC, fontsize=7)
    ax5.grid(axis="x", color="#222", lw=0.4, alpha=0.8)

    # ── 6. Distribuzione ampiezza ─────────────────────────────────────────────
    ax6 = fig.add_subplot(gs[2, 1])
    ax6.set_facecolor("#111113")
    hv, be = np.histogram(y, bins=200, range=(-1,1))
    bc = (be[:-1]+be[1:])/2
    ax6.bar(bc, hv, width=be[1]-be[0], color="#ff9f0a", alpha=0.75, edgecolor="none")
    ax6.axvline( CLIPPING_THR, color=WC, lw=1.2, ls="--")
    ax6.axvline(-CLIPPING_THR, color=WC, lw=1.2, ls="--")
    ax6.set_title("DISTRIBUZIONE AMPIEZZA", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax6.tick_params(colors=TC, labelsize=7)
    ax6.set_xlabel("Ampiezza norm.", color=TC, fontsize=7)
    ax6.set_ylabel("Campioni",       color=TC, fontsize=7)

    # ── 7. DC Offset locale (NUOVO v3.0) ──────────────────────────────────────
    ax7 = fig.add_subplot(gs[2, 2])
    ax7.set_facecolor("#111113")
    try:
        win  = int(sr * DC_LOCAL_WIN_SEC)
        hop7 = win // 2
        dc_vals, dc_t = [], []
        for i in range(0, len(y)-win, hop7):
            dc_vals.append(float(np.mean(y[i:i+win])))
            dc_t.append(i/sr)
        if dc_vals:
            ax7.plot(dc_t, dc_vals, color="#64d2ff", lw=1.0, label="DC locale")
            ax7.axhline(float(np.mean(y)), color=GC, lw=0.8, ls="--", label="DC globale")
            ax7.axhline( DC_THR, color=WC, lw=0.6, ls=":", alpha=0.7)
            ax7.axhline(-DC_THR, color=WC, lw=0.6, ls=":", alpha=0.7)
            ax7.legend(fontsize=6, facecolor="#1c1c1e")
    except: pass
    ax7.set_title("DC OFFSET LOCALE", color=AC, fontsize=9, fontweight="bold", pad=5)
    ax7.tick_params(colors=TC, labelsize=7)
    ax7.set_xlabel("Tempo (s)", color=TC, fontsize=7)
    ax7.set_ylabel("DC",        color=TC, fontsize=7)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fig.subplots_adjust(left=0.06, right=0.97, top=0.94,
                            bottom=0.07, hspace=0.55, wspace=0.38)
    return fig


def save_plots_png(fig, out_dir, basename):
    path = os.path.join(out_dir, f"{basename}_forensics.png")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fig.savefig(path, dpi=150, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
    return path

# ─────────────────────────────────────────────────────────────────────────────
# REPORT HTML
# ─────────────────────────────────────────────────────────────────────────────

def generate_html_report(data, plot_path, out_path):
    iv   = data["integrity"]; vc=iv["color"]; sc=iv["score"]
    hashes=data["hashes"]; wf=data["waveform"]; fi=data["file_info"]
    anoms=data["anomalies"]; meta=data["metadata"]; sp=data["spectral"]
    enf  = data.get("enf",{}); butt=data.get("butt_splice",{})
    dc_l = data.get("dc_local",{}); quant=data.get("quantization",{})
    plot_rel = os.path.basename(plot_path)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def row(l, v, mono=False):
        st = 'font-family:monospace;font-size:12px;color:#a8d8ff;' if mono else ''
        return f'<tr><td class="lbl">{l}</td><td style="{st}">{v}</td></tr>'

    def arow(a):
        sc_map = {"ALTA":"#ff453a","MEDIA":"#ff9f0a","BASSA":"#64d2ff"}
        c = sc_map.get(a.get("severità","BASSA"),"#aaa")
        ref = a.get("ref","")
        return (f'<div class="anom" style="border-left:4px solid {c}">'
                f'<span style="color:{c};font-weight:700">[{a["severità"]}] {a["tipo"]}</span><br>'
                f'<span style="font-size:12px">{a["dettaglio"]}</span><br>'
                f'<span style="font-size:11px;color:#888;font-style:italic">⚖ {a["forense"]}</span>'
                + (f'<br><span style="font-size:10px;color:#555">{ref}</span>' if ref else '')
                + '</div>')

    ano_html = "\n".join(arow(a) for a in anoms) if anoms else \
               '<p style="color:#30d158">✓ Nessuna anomalia rilevata</p>'

    meta_rows = ""
    for cat, label in [("_functional","Funzionali"),("_library","Library"),
                       ("_software","Software")]:
        if meta.get(cat):
            meta_rows += f'<tr><td colspan="2" style="color:#0a84ff;font-weight:700;padding-top:8px">{label}</td></tr>'
            for k,v in meta[cat].items():
                meta_rows += row(k,v)
    if not meta_rows:
        meta_rows = row("—","Nessun metadato disponibile")

    spec_rows = "".join(row(k,v) for k,v in sp.items()
                        if k not in("mfcc_means","error"))

    # ENF section
    enf_html = ""
    if enf:
        present_str = "✓ Rilevata" if enf.get("enf_present") else "✗ Non rilevata/assente"
        match_str   = "✓ Compatibile" if enf.get("nominal_match") else \
                      "⚠ NON compatibile" if enf.get("nominal_match")==False else "N/A"
        enf_html = f"""
        <div class="card"><div class="ctitle">⚡ ENF Analysis (§5.4.1)</div>
        <div class="cbody"><table>
          {row("Componente ENF",present_str)}
          {row("Frequenza nominale",f"{enf.get('nominal_hz','?')} Hz")}
          {row("ENF media misurata",f"{enf.get('enf_mean_hz','N/A')} Hz")}
          {row("ENF std",f"{enf.get('enf_std_hz','N/A')} Hz")}
          {row("Match rete elettrica",match_str)}
          {row("Discontinuità di fase",str(enf.get('phase_jumps',0)))}
          {row("SNR banda ENF",f"{enf.get('snr_db','N/A')} dB")}
          {row("Note",enf.get('note','') or '—')}
        </table></div></div>"""

    # Butt-splice section
    butt_html = ""
    if butt:
        splices = butt.get("splices",[])
        butt_html = f"""
        <div class="card"><div class="ctitle">✂ Butt-Splice PCM (§5.4.2 Cooper)</div>
        <div class="cbody">"""
        if splices:
            butt_html += f'<p style="color:#ff453a;font-weight:700">⚑ {len(splices)} tagli rilevati</p>'
            for s in splices[:8]:
                butt_html += f'<div style="font-size:12px;padding:3px 0">t={s["timestamp_fmt"]} | ratio={s["ratio"]}σ</div>'
        else:
            butt_html += '<p style="color:#30d158">✓ Nessun butt-splice PCM rilevato</p>'
        butt_html += "</div></div>"

    # DC Local section
    dcl_html = ""
    if dc_l:
        dcl_anom = dc_l.get("anomalies",[])
        dcl_html = f"""
        <div class="card"><div class="ctitle">〰 DC Offset Locale (§5.4.2)</div>
        <div class="cbody"><table>
          {row("DC globale",str(dc_l.get('global_dc','?')))}
          {row("Std locale",str(dc_l.get('local_std','?')))}
          {row("Soglia",str(dc_l.get('threshold','?')))}
          {row("Anomalie locali",str(len(dcl_anom)))}
        </table>"""
        for a in dcl_anom[:5]:
            dcl_html += f'<div style="font-size:11px;color:#ff9f0a;padding:2px 0">⚑ t={a["timestamp_fmt"]} | ΔDC={a["delta"]:.5f}</div>'
        dcl_html += "</div></div>"

    # Quant section
    quant_html = ""
    if quant:
        quant_html = f"""
        <div class="card"><div class="ctitle">📊 Quantizzazione (§5.4.4)</div>
        <div class="cbody"><table>
          {row("Livelli usati / totali",f"{quant.get('used_levels','?')} / {quant.get('total_levels','?')}")}
          {row("Fill ratio",str(quant.get('fill_ratio','?')))}
          {row("Gap centrali",str(quant.get('central_gaps','?')))}
          {row("Gap periodici",str(quant.get('periodic_gap','No')))}
          {row("Gain digitale sospetto","⚠ SÌ" if quant.get('suspected_gain') else "✓ No")}
          {row("Bit-depth reale stimato",str(quant.get('real_bit_depth','?')))}
        </table></div></div>"""

    flags_html = ""
    if iv.get("flags"):
        flags_html = '<div class="card full" style="margin-bottom:16px"><div class="ctitle">🚩 Flag Forensi</div><div class="flags">'
        flags_html += "".join(f'<span class="flag">{f}</span>' for f in iv["flags"])
        flags_html += "</div></div>"

    # ── Codec Frames HTML ────────────────────────────────────────────────────
    cf   = data.get("codec_frames", {})
    cf_html = ""
    if cf and cf.get("applicable"):
        framing_ok = cf.get("framing_ok")
        badge = '✓ OK' if framing_ok else ('⚠ ANOMALIE' if framing_ok==False else '—')
        badge_col = '#30d158' if framing_ok else ('#ff453a' if framing_ok==False else '#888')
        cf_html = f"""
        <div class="card"><div class="ctitle">🎵 OGG Codec Frames (§5.4.3)</div>
        <div class="cbody"><table>
          {row("Framing status",f'<span style="color:{badge_col}">{badge}</span>')}
          {row("Pagine totali",str(cf.get('total_pages',0)))}
          {row("Serial streams",str(cf.get('serial_streams',[])))}
          {row("Granule jumps",str(len(cf.get('granule_jumps',[]))))}
          {row("Sequence gaps",str(len(cf.get('seq_gaps',[]))))}
          {row("Page size anomalies",str(len(cf.get('page_size_anomalies',[]))))}
        </table>"""
        for gj in cf.get("granule_jumps",[])[:4]:
            cf_html += f'<div style="font-size:11px;color:#ff453a;padding:2px 0">⚑ Granule {gj["tipo"]} @ {gj["offset_hex"]} (Δ={gj["delta"]})</div>'
        for sg in cf.get("seq_gaps",[])[:4]:
            cf_html += f'<div style="font-size:11px;color:#ff453a;padding:2px 0">⚑ Seq gap @ {sg["offset_hex"]} (atteso={sg["expected"]}, trovato={sg["found"]})</div>'
        cf_html += "</div></div>"

    # ── Double Encoding HTML ─────────────────────────────────────────────────
    de   = data.get("double_enc", {})
    de_html = ""
    if de:
        de_suspected = de.get("double_enc_suspected", False)
        de_col = '#ff453a' if de_suspected else '#30d158'
        de_html = f"""
        <div class="card"><div class="ctitle">🔁 Double Encoding (§5.4.4)</div>
        <div class="cbody"><table>
          {row("Doppia codifica sospetta",f'<span style="color:{de_col}">{"⚠ SÌ" if de_suspected else "✓ No"}</span>')}
          {row("Confidenza",de.get('confidence','—'))}
          {row("Cutoff frequenza",f"{de.get('cutoff_freq_hz','N/A')} Hz")}
          {row("Cutoff anomalo","⚠ SÌ" if de.get('cutoff_anomaly') else "✓ No")}
          {row("MDCT periodicità",str(de.get('mdct_periodicity','—')))}
          {row("Note",de.get('note','—'))}
        </table></div></div>"""

    # ── Resampling HTML ──────────────────────────────────────────────────────
    rs   = data.get("resampling", {})
    rs_html = ""
    if rs:
        rs_det = rs.get("resampling_detected", False)
        rs_col = '#ff453a' if rs_det else '#30d158'
        rs_html = f"""
        <div class="card"><div class="ctitle">🔀 Resampling (§5.4.4)</div>
        <div class="cbody"><table>
          {row("Resampling rilevato",f'<span style="color:{rs_col}">{"⚠ SÌ" if rs_det else "✓ No"}</span>')}
          {row("Confidenza",rs.get('confidence','—'))}
          {row("Periodo (campioni)",str(rs.get('period_samples','—')))}
          {row("Periodo (ms)",str(rs.get('period_time_ms','—')))}
          {row("Ratio stimato P/Q",rs.get('estimated_ratio','—'))}
          {row("SR originale stimato",f"{rs.get('original_sr_estimate','—')} Hz" if rs.get('original_sr_estimate') else '—')}
          {row("Picco autocorr.",str(rs.get('autocorr_peak','—')))}
          {row("Note",rs.get('note','—'))}
        </table></div></div>"""

    # ── Copy-Move HTML ───────────────────────────────────────────────────────
    cm   = data.get("copy_move", {})
    cm_html = ""
    if cm:
        cm_det = cm.get("copy_move_suspected", False)
        cm_col = '#ff453a' if cm_det else '#30d158'
        cm_html = f"""
        <div class="card"><div class="ctitle">📋 Copy-Move (§5.4.4)</div>
        <div class="cbody">
          <p style="color:{cm_col};font-weight:700;margin-bottom:8px">
            {"⚠ " + str(len(cm.get("matches_found",[]))) + " coppie sospette" if cm_det else "✓ Nessun copy-paste rilevato"}
          </p><table>
          {row("Segmenti analizzati",str(cm.get('segments_analyzed','—')))}
          {row("Similarità massima",str(cm.get('max_similarity','—')))}
        </table>"""
        for m in cm.get("matches_found",[])[:5]:
            cm_html += (f'<div style="font-size:11px;color:#ff9f0a;padding:3px 0">'
                        f'⚑ t={m["t1_fmt"]} ≈ t={m["t2_fmt"]} | sim={m["cosine_sim"]:.4f}'
                        f'</div>')
        cm_html += f'<div style="font-size:10px;color:#636366;margin-top:4px">{cm.get("note","")}</div>'
        cm_html += "</div></div>"

    html = f"""<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
<title>Report Forense — {fi['filename']}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#1c1c1e;color:#f2f2f7;font-family:'Segoe UI',sans-serif;font-size:14px;line-height:1.6}}
.hdr{{background:#111113;border-bottom:2px solid {vc};padding:20px 28px}}
.hdr h1{{font-size:20px;color:{vc};letter-spacing:2px;text-transform:uppercase}}
.hdr .sub{{color:#636366;font-size:11px;font-family:monospace;margin-top:4px}}
.vb{{display:flex;align-items:center;gap:16px;background:#2c2c2e;
     border:2px solid {vc};border-radius:8px;padding:14px 20px;margin:20px 28px}}
.vlbl{{font-size:24px;font-weight:700;color:{vc};letter-spacing:2px}}
.vscore{{margin-left:auto;text-align:right}}
.snum{{font-family:monospace;font-size:44px;color:{vc};line-height:1}}
.slbl{{color:#636366;font-size:10px;text-transform:uppercase;letter-spacing:1px}}
.cont{{padding:0 28px 40px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px}}
.card{{background:#2c2c2e;border:1px solid #3a3a3c;border-radius:6px;overflow:hidden}}
.full{{grid-column:1/-1}}
.ctitle{{background:#232325;padding:8px 14px;font-size:10px;font-weight:700;
         color:{vc};letter-spacing:2px;text-transform:uppercase;
         border-bottom:1px solid #3a3a3c}}
.cbody{{padding:12px 14px}}
table{{width:100%;border-collapse:collapse}}
tr:nth-child(even){{background:rgba(255,255,255,.025)}}
td{{padding:4px 8px;font-size:12px;vertical-align:top}}
td.lbl{{color:#636366;width:44%;border-right:1px solid #3a3a3c;font-size:11px}}
.hb{{font-family:monospace;font-size:11px;color:#a8d8ff;word-break:break-all;
     padding:6px 10px;background:#111113;border-radius:4px;margin-bottom:8px}}
.hlbl{{color:#636366;font-size:10px;text-transform:uppercase;
       letter-spacing:1px;margin-bottom:2px}}
.anom{{background:rgba(255,255,255,.03);border-radius:4px;
       padding:8px 12px;margin-bottom:8px}}
.flags{{display:flex;flex-wrap:wrap;gap:8px;padding:12px 14px}}
.flag{{background:rgba(255,69,58,.1);border:1px solid #ff453a;color:#ff453a;
       border-radius:4px;padding:2px 8px;font-size:11px;font-family:monospace}}
img{{width:100%;border-radius:4px}}
.bpmref{{background:#0a1a0a;border:1px solid #1a3a1a;border-radius:4px;
         padding:8px 12px;font-family:monospace;font-size:10px;
         color:#30d158;margin:12px 28px}}
.footer{{text-align:center;color:#636366;font-size:11px;padding:16px;
         border-top:1px solid #3a3a3c;font-family:monospace}}
@media(max-width:700px){{.grid,.grid3{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="hdr">
  <h1>⚖ Report Forense Audio</h1>
  <div class="sub">Generato: {ts} · Audio Forensics Analyzer v{VERSION}
    · {platform.system()} {platform.release()}</div>
</div>
<div class="bpmref">⚖ Riferimento normativo: {BPM_REF} | SWGDE Best Practices for Digital Audio Authentication 1.2</div>
<div class="vb">
  <div style="font-size:32px">{'✅' if iv['verdict']=='INTEGRO' else '⚠️' if iv['verdict']=='SOSPETTO' else '🚨'}</div>
  <div><div class="vlbl">{iv['verdict']}</div>
  <div style="color:#636366;font-size:11px;margin-top:2px">Valutazione attendibilità forense</div></div>
  <div class="vscore"><div class="snum">{sc}</div><div class="slbl">Score / 100</div></div>
</div>
<div class="cont">
  <div class="card full" style="margin-bottom:16px">
    <div class="ctitle">📊 Analisi Visuale</div>
    <div class="cbody" style="padding:0"><img src="{plot_rel}" alt="Grafici forensi"></div>
  </div>
  {flags_html}
  <div class="grid">
    <div class="card"><div class="ctitle">📁 File</div><div class="cbody"><table>
      {row("Nome",fi['filename'])}{row("Dimensione",fi['size_human'])}
      {row("Formato rilevato",fi['format_detected'])}{row("Estensione",fi['extension'])}
      {row("Mismatch","⚠ SÌ" if iv['mismatch'] else "✓ No")}
      {row("Modifica",fi['mtime'])}
    </table></div></div>
    <div class="card"><div class="ctitle">〰 Forma d'Onda</div><div class="cbody"><table>
      {row("Durata",wf['duration_fmt'])}{row("Sample rate",f"{wf['sample_rate']:,} Hz")}
      {row("Campioni",f"{wf['num_samples']:,}")}{row("RMS",f"{wf['rms_db']} dB")}
      {row("Peak",f"{wf['peak_db']} dB")}{row("DC globale",str(wf['dc_offset_global']))}
      {row("Crest Factor",f"{wf['crest_factor_db']} dB")}
      {row("Dyn. Range",f"{wf['dynamic_range_db']} dB")}
      {row("Kurtosis",str(wf['kurtosis']))}{row("Skewness",str(wf['skewness']))}
    </table></div></div>
    <div class="card"><div class="ctitle">🔐 Hash (§8 BPM)</div><div class="cbody">
      <div class="hlbl">SHA-1</div><div class="hb">{hashes['sha1']}</div>
      <div class="hlbl">SHA-256</div><div class="hb">{hashes['sha256']}</div>
      <div class="hlbl">MD5</div><div class="hb">{hashes['md5']}</div>
    </div></div>
    <div class="card"><div class="ctitle">🔍 Anomalie ({len(anoms)})</div>
      <div class="cbody">{ano_html}</div></div>
    {enf_html}
    {butt_html}
    {dcl_html}
    {quant_html}
    {cf_html}
    {de_html}
    {rs_html}
    {cm_html}
    <div class="card"><div class="ctitle">📡 Spettrale</div>
      <div class="cbody"><table>{spec_rows}</table></div></div>
    <div class="card"><div class="ctitle">🏷 Metadati (§5.4.4)</div>
      <div class="cbody"><table>{meta_rows}</table></div></div>
  </div>
</div>
<div class="footer">Audio Forensics Analyzer v{VERSION} · {BPM_REF}<br>
Il report non sostituisce una perizia tecnica certificata.
Gli hash devono essere confrontati con quelli acquisiti originalmente.</div>
</body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

# ─────────────────────────────────────────────────────────────────────────────
# ENGINE PRINCIPALE
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis(filepath, out_dir, progress_cb, log_cb, enf_nominal=ENF_NOMINAL_EU):
    filepath = os.path.abspath(filepath)
    bn  = Path(filepath).stem
    ext = Path(filepath).suffix
    os.makedirs(out_dir, exist_ok=True)

    log_cb(f"▶ Avvio analisi: {os.path.basename(filepath)}", "info")
    log_cb(f"  Riferimento: {BPM_REF}", "info")
    progress_cb(3, "Verifica file...")

    st = os.stat(filepath)
    fi = {
        "filename":        os.path.basename(filepath),
        "filepath":        filepath,
        "size_bytes":      st.st_size,
        "size_human":      format_bytes(st.st_size),
        "extension":       ext,
        "format_detected": detect_magic(filepath),
        "mtime":           datetime.datetime.fromtimestamp(st.st_mtime)
                                          .strftime("%Y-%m-%d %H:%M:%S"),
    }
    log_cb(f"  Formato rilevato: {fi['format_detected']}", "info")
    progress_cb(10, "Calcolo hash (SHA1/SHA256/MD5)...")

    hashes = compute_hashes(filepath)
    log_cb(f"  SHA1:   {hashes['sha1']}", "ok")
    log_cb(f"  SHA256: {hashes['sha256']}", "ok")
    log_cb(f"  MD5:    {hashes['md5']}", "ok")
    progress_cb(20, "Metadati...")

    meta    = extract_metadata(filepath)
    n_meta  = sum(len(v) for v in meta.values() if isinstance(v, dict))
    log_cb(f"  Metadati: {n_meta} campi (funzionali/library/software)", "info")
    ogg_info = {}
    if ext.lower() in (".ogg", ".oga", ".opus"):
        ogg_info = ogg_header_check(filepath)
        log_cb(f"  OGG header: {'✓ valido' if ogg_info['valid'] else '✗ non valido'} "
               f"— {ogg_info['pages']} pagine", "ok" if ogg_info["valid"] else "warn")
    progress_cb(30, "Caricamento audio...")

    y, sr = load_audio(filepath)
    log_cb(f"  Audio: {len(y):,} campioni @ {sr:,} Hz", "info")
    progress_cb(40, "Analisi forma d'onda...")

    wf = analyze_waveform(y, sr)
    log_cb(f"  Durata: {wf['duration_fmt']} | RMS: {wf['rms_db']}dB "
           f"| Peak: {wf['peak_db']}dB | DC: {wf['dc_offset_global']}", "info")
    progress_cb(50, "ENF Analysis (§5.4.1)...")

    enf = analyze_enf(y, sr, nominal_hz=enf_nominal)
    if enf["enf_present"]:
        log_cb(f"  ENF: {enf['enf_mean_hz']}Hz | std={enf['enf_std_hz']} "
               f"| {enf['phase_jumps']} discontinuità",
               "warn" if enf["phase_jumps"] > 0 else "ok")
    else:
        log_cb(f"  ENF: non rilevabile ({enf.get('note','assente')})", "info")
    progress_cb(60, "Butt-splice detection (§5.4.2 Cooper)...")

    butt = detect_butt_splices(y, sr)
    log_cb(f"  Butt-splice: {butt['count']} rilevati",
           "warn" if butt["count"] > 0 else "ok")
    progress_cb(68, "DC offset locale (§5.4.2)...")

    dc_local = analyze_dc_offset_local(y, sr)
    n_dc_anom = len(dc_local.get("anomalies", []))
    log_cb(f"  DC locale: {n_dc_anom} anomalie | globale={dc_local['global_dc']}",
           "warn" if n_dc_anom > 0 else "ok")
    progress_cb(74, "Analisi quantizzazione (§5.4.4)...")

    quant = analyze_quantization_levels(y, sr)
    log_cb(f"  Quantizzazione: {quant['used_levels']}/{quant['total_levels']} livelli "
           f"| gain sospetto: {'SÌ' if quant.get('suspected_gain') else 'No'}",
           "warn" if quant.get("suspected_gain") else "ok")
    progress_cb(78, "Codec frame analysis (§5.4.3)...")

    codec_frames = {}
    if ext.lower() in (".ogg", ".oga", ".opus"):
        codec_frames = analyze_codec_frames_ogg(filepath)
        n_gj = len(codec_frames.get("granule_jumps", []))
        n_sq = len(codec_frames.get("seq_gaps", []))
        log_cb(f"  OGG frames: {codec_frames.get('total_pages',0)} pagine | "
               f"granule jumps={n_gj} | seq gaps={n_sq}",
               "warn" if (n_gj+n_sq)>0 else "ok")
    else:
        log_cb("  Codec frame analysis OGG: non applicabile (formato non OGG)", "info")
    progress_cb(83, "Double encoding detection (§5.4.4)...")

    double_enc = detect_double_encoding(y, sr, filepath)
    if double_enc.get("double_enc_suspected"):
        log_cb(f"  Double encoding: SOSPETTO (confidenza={double_enc['confidence']}) "
               f"| cutoff={double_enc.get('cutoff_freq_hz','?')}Hz", "warn")
    else:
        log_cb(f"  Double encoding: non rilevato", "ok")
    progress_cb(87, "Resampling detection (§5.4.4)...")

    resampling = detect_resampling(y, sr)
    if resampling.get("resampling_detected"):
        log_cb(f"  Resampling: RILEVATO (T={resampling['period_samples']} campioni, "
               f"confidenza={resampling['confidence']}) | {resampling.get('note','')}",
               "warn")
    else:
        log_cb(f"  Resampling: non rilevato", "ok")
    progress_cb(91, "Copy-move forgery detection (§5.4.4)...")

    copy_move = detect_copy_move(y, sr)
    if copy_move.get("copy_move_suspected"):
        log_cb(f"  Copy-move: {len(copy_move['matches_found'])} coppie sospette | "
               f"max sim={copy_move['max_similarity']:.4f}", "warn")
    else:
        log_cb(f"  Copy-move: nessun segmento duplicato rilevato", "ok")

    progress_cb(94, "Analisi spettrale + LTAS...")
    sp   = spectral_features(y, sr)
    ltas = compute_ltas(y, sr)
    log_cb(f"  Centroide spettrale: {sp.get('centroid_mean_hz','N/A')} Hz", "info")
    progress_cb(96, "Rilevamento anomalie integrate (v4.0)...")

    anoms = detect_anomalies(y, sr, wf, enf, dc_local, butt, quant,
                             codec_frames=codec_frames,
                             double_enc=double_enc,
                             resampling=resampling,
                             copy_move=copy_move)
    log_cb(f"  Anomalie totali: {len(anoms)}", "warn" if anoms else "ok")
    for a in anoms:
        col = {"ALTA":"err","MEDIA":"warn","BASSA":"info"}.get(a["severità"],"info")
        log_cb(f"    ⚑ [{a['severità']}] {a['tipo']}: {a['dettaglio']}", col)
    progress_cb(97, "Grafici...")

    fig = generate_plots_fig(y, sr, enf_data=enf, butt_data=butt)
    plot_path = save_plots_png(fig, out_dir, bn)
    log_cb(f"  Grafici: {os.path.basename(plot_path)}", "info")
    progress_cb(99, "Report HTML...")

    iv = integrity_verdict(anoms, fi["format_detected"], ext,
                           enf, butt, dc_local, quant,
                           codec_frames=codec_frames,
                           double_enc=double_enc,
                           resampling=resampling,
                           copy_move=copy_move)
    data = {
        "file_info":    fi,
        "hashes":       hashes,
        "metadata":     meta,
        "ogg_info":     ogg_info,
        "waveform":     wf,
        "enf":          enf,
        "butt_splice":  butt,
        "dc_local":     dc_local,
        "quantization": quant,
        "codec_frames": codec_frames,
        "double_enc":   double_enc,
        "resampling":   resampling,
        "copy_move":    copy_move,
        "ltas":         ltas,
        "anomalies":    anoms,
        "spectral":     sp,
        "integrity":    iv,
        "bpm_ref":      BPM_REF,
        "analysis_ts":  datetime.datetime.now().isoformat(),
    }

    report_path = os.path.join(out_dir, f"{bn}_report.html")
    generate_html_report(data, plot_path, report_path)

    json_path = os.path.join(out_dir, f"{bn}_forensics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    log_cb(f"  Report: {os.path.basename(report_path)}", "ok")
    log_cb(f"  JSON:   {os.path.basename(json_path)}", "ok")
    progress_cb(100, "Completato")

    vc_map = {"INTEGRO":"ok","SOSPETTO":"warn","NON ATTENDIBILE":"err"}
    log_cb(f"\n  ══ VERDETTO: {iv['verdict']}  |  Score: {iv['score']}/100 ══",
           vc_map[iv["verdict"]])

    data["_report_path"] = report_path
    data["_plot_path"]   = plot_path
    data["_fig"]         = fig
    return data

# ═════════════════════════════════════════════════════════════════════════════
# GUI
# ═════════════════════════════════════════════════════════════════════════════

class ToolTip:
    def __init__(self, widget, text):
        self.widget=widget; self.text=text; self.tip=None
        widget.bind("<Enter>",self.show); widget.bind("<Leave>",self.hide)
    def show(self,e=None):
        x=self.widget.winfo_rootx()+20; y=self.widget.winfo_rooty()+20
        self.tip=tk.Toplevel(self.widget); self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        Label(self.tip,text=self.text,background="#2c2c2e",foreground="#f2f2f7",
              relief="solid",borderwidth=1,font=("Segoe UI",9),padx=6,pady=3).pack()
    def hide(self,e=None):
        if self.tip: self.tip.destroy(); self.tip=None


class AudioForensicsApp(Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Audio Forensics Analyzer  v{VERSION}  —  {BPM_REF}")
        self.geometry("1240x800")
        self.minsize(960, 640)
        self.configure(bg=C["bg"])

        self.file_queue     = []
        self.results        = {}
        self.current_result = None
        self.analyzing      = False
        self._stop_flag     = False
        self.output_dir     = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "ForensicsOutput"))
        self.enf_nominal    = tk.DoubleVar(value=ENF_NOMINAL_EU)

        self._build_styles()
        self._build_menu()
        self._build_toolbar()
        self._build_main()
        self._build_statusbar()
        self._check_deps()

    # ── STILI ─────────────────────────────────────────────────────────────────
    def _build_styles(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TNotebook",background=C["panel"],borderwidth=0,tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",background=C["panel2"],foreground=C["text2"],
                    padding=[14,6],font=("Segoe UI",9),borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected",C["bg"]),("active",C["panel"])],
              foreground=[("selected",C["accent"]),("active",C["text"])])
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=C["panel2"],background=C["accent"],
                    borderwidth=0,thickness=6)
        s.configure("Treeview",background=C["listbg"],foreground=C["text"],
                    fieldbackground=C["listbg"],rowheight=24,borderwidth=0,
                    font=("Segoe UI",9))
        s.configure("Treeview.Heading",background=C["panel2"],foreground=C["text2"],
                    font=("Segoe UI",9,"bold"),borderwidth=0)
        s.map("Treeview",background=[("selected",C["accent"])],
              foreground=[("selected","#ffffff")])
        s.configure("TScrollbar",background=C["panel2"],troughcolor=C["panel"],
                    borderwidth=0,arrowsize=12)

    # ── MENU ──────────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb=Menu(self,bg=C["panel"],fg=C["text"],activebackground=C["accent"],
                activeforeground="#fff",relief="flat",bd=0,font=("Segoe UI",9))
        self.config(menu=mb)
        def m(parent,label,cmd,sep=False):
            if sep: parent.add_separator()
            else: parent.add_command(label=label,command=cmd)
        def sub(label):
            mm=Menu(mb,tearoff=0,bg=C["panel"],fg=C["text"],
                    activebackground=C["accent"],activeforeground="#fff",
                    font=("Segoe UI",9))
            mb.add_cascade(label=label,menu=mm); return mm

        fm=sub("File")
        m(fm,"Aggiungi file…\tCtrl+O",self.add_files)
        m(fm,"Aggiungi cartella…",self.add_folder)
        m(fm,"",None,sep=True)
        m(fm,"Imposta output…",self.choose_output)
        m(fm,"",None,sep=True)
        m(fm,"Esci",self.destroy)

        am=sub("Analisi")
        m(am,"Analizza selezionati\tF5",self.run_selected)
        m(am,"Analizza tutti\tF6",self.run_all)
        m(am,"",None,sep=True)
        m(am,"Imposta ENF 50Hz (Europa)",lambda:self.enf_nominal.set(50.0))
        m(am,"Imposta ENF 60Hz (USA/JP)",lambda:self.enf_nominal.set(60.0))
        m(am,"",None,sep=True)
        m(am,"Rimuovi selezionati",self.remove_selected)
        m(am,"Svuota lista",self.clear_queue)

        rm=sub("Report")
        m(rm,"Apri report HTML",self.open_report)
        m(rm,"Apri cartella output",self.open_output_dir)
        m(rm,"Esporta JSON",self.export_json)

        hm=sub("?")
        m(hm,"Verifica dipendenze",self._check_deps_dialog)
        m(hm,"Riferimenti ENFSI",self.show_enfsi_refs)
        m(hm,"",None,sep=True)
        m(hm,"Informazioni…",self.show_about)

        self.bind("<Control-o>",lambda e: self.add_files())
        self.bind("<F5>",lambda e: self.run_selected())
        self.bind("<F6>",lambda e: self.run_all())

    # ── TOOLBAR ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        tb=Frame(self,bg=C["toolbar"],height=44,bd=0)
        tb.pack(fill="x",side="top"); tb.pack_propagate(False)

        def tbtn(text, cmd, tip=""):
            b=Button(tb,text=text,command=cmd,bg=C["toolbar"],fg=C["text"],
                     activebackground=C["panel2"],activeforeground=C["accent"],
                     relief="flat",bd=0,padx=10,pady=6,font=("Segoe UI",9),
                     cursor="hand2")
            b.pack(side="left",padx=1,pady=4)
            if tip: ToolTip(b,tip)
            return b

        def sep():
            Frame(tb,bg=C["border"],width=1).pack(side="left",fill="y",padx=4,pady=6)

        tbtn("📂 Aggiungi",self.add_files,"Aggiungi file audio (Ctrl+O)")
        tbtn("📁 Cartella",self.add_folder,"Aggiungi cartella")
        sep()
        self.btn_run  = tbtn("▶ Analizza",self.run_all,"Analizza tutti (F6)")
        self.btn_stop = tbtn("■ Stop",self.stop_analysis,"Interrompi")
        self.btn_stop.config(state="disabled")
        sep()
        tbtn("🌐 Report",self.open_report,"Apri report HTML")
        tbtn("📂 Output",self.open_output_dir,"Apri cartella output")
        sep()

        # ENF selector
        Label(tb,text="ENF:",bg=C["toolbar"],fg=C["text2"],
              font=("Segoe UI",9)).pack(side="left",padx=(4,2))
        enf_menu=tk.OptionMenu(tb,self.enf_nominal,50.0,60.0)
        enf_menu.config(bg=C["panel2"],fg=C["text"],activebackground=C["accent"],
                        relief="flat",highlightthickness=0,font=("Segoe UI",8))
        enf_menu.pack(side="left",padx=2)
        Label(tb,text="Hz",bg=C["toolbar"],fg=C["text2"],
              font=("Segoe UI",9)).pack(side="left",padx=(0,4))
        sep()

        Label(tb,text="Output:",bg=C["toolbar"],fg=C["text2"],
              font=("Segoe UI",9)).pack(side="left",padx=(4,2))
        Entry(tb,textvariable=self.output_dir,bg=C["panel2"],fg=C["text"],
              insertbackground=C["text"],relief="flat",font=("Segoe UI",9),
              width=28,bd=0).pack(side="left",ipady=4,padx=2)
        tbtn("…",self.choose_output,"Scegli output")

    # ── LAYOUT PRINCIPALE ─────────────────────────────────────────────────────
    def _build_main(self):
        pw=tk.PanedWindow(self,orient="horizontal",bg=C["border"],
                          sashwidth=4,sashrelief="flat",bd=0)
        pw.pack(fill="both",expand=True)

        # ── SINISTRA: lista file ───────────────────────────────────────────────
        left=Frame(pw,bg=C["panel"],width=290)
        pw.add(left,minsize=200)

        lh=Frame(left,bg=C["panel2"],height=32)
        lh.pack(fill="x"); lh.pack_propagate(False)
        Label(lh,text="FILE IN CODA",bg=C["panel2"],fg=C["accent"],
              font=("Segoe UI",8,"bold"),padx=10).pack(side="left",pady=6)
        self.lbl_count=Label(lh,text="0 file",bg=C["panel2"],fg=C["text3"],
                             font=("Segoe UI",8))
        self.lbl_count.pack(side="right",padx=8)

        self.file_tree=ttk.Treeview(left,columns=("stato","nome","dim"),
                                     show="headings",selectmode="extended")
        self.file_tree.heading("stato",text="")
        self.file_tree.heading("nome",text="Nome file")
        self.file_tree.heading("dim",text="Dim.")
        self.file_tree.column("stato",width=28,stretch=False,anchor="center")
        self.file_tree.column("nome",width=175,stretch=True)
        self.file_tree.column("dim",width=60,stretch=False,anchor="e")
        self.file_tree.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(left,orient="vertical",
                      command=self.file_tree.yview).pack(side="right",fill="y")
        self.file_tree.configure(yscrollcommand=lambda *a:None)
        for tag,fg in [("ok",C["accent2"]),("warn",C["warn"]),
                        ("err",C["danger"]),("queue",C["text2"]),("run",C["accent"])]:
            self.file_tree.tag_configure(tag,foreground=fg)
        self.file_tree.bind("<Double-1>",lambda e: self.show_file_result())
        self.file_tree.bind("<Button-3>",self._context_menu)

        btnf=Frame(left,bg=C["panel"],pady=4)
        btnf.pack(fill="x")
        for txt,cmd,bg in [("+ Aggiungi",self.add_files,C["accent"]),
                            ("✕ Rimuovi",self.remove_selected,C["panel2"]),
                            ("Svuota",self.clear_queue,C["panel2"])]:
            Button(btnf,text=txt,command=cmd,bg=bg,
                   fg="#fff" if bg==C["accent"] else C["text2"],
                   relief="flat",font=("Segoe UI",8),cursor="hand2",
                   padx=8,pady=3).pack(side="left",padx=4)

        # ── DESTRA ────────────────────────────────────────────────────────────
        right=Frame(pw,bg=C["bg"])
        pw.add(right,minsize=640)
        vpw=tk.PanedWindow(right,orient="vertical",bg=C["border"],
                           sashwidth=4,sashrelief="flat",bd=0)
        vpw.pack(fill="both",expand=True)

        # Notebook risultati
        nb_frame=Frame(vpw,bg=C["bg"])
        vpw.add(nb_frame,minsize=380)
        self.nb=ttk.Notebook(nb_frame)
        self.nb.pack(fill="both",expand=True)
        self.tab_summary = self._make_summary_tab()
        self.tab_charts  = self._make_charts_tab()
        self.tab_enf     = self._make_enf_tab()
        self.tab_advanced= self._make_advanced_tab()
        self.tab_details = self._make_details_tab()
        self.tab_hash    = self._make_hash_tab()

        # Log
        log_frame=Frame(vpw,bg=C["log_bg"])
        vpw.add(log_frame,minsize=130)
        lh2=Frame(log_frame,bg=C["panel2"],height=26)
        lh2.pack(fill="x"); lh2.pack_propagate(False)
        Label(lh2,text="LOG ANALISI",bg=C["panel2"],fg=C["accent"],
              font=("Segoe UI",8,"bold"),padx=8).pack(side="left",pady=4)
        Button(lh2,text="Pulisci",command=self.clear_log,bg=C["panel2"],
               fg=C["text3"],relief="flat",font=("Segoe UI",7),
               cursor="hand2",padx=6).pack(side="right",padx=6,pady=3)
        self.log_text=Text(log_frame,bg=C["log_bg"],fg=C["text"],
                           insertbackground=C["text"],relief="flat",
                           font=("Consolas",9),wrap="word",state="disabled",bd=0)
        self.log_text.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(log_frame,orient="vertical",
                      command=self.log_text.yview).pack(side="right",fill="y")
        for tag,fg in [("ok",C["log_ok"]),("warn",C["log_warn"]),
                        ("err",C["log_err"]),("info",C["log_info"]),
                        ("norm",C["text"])]:
            self.log_text.tag_configure(tag,foreground=fg)

    # ── TAB SOMMARIO ──────────────────────────────────────────────────────────
    def _make_summary_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Sommario  ")

        self.verd_frame=Frame(f,bg=C["panel2"])
        self.verd_frame.pack(fill="x",padx=12,pady=(12,6))
        self.lbl_verd_icon=Label(self.verd_frame,text="—",font=("Segoe UI",28),
                                  bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_icon.pack(side="left",padx=(12,6),pady=8)
        vt=Frame(self.verd_frame,bg=C["panel2"]); vt.pack(side="left",pady=8)
        self.lbl_verd_text=Label(vt,text="Nessun file analizzato",
                                  font=("Segoe UI",16,"bold"),bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_text.pack(anchor="w")
        self.lbl_verd_sub=Label(vt,text="Aggiungi file e avvia l'analisi",
                                 font=("Segoe UI",9),bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_sub.pack(anchor="w")
        self.lbl_score=Label(self.verd_frame,text="—",
                              font=("Consolas",36,"bold"),bg=C["panel2"],fg=C["text3"])
        self.lbl_score.pack(side="right",padx=16,pady=8)

        # Card rapide (6 card: +ENF e butt-splice)
        grid=Frame(f,bg=C["bg"]); grid.pack(fill="x",padx=12,pady=4)
        grid.columnconfigure((0,1,2,3,4,5),weight=1,uniform="c")
        self.summary_cards={}
        for i,(key,label) in enumerate([
            ("duration","Durata"),("samplerate","Sample Rate"),
            ("peak","Peak"),("rms","RMS"),
            ("enf","ENF"),("butt","Splice PCM"),
        ]):
            card=Frame(grid,bg=C["panel"]); card.grid(row=0,column=i,padx=3,pady=4,sticky="nsew")
            Label(card,text=label.upper(),font=("Segoe UI",7),bg=C["panel"],
                  fg=C["text3"]).pack(pady=(6,2))
            lv=Label(card,text="—",font=("Consolas",12,"bold"),bg=C["panel"],fg=C["accent"])
            lv.pack(pady=(0,6))
            self.summary_cards[key]=lv

        # Anomalie
        af=Frame(f,bg=C["panel"]); af.pack(fill="both",expand=True,padx=12,pady=(4,8))
        Label(af,text="ANOMALIE RILEVATE",font=("Segoe UI",8,"bold"),
              bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        self.anom_tree=ttk.Treeview(af,
            columns=("sev","tipo","dettaglio","ref"),
            show="headings",height=7)
        self.anom_tree.heading("sev",text="Severità")
        self.anom_tree.heading("tipo",text="Tipo")
        self.anom_tree.heading("dettaglio",text="Dettaglio")
        self.anom_tree.heading("ref",text="Rif. BPM")
        self.anom_tree.column("sev",width=80,stretch=False,anchor="center")
        self.anom_tree.column("tipo",width=160,stretch=False)
        self.anom_tree.column("dettaglio",width=240,stretch=True)
        self.anom_tree.column("ref",width=150,stretch=False)
        self.anom_tree.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(af,orient="vertical",command=self.anom_tree.yview).pack(side="right",fill="y")
        for tag,fg in [("ALTA",C["danger"]),("MEDIA",C["warn"]),("BASSA",C["log_info"])]:
            self.anom_tree.tag_configure(tag,foreground=fg)

        pf=Frame(f,bg=C["bg"]); pf.pack(fill="x",padx=12,pady=(0,6))
        self.progress_bar=ttk.Progressbar(pf,style="Accent.Horizontal.TProgressbar",
                                           orient="horizontal",mode="determinate")
        self.progress_bar.pack(fill="x",side="left",expand=True)
        self.lbl_progress=Label(pf,text="",font=("Segoe UI",8),
                                 bg=C["bg"],fg=C["text2"],width=28,anchor="e")
        self.lbl_progress.pack(side="right",padx=(6,0))
        return f

    # ── TAB GRAFICI ───────────────────────────────────────────────────────────
    def _make_charts_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Grafici  ")
        self.chart_placeholder=Label(f,
            text="Nessun grafico disponibile.\nEsegui prima un'analisi.",
            bg=C["bg"],fg=C["text3"],font=("Segoe UI",12))
        self.chart_placeholder.pack(expand=True)
        self.chart_canvas_widget=None
        return f

    # ── TAB ENF (NUOVO v3.0) ─────────────────────────────────────────────────
    def _make_enf_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  ENF Analysis  ")

        # Header
        hf=Frame(f,bg=C["panel"]); hf.pack(fill="x",padx=12,pady=(12,6))
        Label(hf,text="ELECTRIC NETWORK FREQUENCY — §5.4.1 ENFSI BPM-FSA-002",
              font=("Segoe UI",8,"bold"),bg=C["panel"],
              fg=C["accent"],padx=10).pack(anchor="w",pady=(8,2))
        Label(hf,text="Variazioni ENF (50/60Hz) lasciano tracce nel segnale. "
              "Discontinuità indicano possibile inserimento/rimozione di contenuto.",
              font=("Segoe UI",8),bg=C["panel"],fg=C["text2"],padx=10,
              wraplength=700,justify="left").pack(anchor="w",pady=(0,8))

        # Parametri ENF
        ef=Frame(f,bg=C["panel2"]); ef.pack(fill="x",padx=12,pady=(0,6))
        Label(ef,text="RISULTATI ENF",font=("Segoe UI",8,"bold"),
              bg=C["panel2"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        self.enf_tree=ttk.Treeview(ef,columns=("param","valore"),
                                    show="headings",height=8)
        self.enf_tree.heading("param",text="Parametro")
        self.enf_tree.heading("valore",text="Valore")
        self.enf_tree.column("param",width=220,stretch=False)
        self.enf_tree.column("valore",width=400,stretch=True)
        self.enf_tree.pack(fill="x",padx=0,pady=(0,8))

        # Butt-splice
        bf=Frame(f,bg=C["panel"]); bf.pack(fill="x",padx=12,pady=(0,6))
        Label(bf,text="BUTT-SPLICE PCM — §5.4.2 ENFSI BPM-FSA-002 (Cooper 2010)",
              font=("Segoe UI",8,"bold"),bg=C["panel"],
              fg=C["accent"],padx=10).pack(anchor="w",pady=(8,2))
        Label(bf,text="Differenziale 1°/2° ordine su campioni PCM. "
              "Efficace su file non ricodificati dopo l'editing.",
              font=("Segoe UI",8),bg=C["panel"],fg=C["text2"],padx=10).pack(anchor="w",pady=(0,4))
        self.splice_tree=ttk.Treeview(bf,
            columns=("ts","ts_fmt","ratio","mag"),show="headings",height=6)
        self.splice_tree.heading("ts",text="t (sec)")
        self.splice_tree.heading("ts_fmt",text="Timestamp")
        self.splice_tree.heading("ratio",text="Ratio (σ)")
        self.splice_tree.heading("mag",text="Magnitudine")
        self.splice_tree.column("ts",width=80,stretch=False,anchor="e")
        self.splice_tree.column("ts_fmt",width=110,stretch=False)
        self.splice_tree.column("ratio",width=90,stretch=False,anchor="e")
        self.splice_tree.column("mag",width=120,stretch=False,anchor="e")
        self.splice_tree.pack(fill="x",padx=0,side="left")
        ttk.Scrollbar(bf,orient="vertical",
                      command=self.splice_tree.yview).pack(side="right",fill="y")

        # DC local
        dcf=Frame(f,bg=C["panel2"]); dcf.pack(fill="both",expand=True,padx=12,pady=(0,8))
        Label(dcf,text="DC OFFSET LOCALE — §5.4.2 ENFSI BPM-FSA-002 (Koenig [20][21][22])",
              font=("Segoe UI",8,"bold"),bg=C["panel2"],
              fg=C["accent"],padx=10).pack(anchor="w",pady=(8,2))
        self.dc_tree=ttk.Treeview(dcf,
            columns=("ts","dc_l","dc_g","delta"),show="headings",height=5)
        self.dc_tree.heading("ts",text="Timestamp")
        self.dc_tree.heading("dc_l",text="DC locale")
        self.dc_tree.heading("dc_g",text="DC globale")
        self.dc_tree.heading("delta",text="Δ DC")
        for col in ("ts","dc_l","dc_g","delta"):
            self.dc_tree.column(col,width=120,stretch=True,anchor="e")
        self.dc_tree.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(dcf,orient="vertical",
                      command=self.dc_tree.yview).pack(side="right",fill="y")
        return f

    # ── TAB ADVANCED v4.0 ─────────────────────────────────────────────────────
    def _make_advanced_tab(self):
        f = Frame(self.nb, bg=C["bg"])
        self.nb.add(f, text="  Analisi Avanzata  ")

        # ── OGG Codec frames ──────────────────────────────────────────────────
        cf_f = Frame(f, bg=C["panel"]); cf_f.pack(fill="x", padx=12, pady=(12,6))
        Label(cf_f, text="OGG CODEC FRAME ANALYSIS — §5.4.3 (Gärtner [33], Korycki [34], Yang [35])",
              font=("Segoe UI",8,"bold"), bg=C["panel"], fg=C["accent"], padx=10).pack(anchor="w", pady=(8,2))
        Label(cf_f, text="Granule position monotonicamente crescente · Sequence number consecutivo · Page size anomalie",
              font=("Segoe UI",8), bg=C["panel"], fg=C["text2"], padx=10).pack(anchor="w", pady=(0,4))
        self.cf_tree = ttk.Treeview(cf_f, columns=("param","valore"), show="headings", height=5)
        self.cf_tree.heading("param",  text="Parametro")
        self.cf_tree.heading("valore", text="Valore")
        self.cf_tree.column("param",  width=220, stretch=False)
        self.cf_tree.column("valore", width=400, stretch=True)
        self.cf_tree.pack(fill="x", padx=0, pady=(0,4))

        self.cf_anom_tree = ttk.Treeview(cf_f,
            columns=("tipo","serial","offset","detail"), show="headings", height=4)
        self.cf_anom_tree.heading("tipo",   text="Tipo")
        self.cf_anom_tree.heading("serial", text="Serial")
        self.cf_anom_tree.heading("offset", text="Offset hex")
        self.cf_anom_tree.heading("detail", text="Dettaglio")
        for col, w in [("tipo",100),("serial",80),("offset",100),("detail",280)]:
            self.cf_anom_tree.column(col, width=w, stretch=(col=="detail"))
        self.cf_anom_tree.pack(fill="x", padx=0, pady=(0,8))
        self.cf_anom_tree.tag_configure("jump", foreground=C["danger"])
        self.cf_anom_tree.tag_configure("gap",  foreground=C["warn"])

        # ── Double encoding ───────────────────────────────────────────────────
        de_f = Frame(f, bg=C["panel2"]); de_f.pack(fill="x", padx=12, pady=(0,6))
        Label(de_f, text="DOUBLE ENCODING DETECTION — §5.4.4 (Bianchi [69], Korycki [71])",
              font=("Segoe UI",8,"bold"), bg=C["panel2"], fg=C["accent"], padx=10).pack(anchor="w", pady=(8,2))
        Label(de_f, text="Cutoff frequency · MDCT periodicità · Notches spettrali",
              font=("Segoe UI",8), bg=C["panel2"], fg=C["text2"], padx=10).pack(anchor="w", pady=(0,4))
        self.de_tree = ttk.Treeview(de_f, columns=("param","valore"), show="headings", height=5)
        self.de_tree.heading("param",  text="Parametro")
        self.de_tree.heading("valore", text="Valore")
        self.de_tree.column("param",  width=220, stretch=False)
        self.de_tree.column("valore", width=400, stretch=True)
        self.de_tree.pack(fill="x", padx=0, pady=(0,8))

        # ── Resampling ────────────────────────────────────────────────────────
        rs_f = Frame(f, bg=C["panel"]); rs_f.pack(fill="x", padx=12, pady=(0,6))
        Label(rs_f, text="RESAMPLING DETECTION — §5.4.4 (Vázquez-Padín [67][68])",
              font=("Segoe UI",8,"bold"), bg=C["panel"], fg=C["accent"], padx=10).pack(anchor="w", pady=(8,2))
        Label(rs_f, text="Autocorrelazione residuo LP · Periodicità inter-sample · Stima ratio P/Q",
              font=("Segoe UI",8), bg=C["panel"], fg=C["text2"], padx=10).pack(anchor="w", pady=(0,4))
        self.rs_tree = ttk.Treeview(rs_f, columns=("param","valore"), show="headings", height=6)
        self.rs_tree.heading("param",  text="Parametro")
        self.rs_tree.heading("valore", text="Valore")
        self.rs_tree.column("param",  width=220, stretch=False)
        self.rs_tree.column("valore", width=400, stretch=True)
        self.rs_tree.pack(fill="x", padx=0, pady=(0,8))

        # ── Copy-Move ─────────────────────────────────────────────────────────
        cm_f = Frame(f, bg=C["panel2"]); cm_f.pack(fill="both", expand=True, padx=12, pady=(0,8))
        Label(cm_f, text="COPY-MOVE FORGERY DETECTION — §5.4.4 (Imran [62], Maksimović [63])",
              font=("Segoe UI",8,"bold"), bg=C["panel2"], fg=C["accent"], padx=10).pack(anchor="w", pady=(8,2))
        Label(cm_f, text="MFCC fingerprint · Similarità coseno · Cross-correlazione waveform",
              font=("Segoe UI",8), bg=C["panel2"], fg=C["text2"], padx=10).pack(anchor="w", pady=(0,4))
        self.cm_tree = ttk.Treeview(cm_f,
            columns=("t1","t2","sim","xcorr","gap"), show="headings", height=6)
        self.cm_tree.heading("t1",    text="Segmento 1")
        self.cm_tree.heading("t2",    text="Segmento 2")
        self.cm_tree.heading("sim",   text="Similarità")
        self.cm_tree.heading("xcorr", text="XCorr")
        self.cm_tree.heading("gap",   text="Gap (s)")
        for col, w in [("t1",100),("t2",100),("sim",90),("xcorr",80),("gap",70)]:
            self.cm_tree.column(col, width=w, stretch=(col=="t1"))
        self.cm_tree.pack(fill="both", expand=True, side="left")
        ttk.Scrollbar(cm_f, orient="vertical",
                      command=self.cm_tree.yview).pack(side="right", fill="y")
        self.cm_tree.tag_configure("match", foreground=C["danger"])
        return f

    # ── TAB DETTAGLI ──────────────────────────────────────────────────────────
    def _make_details_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Dettagli  ")
        self.detail_tree=ttk.Treeview(f,columns=("p","v"),show="headings")
        self.detail_tree.heading("p",text="Parametro")
        self.detail_tree.heading("v",text="Valore")
        self.detail_tree.column("p",width=240,stretch=False)
        self.detail_tree.column("v",width=400,stretch=True)
        self.detail_tree.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(f,orient="vertical",command=self.detail_tree.yview).pack(side="right",fill="y")
        self.detail_tree.tag_configure("head",foreground=C["accent"],
                                        font=("Segoe UI",9,"bold"))
        return f

    # ── TAB HASH ──────────────────────────────────────────────────────────────
    def _make_hash_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Hash & Metadati  ")

        hf=Frame(f,bg=C["panel"]); hf.pack(fill="x",padx=12,pady=(12,6))
        Label(hf,text="HASH DI INTEGRITÀ — §8 ENFSI BPM-FSA-002",
              font=("Segoe UI",8,"bold"),bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        self.hash_vars={}
        for algo in ("SHA-1","SHA-256","MD5"):
            row_f=Frame(hf,bg=C["panel"]); row_f.pack(fill="x",padx=10,pady=2)
            Label(row_f,text=algo+":",font=("Segoe UI",9,"bold"),bg=C["panel"],
                  fg=C["text2"],width=8,anchor="e").pack(side="left")
            v=tk.StringVar(value="—"); self.hash_vars[algo]=v
            Entry(row_f,textvariable=v,state="readonly",relief="flat",
                  bg=C["panel2"],fg=C["log_info"],readonlybackground=C["panel2"],
                  font=("Consolas",9),bd=0).pack(side="left",fill="x",expand=True,ipady=4,padx=(4,0))
            Button(row_f,text="Copia",command=lambda a=algo:self._copy_hash(a),
                   bg=C["panel2"],fg=C["text2"],relief="flat",font=("Segoe UI",8),
                   cursor="hand2",padx=6,pady=2).pack(side="left",padx=(4,8))
        Label(hf,text="",bg=C["panel"]).pack(pady=2)

        # Confronta hash
        cmp_f=Frame(f,bg=C["panel2"]); cmp_f.pack(fill="x",padx=12,pady=(0,6))
        Label(cmp_f,text="CONFRONTA HASH (catena di custodia)",
              font=("Segoe UI",8,"bold"),bg=C["panel2"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        cr=Frame(cmp_f,bg=C["panel2"]); cr.pack(fill="x",padx=10,pady=(0,8))
        self.cmp_entry=Entry(cr,bg=C["panel"],fg=C["text"],insertbackground=C["text"],
                             relief="flat",font=("Consolas",9),bd=0)
        self.cmp_entry.pack(side="left",fill="x",expand=True,ipady=5,padx=(0,4))
        Button(cr,text="Verifica",command=self.compare_hash,
               bg=C["accent"],fg="#fff",relief="flat",font=("Segoe UI",9),
               cursor="hand2",padx=12,pady=4).pack(side="left")
        self.lbl_cmp=Label(cmp_f,text="",bg=C["panel2"],font=("Segoe UI",9))
        self.lbl_cmp.pack(anchor="w",padx=10,pady=(0,6))

        # Metadati categorizzati
        mf=Frame(f,bg=C["panel"]); mf.pack(fill="both",expand=True,padx=12,pady=(0,8))
        Label(mf,text="METADATI CATEGORIZZATI — §5.4.4 ENFSI BPM-FSA-002",
              font=("Segoe UI",8,"bold"),bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        self.meta_tree=ttk.Treeview(mf,columns=("cat","k","v"),show="headings",height=8)
        self.meta_tree.heading("cat",text="Categoria")
        self.meta_tree.heading("k",text="Chiave")
        self.meta_tree.heading("v",text="Valore")
        self.meta_tree.column("cat",width=100,stretch=False)
        self.meta_tree.column("k",width=180,stretch=False)
        self.meta_tree.column("v",width=320,stretch=True)
        self.meta_tree.pack(fill="both",expand=True,side="left")
        ttk.Scrollbar(mf,orient="vertical",command=self.meta_tree.yview).pack(side="right",fill="y")
        return f

    # ── STATUSBAR ─────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        sb=Frame(self,bg=C["statusbar"],height=22,bd=0)
        sb.pack(fill="x",side="bottom"); sb.pack_propagate(False)
        self.lbl_status=Label(sb,
            text=f"Audio Forensics Analyzer v{VERSION}  —  Pronto",
            bg=C["statusbar"],fg=C["text3"],font=("Segoe UI",8),padx=8)
        self.lbl_status.pack(side="left")
        Label(sb,text=f"{BPM_REF}  |  {platform.system()} {platform.release()}",
              bg=C["statusbar"],fg=C["text3"],font=("Segoe UI",8),padx=8).pack(side="right")

    # ── CONTEXT MENU ──────────────────────────────────────────────────────────
    def _context_menu(self,event):
        if not self.file_tree.selection(): return
        m=Menu(self,tearoff=0,bg=C["panel"],fg=C["text"],
               activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        m.add_command(label="Analizza selezionati",command=self.run_selected)
        m.add_command(label="Apri report HTML",command=self.open_report)
        m.add_separator()
        m.add_command(label="Mostra in Esplora file",command=self.reveal_in_explorer)
        m.add_separator()
        m.add_command(label="Rimuovi",command=self.remove_selected)
        try: m.tk_popup(event.x_root,event.y_root)
        finally: m.grab_release()

    # ── GESTIONE FILE ─────────────────────────────────────────────────────────
    def add_files(self):
        paths=filedialog.askopenfilenames(
            title="Seleziona file audio",
            filetypes=[("File audio",
                "*.wav *.mp3 *.ogg *.flac *.aiff *.aif *.m4a *.wma *.opus *.oga"),
                       ("Tutti","*.*")])
        for p in paths: self._queue_file(p)
        self._update_count()

    def add_folder(self):
        folder=filedialog.askdirectory(title="Seleziona cartella")
        if not folder: return
        added=0
        for root,dirs,files in os.walk(folder):
            for fn in files:
                if Path(fn).suffix.lower() in SUPPORTED_EXT:
                    self._queue_file(os.path.join(root,fn)); added+=1
        self._update_count()
        self.log(f"Cartella: {added} file trovati","info")

    def _queue_file(self,path):
        if path in self.file_queue: return
        self.file_queue.append(path)
        sz=format_bytes(os.path.getsize(path))
        self.file_tree.insert("","end",iid=path,
                               values=("⏳",os.path.basename(path),sz),tags=("queue",))

    def remove_selected(self):
        for iid in self.file_tree.selection():
            if iid in self.file_queue: self.file_queue.remove(iid)
            self.file_tree.delete(iid)
        self._update_count()

    def clear_queue(self):
        if self.analyzing:
            messagebox.showwarning("Analisi in corso","Attendere o premere Stop.")
            return
        self.file_queue.clear()
        for i in self.file_tree.get_children(): self.file_tree.delete(i)
        self.results.clear(); self._update_count()

    def choose_output(self):
        d=filedialog.askdirectory(title="Cartella output",initialdir=self.output_dir.get())
        if d: self.output_dir.set(d)

    def _update_count(self):
        n=len(self.file_queue)
        self.lbl_count.config(text=f"{n} file")

    def reveal_in_explorer(self):
        sel=self.file_tree.selection()
        if not sel: return
        p=sel[0]
        if platform.system()=="Windows": subprocess.Popen(["explorer","/select,",p])
        elif platform.system()=="Darwin": subprocess.Popen(["open","-R",p])
        else: subprocess.Popen(["xdg-open",os.path.dirname(p)])

    # ── ANALISI ───────────────────────────────────────────────────────────────
    def run_all(self):      self._start_analysis(self.file_queue[:])
    def run_selected(self):
        sel=list(self.file_tree.selection())
        if not sel: messagebox.showinfo("Nessuna selezione","Seleziona almeno un file."); return
        self._start_analysis(sel)
    def stop_analysis(self): self._stop_flag=True; self.log("⚠ Stop richiesto...","warn")

    def _start_analysis(self,files):
        if not files: messagebox.showinfo("Lista vuota","Aggiungi file prima."); return
        if MISSING:
            messagebox.showerror("Dipendenze mancanti",
                                  f"Installa: pip install {' '.join(MISSING)}"); return
        if self.analyzing: return
        self.analyzing=True; self._stop_flag=False
        self.btn_run.config(state="disabled"); self.btn_stop.config(state="normal")
        self.set_status("Analisi in corso…")
        nominal = float(self.enf_nominal.get())

        def worker():
            out=self.output_dir.get(); os.makedirs(out,exist_ok=True)
            for path in files:
                if self._stop_flag: break
                self.after(0,lambda p=path:self.file_tree.item(
                    p,values=("🔄",os.path.basename(p),
                              format_bytes(os.path.getsize(p))),tags=("run",)))
                self.after(0,self.nb.select,0)
                try:
                    data=run_analysis(
                        path, out,
                        lambda pct,msg,p=path:self.after(0,self._on_progress,pct,msg,p),
                        lambda msg,tag="norm":self.after(0,self.log,msg,tag),
                        enf_nominal=nominal
                    )
                    self.results[path]=data
                    verd=data["integrity"]["verdict"]
                    tag={"INTEGRO":"ok","SOSPETTO":"warn","NON ATTENDIBILE":"err"}.get(verd,"queue")
                    icon={"INTEGRO":"✅","SOSPETTO":"⚠️","NON ATTENDIBILE":"🚨"}.get(verd,"?")
                    self.after(0,lambda p=path,t=tag,ic=icon:
                               self.file_tree.item(p,values=(ic,os.path.basename(p),
                                                              format_bytes(os.path.getsize(p))),tags=(t,)))
                    self.after(0,self._display_result,data)
                except Exception as e:
                    self.after(0,self.log,f"ERRORE {os.path.basename(path)}: {e}","err")
                    self.after(0,lambda p=path:
                               self.file_tree.item(p,values=("✗",os.path.basename(p),"—"),tags=("err",)))
            self.after(0,self._analysis_done)
        threading.Thread(target=worker,daemon=True).start()

    def _on_progress(self,pct,msg,filepath):
        self.progress_bar["value"]=pct
        self.lbl_progress.config(text=msg[:32])
        self.set_status(f"{os.path.basename(filepath)} — {msg}")

    def _analysis_done(self):
        self.analyzing=False
        self.btn_run.config(state="normal"); self.btn_stop.config(state="disabled")
        self.progress_bar["value"]=100; self.lbl_progress.config(text="Completato ✓")
        self.set_status("Analisi completata.")

    # ── DISPLAY RISULTATI ─────────────────────────────────────────────────────
    def _display_result(self,data):
        self.current_result=data
        iv=data["integrity"]; wf=data["waveform"]; fi=data["file_info"]
        enf=data.get("enf",{}); butt=data.get("butt_splice",{})

        # ── Sommario banner ───────────────────────────────────────────────────
        vmap={"INTEGRO":("✅",C["accent2"]),"SOSPETTO":("⚠️",C["warn"]),
              "NON ATTENDIBILE":("🚨",C["danger"])}
        icon,col=vmap.get(iv["verdict"],("—",C["text3"]))
        self.lbl_verd_icon.config(text=icon,fg=col,bg=C["panel2"])
        self.lbl_verd_text.config(text=iv["verdict"],fg=col)
        self.lbl_verd_sub.config(
            text=f"{fi['filename']}  ·  {fi['format_detected']}  ·  {BPM_REF}",
            fg=C["text2"])
        self.lbl_score.config(text=str(iv["score"]),fg=col)

        # Card rapide
        self.summary_cards["duration"].config(text=wf["duration_fmt"])
        self.summary_cards["samplerate"].config(text=f"{wf['sample_rate']:,}Hz")
        self.summary_cards["peak"].config(text=f"{wf['peak_db']}dB")
        self.summary_cards["rms"].config(text=f"{wf['rms_db']}dB")
        if enf.get("enf_present"):
            enf_txt = f"{enf['enf_mean_hz']}Hz"
            enf_col = C["accent2"] if enf.get("nominal_match") else C["danger"]
        else:
            enf_txt,enf_col = "—",C["text3"]
        self.summary_cards["enf"].config(text=enf_txt,fg=enf_col)
        butt_n = butt.get("count",0)
        self.summary_cards["butt"].config(
            text=str(butt_n),
            fg=C["danger"] if butt_n>0 else C["accent2"])

        # Anomalie
        for i in self.anom_tree.get_children(): self.anom_tree.delete(i)
        if data["anomalies"]:
            for a in data["anomalies"]:
                self.anom_tree.insert("","end",
                    values=(a["severità"],a["tipo"],a["dettaglio"],a.get("ref","")),
                    tags=(a["severità"],))
        else:
            self.anom_tree.insert("","end",
                values=("—","Nessuna anomalia","✓ File integro",""))

        # ── Grafici ───────────────────────────────────────────────────────────
        if self.chart_canvas_widget:
            self.chart_canvas_widget.get_tk_widget().destroy()
            self.chart_canvas_widget=None
        if hasattr(self,"chart_placeholder") and self.chart_placeholder:
            try: self.chart_placeholder.destroy()
            except: pass
            self.chart_placeholder=None
        fig=data.get("_fig")
        if fig:
            try:
                canvas=FigureCanvasTkAgg(fig,master=self.tab_charts)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="both",expand=True)
                self.chart_canvas_widget=canvas
            except Exception as e:
                Label(self.tab_charts,text=f"Errore grafico: {e}",
                      bg=C["bg"],fg=C["danger"]).pack(expand=True)

        # ── Tab ENF ───────────────────────────────────────────────────────────
        for i in self.enf_tree.get_children(): self.enf_tree.delete(i)
        if enf:
            for k,v in [
                ("Componente ENF rilevata", "✓ Sì" if enf.get("enf_present") else "✗ No"),
                ("Frequenza nominale (impostata)", f"{enf.get('nominal_hz','?')} Hz"),
                ("ENF media misurata", f"{enf.get('enf_mean_hz','N/A')} Hz"),
                ("ENF deviazione std.", f"{enf.get('enf_std_hz','N/A')} Hz"),
                ("Match rete elettrica",
                 "✓ Compatibile" if enf.get("nominal_match") else
                 "⚠ NON compatibile" if enf.get("nominal_match")==False else "N/A"),
                ("Discontinuità di fase/freq.", str(enf.get("phase_jumps",0))),
                ("Timestamp discontinuità",
                 ", ".join(f"{t}s" for t in enf.get("jump_timestamps",[])[:5]) or "—"),
                ("SNR banda ENF", f"{enf.get('snr_db','N/A')} dB"),
                ("Note", enf.get("note","") or "—"),
            ]:
                self.enf_tree.insert("","end",values=(k,v))

        # Butt-splice
        for i in self.splice_tree.get_children(): self.splice_tree.delete(i)
        for sp in butt.get("splices",[]):
            self.splice_tree.insert("","end",
                values=(sp["timestamp_sec"],sp["timestamp_fmt"],
                        sp["ratio"],sp["magnitude"]))

        # DC local
        for i in self.dc_tree.get_children(): self.dc_tree.delete(i)
        dc_l=data.get("dc_local",{})
        for a in dc_l.get("anomalies",[]):
            self.dc_tree.insert("","end",
                values=(a["timestamp_fmt"],a["dc_local"],a["dc_global"],a["delta"]))
        if not dc_l.get("anomalies"):
            self.dc_tree.insert("","end",values=("—","✓ Nessuna anomalia DC locale","",""))

        # ── Tab Advanced (v4.0) ───────────────────────────────────────────────
        # OGG Codec frames
        for i in self.cf_tree.get_children(): self.cf_tree.delete(i)
        for i in self.cf_anom_tree.get_children(): self.cf_anom_tree.delete(i)
        cf = data.get("codec_frames", {})
        if cf and cf.get("applicable"):
            framing_str = "✓ OK" if cf.get("framing_ok") else "⚠ ANOMALIE" if cf.get("framing_ok")==False else "—"
            for k, v in [
                ("Applicabile", "✓ Sì"),
                ("Framing status", framing_str),
                ("Pagine totali", str(cf.get("total_pages", 0))),
                ("Serial streams", str(cf.get("serial_streams", []))),
                ("Granule jumps", str(len(cf.get("granule_jumps", [])))),
                ("Sequence gaps", str(len(cf.get("seq_gaps", [])))),
                ("Page size anomalie", str(len(cf.get("page_size_anomalies", [])))),
                ("Note", cf.get("note", "—") or "—"),
            ]:
                self.cf_tree.insert("","end", values=(k, v))
            for gj in cf.get("granule_jumps", []):
                self.cf_anom_tree.insert("","end",
                    values=(f"GRANULE {gj['tipo']}", gj.get("serial",""), gj.get("offset_hex",""), f"Δ={gj['delta']}"),
                    tags=("jump",))
            for sg in cf.get("seq_gaps", []):
                self.cf_anom_tree.insert("","end",
                    values=("SEQ GAP", sg.get("serial",""), sg.get("offset_hex",""), f"atteso={sg['expected']} trovato={sg['found']}"),
                    tags=("gap",))
        else:
            self.cf_tree.insert("","end", values=("Applicabile", "✗ Non OGG — non applicabile"))

        # Double encoding
        for i in self.de_tree.get_children(): self.de_tree.delete(i)
        de = data.get("double_enc", {})
        if de:
            de_sus = de.get("double_enc_suspected", False)
            for k, v in [
                ("Doppia codifica sospetta", "⚠ SÌ" if de_sus else "✓ No"),
                ("Confidenza", de.get("confidence","—")),
                ("Cutoff frequenza (Hz)", str(de.get("cutoff_freq_hz","N/A"))),
                ("Cutoff anomalo", "⚠ SÌ" if de.get("cutoff_anomaly") else "✓ No"),
                ("Notches spettrali", str(len(de.get("spectral_notches",[])))),
                ("MDCT periodicità", str(de.get("mdct_periodicity","—"))),
                ("Note", de.get("note","—") or "—"),
            ]:
                self.de_tree.insert("","end", values=(k, v))

        # Resampling
        for i in self.rs_tree.get_children(): self.rs_tree.delete(i)
        rs = data.get("resampling", {})
        if rs:
            rs_det = rs.get("resampling_detected", False)
            for k, v in [
                ("Resampling rilevato", "⚠ SÌ" if rs_det else "✓ No"),
                ("Confidenza", rs.get("confidence","—")),
                ("Periodo (campioni)", str(rs.get("period_samples","—"))),
                ("Periodo (ms)", str(rs.get("period_time_ms","—"))),
                ("Ratio stimato P/Q", rs.get("estimated_ratio","—") or "—"),
                ("SR originale stimato", f"{rs.get('original_sr_estimate','—')} Hz" if rs.get("original_sr_estimate") else "—"),
                ("Picco autocorrelazione", str(rs.get("autocorr_peak","—"))),
                ("Note", rs.get("note","—") or "—"),
            ]:
                self.rs_tree.insert("","end", values=(k, v))

        # Copy-Move
        for i in self.cm_tree.get_children(): self.cm_tree.delete(i)
        cm = data.get("copy_move", {})
        if cm:
            matches = cm.get("matches_found", [])
            if matches:
                for m in matches:
                    self.cm_tree.insert("","end",
                        values=(m["t1_fmt"], m["t2_fmt"],
                                f"{m['cosine_sim']:.4f}", f"{m['xcorr_peak']:.3f}",
                                f"{m['gap_sec']:.1f}"),
                        tags=("match",))
            else:
                self.cm_tree.insert("","end",
                    values=("✓ Nessun match","","","",""))

        # ── Tab Dettagli ──────────────────────────────────────────────────────
        for i in self.detail_tree.get_children(): self.detail_tree.delete(i)
        sections = [
            ("── FILE INFO ──",  fi.items()),
            ("── FORMA D'ONDA ──", wf.items()),
            ("── ENF ──",         enf.items() if enf else []),
            ("── QUANTIZZAZIONE ──", data.get("quantization",{}).items()),
            ("── LTAS ──",        {k:v for k,v in data.get("ltas",{}).items()
                                    if k!="ltas_db"}.items()),
            ("── SPETTRALE ──",   {k:v for k,v in data.get("spectral",{}).items()
                                    if k!="mfcc_means"}.items()),
        ]
        if data.get("ogg_info"):
            sections.append(("── OGG HEADER ──", data["ogg_info"].items()))
        for title, items in sections:
            self.detail_tree.insert("","end",values=(title,""),tags=("head",))
            for k,v in items:
                self.detail_tree.insert("","end",values=(k,str(v)[:200]))

        # ── Hash & Metadati ───────────────────────────────────────────────────
        h=data["hashes"]
        self.hash_vars["SHA-1"].set(h.get("sha1","—"))
        self.hash_vars["SHA-256"].set(h.get("sha256","—"))
        self.hash_vars["MD5"].set(h.get("md5","—"))
        self.lbl_cmp.config(text="",bg=C["panel2"])

        for i in self.meta_tree.get_children(): self.meta_tree.delete(i)
        meta=data.get("metadata",{})
        cat_labels={"_functional":"Funzionale","_library":"Library","_software":"Software"}
        any_meta=False
        for cat_key,cat_label in cat_labels.items():
            for k,v in (meta.get(cat_key,{}) or {}).items():
                self.meta_tree.insert("","end",values=(cat_label,k,str(v)[:150]))
                any_meta=True
        if not any_meta:
            self.meta_tree.insert("","end",values=("—","Nessun metadato",""))

    def show_file_result(self):
        sel=self.file_tree.selection()
        if not sel: return
        if sel[0] in self.results: self._display_result(self.results[sel[0]])

    # ── HASH CONFRONTO ────────────────────────────────────────────────────────
    def compare_hash(self):
        ref=self.cmp_entry.get().strip().upper()
        if not ref or not self.current_result: return
        h=self.current_result["hashes"]
        for algo,val in [("SHA1",h.get("sha1","")),
                          ("SHA256",h.get("sha256","")),
                          ("MD5",h.get("md5",""))]:
            if val.upper()==ref:
                self.lbl_cmp.config(
                    text=f"✓ CORRISPONDENZA TROVATA ({algo})",
                    fg=C["accent2"],bg=C["panel2"]); return
        self.lbl_cmp.config(
            text="✗ NESSUNA CORRISPONDENZA — File potenzialmente alterato!",
            fg=C["danger"],bg=C["panel2"])

    def _copy_hash(self,algo):
        v=self.hash_vars.get(algo)
        if v: self.clipboard_clear(); self.clipboard_append(v.get())
        self.set_status(f"{algo} copiato")

    # ── OUTPUT & REPORT ───────────────────────────────────────────────────────
    def open_report(self):
        if not self.current_result:
            messagebox.showinfo("Nessun risultato","Esegui prima un'analisi."); return
        rp=self.current_result.get("_report_path")
        if rp and os.path.isfile(rp): webbrowser.open(f"file://{os.path.abspath(rp)}")
        else: messagebox.showerror("Report non trovato","File non trovato.")

    def open_output_dir(self):
        d=self.output_dir.get(); os.makedirs(d,exist_ok=True)
        if platform.system()=="Windows": os.startfile(d)
        elif platform.system()=="Darwin": subprocess.Popen(["open",d])
        else: subprocess.Popen(["xdg-open",d])

    def export_json(self):
        if not self.current_result:
            messagebox.showinfo("Nessun risultato","Esegui prima un'analisi."); return
        dest=filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON","*.json"),("Tutti","*.*")],
            initialfile=f"{Path(self.current_result['file_info']['filename']).stem}_forensics.json")
        if dest:
            d={k:v for k,v in self.current_result.items() if not k.startswith("_")}
            with open(dest,"w",encoding="utf-8") as f:
                json.dump(d,f,ensure_ascii=False,indent=2,default=str)
            self.set_status(f"JSON esportato: {os.path.basename(dest)}")

    # ── LOG ───────────────────────────────────────────────────────────────────
    def log(self,msg,tag="norm"):
        ts=datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end",f"[{ts}] {msg}\n" if msg else "\n",tag)
        self.log_text.see("end"); self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0","end")
        self.log_text.config(state="disabled")

    def set_status(self,msg):
        self.lbl_status.config(text=f"  {msg}")

    # ── DIPENDENZE ────────────────────────────────────────────────────────────
    def _check_deps(self):
        if MISSING:
            self.log(f"⚠ Dipendenze mancanti: {', '.join(MISSING)}","warn")
            self.log(f"  pip install {' '.join(MISSING)}","warn")
        else:
            self.log(f"✓ Dipendenze OK  |  {BPM_REF}","ok")

    def _check_deps_dialog(self):
        deps=["numpy","librosa","soundfile","matplotlib","mutagen","scipy"]
        lines=[]
        for d in deps:
            try:
                mod=__import__(d); v=getattr(mod,"__version__","?")
                lines.append(f"  ✓  {d:<16} {v}")
            except ImportError:
                lines.append(f"  ✗  {d:<16} NON INSTALLATO")
        messagebox.showinfo("Verifica dipendenze","\n".join(lines))

    def show_enfsi_refs(self):
        refs = (
            "ENFSI BPM-FSA-002 — Best Practice Manual for Digital Audio Authenticity Analysis\n"
            "SWGDE — Best Practices for Digital Audio Authentication v1.2\n\n"
            "Metodi implementati in v4.0:\n"
            "  §5.4.1  ENF Analysis (Grigoras [47], Michałek [51])\n"
            "  §5.4.2  Butt-splice detection (Cooper [23])\n"
            "  §5.4.2  DC Offset locale (Koenig [20][21][22])\n"
            "  §5.4.3  OGG Codec frame analysis (Gärtner [33], Korycki [34], Yang [35])\n"
            "  §5.4.4  Double encoding detection (Bianchi [69], Korycki [71])\n"
            "  §5.4.4  Resampling detection (Vázquez-Padín [67][68])\n"
            "  §5.4.4  Copy-move forgery (Imran [62], Maksimović [63])\n"
            "  §5.4.4  Quantization level analysis (Grigoras [24][25])\n"
            "  §5.4.4  Metadata categorization (Michałek [12])\n"
            "  §5.4.5  LTAS/LTASS (Grigoras [24][28])\n"
            "  §8      Hash SHA-1/SHA-256/MD5\n"
        )
        messagebox.showinfo("Riferimenti ENFSI BPM-FSA-002 v4.0", refs)

    def show_about(self):
        win=tk.Toplevel(self); win.title("Informazioni")
        win.geometry("500x360"); win.resizable(False,False)
        win.configure(bg=C["panel"]); win.transient(self); win.grab_set()
        Label(win,text="⚖ Audio Forensics Analyzer",
              font=("Segoe UI",14,"bold"),bg=C["panel"],fg=C["accent"]).pack(pady=(20,4))
        Label(win,text=f"Versione {VERSION}  —  Conforme {BPM_REF}",
              font=("Segoe UI",9),bg=C["panel"],fg=C["text2"]).pack()
        ttk.Separator(win,orient="horizontal").pack(fill="x",padx=20,pady=14)
        for lbl,val in [
            ("Nuovo v4.0","OGG frame analysis · Double encoding · Resampling detection"),
            ("","Copy-move forgery · 13 moduli ENFSI totali"),
            ("v3.0","ENF Analysis · Butt-splice · DC Offset locale · Quantizzazione"),
            ("Formati","WAV · MP3 · OGG · FLAC · AIFF · M4A · WMA · OPUS"),
            ("Framework","Python · librosa · soundfile · mutagen · scipy"),
            ("Standard","ENFSI BPM-FSA-002 / SWGDE v1.2"),
        ]:
            r=Frame(win,bg=C["panel"]); r.pack(fill="x",padx=20,pady=1)
            if lbl:
                Label(r,text=lbl+":",font=("Segoe UI",9,"bold"),bg=C["panel"],
                      fg=C["text3"],width=12,anchor="e").pack(side="left")
            Label(r,text=val,font=("Segoe UI",9),bg=C["panel"],fg=C["text"]).pack(side="left",padx=6)
        Button(win,text="Chiudi",command=win.destroy,bg=C["accent"],fg="#fff",
               relief="flat",font=("Segoe UI",9),padx=16,pady=4,cursor="hand2").pack(pady=18)

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("--gui",):
        import argparse
        parser=argparse.ArgumentParser(description="Audio Forensics Analyzer CLI")
        parser.add_argument("files",nargs="+")
        parser.add_argument("-o","--output",default="./forensics_output")
        parser.add_argument("--enf",type=float,default=50.0,
                            help="Frequenza nominale ENF (50=EU, 60=USA)")
        args=parser.parse_args()
        os.makedirs(args.output,exist_ok=True)
        for fp in args.files:
            if os.path.isfile(fp):
                run_analysis(fp,args.output,
                             lambda p,m:print(f"[{p:3d}%] {m}"),
                             lambda m,t="":print(f"  {m}"),
                             enf_nominal=args.enf)
        return
    app=AudioForensicsApp()
    app.mainloop()

if __name__=="__main__":
    main()
