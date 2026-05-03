#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        AUDIO FORENSICS ANALYZER  —  GUI Edition  v3.0                  ║
║        Strumento forense per l'analisi di file audio                    ║
║        Conforme: ENFSI BPM-FSA-002 / SWGDE 1.2                         ║
║        Supporta: WAV · MP3 · OGG · FLAC · AIFF · M4A · OPUS · WMA     ║
╚══════════════════════════════════════════════════════════════════════════╝

CHANGELOG v3.0 — vedi CHANGELOG.md

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
VERSION       = "3.0.0"
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
# ANALISI ANOMALIE (aggiornata v3.0)
# ─────────────────────────────────────────────────────────────────────────────

def detect_anomalies(y, sr, wf, enf, dc_local, butt, quant):
    """
    Rileva anomalie forensi integrando tutti i moduli v3.0.
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

    # ── 3. DC Offset locale (NUOVO v3.0) ─────────────────────────────────────
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
        silence_runs=[]
        for i,s in enumerate(is_sil):
            if s and not in_s: in_s=True; st=i
            elif not s and in_s:
                in_s=False
                d=((i-st)*hl)/sr
                if d>=SILENCE_MIN_SEC: silence_runs.append((st*hl/sr,d))
        if in_s:
            d=((len(is_sil)-st)*hl)/sr
            if d>=SILENCE_MIN_SEC: silence_runs.append((st*hl/sr,d))
        for t_start, dur in silence_runs:
            anoms.append({"tipo":"SILENZIO ANOMALO",
                "severità":"MEDIA" if dur>3 else "BASSA",
                "dettaglio":f"{dur:.2f}s @ t={format_dur(t_start)}",
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

    # ── 6. Butt-splice PCM (NUOVO v3.0, Cooper method) ────────────────────────
    if butt and butt.get("splices"):
        for sp in butt["splices"][:10]:  # max 10 segnalazioni
            anoms.append({"tipo":"BUTT-SPLICE PCM","severità":"ALTA",
                "dettaglio":f"Taglio netto @ t={sp['timestamp_fmt']} "
                            f"(ratio={sp['ratio']:.1f}σ)",
                "forense":"Taglio diretto tra campioni PCM — forte indice di editing",
                "ref":"§5.4.2 BPM-FSA-002, Cooper [23]"})

    # ── 7. ENF — discontinuità di fase (NUOVO v3.0) ───────────────────────────
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

    # ── 8. Quantizzazione (NUOVO v3.0) ────────────────────────────────────────
    if quant and quant.get("suspected_gain"):
        anoms.append({"tipo":"GAIN DIGITALE SOSPETTO","severità":"MEDIA",
            "dettaglio":f"Gap periodici nel'istogramma (periodo={quant['periodic_gap']})",
            "forense":"Possibile modifica del guadagno digitale post-registrazione",
            "ref":"§5.4.4 BPM-FSA-002, Grigoras [24]"})

    if quant and quant.get("real_bit_depth") and \
       quant["real_bit_depth"] < quant.get("bit_depth_declared",16) - 2:
        anoms.append({"tipo":"BIT-DEPTH REALE RIDOTTO","severità":"BASSA",
            "dettaglio":f"Bit-depth reale stimato: {quant['real_bit_depth']} "
                        f"(dichiarato: {quant['bit_depth_declared']})",
            "forense":"ADC con risoluzione inferiore al dichiarato",
            "ref":"§5.4.5 BPM-FSA-002, Grigoras [24]"})

    # ── 9. Kurtosis ───────────────────────────────────────────────────────────
    if wf["kurtosis"] > 10:
        anoms.append({"tipo":"KURTOSIS ELEVATA","severità":"BASSA",
            "dettaglio":f"k={wf['kurtosis']} (atteso 0-6 per audio naturale)",
            "forense":"Impulsi/click o manipolazione del segnale",
            "ref":"§5.2 BPM-FSA-002"})

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


def integrity_verdict(anoms, fmt, ext, enf, butt, dc_local, quant):
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
    progress_cb(80, "Analisi spettrale + LTAS...")

    sp   = spectral_features(y, sr)
    ltas = compute_ltas(y, sr)
    log_cb(f"  Centroide spettrale: {sp.get('centroid_mean_hz','N/A')} Hz", "info")
    progress_cb(87, "Rilevamento anomalie integrate...")

    anoms = detect_anomalies(y, sr, wf, enf, dc_local, butt, quant)
    log_cb(f"  Anomalie totali: {len(anoms)}", "warn" if anoms else "ok")
    for a in anoms:
        col = {"ALTA":"err","MEDIA":"warn","BASSA":"info"}.get(a["severità"],"info")
        log_cb(f"    ⚑ [{a['severità']}] {a['tipo']}: {a['dettaglio']}", col)
    progress_cb(92, "Grafici...")

    fig = generate_plots_fig(y, sr, enf_data=enf, butt_data=butt)
    plot_path = save_plots_png(fig, out_dir, bn)
    log_cb(f"  Grafici: {os.path.basename(plot_path)}", "info")
    progress_cb(96, "Report HTML...")

    iv = integrity_verdict(anoms, fi["format_detected"], ext,
                           enf, butt, dc_local, quant)
    data = {
        "file_info":   fi,
        "hashes":      hashes,
        "metadata":    meta,
        "ogg_info":    ogg_info,
        "waveform":    wf,
        "enf":         enf,
        "butt_splice": butt,
        "dc_local":    dc_local,
        "quantization":quant,
        "ltas":        ltas,
        "anomalies":   anoms,
        "spectral":    sp,
        "integrity":   iv,
        "bpm_ref":     BPM_REF,
        "analysis_ts": datetime.datetime.now().isoformat(),
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
            "Metodi implementati:\n"
            "  §5.4.1  ENF Analysis (Grigoras [47], Michałek [51])\n"
            "  §5.4.2  Butt-splice detection (Cooper [23])\n"
            "  §5.4.2  DC Offset locale (Koenig [20][21][22])\n"
            "  §5.4.4  Quantization level analysis (Grigoras [24][25])\n"
            "  §5.4.4  Metadata categorization (Michałek [12])\n"
            "  §5.4.5  LTAS/LTASS (Grigoras [24][28])\n"
            "  §8      Hash SHA-1/SHA-256/MD5\n"
        )
        messagebox.showinfo("Riferimenti ENFSI BPM-FSA-002",refs)

    def show_about(self):
        win=tk.Toplevel(self); win.title("Informazioni")
        win.geometry("480x320"); win.resizable(False,False)
        win.configure(bg=C["panel"]); win.transient(self); win.grab_set()
        Label(win,text="⚖ Audio Forensics Analyzer",
              font=("Segoe UI",14,"bold"),bg=C["panel"],fg=C["accent"]).pack(pady=(20,4))
        Label(win,text=f"Versione {VERSION}  —  Conforme {BPM_REF}",
              font=("Segoe UI",9),bg=C["panel"],fg=C["text2"]).pack()
        ttk.Separator(win,orient="horizontal").pack(fill="x",padx=20,pady=14)
        for lbl,val in [
            ("Nuovo v3.0","ENF Analysis · Butt-splice PCM · DC Offset locale"),
            ("","Quantization analysis · LTAS · Metadati categorizzati"),
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
